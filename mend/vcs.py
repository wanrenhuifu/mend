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


def diff(root: Path | str, base: str = "HEAD") -> str:
    """已跟踪文件的改动 + 未跟踪的新文件，都算进这次运行的产出。"""
    if not is_repo(root):
        return ""
    code, tracked = _git(root, "diff", base)
    if code != 0:
        tracked = f"(git diff 失败: {tracked.strip()[:200]})"
    _, short = _git(root, "status", "--short", "--untracked-files=all")
    untracked = [line[3:].strip() for line in short.splitlines() if line.startswith("??")]
    if untracked:
        tracked += "\n# 新增文件（还没有加入 git）\n" + "\n".join(f"?? {p}" for p in untracked)
    return tracked.strip()
