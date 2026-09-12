"""测试用的最小仓库与配置。"""

from __future__ import annotations

import pytest

from mend.config import Config


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def cfg(repo):
    return Config(root=repo)
