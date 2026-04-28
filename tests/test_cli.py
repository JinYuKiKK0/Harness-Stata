"""End-to-end CLI tests.

Strategy: patch the 7 non-hitl node functions imported into
``harness_stata.graph`` with stubs that return predetermined state
increments. Leave ``hitl`` real so that ``interrupt()`` actually pauses
the graph, exercising the CLI's interrupt / resume handling.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from harness_stata.cli import app
from harness_stata.state import WorkflowState


_REQUIRED_ARGS = [
    "run",
    "--topic", "数字化转型对盈利能力的影响",
    "--x-variable", "DIGITAL",
    "--y-variable", "ROA",
    "--sample-scope", "A股上市公司",
    "--time-range-start", "2015",
    "--time-range-end", "2022",
    "--data-frequency", "yearly",
    "--thread-id", "test-thread",
]


def _stub_node(return_state: dict[str, Any], *, is_async: bool) -> Any:
    """Build a stub node callable returning a fixed state increment."""
    if is_async:
        return AsyncMock(return_value=return_state)
    m = MagicMock(return_value=return_state)
    return m


@pytest.fixture()
def patch_nodes(mocker: Any) -> Callable[..., None]:
    """Patch the 7 business nodes imported into ``harness_stata.graph``.

    The ``hitl`` node is intentionally NOT patched so that the real
    ``interrupt()`` fires during ``ainvoke``.
    """

    def _patch(
        *,
        probe_overall: str = "success",
        probe_workflow_status: str | None = None,
    ) -> None:
        empirical_spec = {
            "topic": "数字化转型对盈利能力的影响",
            "variables": [
                {"name": "ROA", "description": "净资产收益率", "contract_type": "hard", "role": "dependent"},
                {"name": "DIGITAL", "description": "数字化指数", "contract_type": "hard", "role": "independent"},
            ],
            "sample_scope": "A股上市公司",
            "time_range_start": "2015",
            "time_range_end": "2022",
            "data_frequency": "yearly",
            "analysis_granularity": "公司-年度",
        }
        model_plan = {
            "model_type": "双向固定效应面板",
            "equation": "ROA = a + b*DIGITAL + e",
            "core_hypothesis": {"variable_name": "DIGITAL", "expected_sign": "+", "rationale": "r"},
            "data_structure_requirements": ["面板"],
        }
        probe_report = {
            "variable_results": [
                {
                    "variable_name": "DIGITAL",
                    "status": "found",
                    "source": {"database": "CSMAR", "table": "T", "field": "F"},
                    "record_count": 100,
                }
            ],
            "overall_status": probe_overall,
            "failure_reason": None if probe_overall == "success" else "hard-not-found",
        }
        download_manifest = {"tasks": []}

        probe_return: dict[str, Any] = {
            "probe_report": probe_report,
            "download_manifest": download_manifest,
            "empirical_spec": empirical_spec,
        }
        if probe_workflow_status:
            probe_return["workflow_status"] = probe_workflow_status

        mocker.patch("harness_stata.graph.requirement_analysis", _stub_node({"empirical_spec": empirical_spec}, is_async=False))
        mocker.patch("harness_stata.graph.model_construction", _stub_node({"model_plan": model_plan}, is_async=False))
        mocker.patch("harness_stata.graph.data_probe", _stub_node(probe_return, is_async=True))
        mocker.patch("harness_stata.graph.data_download", _stub_node({"downloaded_files": {"files": []}}, is_async=True))

    return _patch


def test_happy_path_approved(tmp_path: Path, mocker: Any, patch_nodes: Callable[..., None]) -> None:
    merged_path = tmp_path / "merged.csv"
    merged_path.write_text("id,y,x\n", encoding="utf-8")

    patch_nodes()
    mocker.patch(
        "harness_stata.graph.data_cleaning",
        _stub_node(
            {
                "merged_dataset": {
                    "file_path": str(merged_path),
                    "row_count": 0,
                    "columns": ["id", "y", "x"],
                    "warnings": [],
                }
            },
            is_async=True,
        ),
    )
    mocker.patch(
        "harness_stata.graph.descriptive_stats",
        _stub_node(
            {"desc_stats_report": {"do_file_path": str(tmp_path / "d.do"), "log_file_path": str(tmp_path / "d.log"), "summary": "ok"}},
            is_async=True,
        ),
    )
    mocker.patch(
        "harness_stata.graph.regression",
        _stub_node(
            {
                "regression_result": {
                    "do_file_path": str(tmp_path / "r.do"),
                    "log_file_path": str(tmp_path / "r.log"),
                    "sign_check": {"consistent": True, "expected": "+", "actual_sign": "+"},
                    "summary": "DIGITAL coef is positive and significant at 1%",
                },
                "workflow_status": "success",
            },
            is_async=True,
        ),
    )

    result = CliRunner().invoke(app, _REQUIRED_ARGS, input="y\n\n")
    assert result.exit_code == 0, result.output
    assert "Workflow finished: status=success" in result.output
    assert "DIGITAL coef is positive" in result.output
    final_json = tmp_path / "final_state.json"
    assert final_json.exists()
    payload = json.loads(final_json.read_text(encoding="utf-8"))
    assert payload["workflow_status"] == "success"


def test_hard_failure_short_circuits(mocker: Any, patch_nodes: Callable[..., None]) -> None:
    patch_nodes(probe_overall="hard_failure", probe_workflow_status="failed_hard_contract")
    cleaning = _stub_node({"merged_dataset": {"file_path": "x", "row_count": 0, "columns": [], "warnings": []}}, is_async=True)
    mocker.patch("harness_stata.graph.data_cleaning", cleaning)
    mocker.patch("harness_stata.graph.descriptive_stats", _stub_node({"desc_stats_report": {}}, is_async=True))
    mocker.patch("harness_stata.graph.regression", _stub_node({"regression_result": {}, "workflow_status": "success"}, is_async=True))

    result = CliRunner().invoke(app, _REQUIRED_ARGS)
    assert result.exit_code == 1, result.output
    assert "failed_hard_contract" in result.output
    cleaning.assert_not_called()


def test_hitl_rejected(mocker: Any, patch_nodes: Callable[..., None]) -> None:
    patch_nodes()
    cleaning = _stub_node({"merged_dataset": {"file_path": "x", "row_count": 0, "columns": [], "warnings": []}}, is_async=True)
    mocker.patch("harness_stata.graph.data_cleaning", cleaning)
    mocker.patch("harness_stata.graph.descriptive_stats", _stub_node({"desc_stats_report": {}}, is_async=True))
    mocker.patch("harness_stata.graph.regression", _stub_node({"regression_result": {}, "workflow_status": "success"}, is_async=True))

    result = CliRunner().invoke(app, _REQUIRED_ARGS, input="n\n模型假设不合理\n")
    assert result.exit_code == 1, result.output
    assert "rejected" in result.output
    assert "模型假设不合理" in result.output
    cleaning.assert_not_called()


def test_missing_required_arg() -> None:
    args = [a for a in _REQUIRED_ARGS if a != "--x-variable" and a != "DIGITAL"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "Missing option" in result.output or "--x-variable" in result.output


def test_invalid_data_frequency() -> None:
    args = list(_REQUIRED_ARGS)
    idx = args.index("--data-frequency")
    args[idx + 1] = "weekly"
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "weekly" in result.output or "data-frequency" in result.output.lower()


def test_interrupt_payload_extraction_handles_missing_key() -> None:
    from harness_stata.cli import _interrupt_payload

    assert _interrupt_payload({}) is None
    assert _interrupt_payload({"foo": 1}) is None

    class _Obj:
        value = {"type": "hitl_plan_review", "plan": "hi"}

    assert _interrupt_payload({"__interrupt__": [_Obj()]}) == {"type": "hitl_plan_review", "plan": "hi"}


def test_final_state_not_dumped_without_merged_dataset(tmp_path: Path) -> None:
    from harness_stata.cli import _dump_final_state

    state = {"workflow_status": "failed_hard_contract"}
    assert _dump_final_state(state) is None
