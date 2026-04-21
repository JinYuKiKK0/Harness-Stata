"""Unit tests for the data_cleaning node (F20, DuckDB SQL-first)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.tools import BaseTool
from pytest_mock import MockerFixture

from harness_stata.nodes.data_cleaning import _CleaningOutput, data_cleaning
from harness_stata.state import DownloadedFile, EmpiricalSpec, WorkflowState

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _patch_agent(
    mocker: MockerFixture,
    *,
    sqls: Sequence[str] = (),
    final_view: str = "merged",
    primary_key: Sequence[str] = ("stkcd", "year"),
    raise_truncation: bool = False,
) -> None:
    """Patch ``create_agent`` in the data_cleaning module.

    The fake agent executes ``sqls`` via the real ``run_sql`` tool (so
    ``_check_final_view_exists`` and intermediate-artifact dumps see a real
    DuckDB connection), then returns a structured ``_CleaningOutput`` via
    ``result["structured_response"]``.
    """
    payload = _CleaningOutput(final_view=final_view, primary_key=list(primary_key))

    def _fake_create(
        *,
        model: Any,
        tools: Sequence[BaseTool],
        system_prompt: str,
        middleware: Any,
        response_format: Any,
    ) -> MagicMock:
        del model, system_prompt, middleware, response_format
        assert len(tools) == 1, "data_cleaning binds exactly one run_sql tool"
        sql_tool = tools[0]

        async def _ainvoke(_state: dict[str, Any]) -> dict[str, Any]:
            if raise_truncation:
                raise ModelCallLimitExceededError(1, 1, None, 1)
            for q in sqls:
                await sql_tool.ainvoke({"query": q})
            return {"messages": [], "structured_response": payload}

        fake = MagicMock()
        fake.ainvoke = AsyncMock(side_effect=_ainvoke)
        return fake

    mocker.patch(
        "harness_stata.nodes.data_cleaning.create_agent",
        side_effect=_fake_create,
    )


def _make_session_layout(tmp_path: Path) -> tuple[Path, Path]:
    """Create F18-style ``downloads/<session>/<db_table>/`` layout."""
    session_dir = tmp_path / "downloads" / "session1"
    task_dir = session_dir / "CSMAR_FS_COMBAS"
    task_dir.mkdir(parents=True)
    return session_dir, task_dir


def _make_downloaded_file(
    path: Path, source_table: str = "FS_COMBAS"
) -> DownloadedFile:
    return {
        "path": str(path),
        "source_table": source_table,
        "key_fields": ["stkcd", "year"],
        "variable_names": ["ROA"],
    }


def _write_panel_csv(
    dir_: Path,
    name: str,
    rows: list[dict[str, Any]],
) -> Path:
    src = dir_ / name
    pd.DataFrame(rows).to_csv(src, index=False, encoding="utf-8")
    return src


def _base_state(empirical_spec: EmpiricalSpec, files: list[DownloadedFile]) -> WorkflowState:
    return {
        "empirical_spec": empirical_spec,
        "downloaded_files": {"files": files},
    }


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_cleaning(state))


def _default_rows() -> list[dict[str, Any]]:
    return [
        {"stkcd": 1, "year": 2020, "roa": 0.1, "digital": 0.5, "size": 10.0},
        {"stkcd": 2, "year": 2021, "roa": 0.2, "digital": 0.6, "size": 11.0},
        {"stkcd": 3, "year": 2020, "roa": 0.3, "digital": 0.7, "size": 12.0},
        {"stkcd": 4, "year": 2021, "roa": 0.4, "digital": 0.8, "size": 13.0},
    ]


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_data_cleaning_success_single_table(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    session_dir, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv", _default_rows())
    _patch_agent(
        mocker,
        sqls=["CREATE VIEW merged AS SELECT * FROM src_FS_COMBAS"],
        final_view="merged",
        primary_key=("stkcd", "year"),
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    out = _run(state)

    merged = out["merged_dataset"]
    assert merged["file_path"] == str(session_dir / "merged.csv")
    assert merged["row_count"] == 4
    assert set(merged["columns"]) == {"stkcd", "year", "roa", "digital", "size"}
    assert merged["warnings"] == []
    # Merged file actually exists on disk
    assert Path(merged["file_path"]).exists()


def test_data_cleaning_success_multi_source_files(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Output path derives from first DownloadedFile regardless of extra sources."""
    session_dir, task_dir = _make_session_layout(tmp_path)
    src1 = _write_panel_csv(
        task_dir,
        "fs_combas.csv",
        [
            {"stkcd": 1, "year": 2020, "roa": 0.1, "size": 10.0},
            {"stkcd": 2, "year": 2021, "roa": 0.2, "size": 11.0},
            {"stkcd": 3, "year": 2020, "roa": 0.3, "size": 12.0},
        ],
    )
    task_dir2 = session_dir / "CSMAR_DIG_TRANSFORM"
    task_dir2.mkdir()
    src2 = _write_panel_csv(
        task_dir2,
        "dig.csv",
        [
            {"stkcd": 1, "year": 2020, "digital": 0.5},
            {"stkcd": 2, "year": 2021, "digital": 0.6},
            {"stkcd": 3, "year": 2020, "digital": 0.7},
        ],
    )
    _patch_agent(
        mocker,
        sqls=[
            (
                "CREATE VIEW merged AS "
                "SELECT a.stkcd, a.year, a.roa, a.size, b.digital "
                "FROM src_FS_COMBAS a JOIN src_DIG_TRANSFORM b USING (stkcd, year)"
            )
        ],
    )

    files = [
        _make_downloaded_file(src1, "FS_COMBAS"),
        _make_downloaded_file(src2, "DIG_TRANSFORM"),
    ]
    state = _base_state(make_empirical_spec(), files)
    out = _run(state)

    assert out["merged_dataset"]["row_count"] == 3
    assert out["merged_dataset"]["warnings"] == []


