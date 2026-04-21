"""Unit tests for the descriptive_stats node (F21) — error paths."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.tools import BaseTool
from pytest_mock import MockerFixture

from harness_stata.nodes.descriptive_stats import _DescStatsOutput, descriptive_stats
from harness_stata.state import EmpiricalSpec, MergedDataset, WorkflowState


@asynccontextmanager
async def _empty_stata_tools() -> AsyncIterator[list[BaseTool]]:
    yield []


def _patch_env(
    mocker: MockerFixture,
    *,
    payload: _DescStatsOutput | None = None,
    raise_truncation: bool = False,
) -> None:
    if raise_truncation:
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(side_effect=ModelCallLimitExceededError(1, 1, None, 1))
    else:
        assert payload is not None
        fake_agent = MagicMock()
        fake_agent.ainvoke = AsyncMock(
            return_value={"messages": [], "structured_response": payload}
        )
    mocker.patch("harness_stata.nodes.descriptive_stats.create_agent", return_value=fake_agent)
    mocker.patch(
        "harness_stata.nodes.descriptive_stats.get_stata_tools",
        side_effect=lambda: _empty_stata_tools(),
    )


def _make_session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "downloads" / "session1"
    session_dir.mkdir(parents=True)
    return session_dir


def _make_merged(merged_csv: Path) -> MergedDataset:
    return {
        "file_path": str(merged_csv),
        "row_count": 1,
        "columns": ["stkcd", "year", "roa", "digital"],
        "warnings": [],
    }


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(descriptive_stats(state))


def test_descriptive_stats_react_truncation_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """ModelCallLimitExceededError is translated to RuntimeError."""
    session_dir = _make_session_dir(tmp_path)
    merged_csv = session_dir / "merged.csv"
    merged_csv.write_text("stkcd,year,roa,digital\n1,2020,0.1,0.5\n", encoding="utf-8")
    _patch_env(mocker, raise_truncation=True)

    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "merged_dataset": _make_merged(merged_csv),
    }
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
    _patch_env(
        mocker,
        payload=_DescStatsOutput(
            do_file_path=str(do_file),
            log_file_path=str(missing_log),
            summary="stub",
        ),
    )

    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "merged_dataset": _make_merged(merged_csv),
    }
    with pytest.raises(RuntimeError, match="log_file_path"):
        _run(state)


def test_descriptive_stats_missing_merged_dataset_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="merged_dataset"):
        _run(state)
