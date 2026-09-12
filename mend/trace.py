"""轨迹：每一步都落盘成 jsonl，可回放、可统计。

为什么这件事重要：agent 出错时，你唯一能依据的就是"它当时看到了什么、做了什么"。
没有轨迹的 agent 项目既没法调优，也没法在面试里讲清失败模式。

文件格式：第一行是一条 run 头（任务 / 仓库 / 起始时间），之后每个 step 一行。
渲染成给人看的样子在 ui.py —— 这里只管存和读。
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

RUNS_DIR = ".mend/runs"


@dataclass
class Step:
    index: int
    kind: str  # context | llm | tool | verify | judge | stop
    name: str = ""
    ok: bool = True
    at_ms: int = 0  # 相对本次运行开始的毫秒数
    duration_ms: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


class Trace:
    """一次运行的轨迹。写 jsonl 而不是在内存里攒着，是因为 agent 可能超时/被杀掉。"""

    def __init__(
        self,
        root: Path | str,
        task: str,
        run_id: str | None = None,
        on_step: Callable[[Step], None] | None = None,
    ) -> None:
        self.run_id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        self.task = task
        self.started = time.time()
        self.on_step = on_step
        self.steps: list[Step] = []
        self.dir = Path(root).resolve() / RUNS_DIR
        self.path = self.dir / f"{self.run_id}.jsonl"
        self.header: dict[str, Any] = {
            "kind": "run",
            "run_id": self.run_id,
            "task": task,
            "root": str(Path(root).resolve()),
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._write(self.header)

    def _write(self, payload: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def record(self, kind: str, name: str = "", ok: bool = True, duration_ms: int = 0, **detail: Any) -> Step:
        step = Step(
            index=len(self.steps),
            kind=kind,
            name=name,
            ok=ok,
            at_ms=int((time.time() - self.started) * 1000),
            duration_ms=duration_ms,
            detail=detail,
        )
        self.steps.append(step)
        self._write(asdict(step))
        if self.on_step is not None:
            self.on_step(step)
        return step

    def records(self) -> list[dict[str, Any]]:
        """内存里的完整记录（含 run 头），交给 ui.render_run 渲染。"""
        return [self.header, *[asdict(step) for step in self.steps]]


def list_runs(root: Path | str) -> list[Path]:
    directory = Path(root).resolve() / RUNS_DIR
    return sorted(directory.glob("*.jsonl"), reverse=True) if directory.exists() else []


def load_run(path: Path | str) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_header(path: Path | str) -> dict[str, Any]:
    """只读第一行——列出运行记录时不用把整个轨迹读进内存。"""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                data = json.loads(line)
                return data if data.get("kind") == "run" else {}
    return {}