def test_data_cleaning_dumps_intermediate_views_including_failures(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """All non-src_ relations dump to _stage/, including views kept around from
    earlier failed attempts."""
    session_dir, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv", _default_rows())
    _patch_agent(
        mocker,
        sqls=[
            # a "tmp" view the LLM tried first and didn't drop
            "CREATE VIEW tmp_failed_attempt AS SELECT stkcd FROM src_FS_COMBAS",
            "CREATE VIEW clean_combas AS SELECT * FROM src_FS_COMBAS",
            "CREATE VIEW merged AS SELECT * FROM clean_combas",
        ],
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    _run(state)

    stage_dir = session_dir / "_stage"
    dumped = {p.name for p in stage_dir.iterdir()}
    # All three non-src relations must be dumped (including the failed attempt)
    assert dumped == {"tmp_failed_attempt.csv", "clean_combas.csv", "merged.csv"}
    # No src_ views should leak into _stage
    assert not any(name.startswith("src_") for name in dumped)


def test_data_cleaning_coverage_and_missing_column_warn(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Soft failures surface in warnings and do not raise."""
    session_dir, task_dir = _make_session_layout(tmp_path)
    # roa: 50% non-null -> coverage warning; size column absent -> missing warning.
    src = _write_panel_csv(
        task_dir,
        "data.csv",
        [
            {"stkcd": 1, "year": 2020, "roa": 0.1, "digital": 0.5},
            {"stkcd": 2, "year": 2021, "roa": 0.2, "digital": 0.6},
            {"stkcd": 3, "year": 2020, "roa": None, "digital": 0.7},
            {"stkcd": 4, "year": 2021, "roa": None, "digital": 0.8},
        ],
    )
    _patch_agent(
        mocker,
        sqls=["CREATE VIEW merged AS SELECT * FROM src_FS_COMBAS"],
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    out = _run(state)

    warnings = out["merged_dataset"]["warnings"]
    assert len(warnings) == 2
    assert any("'ROA'" in w and "coverage" in w for w in warnings)
    assert any("'SIZE'" in w and "not found" in w for w in warnings)
    assert Path(session_dir / "merged.csv").exists()


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_data_cleaning_duplicate_primary_key_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(
        task_dir,
        "data.csv",
        [
            {"stkcd": 1, "year": 2020, "roa": 0.1},
            {"stkcd": 1, "year": 2020, "roa": 0.2},  # dup primary key
            {"stkcd": 2, "year": 2021, "roa": 0.3},
        ],
    )
    _patch_agent(
        mocker,
        sqls=["CREATE VIEW merged AS SELECT * FROM src_FS_COMBAS"],
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    with pytest.raises(RuntimeError, match="duplicate"):
        _run(state)


def test_data_cleaning_final_view_missing_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """LLM declares a final_view that was never created -> RuntimeError."""
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv", _default_rows())
    _patch_agent(
        mocker,
        sqls=["CREATE VIEW clean_combas AS SELECT * FROM src_FS_COMBAS"],
        final_view="nonexistent_view",
    )

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    with pytest.raises(RuntimeError, match="final_view.*not found"):
        _run(state)


def test_data_cleaning_final_view_illegal_identifier_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """Non-identifier final_view name -> RuntimeError (defense against injection)."""
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv", _default_rows())
    _patch_agent(
        mocker,
        sqls=["CREATE VIEW merged AS SELECT * FROM src_FS_COMBAS"],
        final_view="merged; DROP TABLE x",
    )
    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    with pytest.raises(RuntimeError, match="legal SQL identifier"):
        _run(state)


def test_data_cleaning_react_truncation_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """ModelCallLimitExceededError from create_agent is translated to RuntimeError."""
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv", _default_rows())
    _patch_agent(mocker, raise_truncation=True)

    state = _base_state(make_empirical_spec(), [_make_downloaded_file(src)])
    with pytest.raises(RuntimeError, match="max_iterations"):
        _run(state)


def test_data_cleaning_illegal_source_table_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """source_table with non-identifier characters -> RuntimeError before LLM runs."""
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv", _default_rows())
    # No need to patch agent: failure happens during _register_sources.
    file_ = _make_downloaded_file(src, source_table="FS; DROP TABLE x")
    state = _base_state(make_empirical_spec(), [file_])
    with pytest.raises(RuntimeError, match="illegal source_table"):
        _run(state)


def test_data_cleaning_xlsx_source_raises_not_implemented(
    mocker: MockerFixture,
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """xlsx is reserved for a future phase and must raise NotImplementedError."""
    _, task_dir = _make_session_layout(tmp_path)
    xlsx = task_dir / "data.xlsx"
    xlsx.write_bytes(b"not-a-real-xlsx")
    state = _base_state(make_empirical_spec(), [_make_downloaded_file(xlsx)])
    with pytest.raises(NotImplementedError, match="xlsx"):
        _run(state)


def test_data_cleaning_missing_downloaded_files_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="downloaded_files"):
        _run(state)
