#!/usr/bin/env python3
"""Session 启动脚本。每次新开 Claude session 时运行，让 Claude 一次性看到当前项目状态。

执行内容：
1. 跑全套质量门禁（scripts/check.py：ruff / pyright / import-linter / custom lint）
2. 展示最近若干条 git 提交，帮助 Claude 快速对齐项目最近的演进
3. 输出 PROGRESS.md 的内容，让 Claude 立刻知道当前焦点与下一步

用法：
    python scripts/init.py

退出码：质量门禁失败或 git 命令失败时为 1；全部通过时为 0
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS_FILE = ROOT / "PROGRESS.md"
GIT_LOG_COUNT = 10


def run(header: str, cmd: list[str]) -> int:
    print(f"\n{'=' * 60}")
    print(f">>> {header}")
    print(f"    $ {' '.join(cmd)}")
    print("=" * 60)
    try:
        result = subprocess.run(cmd, cwd=ROOT)
    except FileNotFoundError:
        print(f"!!! 找不到命令 {cmd[0]!r}")
        return 127
    return result.returncode


def dump_progress() -> int:
    print(f"\n{'=' * 60}")
    print(f">>> Step 3/3  进度文件（{PROGRESS_FILE.relative_to(ROOT)}）")
    print("=" * 60)
    if not PROGRESS_FILE.exists():
        print(f"!!! 进度文件不存在：{PROGRESS_FILE}")
        return 1
    sys.stdout.write(PROGRESS_FILE.read_text(encoding="utf-8"))
    sys.stdout.write("\n")
    return 0


def main() -> int:
    quality_code = run(
        "Step 1/3  质量门禁",
        [sys.executable, "scripts/check.py"],
    )
    log_code = run(
        f"Step 2/3  最近 {GIT_LOG_COUNT} 条 git 提交",
        ["git", "log", "-n", str(GIT_LOG_COUNT), "--oneline", "--decorate"],
    )
    progress_code = dump_progress()

    print(f"\n{'=' * 60}")
    print("Session init summary")
    print("=" * 60)
    print(f"  质量门禁   : {'PASS' if quality_code == 0 else 'FAIL'}")
    print(f"  git log    : {'OK' if log_code == 0 else 'FAIL'}")
    print(f"  PROGRESS.md: {'OK' if progress_code == 0 else 'FAIL'}")
    print()

    return 0 if quality_code == 0 and log_code == 0 and progress_code == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
