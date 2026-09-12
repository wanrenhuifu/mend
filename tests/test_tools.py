"""工具层的安全与正确性：路径越界、唯一匹配、权限分级、命令白名单。"""

from __future__ import annotations

from mend.tools import ToolRegistry


def test_read_file_numbers_lines(repo, cfg):
    result = ToolRegistry(repo, cfg).call("read_file", {"path": "calc.py"})
    assert result.ok
    assert "1| def add" in result.output


def test_edit_file_requires_unique_match(repo, cfg):
    (repo / "dup.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    result = ToolRegistry(repo, cfg).call("edit_file", {"path": "dup.py", "old": "x = 1", "new": "x = 2"})
    assert not result.ok
    assert "不唯一" in result.error
    # 报错时绝不能"随便改一处"：文件必须原封不动
    assert (repo / "dup.py").read_text(encoding="utf-8") == "x = 1\nx = 1\n"


def test_edit_file_reports_missing_snippet(repo, cfg):
    result = ToolRegistry(repo, cfg).call("edit_file", {"path": "calc.py", "old": "不存在的片段", "new": "x"})
    assert not result.ok
    assert "找不到" in result.error


def test_edit_file_applies_single_match(repo, cfg):
    result = ToolRegistry(repo, cfg).call("edit_file", {"path": "calc.py", "old": "a + b", "new": "b + a"})
    assert result.ok
    assert "b + a" in (repo / "calc.py").read_text(encoding="utf-8")


def test_path_escape_blocked(repo, cfg):
    result = ToolRegistry(repo, cfg).call("read_file", {"path": "../secret.txt"})
    assert not result.ok
    assert "越界" in result.error


def test_forbidden_path_blocked(repo, cfg):
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("secret", encoding="utf-8")
    result = ToolRegistry(repo, cfg).call("read_file", {"path": ".git/config"})
    assert not result.ok
    assert "禁止访问" in result.error


def test_unknown_tool_and_arguments_rejected(repo, cfg):
    registry = ToolRegistry(repo, cfg)
    assert not registry.call("rm_rf", {}).ok
    result = registry.call("read_file", {"path": "calc.py", "bogus": 1})
    assert not result.ok
    assert "不支持参数" in result.error
    missing = registry.call("read_file", {})
    assert not missing.ok
    assert "缺少参数" in missing.error


def test_read_only_registry_rejects_writes(repo, cfg):
    """只读模式是结构保证的：写工具既不在 schema 里，也调不动。"""
    read_only = ToolRegistry(repo, cfg, levels=("read",))
    names = [tool.name for tool in read_only.tools()]
    assert "read_file" in names
    assert "edit_file" not in names and "run_command" not in names

    result = read_only.call("edit_file", {"path": "calc.py", "old": "a + b", "new": "a - b"})
    assert not result.ok
    assert "没有挂载" in result.error
    assert "a + b" in (repo / "calc.py").read_text(encoding="utf-8")


def test_command_allowlist(repo, cfg):
    result = ToolRegistry(repo, cfg).call("run_command", {"command": "curl http://example.com"})
    assert not result.ok
    assert "不在白名单" in result.error


def test_run_command_returns_output(repo, cfg):
    (repo / "hello.py").write_text("print('mend-ok')\n", encoding="utf-8")
    result = ToolRegistry(repo, cfg).call("run_command", {"command": "python hello.py"})
    assert result.ok
    assert "mend-ok" in result.output
    assert "exit=0" in result.output


def test_run_command_reports_failure_as_observation(repo, cfg):
    """命令失败不是"工具坏了"：退出码和输出要原样交回给模型看。"""
    (repo / "boom.py").write_text("raise SystemExit(3)\n", encoding="utf-8")
    result = ToolRegistry(repo, cfg).call("run_command", {"command": "python boom.py"})
    assert not result.ok
    assert "exit=3" in result.text()
