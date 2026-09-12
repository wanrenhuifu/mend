"""配置：默认值 <- mend.toml <- 环境变量，后者覆盖前者。

密钥只从环境变量（或 .env）读，配置文件里永远不出现 key。
"""

from __future__ import annotations

import fnmatch
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

CONFIG_NAME = "mend.toml"
EXAMPLE_NAME = "mend.example.toml"

DEFAULT_ALLOWED = [
    "pytest", "python", "python3", "pip",
    "npm", "node", "npx", "go", "cargo", "make",
    "ruff", "mypy", "tsc", "eslint",
    "ls",  # 只读且无副作用。模型习惯性地敲 ls，不给它加这行它会白费一步
]
DEFAULT_FORBIDDEN = [
    ".git", ".env", ".venv", "venv", "node_modules",
    ".mend", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".idea", ".vscode",
]
DEFAULT_PROTECTED = [
    "tests/",  # 结尾带 / 表示"路径里任何一层叫这个名字的目录"
    "spec/",
    "test_*.py",
    "*_test.py",
    "*_test.go",
    "conftest.py",
]


def _load_dotenv(path: Path) -> None:
    """极简 .env 读取：只填 os.environ 里还没有的键，不覆盖真实环境变量。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Config:
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "MEND_API_KEY"
    max_steps: int = 25
    timeout_s: int = 60
    test_command: str = ""
    allowed_commands: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED))
    forbidden_paths: list[str] = field(default_factory=lambda: list(DEFAULT_FORBIDDEN))
    protected_paths: list[str] = field(default_factory=lambda: list(DEFAULT_PROTECTED))

    # 运行期字段：不来自配置文件
    root: Path = field(default_factory=Path.cwd, compare=False)
    api_key: str = field(default="", compare=False)

    @classmethod
    def load(cls, root: Path) -> Config:
        root = Path(root).resolve()
        data: dict = {}
        path = root / CONFIG_NAME
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)} - {"root", "api_key"}
        cfg = cls(**{k: v for k, v in data.items() if k in known})
        cfg.root = root
        _load_dotenv(root / ".env")
        cfg.model = os.environ.get("MEND_MODEL", cfg.model)
        cfg.base_url = os.environ.get("MEND_BASE_URL", cfg.base_url)
        cfg.api_key = os.environ.get(cfg.api_key_env, "")
        return cfg

    def test_command_or_none(self) -> str | None:
        return self.test_command.strip() or None

    def is_protected(self, path: str) -> bool:
        """这个路径是不是"测试文件"——是的话写工具会直接拒绝改它。

        为什么要有这个：提示词里写"禁止改测试"是一种请求，模型可以不听；
        把测试文件设成不可写是一种保证。测试是判定的依据，不是可以商量的目标。

        默认只保护测试代码，不保护配置文件（pytest.ini / pyproject.toml 之类）：
        agent 经常需要合法地改它们（加依赖、改打包配置），一刀切会挡住正常活。
        想更严就自己往 protected_paths 里加。
        """
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                candidate = candidate.resolve().relative_to(self.root.resolve())
            except ValueError:
                pass  # 仓库外的路径交给 tools 的越界检查，这里不管
        rel = candidate.as_posix()
        if rel.startswith("./"):
            rel = rel[2:]
        parts = Path(rel).parts
        for pattern in self.protected_paths:
            if pattern.endswith("/"):
                if pattern.rstrip("/") in parts:
                    return True
            elif fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern):
                return True
        return False

    def with_test_command(self, command: str) -> Config:
        """给评测用：不改原对象，返回一份覆盖了测试命令的配置。"""
        from dataclasses import replace

        return replace(self, test_command=command)
