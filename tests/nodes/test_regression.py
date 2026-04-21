"""Unit tests for the regression node (F22)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.tools import BaseTool
from pytest_mock import MockerFixture

from harness_stata.nodes.regression import _RegressionOutput, regression
from harness_stata.state import EmpiricalSpec, MergedDataset, ModelPlan, WorkflowState

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _empty_stata_tools() -> AsyncIterator[list[BaseTool]]:
    yield []


def _patch_env(
    mocker: MockerFixture,
    *,
    payload: _RegressionOutput | None = None,
    raise_truncation: bool = False,
) -> MagicMock:
    """Patch create_agent + get_stata_tools at the regression module."""
    if raise_truncation:
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(side_effect=ModelCallLimitExceededError(1, 1, None, 1))
    else:
        assert payload is not None
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(
            return_value={"messages": [], "structured_response": payload}
        )
    mocker.patch(
        "harness_stata.nodes.regression.create_agent",
        return_value=fake_agent,
    )
    mocker.patch(
        "harness_stata.nodes.regression.get_stata_tools",
        side_effect=lambda: _empty_stata_tools(),
    )
    return fake_agent


def _make_session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "downloads" / "session1"
    session_dir.mkdir(parents=True)
    return session_dir


def _write_artifacts(session_dir: Path) -> tuple[Path, Path, Path]:
    """Create merged.csv + regression.do + regression.log under session_dir."""
    merged_csv = session_dir / "merged.csv"
    merged_csv.write_text("stkcd,year,roa,digital\n1,2020,0.1,0.5\n", encoding="utf-8")
    do_file = session_dir / "regression.do"
    do_file.write_text("reg roa digital\n", encoding="utf-8")
    log_file = session_dir / "regression.log"
    log_file.write_text("(stata log stub)\n", encoding="utf-8")
    return merged_csv, do_file, log_file


def _make_merged(merged_csv: Path) -> MergedDataset:
    return {
        "file_path": str(merged_csv),
        "row_count": 1,
        "columns": ["stkcd", "year", "roa", "digital"],
        "warnings": [],
    }


def _base_state(spec: EmpiricalSpec, plan: ModelPlan, merged: MergedDataset) -> WorkflowState:
    return {
        "empirical_spec": spec,
        "model_plan": plan,
        "merged_dataset": merged,
    }


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(regression(state))


def _payload(
    do_file: Path, log_file: Path, actual_sign: Literal["+", "-", "0"]
) -> _RegressionOutput:
    return _RegressionOutput(
        do_file_path=str(do_file),
        log_file_path=str(log_file),
        actual_sign=actual_sign,
        summary="Core coefficient estimated; see log for details.",
    )


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_regression_success_sign_consistent(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    session_dir = _make_session_dir(tmp_path)
    merged_csv, do_file, log_file = _write_artifacts(session_dir)
    _patch_env(mocker, payload=_payload(do_file, log_file, "+"))

    state = _base_state(make_empirical_spec(), make_model_plan(), _make_merged(merged_csv))
    out = _run(state)

    result = out["regression_result"]
    assert result["do_file_path"] == str(do_file)
    assert result["log_file_path"] == str(log_file)
    assert result["sign_check"] == {
        "variable_name": "DIGITAL",
        "expected_sign": "+",
        "actual_sign": "+",
        "consistent": True,
    }
    assert out["workflow_status"] == "success"


def test_regression_success_sign_inconsistent_does_not_raise(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    """Sign mismatch is a valid empirical outcome, not an error."""
    session_dir = _make_session_dir(tmp_path)
    merged_csv, do_file, log_file = _write_artifacts(session_dir)
    _patch_env(mocker, payload=_payload(do_file, log_file, "-"))

    state = _base_state(make_empirical_spec(), make_model_plan(), _make_merged(merged_csv))
    out = _run(state)

    sign_check = out["regression_result"]["sign_check"]
    assert sign_check["expected_sign"] == "+"
    assert sign_check["actual_sign"] == "-"
    assert sign_check["consistent"] is False
    assert out["workflow_status"] == "success"


def test_regression_success_sign_ambiguous_always_consistent(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    session_dir = _make_session_dir(tmp_path)
    merged_csv, do_file, log_file = _write_artifacts(session_dir)
    _patch_env(mocker, payload=_payload(do_file, log_file, "+"))

    plan = make_model_plan(
        core_hypothesis={
            "variable_name": "DIGITAL",
            "expected_sign": "ambiguous",
            "rationale": "方向理论未定",
        }
    )
    state = _base_state(make_empirical_spec(), plan, _make_merged(merged_csv))
    out = _run(state)

    sign_check = out["regression_result"]["sign_check"]
    assert sign_check["expected_sign"] == "ambiguous"
    assert sign_check["actual_sign"] == "+"
    assert sign_check["consistent"] is True


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_regression_react_truncation_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    """ModelCallLimitExceededError is translated to RuntimeError."""
    session_dir = _make_session_dir(tmp_path)
    merged_csv, _, _ = _write_artifacts(session_dir)
    _patch_env(mocker, raise_truncation=True)

    state = _base_state(make_empirical_spec(), make_model_plan(), _make_merged(merged_csv))
    with pytest.raises(RuntimeError, match="max_iterations"):
        _run(state)


def test_regression_log_file_missing_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    """LLM reports a log path that was never actually written."""
    session_dir = _make_session_dir(tmp_path)
    merged_csv = session_dir / "merged.csv"
    merged_csv.write_text("stkcd,year,roa,digital\n1,2020,0.1,0.5\n", encoding="utf-8")
    do_file = session_dir / "regression.do"
    do_file.write_text("reg roa digital\n", encoding="utf-8")
    missing_log = session_dir / "regression.log"  # deliberately NOT created
    _patch_env(mocker, payload=_payload(do_file, missing_log, "+"))

    state = _base_state(make_empirical_spec(), make_model_plan(), _make_merged(merged_csv))
    with pytest.raises(RuntimeError, match="log_file_path"):
        _run(state)


def test_regression_missing_merged_dataset_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "model_plan": make_model_plan(),
    }
    with pytest.raises(ValueError, match="merged_dataset"):
        _run(state)
