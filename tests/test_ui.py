"""输出层：颜色、运行记录排版、diff 上色。"""

from __future__ import annotations

from mend import ui


def test_detect_color_explicit():
    assert ui.detect_color("always") is True
    assert ui.detect_color("never") is False


def test_no_color_env_wins(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert ui.detect_color("auto") is False


def test_force_color_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert ui.detect_color("auto") is True


def test_paint_only_when_enabled():
    on = ui.Console(color=True)
    off = ui.Console(color=False)
    assert on.paint("x", ui.GREEN) == f"{ui.GREEN}x{ui.RESET}"
    assert off.paint("x", ui.GREEN) == "x"


def test_colorize_diff(monkeypatch):
    monkeypatch.setattr(ui, "console", ui.Console(color=True))
    painted = ui.colorize_diff("+added\n-removed\n context\n@@ hunk @@\n# 新增文件")
    assert f"{ui.GREEN}+added" in painted
    assert f"{ui.RED}-removed" in painted
    assert f"{ui.CYAN}@@ hunk @@" in painted
    assert "\n context\n" in painted  # 上下文行不动
    assert "\n" in painted and "added" in painted


def test_colorize_diff_without_color_is_plain():
    text = "+a\n-b\n@@ @@"
    assert ui.colorize_diff(text) == text  # 默认 console 是关色的


def test_render_run_shows_header_steps_and_failure_detail():
    records = [
        {
            "kind": "run",
            "run_id": "20260101-000000-abcd",
            "task": "把 target.txt 修好",
            "root": "/tmp/demo",
            "started": "2026-01-01 00:00:00",
        },
        {"kind": "llm", "index": 0, "ok": True, "at_ms": 10, "duration_ms": 5, "detail": {"tools": ["read_file"]}},
        {
            "kind": "tool",
            "index": 1,
            "ok": False,
            "at_ms": 20,
            "duration_ms": 30,
            "detail": {"args": {"command": "python -m pytest -q"}, "result": "$ python -m pytest -q\nexit=1\n1 failed"},
        },
        {"kind": "stop", "index": 2, "ok": True, "name": "done", "at_ms": 60, "duration_ms": 0,
         "detail": {"steps": 2, "verified": True, "changed": True}},
    ]
    text = ui.render_run(records)
    assert "20260101-000000-abcd" in text
    assert "把 target.txt 修好" in text
    assert "-> read_file" in text
    assert "FAIL" in text
    assert "exit=1" in text  # 失败步骤的细节要打出来
    assert "done" in text and "测试通过" in text


def test_step_summary_of_context_and_stop():
    context = {"kind": "context", "ok": True, "detail": {"hints": ["a.py", "b.py"]}}
    assert "a.py" in ui.step_summary(context)
    stop = {"kind": "stop", "ok": True, "name": "done", "detail": {"verified": False, "changed": True}}
    assert "未验证" in ui.step_summary(stop) and "有" in ui.step_summary(stop)
