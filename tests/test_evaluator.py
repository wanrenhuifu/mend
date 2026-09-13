"""评测层：任务加载、离线跳过的语义、表格渲染。"""

from __future__ import annotations

from mend.config import Config
from mend.evaluator import EvalRow, load_tasks, render_table, run_suite


def _demo_task(tmp_path, name: str = "demo", fake_script: str = "") -> str:
    fixture = tmp_path / "evals" / "fixtures" / name
    fixture.mkdir(parents=True)
    (fixture / "check.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    tasks_dir = tmp_path / "evals" / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    script_line = f'fake_script = "{fake_script}"\n' if fake_script else ""
    (tasks_dir / f"{name}.toml").write_text(
        f'id = "{name}"\ndescription = "示例任务"\nfixture = "evals/fixtures/{name}"\n'
        f'test_command = "python check.py"\n{script_line}',
        encoding="utf-8",
    )
    return str(tasks_dir)


def test_fake_run_skips_tasks_without_script(tmp_path):
    """离线模式跳过没有剧本的任务——否则加一个新任务会顺手把 CI 弄红。"""
    tasks_dir = _demo_task(tmp_path, "no_script")
    rows = run_suite(Config(root=tmp_path), load_tasks(tasks_dir), tmp_path, use_fake=True)
    assert len(rows) == 1
    assert rows[0].skipped is True


def test_skipped_rows_do_not_count_as_failures():
    rows = [
        EvalRow("a", True, "done", 4, 1.0),
        EvalRow("b", True, "skipped", skipped=True, note="离线模式跳过"),
    ]
    table = render_table(rows)
    assert "通过 1/1" in table
    assert "1 个任务离线跳过" in table
    assert "--" in table  # 跳过的任务不显示成"通过"


def test_load_tasks_ignores_unknown_keys(tmp_path):
    tasks_dir = _demo_task(tmp_path, "extra_key")
    path = tmp_path / "evals" / "tasks" / "extra_key.toml"
    path.write_text(path.read_text(encoding="utf-8") + 'whatever = "ignored"\n', encoding="utf-8")
    tasks = load_tasks(tasks_dir)
    assert len(tasks) == 1 and tasks[0].id == "extra_key"
