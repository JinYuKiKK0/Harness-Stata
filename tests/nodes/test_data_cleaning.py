"""Unit tests for the data_cleaning node (F20)."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from langchain_core.messages import AIMessage
from pytest_mock import MockerFixture

from harness_stata.nodes.data_cleaning import data_cleaning
from harness_stata.state import DownloadedFile, EmpiricalSpec, WorkflowState


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _patch_subgraph(
    mocker: MockerFixture,
    *,
    final_content: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Patch build_react_subgraph to return a fake subgraph with canned ainvoke result."""
    final_msg = AIMessage(content=final_content, tool_calls=tool_calls or [])
    fake_result = {"messages": [final_msg], "iteration_count": 1}
    fake_subgraph = MagicMock()
    fake_subgraph.ainvoke = AsyncMock(return_value=fake_result)
    mocker.patch(
        "harness_stata.nodes.data_cleaning.build_react_subgraph",
        return_value=fake_subgraph,
    )
    return fake_subgraph


def _make_session_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Create F18-style ``downloads/<session>/<db_table>/`` layout."""
    session_dir = tmp_path / "downloads" / "session1"
    task_dir = session_dir / "CSMAR_FS_COMBAS"
    task_dir.mkdir(parents=True)
    return session_dir, task_dir


def _make_downloaded_file(path: Path) -> DownloadedFile:
    return {
        "path": str(path),
        "source_table": "FS_COMBAS",
        "key_fields": ["stkcd", "year"],
        "variable_names": ["ROA"],
    }


def _write_source_csv(task_dir: Path, name: str = "data.csv") -> Path:
    src = task_dir / name
    src.write_text("stkcd,year,col\n1,2020,0.1\n", encoding="utf-8")
    return src


def _base_state(empirical_spec: EmpiricalSpec, files: list[DownloadedFile]) -> WorkflowState:
    return {
        "empirical_spec": empirical_spec,
        "downloaded_files": {"files": files},
    }


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_cleaning(state))


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_data_cleaning_success_single_table(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    session_dir, task_dir = _make_session_layout(tmp_path)
    src = _write_source_csv(task_dir)
    output_path = session_dir / "merged.csv"
    pd.DataFrame(
        {
            "stkcd": [1, 2, 3, 4],
            "year": [2020, 2021, 2020, 2021],
            "roa": [0.1, 0.2, 0.3, 0.4],
            "digital": [0.5, 0.6, 0.7, 0.8],
            "size": [10.0, 11.0, 12.0, 13.0],
        }
    ).to_csv(output_path, index=False)
    _patch_subgraph(
        mocker,
        final_content=json.dumps({"file_path": str(output_path), "primary_key": ["stkcd", "year"]}),
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    out = _run(state)

    merged = out["merged_dataset"]
    assert merged["file_path"] == str(output_path)
    assert merged["row_count"] == 4
    assert set(merged["columns"]) == {"stkcd", "year", "roa", "digital", "size"}
    assert merged["warnings"] == []


def test_data_cleaning_success_multi_source_files(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Output path derives from first DownloadedFile regardless of extra sources."""
    session_dir, task_dir = _make_session_layout(tmp_path)
    src1 = _write_source_csv(task_dir, "fs_combas.csv")
    task_dir2 = session_dir / "CSMAR_DIG_TRANSFORM"
    task_dir2.mkdir()
    src2 = task_dir2 / "dig.csv"
    src2.write_text("stkcd,year,digital\n1,2020,0.5\n", encoding="utf-8")

    output_path = session_dir / "merged.csv"
    pd.DataFrame(
        {
            "stkcd": [1, 2, 3],
            "year": [2020, 2021, 2020],
            "roa": [0.1, 0.2, 0.3],
            "digital": [0.5, 0.6, 0.7],
            "size": [10.0, 11.0, 12.0],
        }
    ).to_csv(output_path, index=False)
    _patch_subgraph(
        mocker,
        final_content=json.dumps({"file_path": str(output_path), "primary_key": ["stkcd", "year"]}),
    )

    files = [_make_downloaded_file(src1), _make_downloaded_file(src2)]
    state = _base_state(make_empirical_spec(), files)
    out = _run(state)

    assert out["merged_dataset"]["row_count"] == 3
    assert out["merged_dataset"]["warnings"] == []


def test_data_cleaning_coverage_and_missing_column_warn(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Soft failures surface in warnings and do not raise."""
    session_dir, task_dir = _make_session_layout(tmp_path)
    src = _write_source_csv(task_dir)
    output_path = session_dir / "merged.csv"
    # roa: 50% non-null -> coverage warning; size column absent -> missing warning.
    pd.DataFrame(
        {
            "stkcd": [1, 2, 3, 4],
            "year": [2020, 2021, 2020, 2021],
            "roa": [0.1, 0.2, math.nan, math.nan],
            "digital": [0.5, 0.6, 0.7, 0.8],
        }
    ).to_csv(output_path, index=False)
    _patch_subgraph(
        mocker,
        final_content=json.dumps({"file_path": str(output_path), "primary_key": ["stkcd", "year"]}),
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    out = _run(state)

    warnings = out["merged_dataset"]["warnings"]
    assert len(warnings) == 2
    assert any("'ROA'" in w and "coverage" in w for w in warnings)
    assert any("'SIZE'" in w and "not found" in w for w in warnings)


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_data_cleaning_duplicate_primary_key_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    session_dir, task_dir = _make_session_layout(tmp_path)
    src = _write_source_csv(task_dir)
    output_path = session_dir / "merged.csv"
    pd.DataFrame(
        {
            "stkcd": [1, 1, 2, 2],
            "year": [2020, 2020, 2021, 2021],
            "roa": [0.1, 0.2, 0.3, 0.4],
        }
    ).to_csv(output_path, index=False)
    _patch_subgraph(
        mocker,
        final_content=json.dumps({"file_path": str(output_path), "primary_key": ["stkcd", "year"]}),
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    with pytest.raises(RuntimeError, match="duplicate"):
        _run(state)


def test_data_cleaning_react_truncation_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Final AIMessage with non-empty tool_calls = max_iterations hit."""
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_source_csv(task_dir)
    _patch_subgraph(
        mocker,
        final_content="",
        tool_calls=[
            {
                "name": "run_python",
                "args": {"code": "print('not done')"},
                "id": "call_1",
                "type": "tool_call",
            }
        ],
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    with pytest.raises(RuntimeError, match="max_iterations"):
        _run(state)


def test_data_cleaning_missing_downloaded_files_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="downloaded_files"):
        _run(state)
