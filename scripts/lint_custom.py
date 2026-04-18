#!/usr/bin/env python3
"""项目专属的自定义 lint 检查（ruff/pyright/import-linter 之外的）。

包含：
- check_prompts：nodes/subgraphs 中 load_prompt('xxx') 引用的 prompts/xxx.md 必须真实存在
- check_node_exports：nodes/<name>.py 必须定义同名的模块级 callable（节点对外契约）
- check_file_size：单个 .py 文件超出阈值时报告（warn/error 两级）

用法：
    python scripts/lint_custom.py

退出码：发现 error 级问题 → 1；只有 warn → 0
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "harness_stata"
NODES_DIR = SRC / "nodes"
SUBGRAPHS_DIR = SRC / "subgraphs"
PROMPTS_DIR = SRC / "prompts"

WARN_LINES = 300
FAIL_LINES = 500

SCAN_PY_DIRS = [SRC, ROOT / "scripts", ROOT / "tests"]


Severity = Literal["warn", "error"]


@dataclass
class Issue:
    check: str
    severity: Severity
    file: Path
    msg: str

    def render(self) -> str:
        rel = self.file.relative_to(ROOT)
        return f"  [{self.severity.upper():5}] [{self.check}] {rel}: {self.msg}"


def _iter_py(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(p for p in directory.rglob("*.py") if p.name != "__init__.py")


def _has_real_code(tree: ast.Module) -> bool:
    """模块体内是否包含 docstring/pass 之外的内容"""
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            continue
        if isinstance(node, ast.Pass):
            continue
        return True
    return False


def check_prompts() -> list[Issue]:
    issues: list[Issue] = []
    for py in [*_iter_py(NODES_DIR), *_iter_py(SUBGRAPHS_DIR)]:
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            issues.append(Issue("check_prompts", "error", py, f"无法解析: {e}"))
            continue

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "load_prompt"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            name = node.args[0].value
            target = PROMPTS_DIR / f"{name}.md"
            if not target.exists():
                rel = target.relative_to(ROOT)
                issues.append(
                    Issue(
                        "check_prompts",
                        "error",
                        py,
                        f"line {node.lineno}: 引用了不存在的 prompt {name!r}（期望 {rel}）。"
                        f" Fix: 在 {PROMPTS_DIR.relative_to(ROOT)} 下创建 {name}.md，或修正引用名。",
                    )
                )
    return issues


def check_node_exports() -> list[Issue]:
    issues: list[Issue] = []
    for py in _iter_py(NODES_DIR):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError as e:
            issues.append(Issue("check_node_exports", "error", py, f"无法解析: {e}"))
            continue

        if not _has_real_code(tree):
            continue  # 空骨架文件不检；待写入实质内容后再验证

        expected = py.stem
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        names.add(tgt.id)

        if expected not in names:
            issues.append(
                Issue(
                    "check_node_exports",
                    "error",
                    py,
                    f"未定义同名实体 {expected!r}（按约定 nodes/<name>.py 必须导出 callable {expected!r}）。"
                    f" Fix: 在该文件内定义 def {expected}(state) 或赋值 {expected} = <compiled_subgraph>。",
                )
            )
    return issues


def check_file_size() -> list[Issue]:
    issues: list[Issue] = []
    for d in SCAN_PY_DIRS:
        if not d.exists():
            continue
        for py in d.rglob("*.py"):
            n = len(py.read_text(encoding="utf-8").splitlines())
            if n > FAIL_LINES:
                issues.append(
                    Issue(
                        "check_file_size",
                        "error",
                        py,
                        f"{n} 行 (>{FAIL_LINES})——必须拆分。"
                        f" Fix: 按职责切分为多个文件；nodes/ 目录下的复杂节点可拆出 helper 模块。",
                    )
                )
            elif n > WARN_LINES:
                issues.append(
                    Issue(
                        "check_file_size",
                        "warn",
                        py,
                        f"{n} 行 (>{WARN_LINES})——建议拆分。",
                    )
                )
    return issues


def main() -> int:
    issues: list[Issue] = []
    for fn in (check_prompts, check_node_exports, check_file_size):
        issues.extend(fn())

    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity == "warn"]

    if not issues:
        sys.stdout.write("custom lint: all checks passed\n")
        return 0

    sys.stdout.write("custom lint issues:\n")
    for i in issues:
        sys.stdout.write(i.render() + "\n")
    sys.stdout.write(f"\n{len(errors)} error(s), {len(warns)} warning(s)\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
