"""Unit tests for the probe_subgraph factory — routing and state-machine logic."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import tool

from harness_stata.state import EmpiricalSpec, VariableDefinition
from harness_stata.subgraphs.probe_subgraph import (
    ProbeState,
    _VariableProbeFindingModel,
    build_probe_subgraph,
)

# ---------------------------------------------------------------------------
# Fake tool (no side effects)
# ---------------------------------------------------------------------------


@tool
def csmar_probe(table: str) -> str:
    """Return a canned probe result (test double)."""
    return f"probe:{table}"


# ---------------------------------------------------------------------------
# Mock wiring helpers
# ---------------------------------------------------------------------------


def _wire_agent(
    mocker: Any,
    findings: list[_VariableProbeFindingModel],
) -> MagicMock:
    """Patch ``create_agent``; each call consumes the next finding from the list."""

    def _make_agent(finding: _VariableProbeFindingModel) -> MagicMock:
        agent = MagicMock()

        async def _ainvoke(state: dict[str, Any]) -> dict[str, Any]:
            return {"messages": [], "structured_response": finding}

        agent.ainvoke = AsyncMock(side_effect=_ainvoke)
        return agent

    return mocker.patch(
        "harness_stata.subgraphs.probe_subgraph.create_agent",
        side_effect=[_make_agent(f) for f in findings],
    )


def _var(name: str, role: str = "independent", contract: str = "hard") -> VariableDefinition:
    return VariableDefinition(
        name=name,
        description=f"desc of {name}",
        contract_type=contract,  # type: ignore[typeddict-item]
        role=role,  # type: ignore[typeddict-item]
    )


def _spec(variables: list[VariableDefinition]) -> EmpiricalSpec:
    return EmpiricalSpec(
        topic="t",
        variables=variables,
        sample_scope="s",
        time_range_start="2010",
        time_range_end="2020",
        data_frequency="yearly",
        analysis_granularity="firm-year",
    )


def _found(
    *,
    database: str = "CSMAR",
    table: str = "TRD",
    field: str = "ROA",
    record_count: int = 1000,
    key_fields: list[str] | None = None,
    filters: dict[str, str] | None = None,
) -> _VariableProbeFindingModel:
    return _VariableProbeFindingModel(
        status="found",
        database=database,
        table=table,
        field=field,
        record_count=record_count,
        key_fields=key_fields if key_fields is not None else ["stkcd", "year"],
        filters=filters if filters is not None else {"year": "2010-2020"},
    )


def _not_found(
    *,
    substitute_name: str | None = None,
    substitute_description: str | None = None,
    substitute_reason: str | None = None,
) -> _VariableProbeFindingModel:
    return _VariableProbeFindingModel(
        status="not_found",
        candidate_substitute_name=substitute_name,
        candidate_substitute_description=substitute_description,
        candidate_substitute_reason=substitute_reason,
    )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_empty_tools_rejected(self) -> None:
        with pytest.raises(ValueError, match="tools must not be empty"):
            build_probe_subgraph(tools=[], prompt="p", per_variable_max_calls=1)

    def test_non_positive_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="per_variable_max_calls must be >= 1"):
            build_probe_subgraph(tools=[csmar_probe], prompt="p", per_variable_max_calls=0)


# ---------------------------------------------------------------------------
# Empty queue: no LLM invocation, downstream slices initialized
# ---------------------------------------------------------------------------


class TestEmptyQueue:
    def test_empty_variables_list_skips_llm(self) -> None:
        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=3
        )
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([])}))

        assert result["queue_initialized"] is True
        assert result["variable_queue"] == []
        assert result["current_variable"] is None
        assert result["probe_report"] == {
            "variable_results": [],
            "overall_status": "success",
            "failure_reason": None,
        }
        assert result["download_manifest"] == {"items": []}


# ---------------------------------------------------------------------------
# F16: result_handler branches (all deterministic _result_handler logic)
# ---------------------------------------------------------------------------


class TestFoundSingleVariable:
    def test_found_writes_manifest_and_report(self, mocker: Any) -> None:
        finding = _found(database="CSMAR", table="TRD", field="ROA", record_count=12345)
        _wire_agent(mocker, findings=[finding])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="p", per_variable_max_calls=3
        )
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([_var("ROA")])}))

        report = result["probe_report"]
        assert report["overall_status"] == "success"
        assert report["failure_reason"] is None
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROA"
        assert vr["status"] == "found"
        assert vr["source"] == {"database": "CSMAR", "table": "TRD", "field": "ROA"}
        assert vr["record_count"] == 12345
        assert vr["substitution_trace"] is None

        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["database"] == "CSMAR"
        assert items[0]["table"] == "TRD"
        assert items[0]["variable_fields"] == ["ROA"]
        assert items[0]["variable_names"] == ["ROA"]
        assert items[0]["key_fields"] == ["stkcd", "year"]


class TestHardNotFound:
    def test_hard_not_found_routes_end_with_workflow_status(self, mocker: Any) -> None:
        _wire_agent(mocker, findings=[_not_found()])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="p", per_variable_max_calls=3
        )
        result = asyncio.run(
            graph.ainvoke(
                {"empirical_spec": _spec([_var("ROA", contract="hard"), _var("LEV")])}
            )
        )

        report = result["probe_report"]
        assert report["overall_status"] == "hard_failure"
        assert report["failure_reason"] is not None
        assert "ROA" in report["failure_reason"]
        assert result["workflow_status"] == "failed_hard_contract"
        # Routed straight to END: LEV was never processed
        assert len(report["variable_results"]) == 1
        assert report["variable_results"][0]["variable_name"] == "ROA"
        assert report["variable_results"][0]["status"] == "not_found"
        assert result["download_manifest"]["items"] == []


class TestSoftSubstituteSuccess:
    def test_soft_substitute_writes_back_spec_and_manifest(self, mocker: Any) -> None:
        finding1 = _not_found(
            substitute_name="ROA",
            substitute_description="ROA proxies ROE",
            substitute_reason="similar economic meaning",
        )
        finding2 = _found(database="CSMAR", table="TRD", field="ROA")
        _wire_agent(mocker, findings=[finding1, finding2])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="p", per_variable_max_calls=3
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe])}))

        report = result["probe_report"]
        assert report["overall_status"] == "success"
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "substituted"
        assert vr["source"] == {"database": "CSMAR", "table": "TRD", "field": "ROA"}
        trace = vr["substitution_trace"]
        assert trace["original"] == "ROE"
        assert trace["substitute"] == "ROA"
        assert trace["reason"] == "similar economic meaning"

        # EmpiricalSpec writeback
        spec_vars = [v["name"] for v in result["empirical_spec"]["variables"]]
        assert "ROA" in spec_vars
        assert "ROE" not in spec_vars

        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["variable_names"] == ["ROA"]


class TestSoftSubstituteFailure:
    def test_substitute_chain_terminates_after_one_attempt(self, mocker: Any) -> None:
        finding1 = _not_found(
            substitute_name="ROA", substitute_description="d", substitute_reason="r"
        )
        finding2 = _not_found()
        mock_create = _wire_agent(mocker, findings=[finding1, finding2])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="p", per_variable_max_calls=3
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe])}))

        report = result["probe_report"]
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "not_found"
        assert report["overall_status"] == "success"
        assert "workflow_status" not in result
        assert mock_create.call_count == 2
        assert result["download_manifest"]["items"] == []


class TestMultiVariableSameTable:
    def test_two_variables_same_table_merge_into_one_task(self, mocker: Any) -> None:
        finding1 = _found(database="CSMAR", table="TRD", field="ROA")
        finding2 = _found(database="CSMAR", table="TRD", field="LEV")
        _wire_agent(mocker, findings=[finding1, finding2])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="p", per_variable_max_calls=3
        )
        result = asyncio.run(
            graph.ainvoke(
                {"empirical_spec": _spec([_var("ROA"), _var("LEV", role="control")])}
            )
        )

        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["database"] == "CSMAR"
        assert items[0]["table"] == "TRD"
        assert sorted(items[0]["variable_fields"]) == ["LEV", "ROA"]
        assert sorted(items[0]["variable_names"]) == ["LEV", "ROA"]
        assert items[0]["key_fields"] == ["stkcd", "year"]
