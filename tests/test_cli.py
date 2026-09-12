"""CLI 冒烟测试：help / doctor / init / plan --fake 都要能真跑。"""

from __future__ import annotations

from pathlib import Path

import pytest

from mend import cli


def test_help_exits_zero():
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])
    assert excinfo.value.code == 0


def test_no_command_prints_help(capsys):
    assert cli.main([]) == 1
    assert "mend" in capsys.readouterr().out


def test_doctor_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "Python 版本" in output
    assert "测试命令" in output


def test_init_writes_config_and_refuses_overwrite(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["init"]) == 0
    assert (tmp_path / "mend.toml").exists()

    assert cli.main(["init"]) == 1  # 默认不覆盖
    assert cli.main(["init", "--force"]) == 0
    assert "已生成" in capsys.readouterr().out


def test_plan_fake_shows_context_and_tools(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    assert cli.main(["plan", "看看这个仓库", "--fake"]) == 0

    output = capsys.readouterr().out
    assert "calc.py" in output          # 上下文里有仓库地图
    assert "只读工具" in output
    assert "read_file" in output        # 只读模式下挂载的工具
    assert "run_command" not in output.split("=== 只读工具")[1].split("===")[0]


def test_run_fake_full_path(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert cli.main(["run", "看看仓库", "--fake", "--max-steps", "5"]) == 0
    output = capsys.readouterr().out
    assert "结果" in output
    assert "轨迹回放" in output


def test_trace_list_after_run(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    cli.main(["plan", "看看仓库", "--fake"])
    capsys.readouterr()
    assert cli.main(["trace", "--list"]) == 0
    assert "次运行" in capsys.readouterr().out


def test_eval_fake_runs_bundled_task(monkeypatch, capsys):
    """离线端到端：仓库里的评测任务自带剧本，不需要密钥就能验证整条流水线。"""
    project_root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(project_root)
    assert cli.main(["eval", "--fake", "--task", "off_by_one"]) == 0
    output = capsys.readouterr().out
    assert "通过 1/1" in output
