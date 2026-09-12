"""终端输出：颜色 + 排版。零依赖，纯 ANSI。

三条原则：
1. 不是 TTY 就不输出颜色——管道、重定向、CI 日志里不该出现一堆转义码。
2. 尊重 NO_COLOR 和 FORCE_COLOR 这两个通行约定（https://no-color.org）。
3. 颜色只用来区分信息等级（成功 / 失败 / 元信息），不用来装饰。

为什么不用 rich / colorama：核心零依赖是这个项目的卖点（见 docs/design.md 的 ADR-3），
而颜色只是锦上添花，不值得为它引入一个依赖。
"""

from __future__ import annotations

import os
import sys
from typing import Any

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"

LEVEL_COLOR = {"read": BLUE, "write": YELLOW, "shell": MAGENTA}


def detect_color(mode: str = "auto") -> bool:
    if mode == "always":
        return True
    if mode == "never":
        return False
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


class Console:
    def __init__(self, color: bool = False) -> None:
        self.color = color

    def paint(self, text: str, *codes: str) -> str:
        if not self.color or not codes:
            return text
        return f"{''.join(codes)}{text}{RESET}"


console = Console()


def configure(mode: str = "auto") -> None:
    """cli.main 在最开始调用一次。"""
    console.color = detect_color(mode)


# ---------- 取值：轨迹既可能是 Step 对象（运行时），也可能是 dict（从 jsonl 读回来） ----------


def _get(step: Any, key: str, default: Any = None) -> Any:
    if isinstance(step, dict):
        return step.get(key, default)
    return getattr(step, key, default)


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _last_line(text: str) -> str:
    for line in reversed((text or "").splitlines()):
        if line.strip():
            return line.strip()
    return ""


# ---------- 摘要与单行渲染 ----------


def step_summary(step: Any, limit: int = 84) -> str:
    """一句话说清这一步干了什么。宁可少说，也不要让一行糊满整个屏幕。"""
    kind = _get(step, "kind", "")
    detail = _get(step, "detail") or {}

    if kind == "llm":
        tools = detail.get("tools")
        return "-> " + ", ".join(tools) if tools else _clip(detail.get("text") or "", limit)

    if kind == "tool":
        name = str(_get(step, "name", "") or "")
        detail_result = detail.get("result") or ""
        args = detail.get("args") or {}
        lines = [line.strip() for line in detail_result.splitlines() if line.strip()]
        if name == "run_command":
            # 首行是命令本身，第二行（退出码 + 耗时）信息量更大
            head_line = lines[0] if lines else ""
            body = lines[1] if len(lines) > 1 and head_line.startswith("$ ") else head_line
            return _clip(body, limit)
        if name == "search":
            return _clip(f"匹配 {args.get('pattern', '')}: {_first_line(detail_result)}", limit)
        return _clip(_first_line(detail_result), limit)

    if kind == "verify":
        verdict = "通过" if _get(step, "ok", True) else "失败"
        # 测试输出的最后一行通常是 "3 passed in 0.28s" 这种摘要，比命令本身有用
        detail_text = detail.get("reason") or _last_line(detail.get("tail") or "")
        return _clip(f"{verdict}  {detail_text}", limit)

    if kind == "context":
        hints = detail.get("hints") or []
        return "候选文件: " + _clip(", ".join(hints), limit - 10) if hints else "候选文件: 无"

    if kind == "judge":
        return detail.get("note") or ("通过" if _get(step, "ok", True) else "失败")

    if kind == "stop":
        verified = "通过" if detail.get("verified") else "未验证"
        changed = "有" if detail.get("changed") else "无"
        return f"验证={verified}  改动={changed}"

    return ""


