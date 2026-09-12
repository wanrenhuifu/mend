"""验证：agent 说自己改好了不算数，测试绿了才算。

这是整个项目的判定标准，也是它和"AI 写代码 demo"的区别所在。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

# 测试可能比较慢，给它单独的超时预算（默认单条命令 60s 太紧）
VERIFY_TIMEOUT_S = 300


@dataclass
class VerifyResult:
    ok: bool
    command: str = ""
    output: str = ""
    duration_ms: int = 0
    reason: str = ""


def detect_test_command(root: Path | str) -> str | None:
    """按项目类型猜一条最可能正确的测试命令。猜错了就在 mend.toml 里显式写。"""
    root = Path(root)
    if (root / "go.mod").exists():
        return "go test ./..."
    if (root / "Cargo.toml").exists():
        return "cargo test --quiet"
    if (root / "package.json").exists():
        return "npm test --silent"
    if (root / "tests").is_dir() or (root / "pytest.ini").exists() or (root / "pyproject.toml").exists():
        return "python -m pytest -q"
    return None


def run_tests(root, cfg, registry, command: str | None = None, timeout_s: int = VERIFY_TIMEOUT_S, trace=None) -> VerifyResult:
    """跑判定命令。故意走 registry，这样它和白名单、超时、轨迹是同一套约束。"""
    resolved = command or cfg.test_command_or_none() or detect_test_command(root)
    if not resolved:
        result = VerifyResult(False, reason="没有探测到测试命令；在 mend.toml 里写 test_command，或先补一个最小复现测试")
    else:
        started = time.time()
        outcome = registry.call("run_command", {"command": resolved, "timeout_s": timeout_s})
        result = VerifyResult(
            ok=outcome.ok,
            command=resolved,
            output=outcome.text(),
            duration_ms=int((time.time() - started) * 1000),
        )
    if trace is not None:
        trace.record(
            "verify",
            result.command or "(没有测试命令)",
            result.ok,
            result.duration_ms,
            reason=result.reason or None,
            tail=result.output[-600:] if result.output else None,
        )
    return result
