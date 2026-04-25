#!/usr/bin/env python3
"""统一质量门禁。一次性跑完 ruff、pyright、import-linter、custom lint全部检查。

用法（确保已激活 venv 或工具在 PATH 上）：
    uv run scripts/check.py

行为：
- 顺序执行所有检查，单项失败不中断后续
- 通过的检查仅打印一行 PASS；失败时输出完整日志便于定位
- 末尾打印汇总；任一项失败时退出码 1
- 不做自动修复（与 pre-commit 区别）；本脚本仅做检测
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


PATHS = ["src/harness_stata"]
ROOT = Path(__file__).resolve().parent.parent
TMP_ROOT = ROOT / ".tmp"
CSMAR_MCP_ROOT = "packages/CSMAR-Data-MCP"
CSMAR_MCP_PKG = f"{CSMAR_MCP_ROOT}/csmar_mcp"
CSMAR_MCP_CONFIG = f"{CSMAR_MCP_ROOT}/pyproject.toml"


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]


UV = ["uv", "run", "--"]

CHECKS: list[Check] = [
    Check("pytest", [*UV, "python", "-m", "pytest", "-x", "-q", "-s", "--tb=short", "-m", "not integration"]),
    Check("ruff lint", [*UV, "ruff", "check", *PATHS]),
    Check("ruff format", [*UV, "ruff", "format", "--check", *PATHS]),
    Check("pyright", [*UV, "pyright"]),
    Check("import-linter", [*UV, "lint-imports"]),
    Check("custom lint", [*UV, "python", "scripts/lint_custom.py"]),
    Check("ruff lint (csmar-mcp)", [*UV, "ruff", "check", CSMAR_MCP_PKG, "--config", CSMAR_MCP_CONFIG]),
    Check("ruff format (csmar-mcp)", [*UV, "ruff", "format", "--check", CSMAR_MCP_PKG, "--config", CSMAR_MCP_CONFIG]),
    Check("pyright (csmar-mcp)", [*UV, "pyright", "-p", CSMAR_MCP_CONFIG]),
]


def run_check(check: Check) -> int:
    TMP_ROOT.mkdir(exist_ok=True)
    try:
        result = subprocess.run(
            check.cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={
                **os.environ,
                "TMPDIR": str(TMP_ROOT),
                "TMP": str(TMP_ROOT),
                "TEMP": str(TMP_ROOT),
            },
        )
    except FileNotFoundError:
        print(f"  FAIL  {check.name}: 找不到命令 {check.cmd[0]!r}。请先激活 venv 并安装 dev 依赖。")
        return 127

    if result.returncode == 0:
        print(f"  PASS  {check.name}")
    else:
        print(f"  FAIL  {check.name}: {' '.join(check.cmd)}")
        output = (result.stdout or "").rstrip()
        if output:
            print(output)
    return result.returncode


def main() -> int:
    print("Quality gate")
    print("=" * 60)
    results: list[tuple[Check, int]] = [(check, run_check(check)) for check in CHECKS]

    failed = [check for check, code in results if code != 0]
    print("=" * 60)
    if failed:
        print(f"{len(failed)} of {len(results)} checks failed: {', '.join(c.name for c in failed)}")
        return 1
    print(f"All {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
