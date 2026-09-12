"""命令行入口：8 个子命令，覆盖 看 -> 计划 -> 改 -> 验证 -> 复盘 -> 度量 的完整工作流。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from . import __version__, vcs
from .agent import Agent, RunResult
from .config import CONFIG_NAME, EXAMPLE_NAME, Config
from .context import pick_files, repo_map
from .evaluator import load_tasks, render_table, run_suite
from .llm import DEMO_SCRIPT, FakeLLM, LLM, OpenAICompatLLM
from .tools import ToolRegistry
from .trace import RUNS_DIR, Trace, list_runs, load_run, render
from .verify import detect_test_command, run_tests

DEFAULT_CONFIG_TOML = """# Mend 配置（`mend init` 生成）。密钥不写在这里，只从环境变量读。
model = "deepseek-chat"
base_url = "https://api.deepseek.com/v1"
api_key_env = "MEND_API_KEY"
max_steps = 25
timeout_s = 60
test_command = ""
allowed_commands = ["pytest", "python", "python3", "pip", "npm", "node", "npx", "go", "cargo", "make", "ruff", "mypy", "tsc", "eslint"]
forbidden_paths = [".git", ".env", ".venv", "venv", "node_modules", ".mend", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox", ".idea", ".vscode"]
"""


# ---------- 小工具 ----------


def _soften_console() -> None:
    """Windows 控制台默认是 GBK，遇到画不出来的字符别让整个程序崩掉。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(errors="replace")
            except ValueError:
                pass


def _build_llm(cfg: Config, use_fake: bool, script: list | None = None) -> LLM:
    if use_fake:
        return FakeLLM(
            list(script or DEMO_SCRIPT),
            final="（离线演示）上面是假模型能看到的上下文和它能调用的工具。接上真实模型后，这一轮会继续走到改代码和跑测试。",
        )
    return OpenAICompatLLM(cfg.model, cfg.base_url, cfg.api_key)


def _make_agent(cfg: Config, task: str, levels: tuple[str, ...], use_fake: bool, verify: bool):
    trace = Trace(cfg.root, task)
    registry = ToolRegistry(cfg.root, cfg, trace, levels=levels)
    agent = Agent(cfg, _build_llm(cfg, use_fake), registry, trace, verify=verify)
    return agent, trace, registry


def _apply_overrides(args, cfg: Config) -> None:
    if getattr(args, "max_steps", 0):
        cfg.max_steps = int(args.max_steps)


def _task_text(args) -> str:
    return " ".join(getattr(args, "task", []) or []).strip()


def _render_trace(trace: Trace) -> str:
    return render([asdict(step) for step in trace.steps])


def _report(result: RunResult, trace: Trace) -> int:
    print("\n=== 结果 ===")
    verified = "测试通过" if result.verified else "未验证"
    print(f"状态: {result.status}    验证: {verified}    步数: {result.steps}")
    print(f"轨迹回放: mend trace {trace.run_id}")
    if result.report:
        print(f"\n=== agent 的说明 ===\n{result.report}")
    if result.patch.strip():
        print(f"\n=== 改动 ===\n{result.patch[:4000]}")
    usage = result.usage or {}
    if usage.get("prompt_tokens") or usage.get("completion_tokens"):
        print(f"\n=== 用量 ===\nprompt={usage.get('prompt_tokens', 0)}  completion={usage.get('completion_tokens', 0)}")
    return 0 if result.status == "done" else 1


# ---------- 子命令 ----------


def cmd_init(args, cfg: Config) -> int:
    target = Path(cfg.root) / CONFIG_NAME
    if target.exists() and not args.force:
        print(f"{CONFIG_NAME} 已存在（要覆盖就加 --force）")
        return 1
    example = Path(cfg.root) / EXAMPLE_NAME
    text = example.read_text(encoding="utf-8") if example.exists() else DEFAULT_CONFIG_TOML
    target.write_text(text, encoding="utf-8")
    print(f"已生成 {CONFIG_NAME}。密钥从环境变量 {cfg.api_key_env} 读，不要写进配置文件。")
    return 0


def cmd_doctor(args, cfg: Config) -> int:
    test_command = cfg.test_command_or_none() or detect_test_command(cfg.root)
    checks = [
        ("Python 版本", f"{sys.version.split()[0]}（需要 >= 3.11）", sys.version_info >= (3, 11)),
        ("git", shutil.which("git") or "没安装", bool(shutil.which("git"))),
        ("git 仓库", "是" if vcs.is_repo(cfg.root) else "否（diff 会不可用，建议 git init）", vcs.is_repo(cfg.root)),
        ("配置文件", CONFIG_NAME if (Path(cfg.root) / CONFIG_NAME).exists() else "没生成（跑 mend init）", True),
        ("模型", f"{cfg.model} @ {cfg.base_url}", True),
        ("密钥", f"{cfg.api_key_env} 已设置" if cfg.api_key else "没设置（用 --fake 可以离线跑通全流程）", bool(cfg.api_key)),
        ("测试命令", test_command or "没探测到（在 mend.toml 里写 test_command）", bool(test_command)),
    ]
    for name, value, ok in checks:
        print(f"[{'ok' if ok else '!!'}] {name}: {value}")
    return 0 if sys.version_info >= (3, 11) else 1


def cmd_plan(args, cfg: Config) -> int:
    task = _task_text(args)
    if not task:
        print('用法: mend plan "任务描述"    （只读模式，不会改一行代码）')
        return 1
    _apply_overrides(args, cfg)
    root = Path(cfg.root).resolve()
    print("=== 上下文（agent 会看到这些）===")
    print(repo_map(root, cfg))
    hints = [path.relative_to(root).as_posix() for path in pick_files(root, cfg, task)]
    print("\n=== 关键词初筛出的文件 ===")
    print("\n".join(f"- {h}" for h in hints) or "- （没有命中，agent 得自己找）")

    agent, trace, registry = _make_agent(cfg, task, ("read",), args.fake, verify=False)
    print(f"\n=== 只读工具（{len(registry.tools())} 个）===")
    print(", ".join(tool.name for tool in registry.tools()))
    result = agent.run(task)
    print(f"\n=== 轨迹 ===\n{_render_trace(trace)}")
    print(f"\n=== 计划 ===\n{result.report}")
    return 0


def cmd_run(args, cfg: Config) -> int:
    task = _task_text(args)
    if not task:
        print('用法: mend run "任务描述"')
        return 1
    _apply_overrides(args, cfg)
    agent, trace, _ = _make_agent(cfg, task, ("read", "write", "shell"), args.fake, verify=True)
    result = agent.run(task)
    return _report(result, trace)


def cmd_fix(args, cfg: Config) -> int:
    _apply_overrides(args, cfg)
    outcome = run_tests(cfg.root, cfg, ToolRegistry(cfg.root, cfg), command=args.test_command or None)
    if outcome.ok:
        print("测试当前是绿的，没有需要修的东西。")
        return 0
    print(f"测试失败，进入修复循环（{outcome.command}）")
    task = "下面这些测试失败了，请把它们修到通过。不要修改测试本身。\n\n" + outcome.output[-3000:]
    agent, trace, _ = _make_agent(cfg, task, ("read", "write", "shell"), args.fake, verify=True)
    result = agent.run(task)
    return _report(result, trace)


def cmd_review(args, cfg: Config) -> int:
    changes = vcs.diff(cfg.root, base=args.base)
    if not changes.strip():
        print(f"工作区没有相对 {args.base} 的改动，没什么可审的。")
        return 0
    task = (
        "以严格的代码审查者身份审查下面的 diff：指出真正的 bug、边界情况、以及可以更小的改法。"
        "只给意见，不要改代码。\n\n```diff\n" + changes[:12000] + "\n```"
    )
    agent, trace, _ = _make_agent(cfg, task, ("read",), args.fake, verify=False)
    result = agent.run(task)
    return _report(result, trace)


def cmd_trace(args, cfg: Config) -> int:
    runs = list_runs(cfg.root)
    if args.list or not args.run_id:
        if not runs:
            print("还没有运行记录。先跑一次 mend run 或者 mend eval --fake。")
            return 0
        print(f"共 {len(runs)} 次运行（最新的在前）：")
        for path in runs[:20]:
            print(f"  {path.stem}")
        return 0
    path = Path(cfg.root).resolve() / RUNS_DIR / f"{args.run_id}.jsonl"
    if not path.exists():
        print(f"没有这次运行: {args.run_id}（用 mend trace --list 看有哪些）")
        return 1
    print(render(load_run(path)))
    return 0


def cmd_eval(args, cfg: Config) -> int:
    tasks = load_tasks(Path(cfg.root).resolve() / "evals" / "tasks")
    if not tasks:
        print("evals/tasks 里没有任务。")
        return 1
    rows = run_suite(cfg, tasks, cfg.root, use_fake=args.fake, only=args.task)
    print(render_table(rows))
    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False, indent=2))
    return 0 if all(row.ok for row in rows) else 1


