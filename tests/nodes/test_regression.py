"""Pure-logic tests for the regression node — pre-LLM paths only."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest

from harness_stata.nodes.regression import (
    _build_human_prompt,
    _check_core_var_present,
    _payload_to_sign_check,
    _SignCheckOutput,
    _strip_stata_noncode,
    _validate,
    regression,
)
from harness_stata.state import EmpiricalSpec, MergedDataset, ModelPlan, WorkflowState

_FAKE_RTF = Path("/tmp/02_regression.rtf")


def _make_merged() -> MergedDataset:
    return {
        "file_path": "/tmp/merged.csv",
        "row_count": 100,
        "columns": ["stkcd", "year", "ROA", "DIGITAL", "SIZE"],
        "warnings": [],
    }


def _state_complete(spec: EmpiricalSpec, plan: ModelPlan) -> WorkflowState:
    return {
        "empirical_spec": spec,
        "model_plan": plan,
        "merged_dataset": _make_merged(),
    }


# ---------------------------------------------------------------------------
# _validate
# ---------------------------------------------------------------------------


def test_validate_missing_empirical_spec(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {"model_plan": make_model_plan(), "merged_dataset": _make_merged()}
    assert "empirical_spec" in (_validate(state) or "")


def test_validate_missing_model_plan(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "merged_dataset": _make_merged(),
    }
    assert "model_plan" in (_validate(state) or "")


def test_validate_missing_merged_dataset(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "model_plan": make_model_plan(),
    }
    assert "merged_dataset" in (_validate(state) or "")


def test_validate_complete_returns_none(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    assert _validate(_state_complete(make_empirical_spec(), make_model_plan())) is None


# ---------------------------------------------------------------------------
# _strip_stata_noncode
# ---------------------------------------------------------------------------


def test_strip_block_comments() -> None:
    s = "reghdfe ROA DIGITAL /* SIZE hidden */ , absorb(stkcd year)"
    out = _strip_stata_noncode(s)
    assert "SIZE" not in out
    assert "DIGITAL" in out


def test_strip_double_slash_line_comments() -> None:
    s = "xtreg ROA DIGITAL // SIZE hidden\n"
    out = _strip_stata_noncode(s)
    assert "SIZE" not in out


def test_strip_full_line_star_comments() -> None:
    s = "* SIZE only here\nxtreg ROA DIGITAL"
    out = _strip_stata_noncode(s)
    assert "SIZE" not in out


def test_strip_double_quoted_strings() -> None:
    s = 'reghdfe ROA DIGITAL\ndi "SIZE in a label"'
    out = _strip_stata_noncode(s)
    assert "SIZE" not in out


# ---------------------------------------------------------------------------
# _check_core_var_present
# ---------------------------------------------------------------------------


def test_check_core_var_present_command_position(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    plan = make_model_plan()  # core var 默认 DIGITAL
    cmds = "xtreg ROA DIGITAL SIZE, fe"
    _check_core_var_present(cmds, plan)


def test_check_core_var_missing_raises(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    plan = make_model_plan()
    cmds = "xtreg ROA SIZE, fe"
    with pytest.raises(ValueError, match="DIGITAL"):
        _check_core_var_present(cmds, plan)


def test_check_core_var_only_in_comment_raises(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    plan = make_model_plan()
    cmds = "xtreg ROA SIZE, fe  // include DIGITAL later"
    with pytest.raises(ValueError, match="DIGITAL"):
        _check_core_var_present(cmds, plan)


def test_check_core_var_substring_not_match(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    plan = make_model_plan()
    # DIGITAL_LAG 含 DIGITAL 子串,边界保护必须否决
    cmds = "xtreg ROA DIGITAL_LAG SIZE, fe"
    with pytest.raises(ValueError, match="DIGITAL"):
        _check_core_var_present(cmds, plan)


def test_check_core_var_case_sensitive(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    plan = make_model_plan()
    cmds = "xtreg ROA digital SIZE, fe"
    with pytest.raises(ValueError, match="DIGITAL"):
        _check_core_var_present(cmds, plan)


# ---------------------------------------------------------------------------
# _payload_to_sign_check
# ---------------------------------------------------------------------------


def test_payload_to_sign_check_round_trip() -> None:
    sc = _SignCheckOutput(
        variable_name="DIGITAL",
        expected_sign="+",
        actual_sign="+",
        consistent=True,
    )
    result = _payload_to_sign_check(sc)
    assert result == {
        "variable_name": "DIGITAL",
        "expected_sign": "+",
        "actual_sign": "+",
        "consistent": True,
    }


# ---------------------------------------------------------------------------
# _build_human_prompt
# ---------------------------------------------------------------------------


def test_human_prompt_contains_equation_and_hypothesis(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    prompt = _build_human_prompt(
        make_empirical_spec(), make_model_plan(), _make_merged(), _FAKE_RTF
    )
    assert "<inputs>" in prompt and "<reminder>" in prompt
    assert "ROA_it = a + b*DIGITAL_it" in prompt
    # core_hypothesis
    assert "DIGITAL" in prompt
    assert "expected_sign" in prompt
    # 数据
    assert "`/tmp/merged.csv`" in prompt
    assert "`stkcd`" in prompt
    # rtf 路径与终止条件
    assert "rtf_table_path" in prompt
    assert str(_FAKE_RTF) in prompt
    assert "esttab using" in prompt
    assert "结构化输出工具" in prompt


def test_human_prompt_no_workflow_timing_leakage(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    prompt = _build_human_prompt(
        make_empirical_spec(), make_model_plan(), _make_merged(), _FAKE_RTF
    )
    forbidden = [
        "model_construction 节点",
        "data_cleaning 节点",
        "descriptive_stats 节点",
        "上游已完成",
        "下游会用",
    ]
    for token in forbidden:
        assert token not in prompt, f"prompt 不应出现工作流时序短语: {token!r}"


# ---------------------------------------------------------------------------
# Node entry — pre-LLM raise paths
# ---------------------------------------------------------------------------


def test_regression_missing_empirical_spec_raises(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {"model_plan": make_model_plan(), "merged_dataset": _make_merged()}
    with pytest.raises(ValueError, match="empirical_spec"):
        asyncio.run(regression(state))


def test_regression_missing_model_plan_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "merged_dataset": _make_merged(),
    }
    with pytest.raises(ValueError, match="model_plan"):
        asyncio.run(regression(state))


def test_regression_missing_merged_dataset_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "model_plan": make_model_plan(),
    }
    with pytest.raises(ValueError, match="merged_dataset"):
        asyncio.run(regression(state))
