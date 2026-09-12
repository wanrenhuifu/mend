"""agent 闭环：验证门、防打转、步数上限、只读模式下改不动文件。"""

from __future__ import annotations

import subprocess

from mend.agent import Agent, failure_signature
from mend.config import Config
from mend.llm import FakeLLM, call, say
from mend.tools import ToolRegistry
from mend.trace import Trace


def test_failure_signature_ignores_timing():
    """指纹必须忽略耗时，否则"同样的失败"永远检测不出来。"""
    first = "$ python -m pytest -q\nexit=1 (123ms)\n1 failed in 0.03s"
    second = "$ python -m pytest -q\nexit=1 (987ms)\n1 failed in 1.20s"
    assert failure_signature(first) == failure_signature(second)
    assert failure_signature(first) != failure_signature("$ python -m pytest -q\nexit=1 (120ms)\n3 failed in 0.03s")


def make_cfg(tmp_path, **overrides) -> Config:
    """一个"测试命令 = 看文件里有没有 GOOD"的最小环境。"""
    (tmp_path / "target.txt").write_text("BAD\n", encoding="utf-8")
    (tmp_path / "check.py").write_text(
        "import pathlib, sys\n"
        "text = pathlib.Path('target.txt').read_text(encoding='utf-8')\n"
        "sys.exit(0 if 'GOOD' in text else 1)\n",
        encoding="utf-8",
    )
    defaults = dict(root=tmp_path, test_command="python check.py", allowed_commands=["python"])
    defaults.update(overrides)
    return Config(**defaults)


def test_verification_gate_forces_another_round(tmp_path):
    """改了但测试没过，不许结束；只有测试绿了才允许收工。"""
    cfg = make_cfg(tmp_path)
    script = [
        call("write_file", path="target.txt", content="STILL BAD\n"),  # 第一轮改错了
        say("我改好了"),
        call("write_file", path="target.txt", content="GOOD\n"),  # 看到报错后改对
        say("这次真的好了"),
    ]
    trace = Trace(tmp_path, "把 target.txt 修好")
    agent = Agent(cfg, FakeLLM(script), ToolRegistry(tmp_path, cfg, trace), trace)

    result = agent.run("把 target.txt 修好")

    assert result.status == "done"
    assert result.verified is True
    verify_steps = [step for step in trace.steps if step.kind == "verify"]
    assert [step.ok for step in verify_steps] == [False, True]
    # 失败被回灌进对话，模型才有机会改对
    assert any("测试没有通过" in message.content for message in agent.messages if message.role == "user")


def test_same_failure_twice_triggers_nudge_instead_of_looping(tmp_path):
    cfg = make_cfg(tmp_path)
    script = [
        say("我改好了"),  # 啥也没改就说完成 -> 验证失败
        say("真的好了"),  # 再试一次同样的路数 -> 触发"换思路"提示
        call("write_file", path="target.txt", content="GOOD\n"),
        say("这次改对了"),
    ]
    trace = Trace(tmp_path, "打转检测")
    agent = Agent(cfg, FakeLLM(script), ToolRegistry(tmp_path, cfg, trace), trace)
    result = agent.run("把 target.txt 修好")

    nudges = [m.content for m in agent.messages if m.role == "user" and "不要再重复" in m.content]
    assert result.verified and nudges


def test_max_steps_stops(tmp_path):
    cfg = make_cfg(tmp_path, max_steps=3)
    script = [call("list_dir", path=".") for _ in range(10)]
    trace = Trace(tmp_path, "步数上限")
    agent = Agent(cfg, FakeLLM(script), ToolRegistry(tmp_path, cfg, trace), trace)

    result = agent.run("随便看看")

    assert result.status == "max_steps"
    assert result.steps == 3


def test_read_only_agent_cannot_write(tmp_path):
    cfg = make_cfg(tmp_path)
    script = [call("write_file", path="target.txt", content="GOOD\n"), say("我改完了")]
    trace = Trace(tmp_path, "读模式")
    registry = ToolRegistry(tmp_path, cfg, trace, levels=("read",))
    agent = Agent(cfg, FakeLLM(script), registry, trace)

    result = agent.run("把文件改掉")

    assert result.status == "done"
    assert result.verified is False  # 没写成功，就没有"需要验证"这回事
    assert "BAD" in (tmp_path / "target.txt").read_text(encoding="utf-8")


