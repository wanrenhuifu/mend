"""轨迹：每一步都落盘成 jsonl，可回放、可统计。

为什么这件事重要：agent 出错时，你唯一能依据的就是"它当时看到了什么、做了什么"。
没有轨迹的 agent 项目既没法调优，也没法在面试里讲清失败模式。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

RUNS_DIR = ".mend/runs"


@dataclass
class Step:
    index: int
    kind: str  # context | llm | tool | verify | stop
    name: str = ""
    ok: bool = True
    duration_ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


class Trace:
    """一次运行的轨迹。写 jsonl 而不是内存里攒着，是因为 agent 可能超时/被杀掉。"""

    def __init__(self, root: Path | str, task: str, run_id: str | None = None) -> None:
        self.run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        self.task = task
        self.steps: list[Step] = []
        self.started = time.time()
        self.dir = Path(root).resolve() / RUNS_DIR
        self.path = self.dir / f"{self.run_id}.jsonl"

    def record(self, kind: str, name: str = "", ok: bool = True, duration_ms: int = 0, **detail: Any) -> Step:
        step = Step(len(self.steps), kind, name, ok, duration_ms, detail)
        self.steps.append(step)
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(step), ensure_ascii=False) + "\n")
        return step

    @property
    def usage_summary(self) -> str:
        return f"{len(self.steps)} 步"


def list_runs(root: Path | str) -> list[Path]:
    directory = Path(root).resolve() / RUNS_DIR
    return sorted(directory.glob("*.jsonl"), reverse=True) if directory.exists() else []


def load_run(path: Path | str) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _short(value: Any, limit: int = 64) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render(steps: list[dict[str, Any]]) -> str:
    """把轨迹渲染成人能读的形式（mend trace 用它）。"""
    lines: list[str] = []
    for step in steps:
        detail = step.get("detail") or {}
        bits = [f"{k}={_short(v)}" for k, v in detail.items() if v not in (None, "", [], {})]
        mark = "ok  " if step.get("ok", True) else "FAIL"
        head = f"#{step.get('index', 0):<3}{mark} {str(step.get('kind', '')):<8}{str(step.get('name') or ''):<12}"
        lines.append(f"{head} {str(step.get('duration_ms', 0)) + 'ms':>8}  {' '.join(bits)}".rstrip())
    return "\n".join(lines) or "(空轨迹)"
