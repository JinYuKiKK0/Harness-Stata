"""Unit tests for the descriptive_stats node (F21)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from pytest_mock import MockerFixture

from harness_stata.nodes.descriptive_stats import descriptive_stats
from harness_stata.state import EmpiricalSpec, MergedDataset, WorkflowState

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _empty_stata_tools() -> AsyncIterator[list[BaseTool]]:
    yield []


def _patch_env(
    mocker: MockerFixture,
    *,
    final_content: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Patch build_react_subgraph + get_stata_tools at the descriptive_stats module."""
    final_msg = AIMessage(content=final_content, tool_calls=tool_calls or [])
    fake_result = {"messages": [final_msg], "iteration_count": 1}
    fake_subgraph = MagicMock()
    fake_subgraph.ainvoke = AsyncMock(return_value=fake_result)
    mocker.patch(
        "harness_stata.nodes.descriptive_stats.build_react_subgraph",
        return_value=fake_subgraph,
    )
    mocker.patch(
        "harness_stata.nodes.descriptive_stats.get_stata_tools",
        side_effect=lambda: _empty_stata_tools(),
    )
    return fake_subgraph


def _make_session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "downloads" / "session1"
    session_dir.mkdir(parents=True)
    return session_dir


def _write_artifacts(session_dir: Path) -> tuple[Path, Path, Path]:
    """Create merged.csv + descriptive_stats.do + descriptive_stats.log under session_dir."""
    merged_csv = session_dir / "merged.csv"
    merged_csv.write_text("stkcd,year,roa,digital\n1,2020,0.1,0.5\n", encoding="utf-8")
    do_file = session_dir / "descriptive_stats.do"
    do_file.write_text("summarize roa digital\n", encoding="utf-8")
    log_file = session_dir / "descriptive_stats.log"
    log_file.write_text("(stata log stub)\n", encoding="utf-8")
    return merged_csv, do_file, log_file


def _make_merged(merged_csv: Path) -> MergedDataset:
    return {
        "file_path": str(merged_csv),
        "row_count": 1,
        "columns": ["stkcd", "year", "roa", "digital"],
        "warnings": [],
    }


def _base_state(spec: EmpiricalSpec, merged: MergedDataset) -> WorkflowState:
    return {"empirical_spec": spec, "merged_dataset": merged}


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(descriptive_stats(state))


def _final_payload(do_file: Path, log_file: Path) -> str:
    return json.dumps(
        {
            "do_file_path": str(do_file),
            "log_file_path": str(log_file),
            "summary": (
                "Mean ROA around 0.1; digital transformation index roughly uniform."
                " No missing values detected; logic checks passed."
            ),
        }
    )


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_descriptive_stats_success_returns_report(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    session_dir = _make_session_dir(tmp_path)
    merged_csv, do_file, log_file = _write_artifacts(session_dir)
    _patch_env(mocker, final_content=_final_payload(do_file, log_file))

    state = _base_state(make_empirical_spec(), _make_merged(merged_csv))
    out = _run(state)

    report = out["desc_stats_report"]
    assert report["do_file_path"] == str(do_file)
    assert report["log_file_path"] == str(log_file)
    assert "ROA" in report["summary"] or "digital" in report["summary"]
    # Non-terminal node: must not write workflow_status.
    assert "workflow_status" not in out


def test_descriptive_stats_success_preserves_merged_session_dir(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    session_dir = _make_session_dir(tmp_path)
    merged_csv, do_file, log_file = _write_artifacts(session_dir)
    _patch_env(mocker, final_content=_final_payload(do_file, log_file))

    state = _base_state(make_empirical_spec(), _make_merged(merged_csv))
    out = _run(state)

    report = out["desc_stats_report"]
    assert Path(report["do_file_path"]).parent == session_dir.resolve()
    assert Path(report["log_file_path"]).parent == session_dir.resolve()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_descriptive_stats_react_truncation_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Final AIMessage with non-empty tool_calls = max_iterations hit."""
    session_dir = _make_session_dir(tmp_path)
    merged_csv, _, _ = _write_artifacts(session_dir)
    _patch_env(
        mocker,
        final_content="",
        tool_calls=[
            {
                "name": "run_do",
                "args": {"script_path": "/tmp/x.do"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )

    state = _base_state(make_empirical_spec(), _make_merged(merged_csv))
    with pytest.raises(RuntimeError, match="max_iterations"):
        _run(state)


def test_descriptive_stats_log_file_missing_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """LLM reports a log path that was never actually written."""
    session_dir = _make_session_dir(tmp_path)
    merged_csv = session_dir / "merged.csv"
    merged_csv.write_text("stkcd,year,roa,digital\n1,2020,0.1,0.5\n", encoding="utf-8")
    do_file = session_dir / "descriptive_stats.do"
    do_file.write_text("summarize roa digital\n", encoding="utf-8")
    missing_log = session_dir / "descriptive_stats.log"  # deliberately NOT created
    _patch_env(mocker, final_content=_final_payload(do_file, missing_log))

    state = _base_state(make_empirical_spec(), _make_merged(merged_csv))
    with pytest.raises(RuntimeError, match="log_file_path"):
        _run(state)


def test_descriptive_stats_missing_merged_dataset_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="merged_dataset"):
        _run(state)
