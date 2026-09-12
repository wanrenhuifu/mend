"""git 封装：只做三件事——是不是仓库、状态、diff。

评测和回滚都靠"复制一份干净仓库再干活"实现（见 evaluator.py），
这里不提供任何会改动用户仓库的函数。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _git(root: Path | str, *args: str) -> tuple[int, str]:
    exe = shutil.which("git")
    if not exe:
        return 127, "没有安装 git"
    try:
        proc = subprocess.run(
            [exe, *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"git 执行失败: {exc}"
    out = proc.stdout or ""
    if proc.stderr:
        out = f"{out}\n{proc.stderr}" if out else proc.stderr
    return proc.returncode, out


def is_repo(root: Path | str) -> bool:
    return (Path(root).resolve() / ".git").exists()


def status(root: Path | str) -> str:
    code, out = _git(root, "status", "--short")
    return out.strip() if code == 0 else f"(git status 失败: {out.strip()[:200]})"


def dirty_paths(root: Path | str) -> set[str]:
    """当前有改动的文件（含未跟踪）。

    用来在运行前后取差集，隔离出"这次运行到底改了什么"——
    不能只看写文件工具，因为模型也可能用 run_command 跑脚本或 formatter 改文件。
    """
    if not is_repo(root):
        return set()
    code, out = _git(root, "status", "--porcelain", "--untracked-files=all")
    if code != 0:
        return set()
    paths: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        entry = line[3:].strip()
        if "->" in entry:  # 重命名：只关心新名字
            entry = entry.split("->", 1)[1].strip()
        if entry:
            paths.add(entry.strip('"'))
    return paths


def diff(root: Path | str, base: str = "HEAD", paths: list[str] | None = None) -> str:
    """工作区相对 base 的改动。

    paths 不为空时只报这些文件——这样报告里出现的是"这次运行改了什么"，
    而不是工作区里所有未提交的改动（包括你自己手动改的）。
    """
    if not is_repo(root):
        return ""
    args = ["diff", base]
    if paths:
        args += ["--", *paths]
    code, tracked = _git(root, *args)
    if code != 0:
        tracked = f"(git diff 失败: {tracked.strip()[:200]})"
    _, short = _git(root, "status", "--short", "--untracked-files=all")
    untracked = [line[3:].strip() for line in short.splitlines() if line.startswith("??")]
    if paths:
        wanted = set(paths)
        untracked = [path for path in untracked if path in wanted]
    if untracked:
        tracked += "\n# 新增文件（还没有加入 git）\n" + "\n".join(f"?? {path}" for path in untracked)
    return tracked.strip()