def step_line(step: Any, width: int = 84) -> str:
    index = _get(step, "index", 0)
    ok = _get(step, "ok", True)
    kind = str(_get(step, "kind", ""))
    name = str(_get(step, "name", "") or "")
    at_ms = int(_get(step, "at_ms", 0) or 0)
    duration = int(_get(step, "duration_ms", 0) or 0)
    level = (_get(step, "detail") or {}).get("level")

    mark = console.paint("ok  ", GREEN) if ok else console.paint("FAIL", RED)
    kind_text = console.paint(f"{kind:<7}", LEVEL_COLOR.get(level, "")) if level else f"{kind:<7}"
    head = f"#{index:<3}{mark} {kind_text} {name:<12}"
    when = console.paint(f"+{at_ms / 1000:>5.1f}s", GREY)
    return f"{head} {when} {duration:>6}ms  {step_summary(step, width)}"


def _detail_lines(step: Any, limit: int = 4) -> list[str]:
    detail = _get(step, "detail") or {}
    text = detail.get("result") or detail.get("tail") or detail.get("reason") or ""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    if len(lines) <= limit:
        return lines
    # 报错集中在末尾：宁可只显示最后几行，也不要显示 pytest 的横幅
    return ["...", *lines[-limit:]]


def print_step(step: Any) -> None:
    """一边跑一边打一行，让人看得见 agent 在动（作为 Trace 的 on_step 回调）。"""
    print(step_line(step), flush=True)
    if not _get(step, "ok", True):
        for line in _detail_lines(step):
            print(console.paint(f"      {line}", GREY), flush=True)


# ---------- 运行记录 ----------


def render_run(records: list[Any], width: int = 84) -> str:
    """把一次运行渲染成"记录"：先元信息，再按时间顺序的每一步。"""
    header = next((item for item in records if _get(item, "kind") == "run"), None)
    steps = [item for item in records if _get(item, "kind") != "run"]
    lines: list[str] = []

    if header is not None:
        lines.append(console.paint(f"运行  {_get(header, 'run_id', '')}", BOLD, CYAN))
        lines.append(f"任务  {_clip(_get(header, 'task', ''), width - 6)}")
        lines.append(console.paint(f"仓库  {_get(header, 'root', '')}", GREY))
        lines.append(console.paint(f"开始  {_get(header, 'started', '')}", GREY))

    stop = next((item for item in reversed(steps) if _get(item, "kind") == "stop"), None)
    if stop is not None:
        detail = _get(stop, "detail") or {}
        status = str(_get(stop, "name", "") or "")
        status_text = console.paint(status, GREEN if status == "done" else YELLOW)
        elapsed = max(
            (int(_get(item, "at_ms", 0) or 0) + int(_get(item, "duration_ms", 0) or 0)) for item in steps
        ) / 1000
        verified = "测试通过" if detail.get("verified") else "未验证"
        changed = "有改动" if detail.get("changed") else "无改动"
        lines.append(
            f"结果  {status_text}  {verified}  {changed}  "
            f"步数 {detail.get('steps', '?')}  用时 {elapsed:.1f}s"
        )

    lines.append("")
    if not steps:
        lines.append(console.paint("(这次运行没有任何步骤)", GREY))
    for step in steps:
        lines.append(step_line(step, width))
        if not _get(step, "ok", True):
            for line in _detail_lines(step):
                lines.append(console.paint(f"      {line}", GREY))
    return "\n".join(lines)


# ---------- diff 上色 ----------


def colorize_diff(text: str) -> str:
    """diff 的配色按 git 的通行约定：加了是绿，删了是红，头是粗体，注解是灰。"""
    lines: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith(("diff ", "index ", "+++", "---")):
            lines.append(console.paint(line, BOLD))
        elif line.startswith("@@"):
            lines.append(console.paint(line, CYAN))
        elif line.startswith(("+", "-")):
            lines.append(console.paint(line, GREEN if line.startswith("+") else RED))
        elif line.startswith("??"):
            lines.append(console.paint(line, YELLOW))
        elif line.startswith("#"):
            lines.append(console.paint(line, GREY))
        else:
            lines.append(line)
    return "\n".join(lines)