def test_trace_is_written_to_disk(tmp_path):
    cfg = make_cfg(tmp_path)
    trace = Trace(tmp_path, "轨迹落盘")
    registry = ToolRegistry(tmp_path, cfg, trace, levels=("read",))
    agent = Agent(cfg, FakeLLM([say("看完了")]), registry, trace)
    agent.run("看看仓库")

    assert trace.path.exists()
    content = trace.path.read_text(encoding="utf-8")
    assert '"kind": "run"' in content  # 第一行是运行头：任务 / 仓库 / 起始时间
    assert '"kind": "stop"' in content


def test_no_test_command_means_no_claim_of_success(tmp_path):
    """没有可跑的测试 = 没有完成标准：明确说"无法验证"，而不是给一份假装完成的报告。"""
    (tmp_path / "target.txt").write_text("BAD\n", encoding="utf-8")
    cfg = Config(root=tmp_path, test_command="")  # 临时目录里探测不到任何测试命令
    script = [call("write_file", path="target.txt", content="GOOD\n"), say("我改好了")]
    trace = Trace(tmp_path, "没有验证手段")
    agent = Agent(cfg, FakeLLM(script), ToolRegistry(tmp_path, cfg, trace), trace)

    result = agent.run("把文件改好")

    assert result.status == "no_verifier"
    assert result.verified is False
    assert "不能声称完成" in result.report


def test_patch_is_scoped_to_files_the_agent_touched(tmp_path):
    """报告里只该出现它自己改过的文件，别把工作区里已有的改动算成它的产出。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    (tmp_path / "a.txt").write_text("old-a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("old-b\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        capture_output=True,
    )
    (tmp_path / "b.txt").write_text("dirty-b\n", encoding="utf-8")  # 跟这次运行无关的改动

    cfg = Config(root=tmp_path, test_command="python -c pass", allowed_commands=["python"])
    script = [call("write_file", path="a.txt", content="new-a\n"), say("改好了")]
    trace = Trace(tmp_path, "只报自己改的文件")
    agent = Agent(cfg, FakeLLM(script), ToolRegistry(tmp_path, cfg, trace), trace)

    result = agent.run("把 a.txt 改掉")

    assert result.verified
    assert "new-a" in result.patch
    assert "dirty-b" not in result.patch  # 无关改动不该出现在产出里


def test_patch_catches_changes_made_by_commands(tmp_path):
    """模型用 run_command 跑脚本改文件时，产出里也得体现出来。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    (tmp_path / "target.txt").write_text("before\n", encoding="utf-8")
    (tmp_path / "rewrite.py").write_text(
        "import pathlib\npathlib.Path('target.txt').write_text('after\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        capture_output=True,
    )

    cfg = Config(root=tmp_path, test_command="python -c pass", allowed_commands=["python"])
    script = [call("run_command", command="python rewrite.py"), say("改完了")]
    trace = Trace(tmp_path, "用命令改文件")
    agent = Agent(cfg, FakeLLM(script), ToolRegistry(tmp_path, cfg, trace), trace)

    result = agent.run("把 target.txt 改掉")

    assert "+after" in result.patch


def test_no_changes_means_empty_patch(tmp_path):
    """什么都没改的运行，产出应该是空的——不能把工作区里已有的改动算成它的产出。"""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, capture_output=True)
    (tmp_path / "a.txt").write_text("a\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("old-b\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"],
        cwd=tmp_path,
        capture_output=True,
    )
    (tmp_path / "b.txt").write_text("dirty-b\n", encoding="utf-8")  # 运行前就脏的文件

    cfg = Config(root=tmp_path, test_command="python -c pass", allowed_commands=["python"])
    script = [call("read_file", path="a.txt"), say("只是看了一眼")]
    trace = Trace(tmp_path, "什么都不改")
    agent = Agent(cfg, FakeLLM(script), ToolRegistry(tmp_path, cfg, trace), trace)

    result = agent.run("看一眼 a.txt")

    assert result.status == "done"
    assert result.patch == ""
