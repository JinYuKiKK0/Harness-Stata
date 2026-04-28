"""Unit tests for the descriptive_stats node — pre-LLM validation paths only.

按项目测试约定:不 mock LLM/MCP。本文件只覆盖 ``_validate`` 阶段的确定性错误。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from harness_stata.nodes.descriptive_stats import descriptive_stats
from harness_stata.state import EmpiricalSpec, WorkflowState


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(descriptive_stats(state))


def test_descriptive_stats_missing_merged_dataset_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="merged_dataset"):
        _run(state)
