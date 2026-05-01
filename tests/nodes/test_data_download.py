"""Unit tests for the data_download node — pre-MCP validation paths only.

按项目测试约定:不 mock LLM/MCP。日期/字段过滤的检查目前在 ``async with
get_csmar_tools()`` 之内调用,无法不 mock MCP 测;留待生产代码把这些检查
提到 ``_validate`` 后再补纯测试。本文件只覆盖空 manifest 在 ``_validate``
阶段直接 raise 的纯路径。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from harness_stata.nodes.data_download import (
    _build_probe_payload,
    _make_downloaded_files,
    data_download,
)
from harness_stata.state import DownloadTask, WorkflowState


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_download(state))


def test_data_download_empty_manifest_raises() -> None:
    state: WorkflowState = {"download_manifest": {"items": []}}
    with pytest.raises(ValueError, match="non-empty"):
        _run(state)


def test_data_download_missing_manifest_raises() -> None:
    state: WorkflowState = {}
    with pytest.raises(ValueError, match="download_manifest"):
        _run(state)


def test_build_probe_payload_uses_raw_variable_fields() -> None:
    task = DownloadTask(
        database="CSMAR",
        table="T1",
        key_fields=["Stkcd", "AccYear"],
        variable_fields=["EstablishDate", "CashRecoveryRate"],
        variable_names=["Age", "CashFlow"],
        filters={"start_date": "2010-01-01", "end_date": "2020-12-31"},
    )

    payload = _build_probe_payload(task)

    assert payload["columns"] == ["Stkcd", "AccYear", "EstablishDate", "CashRecoveryRate"]


def test_make_downloaded_files_carries_variable_mappings() -> None:
    task = DownloadTask(
        database="CSMAR",
        table="T1",
        key_fields=["Stkcd", "AccYear"],
        variable_fields=["EstablishDate"],
        variable_names=["Age"],
        variable_mappings=[
            {
                "variable_name": "Age",
                "source_fields": ["EstablishDate"],
                "match_kind": "derived",
                "transform": {"op": "firm_age", "date_field": "EstablishDate"},
                "evidence": "企业年龄可由成立日期构造",
            }
        ],
        filters={"start_date": "2010-01-01", "end_date": "2020-12-31"},
    )

    files = _make_downloaded_files(task, ["D:/tmp/T1/data.csv"])

    assert files[0]["variable_mappings"] == task["variable_mappings"]
