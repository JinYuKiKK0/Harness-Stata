"""Unit tests for the data_cleaning node — pre-LLM validation paths only.

按项目测试约定:不 mock LLM/MCP。本文件仅覆盖 ``_validate`` 与 ``_register_sources``
在调用 LLM 之前抛出的确定性错误路径。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from harness_stata.nodes.data_cleaning import (
    _build_human_prompt,
    data_cleaning,
)
from harness_stata.state import DownloadedFile, EmpiricalSpec, WorkflowState


def _make_session_layout(tmp_path: Path) -> tuple[Path, Path]:
    session_dir = tmp_path / "downloads" / "session1"
    task_dir = session_dir / "CSMAR_FS_COMBAS"
    task_dir.mkdir(parents=True)
    return session_dir, task_dir


def _make_downloaded_file(path: Path, source_table: str = "FS_COMBAS") -> DownloadedFile:
    return {
        "path": str(path),
        "source_table": source_table,
        "key_fields": ["stkcd", "year"],
        "variable_names": ["ROA"],
    }


def _write_panel_csv(dir_: Path, name: str) -> Path:
    src = dir_ / name
    pd.DataFrame(
        [{"stkcd": 1, "year": 2020, "roa": 0.1}, {"stkcd": 2, "year": 2021, "roa": 0.2}]
    ).to_csv(src, index=False, encoding="utf-8")
    return src


def _base_state(empirical_spec: EmpiricalSpec, files: list[DownloadedFile]) -> WorkflowState:
    return {"empirical_spec": empirical_spec, "downloaded_files": {"files": files}}


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_cleaning(state))


def test_data_cleaning_missing_downloaded_files_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="downloaded_files"):
        _run(state)


def test_data_cleaning_illegal_source_table_raises(
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """source_table 含非标识符字符 -> 在 _register_sources 阶段 raise,LLM 永不触发。"""
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv")
    file_ = _make_downloaded_file(src, source_table="FS; DROP TABLE x")
    state = _base_state(make_empirical_spec(), [file_])
    with pytest.raises(RuntimeError, match="illegal source_table"):
        _run(state)


def test_data_cleaning_xlsx_source_raises_not_implemented(
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """xlsx 留给后续阶段,当前必须直接 NotImplementedError。"""
    _, task_dir = _make_session_layout(tmp_path)
    xlsx = task_dir / "data.xlsx"
    xlsx.write_bytes(b"not-a-real-xlsx")
    state = _base_state(make_empirical_spec(), [_make_downloaded_file(xlsx)])
    with pytest.raises(NotImplementedError, match="xlsx"):
        _run(state)


def test_data_cleaning_prompt_includes_variable_mappings(
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    _, task_dir = _make_session_layout(tmp_path)
    src = _write_panel_csv(task_dir, "data.csv")
    file_ = _make_downloaded_file(src)
    file_["variable_mappings"] = [
        {
            "variable_name": "ROA",
            "source_fields": ["roa"],
            "match_kind": "direct_field",
            "evidence": "字段即总资产收益率",
        }
    ]

    prompt = _build_human_prompt(make_empirical_spec(), [file_], tmp_path / "merged.csv")

    assert "variable_mappings" in prompt
    assert '"variable_name": "ROA"' in prompt
    assert '"source_fields": [' in prompt
    assert "variable mapping contract" in prompt
