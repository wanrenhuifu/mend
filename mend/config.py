"""配置：默认值 <- mend.toml <- 环境变量，后者覆盖前者。

密钥只从环境变量（或 .env）读，配置文件里永远不出现 key。
"""

from __future__ import annotations

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
]
DEFAULT_FORBIDDEN = [
    ".git", ".env", ".venv", "venv", "node_modules",
    ".mend", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    ".idea", ".vscode",
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

    # 运行期字段：不来自配置文件
    root: Path = field(default_factory=Path.cwd, compare=False)
    api_key: str = field(default="", compare=False)

    @classmethod
    def load(cls, root: Path) -> "Config":
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

    def with_test_command(self, command: str) -> "Config":
        """给评测用：不改原对象，返回一份覆盖了测试命令的配置。"""
        from dataclasses import replace

        return replace(self, test_command=command)
