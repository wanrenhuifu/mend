"""上下文选择：地图要给全，初筛要准，禁写目录不能漏。"""

from __future__ import annotations

from mend.context import iter_files, pick_files, repo_map


def test_repo_map_lists_files(repo, cfg):
    text = repo_map(repo, cfg)
    assert "calc.py" in text
    assert "README.md" in text


def test_forbidden_dirs_skipped(repo, cfg):
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "junk.js").write_text("console.log(1)\n", encoding="utf-8")
    names = [path.name for path in iter_files(repo, cfg)]
    assert "junk.js" not in names
    assert "junk.js" not in repo_map(repo, cfg)


def test_pick_files_ranks_by_path(repo, cfg):
    (repo / "login.py").write_text("def login():\n    return None\n", encoding="utf-8")
    picked = [path.name for path in pick_files(repo, cfg, "修复 login 超时的问题")]
    assert picked and picked[0] == "login.py"


def test_pick_files_prefers_tests_for_bugfix(repo, cfg):
    (repo / "payment.py").write_text("def pay():\n    return 0\n", encoding="utf-8")
    (repo / "test_payment.py").write_text("def test_pay():\n    assert pay() == 1\n", encoding="utf-8")
    picked = [path.name for path in pick_files(repo, cfg, "把 payment 的测试修好")]
    assert "test_payment.py" in picked


def test_pick_files_returns_empty_when_nothing_matches(repo, cfg):
    assert pick_files(repo, cfg, "qqqqzzzz") == []
