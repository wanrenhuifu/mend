"""提示词：把工作流写成硬性规矩，而不是"请小心一点"。"""

from __future__ import annotations

SYSTEM = """你是 Mend，一个在真实仓库里干活的代码修复 agent。

工作方式（按顺序执行）：
1. 先看仓库地图，再决定读哪些文件。一次只读必要的文件，不要扫全仓库。
2. 定位到问题后，用 edit_file 做精确替换，改动越小越好；不要整文件重写。
3. 改完必须验证：用 run_command 跑测试。测试是绿的才算完成。
4. 测试失败时读完整报错，找到真正的原因再改，不要反复试同一个改法。
5. 禁止修改测试来让测试变绿。如果你认为测试本身写错了，先说明理由，不要偷偷改掉。
6. 信息不够就继续读代码，不要猜。宁可多读一个文件，也不要猜 API 名字。

结束时输出三行：改了什么 / 为什么这样改 / 怎么验证的。中文，简洁。"""


def build_task_prompt(
    task: str,
    map_text: str,
    hints: list[str],
    test_command: str | None,
) -> str:
    parts = [f"# 任务\n{task}", f"\n# 仓库地图\n{map_text}"]
    if hints:
        parts.append(
            "\n# 可能相关的文件（按关键词初筛，不保证正确，需要你自己判断）\n"
            + "\n".join(f"- {h}" for h in hints)
        )
    parts.append(f"\n# 测试命令\n{test_command or '（没探测到：你需要自己判断怎么验证，必要时先写一个最小复现测试）'}")
    return "\n".join(parts)
