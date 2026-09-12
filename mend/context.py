"""仓库地图与上下文选择。

核心取舍：不要把仓库塞进上下文。先给模型一张地图（目录 + 文件名 + 行数），
再按任务关键词初筛出少量候选文件。上下文长度 = 成本 = 延迟，
而且"上下文里塞了什么"是 agent 最常见的失败来源。
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

MAX_MAP_FILES = 200
MAX_SCAN_BYTES = 200_000  # 超大文件（日志、压缩包）不参与关键词扫描


def _forbidden(path: Path, root: Path, cfg) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return True
    return any(part in cfg.forbidden_paths for part in parts)


def iter_files(root: Path | str, cfg, limit: int = 5000) -> list[Path]:
    """列出仓库里的文件。优先用 git（这样 .gitignore 自动生效），不是 git 仓库时退回 os.walk。"""
    root = Path(root).resolve()
    files: list[Path] = []

    if (root / ".git").exists():
        try:
            proc = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and proc.returncode == 0:
            for line in proc.stdout.splitlines():
                candidate = root / line.strip()
                if candidate.is_file() and not _forbidden(candidate, root, cfg):
                    files.append(candidate)
            return files[:limit]

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [d for d in dirnames if not _forbidden(current / d, root, cfg)]
        for name in filenames:
            candidate = current / name
            if not _forbidden(candidate, root, cfg):
                files.append(candidate)
    return files[:limit]


def _count_lines(path: Path) -> int:
    try:
        if path.stat().st_size > 2_000_000:
            return -1  # 太大，懒得数
        return len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    except OSError:
        return 0


def repo_map(root: Path | str, cfg, max_files: int = MAX_MAP_FILES) -> str:
    """目录级摘要：让模型先知道"仓库里有什么、每个文件多大"，再决定读谁。"""
    root = Path(root).resolve()
    files = iter_files(root, cfg)
    by_dir: dict[str, list[Path]] = {}
    for path in files[:max_files]:
        rel = path.relative_to(root)
        by_dir.setdefault(rel.parent.as_posix() or ".", []).append(path)

    lines: list[str] = []
    for directory in sorted(by_dir):
        entries = sorted(by_dir[directory])
        total_lines = 0
        names: list[str] = []
        for path in entries:
            count = _count_lines(path)
            total_lines += max(count, 0)
            names.append(f"{path.name}({count})" if count >= 0 else path.name)
        lines.append(f"{directory}/  [{len(entries)} 个文件, {total_lines} 行]")
        lines.append("  " + "  ".join(names))
    if len(files) > max_files:
        lines.append(f"... 还有 {len(files) - max_files} 个文件没列出")
    return "\n".join(lines) or "(空仓库)"


def _keywords(text: str) -> list[str]:
    """英文按单词切；中文没有词边界，用 2-gram 兜底（修一下登录超时 -> 修一/一下/下登/登录/录超/超时）。"""
    tokens = [t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)]
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def pick_files(root: Path | str, cfg, task: str, limit: int = 8) -> list[Path]:
    """按任务关键词给文件打分：路径命中权重高，内容命中权重低。"""
    root = Path(root).resolve()
    words = _keywords(task)
    if not words:
        return []

    scored: list[tuple[int, Path]] = []
    for path in iter_files(root, cfg):
        rel = path.relative_to(root).as_posix().lower()
        score = sum(3 for word in words if word in rel)
        if score == 0:
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue
            score = sum(1 for word in words if word in text)
        if score and ("test" in rel or "spec" in rel):
            score += 1  # 修 bug 的任务里，测试文件是最有价值的线索
        if score:
            scored.append((score, path))

    scored.sort(key=lambda item: (-item[0], len(str(item[1]))))
    return [path for _, path in scored[:limit]]
