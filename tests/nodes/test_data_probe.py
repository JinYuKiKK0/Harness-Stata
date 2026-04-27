"""Unit tests for the data_probe node (F25) — input validation only.

Validation fires before the async CSMAR context manager is entered, so these
tests require no mocking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from harness_stata.nodes.data_probe import (
    ALLOWED_REACT_TOOLS,
    FALLBACK_TOOLS,
    PLANNING_TOOLS,
    data_probe,
)
from harness_stata.state import EmpiricalSpec, ModelPlan, WorkflowState


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_probe(state))


def test_data_probe_missing_empirical_spec_raises(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {"model_plan": make_model_plan()}
    with pytest.raises(ValueError, match="empirical_spec"):
        _run(state)


def test_data_probe_missing_model_plan_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="model_plan"):
        _run(state)


def test_data_probe_empty_variables_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    spec = make_empirical_spec(variables=[])
    state: WorkflowState = {"empirical_spec": spec, "model_plan": make_model_plan()}
    with pytest.raises(ValueError, match="non-empty"):
        _run(state)


def test_data_probe_react_tool_whitelist_pinned() -> None:
    """字段发现阶段允许暴露给 Agent 的工具集是显式白名单。

    Planning Agent 只能用 list_tables(候选表必须出自 list_tables 返回);
    Fallback 单变量 ReAct 可以用 list_tables + bulk_schema + get_table_schema。
    csmar_search_field / csmar_list_databases / probe_query /
    materialize_query / refresh_cache 永远不应出现在任何 Agent 的工具集里。
    """
    assert PLANNING_TOOLS == frozenset({"csmar_list_tables"})
    assert FALLBACK_TOOLS == frozenset(
        {
            "csmar_list_tables",
            "csmar_bulk_schema",
            "csmar_get_table_schema",
        }
    )
    # ALLOWED_REACT_TOOLS 保留为 FALLBACK_TOOLS 的别名,保证旧调用方契约
    assert ALLOWED_REACT_TOOLS == FALLBACK_TOOLS

    forbidden = {
        "csmar_search_field",
        "csmar_list_databases",
        "csmar_probe_query",
        "csmar_materialize_query",
        "csmar_refresh_cache",
    }
    assert PLANNING_TOOLS.isdisjoint(forbidden)
    assert FALLBACK_TOOLS.isdisjoint(forbidden)