# ---------- 解析 ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mend",
        description="Mend —— 会自己跑测试证明改对了的 coding agent",
    )
    parser.add_argument("--version", action="version", version=f"mend {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    init = sub.add_parser("init", help="生成 mend.toml 配置")
    init.add_argument("--force", action="store_true", help="覆盖已存在的配置")

    sub.add_parser("doctor", help="环境自检：Python / git / 密钥 / 测试命令")

    for name, help_text in (
        ("plan", "只读模式：读仓库、给计划，不改任何代码"),
        ("run", "完整闭环：读仓库 -> 改代码 -> 跑测试 -> 迭代"),
        ("fix", "把当前失败的测试修到通过"),
        ("review", "审查当前 diff（只读，只给意见）"),
    ):
        item = sub.add_parser(name, help=help_text)
        if name != "review":
            item.add_argument("task", nargs="*", help="任务描述")
        item.add_argument("--fake", action="store_true", help="用离线假模型跑通流程（不需要密钥）")
        item.add_argument("--max-steps", type=int, default=0, help="覆盖配置里的步数上限")
        if name == "fix":
            item.add_argument("--test-command", default="", help="指定测试命令")
        if name == "review":
            item.add_argument("--base", default="HEAD", help="对比基线，默认 HEAD")

    trace = sub.add_parser("trace", help="列出/回放历史运行轨迹")
    trace.add_argument("run_id", nargs="?", default="", help="运行 id，不带就列出全部")
    trace.add_argument("--list", action="store_true", help="只列出来")

    evaluate = sub.add_parser("eval", help="跑评测集：把每个坏仓库修好并判定")
    evaluate.add_argument("--fake", action="store_true", help="离线跑（用任务自带的剧本）")
    evaluate.add_argument("--task", default="", help="只跑某个任务")
    evaluate.add_argument("--json", action="store_true", help="额外输出 json")

    return parser


def main(argv: list[str] | None = None) -> int:
    _soften_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 1

    cfg = Config.load(Path.cwd())
    handlers = {
        "init": cmd_init,
        "doctor": cmd_doctor,
        "plan": cmd_plan,
        "run": cmd_run,
        "fix": cmd_fix,
        "review": cmd_review,
        "trace": cmd_trace,
        "eval": cmd_eval,
    }
    try:
        return handlers[args.command](args, cfg)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"错误: {exc}")
        return 2
