"""Unit tests for the data_cleaning node — pre-LLM validation paths only.

按项目测试约定:不 mock LLM/MCP。本文件仅覆盖 ``_validate`` 与 ``_register_sources``
在调用 LLM 之前抛出的确定性错误路径。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import pytest

from harness_stata.nodes.data_cleaning import (
    _build_human_prompt,
    _derive_output_path,
    _register_sources,
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


def test_derive_output_path_deep_layout() -> None:
    """生产形态 ``<root>/<utc_ts>/<db_table>/<file>.csv``——公共父 = ``<root>/<utc_ts>``。"""
    files: list[DownloadedFile] = [
        _make_downloaded_file(Path("/r/2026/CSMAR_FS/a.csv"), source_table="FS"),
        _make_downloaded_file(Path("/r/2026/CSMAR_FI/b.csv"), source_table="FI"),
    ]
    assert _derive_output_path(files) == Path("/r/2026/merged.csv")


def test_derive_output_path_flat_layout() -> None:
    """fixture 扁平形态 ``<root>/<file>.csv``——公共父 = ``<root>`` 自身。"""
    files: list[DownloadedFile] = [
        _make_downloaded_file(Path("/fx/03/x.csv"), source_table="X"),
        _make_downloaded_file(Path("/fx/03/y.csv"), source_table="Y"),
    ]
    assert _derive_output_path(files) == Path("/fx/03/merged.csv")


def test_derive_output_path_three_level_layout() -> None:
    """fixture 三层形态 ``<root>/<table_dir>/<dl_dir>/<file>.csv``——公共父 = ``<root>``。"""
    files: list[DownloadedFile] = [
        _make_downloaded_file(Path("/fx/01/T5/dl_a/a.csv"), source_table="T5"),
        _make_downloaded_file(Path("/fx/01/T1/dl_b/b.csv"), source_table="T1"),
    ]
    assert _derive_output_path(files) == Path("/fx/01/merged.csv")


def test_register_sources_recovers_double_dtype_under_excel_pollution(tmp_path: Path) -> None:
    """脏 CSV(含 ``#DIV/0!`` 与空 cell)注册后,数值列必须被推断为 ``DOUBLE``。

    防回归:DuckDB ``read_csv(na_values=...)`` 会**覆盖**默认空串语义,
    若 ``_NULL_TOKENS`` 漏 ``""``,任何含空 cell 的列都会落回 VARCHAR。
    """
    csv_path = tmp_path / "dirty.csv"
    csv_path.write_text(
        "stkcd,year,ratio,asset\n"
        "1,2020,0.5,1000\n"
        "2,2020,#DIV/0!,2000\n"
        "3,2020,0.7,\n"
        "4,2020,#N/A,3000\n"
        # Excel 365 dynamic-array errors must also be recognized
        "5,2020,#SPILL!,#CALC!\n"
        "6,2020,#FIELD!,4000\n",
        encoding="utf-8",
    )
    file_ = _make_downloaded_file(csv_path, source_table="DIRTY")
    conn = duckdb.connect(":memory:")
    try:
        _register_sources(conn, [file_])
        rows = conn.execute("DESCRIBE src_DIRTY").fetchall()
        schema = {row[0]: row[1] for row in rows}
    finally:
        conn.close()
    assert schema["ratio"] == "DOUBLE", f"脏值列应回归 DOUBLE,实际 {schema['ratio']}"
    assert schema["asset"] == "BIGINT" or schema["asset"] == "DOUBLE", (
        f"空 cell 列应被推断为数值,实际 {schema['asset']}"
    )


def test_data_cleaning_prompt_includes_variable_mappings_and_schema_preview(
    tmp_path: Path,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    """HumanMessage 必须包含 variable_mappings、视图 schema 与样本预览,
    且末尾有 ``<reminder>`` 块复述终止条件,output_path 不应被渲染。"""
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

    conn = duckdb.connect(":memory:")
    try:
        view_metas = _register_sources(conn, [file_])
        prompt = _build_human_prompt(make_empirical_spec(), [file_], view_metas)
    finally:
        conn.close()

    assert "variable_mappings" in prompt
    assert '"variable_name": "ROA"' in prompt
    assert '"source_fields": [' in prompt
    # schema + preview 必须内嵌,省去 LLM 探查
    assert "schema:" in prompt
    assert "`stkcd`" in prompt
    assert "preview (first 3 rows):" in prompt
    # 终止条件 reminder 块
    assert "<reminder>" in prompt
    assert "GROUP BY" in prompt
    # output_path 不应被渲染(避免诱导 LLM 自行 COPY)
    assert "output_path" not in prompt
    assert "merged.csv" not in prompt
