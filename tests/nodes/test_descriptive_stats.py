"""Pure-logic tests for the descriptive_stats node — pre-LLM paths only."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from harness_stata.nodes.descriptive_stats import (
    _build_human_prompt,
    _check_variables_covered,
    _strip_stata_noncode,
    _validate,
    descriptive_stats,
)
from harness_stata.state import EmpiricalSpec, MergedDataset, WorkflowState

_FAKE_RTF = Path("/tmp/01_descriptive_stats.rtf")


def _make_merged() -> MergedDataset:
    return {
        "file_path": "/tmp/merged.csv",
        "row_count": 100,
        "columns": ["stkcd", "year", "ROA", "DIGITAL", "SIZE"],
        "warnings": [],
    }


def _state_complete(spec: EmpiricalSpec) -> WorkflowState:
    return {"empirical_spec": spec, "merged_dataset": _make_merged()}


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------


def test_validate_missing_empirical_spec(
    make_empirical_spec: Callable[..., EmpiricalSpec],  # noqa: ARG001
) -> None:
    state: WorkflowState = {"merged_dataset": _make_merged()}
    assert _validate(state) is not None
    assert "empirical_spec" in (_validate(state) or "")


def test_validate_missing_merged_dataset(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    assert "merged_dataset" in (_validate(state) or "")


def test_validate_empty_file_path(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    merged: MergedDataset = {"file_path": "", "row_count": 0, "columns": [], "warnings": []}
    state: WorkflowState = {"empirical_spec": make_empirical_spec(), "merged_dataset": merged}
    assert "file_path" in (_validate(state) or "")


def test_validate_complete_returns_none(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    assert _validate(_state_complete(make_empirical_spec())) is None


# ---------------------------------------------------------------------------
# _strip_stata_noncode
# ---------------------------------------------------------------------------


def test_strip_block_comments() -> None:
    s = "sum X /* hide Y here */ Z"
    out = _strip_stata_noncode(s)
    assert "Y" not in out
    assert "X" in out and "Z" in out


def test_strip_block_comments_multiline() -> None:
    s = "sum X\n/* hide\nY across\nlines */\nsum Z"
    out = _strip_stata_noncode(s)
    assert "Y" not in out


def test_strip_double_slash_line_comments() -> None:
    s = "sum X // mention Y here\nsum Z"
    out = _strip_stata_noncode(s)
    assert "Y" not in out
    assert "X" in out and "Z" in out


def test_strip_full_line_star_comments() -> None:
    s = "* hide Y on this line\nsum X"
    out = _strip_stata_noncode(s)
    assert "Y" not in out
    assert "X" in out


def test_strip_double_quoted_strings() -> None:
    s = 'di "Y is hidden here"\nsum X'
    out = _strip_stata_noncode(s)
    assert "Y" not in out
    assert "X" in out


# ---------------------------------------------------------------------------
# _check_variables_covered
# ---------------------------------------------------------------------------


def test_check_variables_covered_all_present(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    spec = make_empirical_spec()
    cmds = "sum ROA DIGITAL SIZE"
    _check_variables_covered(cmds, spec)  # 不抛即 OK


def test_check_variables_covered_one_missing_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    spec = make_empirical_spec()
    cmds = "sum ROA DIGITAL"   # 缺 SIZE
    with pytest.raises(ValueError, match="SIZE"):
        _check_variables_covered(cmds, spec)


def test_check_variables_covered_only_in_block_comment_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    spec = make_empirical_spec()
    cmds = "sum ROA DIGITAL /* SIZE mentioned only here */"
    with pytest.raises(ValueError, match="SIZE"):
        _check_variables_covered(cmds, spec)


def test_check_variables_covered_only_in_string_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    spec = make_empirical_spec()
    cmds = 'sum ROA DIGITAL\ndi "SIZE will appear in a label"'
    with pytest.raises(ValueError, match="SIZE"):
        _check_variables_covered(cmds, spec)


def test_check_variables_covered_substring_not_treated_as_match(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    spec = make_empirical_spec()
    # ROAB 含 ROA 子串,但 \w 边界保护必须否决
    cmds = "sum ROAB DIGITAL SIZE"
    with pytest.raises(ValueError, match="ROA"):
        _check_variables_covered(cmds, spec)


def test_check_variables_covered_case_sensitive(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Stata 变量大小写敏感:`roa` 不应命中变量 `ROA`。"""
    spec = make_empirical_spec()
    cmds = "sum roa DIGITAL SIZE"
    with pytest.raises(ValueError, match="ROA"):
        _check_variables_covered(cmds, spec)


# ---------------------------------------------------------------------------
# _build_human_prompt
# ---------------------------------------------------------------------------


def test_human_prompt_contains_inputs_and_reminder_blocks(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    spec = make_empirical_spec()
    merged = _make_merged()
    prompt = _build_human_prompt(spec, merged, _FAKE_RTF)
    assert "<inputs>" in prompt and "</inputs>" in prompt
    assert "<reminder>" in prompt and "</reminder>" in prompt
    assert "merged_dataset_path" in prompt
    assert "`/tmp/merged.csv`" in prompt
    # 列名清单
    assert "`stkcd`" in prompt
    assert "`ROA`" in prompt
    # 变量定义
    assert "DIGITAL" in prompt
    # rtf 路径与终止条件
    assert "rtf_table_path" in prompt
    assert str(_FAKE_RTF) in prompt
    assert "esttab using" in prompt
    # reminder 应复述终止条件
    assert "结构化输出工具" in prompt


def test_human_prompt_no_workflow_timing_leakage(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """agent-node-prompting skill 红线:不渲染上下游身份/工作流时序。"""
    prompt = _build_human_prompt(make_empirical_spec(), _make_merged(), _FAKE_RTF)
    forbidden = ["data_cleaning 节点", "上游已完成", "下游会用", "工作流"]
    for token in forbidden:
        assert token not in prompt, f"prompt 不应出现工作流时序短语: {token!r}"


# ---------------------------------------------------------------------------
# Node entry — pre-LLM raise paths
# ---------------------------------------------------------------------------


def test_descriptive_stats_missing_empirical_spec_raises() -> None:
    state: WorkflowState = {"merged_dataset": _make_merged()}
    with pytest.raises(ValueError, match="empirical_spec"):
        asyncio.run(descriptive_stats(state))


def test_descriptive_stats_missing_merged_dataset_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="merged_dataset"):
        asyncio.run(descriptive_stats(state))
