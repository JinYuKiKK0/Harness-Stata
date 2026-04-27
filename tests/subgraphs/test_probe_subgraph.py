"""Topology-level tests for the probe subgraph factory.

These tests cover only the deterministic boundaries: input validation on
:func:`build_probe_subgraph` and the empty-variable contract that the
subgraph must always emit shaped probe_report / download_manifest slices.

LLM-driven branches (Planning Agent / Verification / Fallback ReAct) are
exercised end-to-end with the real CSMAR + LLM stack outside this file
(manually via langgraph dev). Mocking ``create_agent`` to validate node
unpacking would only verify the test harness, not the system — see
CLAUDE.md for the project's testing conventions.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import BaseTool, tool

from harness_stata.state import EmpiricalSpec
from harness_stata.subgraphs.probe_subgraph import build_probe_subgraph


@tool
def csmar_list_tables(database_name: str) -> str:
    """Test double for the list_tables tool."""
    return f"tables:{database_name}"


def _stub_tool(name: str) -> BaseTool:
    stub: Any = MagicMock(spec=BaseTool)
    stub.name = name
    stub.ainvoke = AsyncMock(side_effect=AssertionError(f"{name} unexpectedly called"))
    return stub


def _empty_spec() -> EmpiricalSpec:
    return EmpiricalSpec(
        topic="t",
        variables=[],
        sample_scope="s",
        time_range_start="2010",
        time_range_end="2020",
        data_frequency="yearly",
        analysis_granularity="firm-year",
    )


def _build_default(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "planning_tools": [csmar_list_tables],
        "fallback_tools": [csmar_list_tables],
        "bulk_schema_tool": _stub_tool("csmar_bulk_schema"),
        "probe_tool": _stub_tool("csmar_probe_query"),
        "planning_prompt": "p",
        "verification_prompt": "v",
        "fallback_prompt": "f",
        "planning_agent_max_calls": 4,
        "fallback_react_max_calls": 2,
        "substitute_max_rounds": 1,
    }
    kwargs.update(overrides)
    return build_probe_subgraph(**kwargs)


class TestInputValidation:
    def test_empty_planning_tools_rejected(self) -> None:
        with pytest.raises(ValueError, match="planning_tools must not be empty"):
            _build_default(planning_tools=[])

    def test_empty_fallback_tools_rejected(self) -> None:
        with pytest.raises(ValueError, match="fallback_tools must not be empty"):
            _build_default(fallback_tools=[])

    def test_non_positive_planning_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="planning_agent_max_calls must be >= 1"):
            _build_default(planning_agent_max_calls=0)

    def test_non_positive_fallback_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="fallback_react_max_calls must be >= 1"):
            _build_default(fallback_react_max_calls=0)

    def test_negative_substitute_rounds_rejected(self) -> None:
        with pytest.raises(ValueError, match="substitute_max_rounds must be >= 0"):
            _build_default(substitute_max_rounds=-1)


class TestEmptyVariablesContract:
    def test_empty_variables_emits_shaped_report_and_manifest(self) -> None:
        graph = _build_default()
        result = asyncio.run(graph.ainvoke({"empirical_spec": _empty_spec()}))
        assert result["probe_report"] == {
            "variable_results": [],
            "overall_status": "success",
            "failure_reason": None,
        }
        assert result["download_manifest"] == {"items": []}
