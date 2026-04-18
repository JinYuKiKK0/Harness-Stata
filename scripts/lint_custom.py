#!/usr/bin/env python3
"""项目专属的自定义 lint 检查（ruff/pyright/import-linter 之外的）。

包含：
- check_prompts：nodes/subgraphs 中 load_prompt('xxx') 引用的 prompts/xxx.md 必须真实存在
- check_node_exports：nodes/<name>.py 必须定义同名的模块级 callable（节点对外契约）
- check_file_size：单个 .py 文件超出阈值时报告（warn/error 两级）
- check_architecture：CLAUDE.md 架构树与实际文件系统的一致性
- check_state_docs：state.py TypedDict 定义与 docs/state.md 文档的一致性

用法：
    python scripts/lint_custom.py

退出码：发现 error 级问题 → 1；只有 warn → 0
"""

from __future__ import annotations

import ast
import re
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

SCAN_PY_DIRS = [SRC]

CLAUDE_MD = ROOT / "CLAUDE.md"
STATE_PY = SRC / "state.py"
STATE_MD = ROOT / "docs" / "state.md"


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


def _parse_architecture_tree(tree_lines: list[str]) -> tuple[set[str], set[str]]:
    """解析 ASCII 树为 (所有路径, 目录路径)，路径相对于 ROOT。"""
    entry_re = re.compile(r"^([\s│]*)([├└]──\s+)(.+?)(\s+#.*)?$")

    all_paths: set[str] = set()
    dir_paths: set[str] = set()
    # (depth, dir_name) 栈，用于拼接完整路径
    dir_stack: list[tuple[int, str]] = [(-1, "")]

    for line in tree_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("harness-stata"):
            continue

        m = entry_re.match(line)
        if not m:
            continue

        prefix = m.group(1)
        name = m.group(3).strip()
        depth = len(prefix) // 4

        while len(dir_stack) > 1 and dir_stack[-1][0] >= depth:
            dir_stack.pop()

        parent_parts = [s[1] for s in dir_stack if s[1]]
        parent_path = "/".join(parent_parts)

        is_dir = name.endswith("/")
        clean_name = name.rstrip("/")
        full_path = f"{parent_path}/{clean_name}" if parent_path else clean_name

        all_paths.add(full_path)
        if is_dir:
            dir_paths.add(full_path)
            dir_stack.append((depth, clean_name))

    return all_paths, dir_paths


