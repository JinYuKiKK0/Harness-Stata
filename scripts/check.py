#!/usr/bin/env python3
"""统一质量门禁。一次性跑完 ruff、pyright、import-linter、custom lint全部检查。

用法（确保已激活 venv 或工具在 PATH 上）：
    python scripts/check.py

行为：
- 顺序执行所有检查，单项失败不中断后续
- 末尾打印汇总；任一项失败时退出码 1
- 不做自动修复（与 pre-commit 区别）；本脚本仅做检测
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass


PATHS = ["src/harness_stata"]


@dataclass(frozen=True)
class Check:
    name: str
    cmd: list[str]


CHECKS: list[Check] = [
    Check("ruff lint", ["ruff", "check", *PATHS]),
    Check("ruff format", ["ruff", "format", "--check", *PATHS]),
    Check("pyright", ["pyright"]),
    Check("import-linter", ["lint-imports"]),
    Check("custom lint", [sys.executable, "scripts/lint_custom.py"]),
]


def run_check(check: Check) -> int:
    print(f"\n>>> {check.name}: {' '.join(check.cmd)}")
    try:
        result = subprocess.run(check.cmd)
    except FileNotFoundError:
        print(f"!!! 找不到命令 {check.cmd[0]!r}。请先激活 venv 并安装 dev 依赖。")
        return 127
    return result.returncode


def main() -> int:
    results: list[tuple[Check, int]] = [(check, run_check(check)) for check in CHECKS]

    print("\n" + "=" * 60)
    print("Quality gate summary")
    print("=" * 60)

    failed = 0
    for check, code in results:
        status = "PASS" if code == 0 else "FAIL"
        print(f"  {status}  {check.name}")
        if code != 0:
            failed += 1

    print()
    if failed:
        print(f"{failed} of {len(results)} checks failed")
        return 1
    print(f"All {len(results)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
