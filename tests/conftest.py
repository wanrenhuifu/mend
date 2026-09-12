"""测试用的最小仓库与配置。"""

from __future__ import annotations

import pytest

from mend import ui
from mend.config import Config


@pytest.fixture(autouse=True)
def _quiet_console(monkeypatch):
    """每个测试都从一个"关色"的输出层开始。

    cli.main 会按 --color 改全局的 ui.console，不隔离的话测试之间会互相污染
    （前一个测试强制开色，后一个测试的纯文本断言就会挂）。
    """
    monkeypatch.setattr(ui, "console", ui.Console(color=False))
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def cfg(repo):
    return Config(root=repo)
