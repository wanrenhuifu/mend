"""工具层的安全与正确性：路径越界、唯一匹配、权限分级、命令白名单、测试文件保护。"""

from __future__ import annotations

from mend.config import Config
from mend.tools import ToolRegistry
from mend.trace import Trace


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


def test_command_cannot_reach_forbidden_paths(repo, cfg):
    """白名单只管第一段命令名，所以"白名单内的命令 + 敏感路径"必须再单独挡一层。"""
    (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (repo / ".mend" / "runs").mkdir(parents=True)
    (repo / ".mend" / "runs" / "x.jsonl").write_text("{}\n", encoding="utf-8")
    registry = ToolRegistry(repo, cfg)

    # python 在白名单里，所以这两条只能靠路径检查拦住
    for command in (
        "python -c \"print(open('.env').read())\"",
        "python -c \"print(open('.mend/runs/x.jsonl').read())\"",
    ):
        result = registry.call("run_command", {"command": command})
        assert not result.ok, command
        assert "禁止访问" in result.error, command

    # 白名单是另一层防线：cat / rm 这些根本不在允许列表里
    for command in ("cat .env", "rm -rf .git"):
        assert not registry.call("run_command", {"command": command}).ok, command


def test_forbidden_path_check_does_not_false_positive(repo, cfg):
    """`os.environ` 里也有 ".env"，但不能因此把正常命令拦掉。"""
    (repo / "show.py").write_text("import os\nprint('env vars:', len(os.environ))\n", encoding="utf-8")
    result = ToolRegistry(repo, cfg).call("run_command", {"command": "python show.py"})
    assert result.ok, result.error


def test_api_key_is_not_inherited_by_child_processes(repo, cfg, monkeypatch):
    """API key 不传给子进程：不是靠字符串匹配挡住，而是根本不传给它。"""
    monkeypatch.setenv("MEND_API_KEY", "sk-should-not-leak")
    (repo / "dump_env.py").write_text(
        "import os\nprint('LEAK' if 'MEND_API_KEY' in os.environ else 'CLEAN')\n", encoding="utf-8"
    )
    result = ToolRegistry(repo, cfg).call("run_command", {"command": "python dump_env.py"})
    assert result.ok
    assert "CLEAN" in result.output
    assert "sk-should-not-leak" not in result.output


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


# ---------- 测试文件保护：禁止"改测试让测试变绿"的结构保证 ----------


def test_write_to_test_file_is_rejected(repo, cfg):
    (repo / "tests").mkdir()
    target = repo / "tests" / "test_calc.py"
    target.write_text("def test_add():\n    assert add(1, 2) == 3\n", encoding="utf-8")
    registry = ToolRegistry(repo, cfg)

    edit = registry.call("edit_file", {"path": "tests/test_calc.py", "old": "== 3", "new": "== 4"})
    assert not edit.ok
    assert "受保护" in edit.error
    assert "== 3" in target.read_text(encoding="utf-8")  # 文件一个字没变

    write = registry.call("write_file", {"path": "tests/test_calc.py", "content": "x = 1\n"})
    assert not write.ok
    assert "受保护" in write.error


def test_test_file_glob_matches_basename(repo, cfg):
    (repo / "test_login.py").write_text("def test_login():\n    assert True\n", encoding="utf-8")
    result = ToolRegistry(repo, cfg).call("write_file", {"path": "test_login.py", "content": "x = 1\n"})
    assert not result.ok
    assert "受保护" in result.error


def test_reading_test_files_is_still_allowed(repo, cfg):
    """只拦写不拦读：agent 必须能看测试，否则它不知道要修成什么样。"""
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")
    assert ToolRegistry(repo, cfg).call("read_file", {"path": "tests/test_calc.py"}).ok


def test_rejected_write_is_recorded_in_trace(repo, cfg):
    """被拒绝的调用也要留痕："它试图改测试文件"必须能被 review 到。"""
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text("x = 1\n", encoding="utf-8")
    trace = Trace(repo, "拒绝留痕")
    ToolRegistry(repo, cfg, trace).call("edit_file", {"path": "tests/test_calc.py", "old": "x = 1", "new": "x = 2"})

    rejected = [step for step in trace.steps if step.detail.get("rejected")]
    assert rejected and rejected[0].ok is False


def test_protected_paths_are_configurable(repo):
    """想更严就往 protected_paths 里加（注意是替换默认值，不是追回）。"""
    cfg = Config(root=repo, protected_paths=["migrations/"])
    (repo / "migrations").mkdir()
    (repo / "migrations" / "0001_init.py").write_text("x = 1\n", encoding="utf-8")
    blocked = ToolRegistry(repo, cfg).call("write_file", {"path": "migrations/0001_init.py", "content": "y = 2\n"})
    assert not blocked.ok and "受保护" in blocked.error

    (repo / "tests").mkdir()
    (repo / "tests" / "t.py").write_text("x = 1\n", encoding="utf-8")
    assert ToolRegistry(repo, cfg).call("edit_file", {"path": "tests/t.py", "old": "x", "new": "y"}).ok
