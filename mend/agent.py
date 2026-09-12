"""agent 主循环：think -> act -> observe -> 验证 ->（不通过就再来一轮）。

这个文件是项目的核心论点，三个约束都在这里：
1. 说"改好了"不算数：只要动过文件，就必须跑测试，绿了才允许结束。
2. 防打转：同样的失败连着出现两次，就把"换思路"的要求显式写回对话。
3. 有预算：步数上限 + 单步超时；超了就带着现状停下，交给人。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import vcs
from .context import pick_files, repo_map
from .llm import LLM, Message
from .prompt import SYSTEM, build_task_prompt
from .tools import ToolRegistry
from .trace import Trace
from .verify import VERIFY_TIMEOUT_S, run_tests

WRITE_TOOLS = {"write_file", "edit_file"}
# 连续这么多次同样的失败就停下交给人：再跑下去只是烧 token，问题大概率已经不在模型手里了
MAX_SAME_FAILURE = 3

_HEADER_LINE = re.compile(r"^(?:\$ .*|exit=\d+ \(.*\))\s*$", re.MULTILINE)
_TIMING = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|seconds?)\b")


def failure_signature(output: str, size: int = 300) -> str:
    """给一次失败提取「指纹」，用来判断模型是不是在原地打转。

    不能直接比较输出原文：工具输出头部带了耗时（exit=1 (123ms)），每次都不同，
    直接比会导致"同样的失败"永远检测不出来。这里去掉带时间的头部行、抹掉时长数字，
    再取末尾——报错摘要通常落在最后几行。
    """
    body = _HEADER_LINE.sub("", output)
    body = _TIMING.sub("T", body)
    return " ".join(body[-size:].split())


@dataclass
class RunResult:
    status: str  # done（测试绿了）| no_verifier（没有验证手段）| max_steps（超预算）
    report: str = ""
    run_id: str = ""
    steps: int = 0
    verified: bool = False
    patch: str = ""
    usage: dict[str, int] = field(default_factory=dict)


class Agent:
    def __init__(self, cfg, llm: LLM, registry: ToolRegistry, trace: Trace, verify: bool = True) -> None:
        self.cfg = cfg
        self.llm = llm
        self.registry = registry
        self.trace = trace
        # 验证门只在「有写权限」时挂载：只读模式本来就改不了东西，没什么可验证的
        self.gate = verify and "write" in registry.levels
        self.messages: list[Message] = []

    def run(self, task: str) -> RunResult:
        root = Path(self.cfg.root).resolve()
        # 记下开场时工作区里已经脏了的文件，结束时才能把它们和"本次产出"区分开
        self.baseline_dirty = vcs.dirty_paths(root)
        hints = [path.relative_to(root).as_posix() for path in pick_files(root, self.cfg, task)]
        map_text = repo_map(root, self.cfg)
        self.trace.record("context", "repo_map", hints=hints[:8] or None)

        self.messages = [
            Message("system", SYSTEM),
            Message("user", build_task_prompt(task, map_text, hints, self.cfg.test_command_or_none())),
        ]
        schemas = [tool.schema() for tool in self.registry.tools()]
        self.changed = False
        last_failure: str | None = None
        streak = 0

        for step in range(1, self.cfg.max_steps + 1):
            started = time.time()
            reply = self.llm.chat(self.messages, schemas)
            self.messages.append(reply)
            self.trace.record(
                "llm",
                f"step{step}",
                duration_ms=int((time.time() - started) * 1000),
                tools=[call.name for call in reply.tool_calls] or None,
                text=(reply.content or "")[:200] or None,
            )

            if reply.tool_calls:
                for tool_call in reply.tool_calls:
                    outcome = self.registry.call(tool_call.name, tool_call.arguments)
                    if tool_call.name in WRITE_TOOLS and outcome.ok:
                        self.changed = True
                    self.messages.append(Message("tool", outcome.text(), tool_call_id=tool_call.id))
                continue

            # 模型认为它做完了 —— 这里是「验证门」。
            # 注意：不管它有没有真的动过文件都验证。"没改任何东西却说修好了"正是最该被拦下的情况。
            if not self.gate:
                return self._finish("done", reply.content, step)

            check = run_tests(root, self.cfg, self.registry, timeout_s=VERIFY_TIMEOUT_S, trace=self.trace)
            if check.ok:
                return self._finish("done", reply.content, step, verified=True)
            if check.reason:
                # 没有测试 = 没有完成标准。宁可明确告诉你"无法验证"，也不给一份假装完成的报告。
                return self._finish(
                    "no_verifier",
                    f"改动已保留，但我不能声称完成：{check.reason}。"
                    "Mend 的完成标准是测试通过；没有可跑的测试就没有完成标准"
                    "（在 mend.toml 里配置 test_command，或者先补一个最小复现测试）。",
                    step,
                )

            tail = check.output[-1500:]
            signature = failure_signature(check.output)
            streak = streak + 1 if signature == last_failure else 1
            last_failure = signature

            if streak >= MAX_SAME_FAILURE:
                # 兜底：连续多次同一个失败，说明卡住的不是"这一行代码"，
                # 可能是测试本身写错了，也可能思路不对——停下来交给人，比继续烧 token 好。
                return self._finish(
                    "stuck",
                    f"同一个失败连续出现了 {streak} 次，我判断不出该怎么继续了，所以停下来。"
                    "可能是测试本身有问题（比如断言写错了），也可能是我的思路不对。"
                    f"\n\n最近一次判定输出：\n{tail[-600:]}",
                    step,
                )

            if streak > 1:
                nudge = (
                    "同样的失败又出现了一次。不要再重复上一轮的做法："
                    "重新读一遍相关文件和完整报错，找出真正的原因再动手。"
                    "如果结论是测试本身写错了，就在报告里说明理由，不要再改代码去迎合它。"
                )
            else:
                nudge = f"测试没有通过，不能算完成。根据下面的输出继续修（禁止改测试来让它变绿）：\n\n{tail}"
            self.messages.append(Message("user", nudge))

        return self._finish(
            "max_steps",
            f"达到步数上限（{self.cfg.max_steps} 步），已经停下，改动保留在工作区。",
            self.cfg.max_steps,
        )

    def _patch(self) -> str:
        """产出 = 写工具碰过的文件 ∪ 本次运行期间新变脏的文件。

        为什么要看工作区：模型可能通过 run_command（迁移脚本、formatter）改文件，
        只看写工具会漏；而运行前就已经脏的文件不该被算成它的产出。

        为什么要过滤 forbidden_paths：跑一次 pytest 就会生成 __pycache__，
        那不是 agent 的产出——"它碰不到的路径"和"它的产出"应该是同一个集合。
        两边都空，就是这次运行什么都没改，产出为空。
        """
        touched = set(self.registry.touched)
        new_dirty = vcs.dirty_paths(self.cfg.root) - getattr(self, "baseline_dirty", set())
        forbidden = set(self.cfg.forbidden_paths)
        paths = sorted(
            path for path in (touched | new_dirty) if not (set(Path(path).parts) & forbidden)
        )
        return vcs.diff(self.cfg.root, paths=paths) if paths else ""

    def _finish(self, status: str, report: str, steps: int, verified: bool = False) -> RunResult:
        self.trace.record("stop", status, steps=steps, verified=verified, changed=getattr(self, "changed", False))
        return RunResult(
            status=status,
            report=(report or "").strip(),
            run_id=self.trace.run_id,
            steps=steps,
            verified=verified,
            patch=self._patch(),
            usage=dict(self.llm.usage),
        )
