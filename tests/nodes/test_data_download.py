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

from harness_stata.nodes.data_download import data_download
from harness_stata.state import WorkflowState


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