def check_architecture() -> list[Issue]:
    """CLAUDE.md 架构树与实际文件系统的一致性检查。"""
    issues: list[Issue] = []

    try:
        text = CLAUDE_MD.read_text(encoding="utf-8")
    except OSError as e:
        issues.append(Issue("check_architecture", "error", CLAUDE_MD, f"无法读取: {e}"))
        return issues

    # 定位 "## 项目组织架构" 后的代码块
    lines = text.splitlines()
    in_section = False
    in_block = False
    tree_lines: list[str] = []

    for line in lines:
        if line.startswith("## 项目组织架构"):
            in_section = True
            continue
        if in_section and not in_block:
            if line.strip().startswith("```"):
                in_block = True
                continue
            if line.startswith("## "):
                break
        if in_block:
            if line.strip().startswith("```"):
                break
            tree_lines.append(line)

    if not tree_lines:
        issues.append(
            Issue(
                "check_architecture",
                "error",
                CLAUDE_MD,
                "未找到 '项目组织架构' 代码块。"
                " Fix: 在 CLAUDE.md 中添加架构树代码块。",
            )
        )
        return issues

    all_paths, dir_paths = _parse_architecture_tree(tree_lines)

    # 正向检查：架构树中的路径必须在磁盘上存在
    for path_str in sorted(all_paths):
        disk_path = ROOT / path_str
        if path_str in dir_paths:
            if not disk_path.is_dir():
                issues.append(
                    Issue(
                        "check_architecture",
                        "error",
                        CLAUDE_MD,
                        f"架构树中的目录 {path_str}/ 在磁盘上不存在。"
                        f" Fix: 创建该目录，或从 CLAUDE.md 架构树中移除。",
                    )
                )
        elif not disk_path.is_file():
            issues.append(
                Issue(
                    "check_architecture",
                    "error",
                    CLAUDE_MD,
                    f"架构树中的文件 {path_str} 在磁盘上不存在。"
                    f" Fix: 创建该文件，或从 CLAUDE.md 架构树中移除。",
                )
            )

    # 反向检查：src/harness_stata/ 下的 .py 文件和目录是否都在架构树中
    src_prefix = "src/harness_stata"
    tree_src_paths = {p for p in all_paths if p.startswith(src_prefix)}

    for item in sorted(SRC.rglob("*")):
        if "__pycache__" in item.parts:
            continue
        if item.name == "__init__.py":
            continue
        if item.is_file() and item.suffix != ".py":
            continue

        rel = str(item.relative_to(ROOT)).replace("\\", "/")
        if rel not in tree_src_paths:
            kind = "目录" if item.is_dir() else "文件"
            issues.append(
                Issue(
                    "check_architecture",
                    "warn",
                    CLAUDE_MD,
                    f"磁盘上的{kind} {rel} 未在 CLAUDE.md 架构树中列出。"
                    f" Fix: 将其添加到 CLAUDE.md 架构树，或确认是否应删除。",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# check_state_docs helpers
# ---------------------------------------------------------------------------


def _parse_typedicts_from_code(source: str) -> dict[str, list[str]]:
    """AST 解析 state.py，提取 TypedDict 类名及其字段名列表。"""
    tree = ast.parse(source)
    result: dict[str, list[str]] = {}

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name == "WorkflowState":
            continue

        is_typeddict = any(
            isinstance(b, ast.Name) and b.id == "TypedDict" for b in node.bases
        )
        if not is_typeddict:
            continue

        fields = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
        if fields:
            result[node.name] = fields

    return result


def _parse_typedicts_from_docs(text: str) -> dict[str, list[str]]:
    """解析 docs/state.md，提取已文档化的 TypedDict 名称及其字段名列表。"""
    lines = text.splitlines()
    result: dict[str, list[str]] = {}

    header_re = re.compile(r"^#{4}\s+(\w+)")
    bold_re = re.compile(r"^\*\*(\w+)\*\*\s*$")

    i = 0
    while i < len(lines):
        line = lines[i]
        name: str | None = None

        m = header_re.match(line)
        if m:
            name = m.group(1)
        else:
            m = bold_re.match(line)
            if m:
                name = m.group(1)

        if name is None:
            i += 1
            continue

        # 向前扫描，寻找紧随的 Markdown 表格
        j = i + 1
        while j < len(lines) and not lines[j].strip().startswith("|"):
            if header_re.match(lines[j]) or bold_re.match(lines[j]):
                break
            j += 1

        if j >= len(lines) or not lines[j].strip().startswith("|"):
            i += 1
            continue

        # 解析表格：跳过表头行和分隔行
        fields: list[str] = []
        header_seen = False
        separator_seen = False

        while j < len(lines) and lines[j].strip().startswith("|"):
            cells = [c.strip() for c in lines[j].split("|")]
            cells = [c for c in cells if c]

            if not header_seen:
                header_seen = True
                j += 1
                continue

            if not separator_seen:
                if cells and all(set(c) <= {"-", " ", ":"} for c in cells):
                    separator_seen = True
                    j += 1
                    continue

            if cells:
                fields.append(cells[0])
            j += 1

        if fields:
            result[name] = fields

        i = max(i + 1, j)

    return result


def _normalize_name(name: str) -> str:
    """归一化名称用于模糊匹配（PascalCase / snake_case 统一）。"""
    return name.lower().replace("_", "")


def check_state_docs() -> list[Issue]:
    """state.py TypedDict 定义与 docs/state.md 文档的一致性检查。"""
    issues: list[Issue] = []

    try:
        source = STATE_PY.read_text(encoding="utf-8")
    except OSError as e:
        issues.append(Issue("check_state_docs", "error", STATE_PY, f"无法读取: {e}"))
        return issues

    try:
        code_typedicts = _parse_typedicts_from_code(source)
    except SyntaxError as e:
        issues.append(Issue("check_state_docs", "error", STATE_PY, f"无法解析: {e}"))
        return issues

    try:
        doc_text = STATE_MD.read_text(encoding="utf-8")
    except OSError as e:
        issues.append(Issue("check_state_docs", "error", STATE_MD, f"无法读取: {e}"))
        return issues

    doc_typedicts = _parse_typedicts_from_docs(doc_text)

    # 构建归一化名称查找表
    doc_normalized: dict[str, str] = {}
    for doc_name in doc_typedicts:
        doc_normalized[_normalize_name(doc_name)] = doc_name

    for code_name, code_fields in sorted(code_typedicts.items()):
        norm = _normalize_name(code_name)
        doc_name = doc_normalized.get(norm)

        if doc_name is None:
            issues.append(
                Issue(
                    "check_state_docs",
                    "error",
                    STATE_PY,
                    f"TypedDict {code_name!r} 未在 docs/state.md 中记录。"
                    f" Fix: 在 docs/state.md 中添加 {code_name} 的字段文档表格。",
                )
            )
            continue

        doc_field_set = set(doc_typedicts[doc_name])
        code_field_set = set(code_fields)

        for f in sorted(code_field_set - doc_field_set):
            issues.append(
                Issue(
                    "check_state_docs",
                    "error",
                    STATE_MD,
                    f"{code_name}.{f} 在代码中存在但未在 docs/state.md 中记录。"
                    f" Fix: 在 docs/state.md 的 {code_name} 表格中添加字段 {f}。",
                )
            )

        for f in sorted(doc_field_set - code_field_set):
            issues.append(
                Issue(
                    "check_state_docs",
                    "warn",
                    STATE_MD,
                    f"{code_name} 的文档中有字段 {f!r} 但代码中不存在。"
                    f" Fix: 从 docs/state.md 的 {code_name} 表格中移除字段 {f}，"
                    f"或在 state.py 中添加该字段。",
                )
            )

    return issues


def main() -> int:
    issues: list[Issue] = []
    for fn in (check_prompts, check_node_exports, check_file_size, check_architecture, check_state_docs):
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
