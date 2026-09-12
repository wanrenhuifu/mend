"""工具层：agent 唯一能碰世界的入口。

三级权限：read（只看）/ write（改文件）/ shell（跑命令）。
只读模式是**结构上**保证的：registry 按级别挂载工具，模型拿不到写工具的 schema；
即使它幻觉出一个写调用，也会在 call() 里被挡掉。不靠提示词求模型"别乱改"。
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .context import iter_files

READ_LINE_LIMIT = 400
MAX_OUTPUT_CHARS = 8000
LIST_LIMIT = 300


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""

    def text(self) -> str:
        if self.ok:
            return self.output or "(没有输出)"
        return f"错误: {self.error}" if self.error else (self.output or "(没有输出)")


@dataclass
class Tool:
    name: str
    description: str
    level: str  # read | write | shell
    parameters: dict[str, Any]
    handler: Callable[..., ToolResult]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _schema(required: list[str], **props: tuple[str, str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": kind, "description": doc} for name, (kind, doc) in props.items()},
        "required": required,
    }


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    """超长输出保留头尾：报错集中在末尾，开头有命令和退出码。"""
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit - limit // 3 - 60) :]
    return f"{head}\n...（省略 {len(text) - len(head) - len(tail)} 字符）...\n{tail}"


class ToolRegistry:
    def __init__(
        self,
        root: Path | str,
        cfg,
        trace=None,
        levels: tuple[str, ...] = ("read", "write", "shell"),
    ) -> None:
        self.root = Path(root).resolve()
        self.cfg = cfg
        self.trace = trace
        self.levels = tuple(levels)
        self.touched: list[str] = []  # 本次运行改过的文件，用来把 diff 收窄到"它自己改的"
        self._tools: dict[str, Tool] = {}
        self._register_all()

    # ---------- 对外接口 ----------

    def tools(self) -> list[Tool]:
        """只返回已挂载级别的工具（模型能看到的 schema 就从这里来）。"""
        return [tool for tool in self._tools.values() if tool.level in self.levels]

    def call(self, name: str, args: dict[str, Any], record: bool = True) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, error=f"没有这个工具: {name}；可用工具: {[t.name for t in self.tools()]}")
        if tool.level not in self.levels:
            return ToolResult(False, error=f"{name}（{tool.level} 级）没有挂载，当前只挂载了 {list(self.levels)}")
        if not isinstance(args, dict):
            return ToolResult(False, error="参数必须是一个对象")

        allowed = set((tool.parameters.get("properties") or {}).keys())
        unknown = set(args) - allowed
        if unknown:
            return ToolResult(False, error=f"{name} 不支持参数 {sorted(unknown)}；可用参数: {sorted(allowed)}")
        missing = set(tool.parameters.get("required") or []) - set(args)
        if missing:
            return ToolResult(False, error=f"{name} 缺少参数 {sorted(missing)}")

        started = time.time()
        try:
            result = tool.handler(**args)
        except ValueError as exc:
            result = ToolResult(False, error=str(exc))
        except OSError as exc:
            result = ToolResult(False, error=f"文件系统错误: {exc}")
        except subprocess.SubprocessError as exc:
            result = ToolResult(False, error=f"命令执行失败: {exc}")

        if result.ok and tool.level == "write":
            path_arg = args.get("path")
            if isinstance(path_arg, str):
                try:
                    relative = self._resolve(path_arg).relative_to(self.root).as_posix()
                except (ValueError, OSError):
                    relative = path_arg
                if relative not in self.touched:
                    self.touched.append(relative)

        if self.trace is not None and record:
            self.trace.record(
                "tool",
                tool.name,
                result.ok,
                int((time.time() - started) * 1000),
                level=tool.level,  # 让输出层能按权限级别上色
                args=args,
                result=_truncate(result.text(), 400),
            )
        return result

    # ---------- 内部：路径安全 ----------

    def _resolve(self, path: str) -> Path:
        """把路径解析到仓库内；越界或命中禁写目录直接抛错，而不是"尽量执行"。"""
        raw = (path or ".").strip()
        candidate = Path(raw) if Path(raw).is_absolute() else self.root / raw
        candidate = candidate.resolve()
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"路径越界，只允许操作仓库内的文件: {path}") from exc
        for part in relative.parts:
            if part in self.cfg.forbidden_paths:
                raise ValueError(f"禁止访问 {part}/（在 mend.toml 的 forbidden_paths 里）")
        return candidate

    # ---------- read ----------

    def _list_dir(self, path: str = ".") -> ToolResult:
        target = self._resolve(path)
        if not target.is_dir():
            return ToolResult(False, error=f"不是目录: {path}")
        rows: list[str] = []
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
            try:
                entry.relative_to(self.root)
            except ValueError:
                continue
            if any(part in self.cfg.forbidden_paths for part in entry.relative_to(self.root).parts):
                continue
            rows.append(f"{entry.name}/" if entry.is_dir() else entry.name)
        return ToolResult(True, "\n".join(rows[:LIST_LIMIT]) or "(空目录)")

    def _read_file(self, path: str, start: int = 1, end: int = 0) -> ToolResult:
        target = self._resolve(path)
        if not target.is_file():
            return ToolResult(False, error=f"文件不存在: {path}")
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        first = max(1, int(start or 1))
        last = int(end) if end else min(len(lines), first + READ_LINE_LIMIT - 1)
        chunk = lines[first - 1 : last]
        body = "\n".join(f"{first + i:>5}| {line}" for i, line in enumerate(chunk))
        return ToolResult(True, f"{path} 共 {len(lines)} 行，显示 {first}-{last}：\n{body}")

    def _search(self, pattern: str, glob: str = "", limit: int = 60) -> ToolResult:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return ToolResult(False, error=f"正则不合法: {exc}")
        cap = max(1, min(int(limit or 60), 200))
        hits: list[str] = []
        for path in iter_files(self.root, self.cfg):
            if glob and not path.match(glob):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = path.relative_to(self.root).as_posix()
            for number, line in enumerate(text.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{rel}:{number}: {line.strip()[:160]}")
                    if len(hits) >= cap:
                        return ToolResult(True, "\n".join(hits))
        return ToolResult(True, "\n".join(hits) or f"没有匹配: {pattern}")

    # ---------- write ----------

    def _write_file(self, path: str, content: str) -> ToolResult:
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolResult(True, f"已写入 {path}（{len(content.splitlines())} 行）")

    def _edit_file(self, path: str, old: str, new: str) -> ToolResult:
        """精确替换：old 必须在文件里唯一出现，否则拒绝执行。

        这是"最小改动"的关键：宁可让模型重新读文件、给更长的上下文，
        也不要"猜一个位置"去改——猜错一次，后面所有验证都是白跑。
        """
        target = self._resolve(path)
        if not target.is_file():
            return ToolResult(False, error=f"文件不存在: {path}")
        text = target.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            return ToolResult(False, error=f"在 {path} 里找不到要替换的片段: {old[:80]!r}")
        if count > 1:
            return ToolResult(False, error=f"片段在 {path} 里出现 {count} 次，不唯一，请带上更多上下文")
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        return ToolResult(True, f"已修改 {path}")

    # ---------- shell ----------

    def _run_command(self, command: str, timeout_s: int = 0) -> ToolResult:
        """受限执行：命令白名单 + 超时 + 非 shell 调用（不做字符串拼接）。"""
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            return ToolResult(False, error=f"命令解析失败: {exc}")
        if not parts:
            return ToolResult(False, error="空命令")
        allowed = {name.lower() for name in self.cfg.allowed_commands}
        if Path(parts[0]).name.lower() not in allowed:
            return ToolResult(False, error=f"{parts[0]} 不在白名单里；允许: {sorted(allowed)}")
        exe = shutil.which(parts[0])  # Windows 上要这样解析 .cmd / .exe
        if not exe:
            return ToolResult(False, error=f"找不到可执行文件: {parts[0]}")

        timeout = int(timeout_s or self.cfg.timeout_s)
        started = time.time()
        try:
            proc = subprocess.run(
                [exe, *parts[1:]],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, error=f"命令超时（>{timeout}s）: {command}")
        duration = int((time.time() - started) * 1000)
        body = proc.stdout or ""
        if proc.stderr:
            body = f"{body}\n{proc.stderr}" if body else proc.stderr
        head = f"$ {command}\nexit={proc.returncode} ({duration}ms)"
        payload = _truncate(f"{head}\n{body}")
        return ToolResult(proc.returncode == 0, output=payload)

    def _git_short(self, *args: str) -> ToolResult:
        exe = shutil.which("git")
        if not exe:
            return ToolResult(False, error="没有安装 git")
        proc = subprocess.run(
            [exe, *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        body = (proc.stdout or "") + (proc.stderr or "")
        return ToolResult(proc.returncode == 0, output=_truncate(body.strip() or "(没有输出)"))

    # ---------- 注册表 ----------

    def _add(self, name: str, description: str, level: str, parameters: dict[str, Any], handler) -> None:
        self._tools[name] = Tool(name, description, level, parameters, handler)

    def _register_all(self) -> None:
        self._add(
            "list_dir",
            "列出目录内容（只读）。先用它建立对仓库结构的基本认识。",
            "read",
            _schema([], path=("string", "相对仓库根的目录路径，默认 .")),
            self._list_dir,
        )
        self._add(
            "read_file",
            "读文件内容，返回带行号的文本（只读）。默认最多 400 行，可用 start/end 翻页。",
            "read",
            _schema(
                ["path"],
                path=("string", "相对仓库根的文件路径"),
                start=("integer", "起始行，默认 1"),
                end=("integer", "结束行，默认自动"),
            ),
            self._read_file,
        )
        self._add(
            "search",
            "用正则搜索整个仓库（只读）。找函数定义、报错信息、配置项时比逐个读文件快。",
            "read",
            _schema(
                ["pattern"],
                pattern=("string", "正则表达式"),
                glob=("string", "只搜匹配该模式的路径，例如 **/test_*.py"),
                limit=("integer", "最多返回条数，默认 60"),
            ),
            self._search,
        )
        self._add(
            "git_status",
            "查看工作区改动状态（只读）。",
            "read",
            _schema([]),
            lambda: self._git_short("status", "--short"),
        )
        self._add(
            "git_diff",
            "查看当前相对某个提交的 diff（只读）。改完之后看自己改了什么很有用。",
            "read",
            _schema([], base=("string", "基线提交，默认 HEAD")),
            lambda base="HEAD": self._git_short("diff", base),
        )
        self._add(
            "write_file",
            "写入整个文件（新建文件用这个；改已有文件优先用 edit_file）。",
            "write",
            _schema(["path", "content"], path=("string", "文件路径"), content=("string", "完整文件内容")),
            self._write_file,
        )
        self._add(
            "edit_file",
            "精确替换：把文件里唯一出现的一段 old 换成 new。改动最小，优先使用。",
            "write",
            _schema(
                ["path", "old", "new"],
                path=("string", "文件路径"),
                old=("string", "要被替换的原文，必须与文件内容完全一致且只出现一次"),
                new=("string", "替换后的内容"),
            ),
            self._edit_file,
        )
        self._add(
            "run_command",
            "在仓库根目录执行命令（有白名单和超时限制）。跑测试、跑 linter 用它。",
            "shell",
            _schema(
                ["command"],
                command=("string", "要执行的命令，例如 python -m pytest -q"),
                timeout_s=("integer", "超时秒数，默认用配置里的 timeout_s"),
            ),
            self._run_command,
        )
