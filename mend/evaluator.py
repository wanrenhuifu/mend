"""评测：没有指标的 agent 只能叫 demo。

一个评测任务 = 一个坏仓库（fixture）+ 一句任务描述 + 一条判定命令。

流程：把 fixture 复制到临时目录 -> git init 并提交一个基线 -> 让 agent 去修
      -> 跑判定命令 -> 记录是否通过 / 步数 / 耗时。
你的原仓库全程不动，跑完临时目录直接删掉。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .agent import Agent
from .config import Config
from .llm import LLM, FakeLLM, Message, OpenAICompatLLM, ToolCall
from .tools import ToolRegistry
from .trace import Trace
from .verify import run_tests


@dataclass
class EvalTask:
    id: str
    description: str
    fixture: str
    test_command: str = ""
    expect: str = "pass"  # pass: 修好后判定命令应该通过
    fake_script: str = ""  # 离线跑（--fake）时用的剧本 json


@dataclass
class EvalRow:
    id: str
    ok: bool
    status: str = ""
    steps: int = 0
    seconds: float = 0.0
    note: str = ""


def load_tasks(task_dir: Path | str) -> list[EvalTask]:
    directory = Path(task_dir)
    if not directory.exists():
        return []
    tasks: list[EvalTask] = []
    for path in sorted(directory.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        tasks.append(EvalTask(**{k: v for k, v in data.items() if k in EvalTask.__dataclass_fields__}))
    return tasks


def load_script(path: Path | str) -> list[Message]:
    """把 json 剧本变成 Message 列表（离线评测用，格式见 evals/scripts/*.json）。

    剧本放在 evals/scripts/ 而不是 fixture 目录里：fixture 会被整体复制成任务工作区，
    剧本里写着答案，放在一起就等于把答案留在考场上。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    messages: list[Message] = []
    for index, item in enumerate(data):
        calls = [
            ToolCall(id=f"call_{index}_{j}", name=call["name"], arguments=call.get("arguments") or {})
            for j, call in enumerate(item.get("tool_calls") or [])
        ]
        messages.append(Message(role="assistant", content=item.get("content") or "", tool_calls=calls))
    return messages


def _git_init_commit(work: Path) -> None:
    """评测环境得是一个干净、可回滚的仓库，agent 产出的 diff 才有意义。"""
    if not shutil.which("git"):
        return

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=str(work), capture_output=True, text=True)

    run("init", "-q")
    run("add", "-A")
    run("-c", "user.email=mend@local", "-c", "user.name=mend", "commit", "-qm", "base", "--no-gpg-sign")


def build_llm(cfg: Config, use_fake: bool, script: list[Message] | None = None) -> LLM:
    if use_fake:
        return FakeLLM(script, final="（离线评测：剧本执行完毕）")
    return OpenAICompatLLM(cfg.model, cfg.base_url, cfg.api_key)


def run_suite(
    cfg: Config,
    tasks: list[EvalTask],
    project_root: Path | str,
    use_fake: bool = False,
    only: str = "",
) -> list[EvalRow]:
    project_root = Path(project_root).resolve()
    rows: list[EvalRow] = []

    for task in tasks:
        if only and task.id != only:
            continue
        fixture = project_root / task.fixture
        if not fixture.exists():
            rows.append(EvalRow(task.id, False, note=f"fixture 不存在: {task.fixture}"))
            continue

        work = Path(tempfile.mkdtemp(prefix=f"mend-eval-{task.id}-"))
        try:
            shutil.copytree(fixture, work, dirs_exist_ok=True)
            _git_init_commit(work)
            task_cfg = replace(cfg, root=work, test_command=task.test_command or cfg.test_command)
            script = load_script(project_root / task.fake_script) if (use_fake and task.fake_script) else None
            # 轨迹写在项目里（不是临时目录），这样评测完还能回放
            trace = Trace(project_root, task.description, run_id=f"eval-{task.id}-{time.strftime('%H%M%S')}")
            registry = ToolRegistry(work, task_cfg, trace)
            agent = Agent(task_cfg, build_llm(task_cfg, use_fake, script), registry, trace)

            started = time.time()
            result = agent.run(task.description)
            seconds = time.time() - started

            # 最终判定是评测框架的动作，不是 agent 的动作：不进 agent 的轨迹，单独记一条 judge
            outcome = run_tests(
                work, task_cfg, registry, command=task.test_command or None, trace=None, record_tool=False
            )
            ok = outcome.ok if task.expect == "pass" else not outcome.ok
            note = "" if ok else ("判定命令仍然失败" if task.expect == "pass" else "判定命令意外通过了")
            trace.record("judge", f"expect={task.expect}", ok, note=note or None)
            rows.append(EvalRow(task.id, ok, result.status, result.steps, round(seconds, 1), note))
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return rows


def render_table(rows: list[EvalRow]) -> str:
    if not rows:
        return "(没有任务)"
    width = max(len(row.id) for row in rows)
    lines = [f"{'任务'.ljust(width)}  结果  状态         步数  耗时"]
    for row in rows:
        lines.append(
            f"{row.id.ljust(width)}  {'通过' if row.ok else '失败'}  "
            f"{row.status:<10}  {row.steps:>3}  {row.seconds:>5}s  {row.note}"
        )
    passed = sum(1 for row in rows if row.ok)
    lines.append(f"\n通过 {passed}/{len(rows)}")
    return "\n".join(lines)
