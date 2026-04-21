"""Unit tests for the probe_subgraph factory (F15 skeleton + F16 branching)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool

from harness_stata.state import EmpiricalSpec, VariableDefinition
from harness_stata.subgraphs.probe_subgraph import (
    ProbeState,
    _VariableProbeFindingModel,
    build_probe_subgraph,
)

# ---------------------------------------------------------------------------
# Fake tools (no side effects, deterministic output)
# ---------------------------------------------------------------------------


@tool
def csmar_probe(table: str) -> str:
    """Return a canned probe result (test double)."""
    return f"probe:{table}"


@tool
def csmar_schema(table: str) -> str:
    """Return a canned schema result (test double)."""
    return f"schema:{table}"


# ---------------------------------------------------------------------------
# Mock wiring helpers
# ---------------------------------------------------------------------------


def _wire_agent(
    mocker: Any,
    findings: list[_VariableProbeFindingModel],
) -> tuple[MagicMock, list[dict[str, Any]]]:
    """Patch ``create_agent`` in probe_subgraph.

    Each call to ``create_agent`` consumes the next finding from the list and
    returns a fake agent whose ``ainvoke`` echoes the input messages back plus
    the finding as ``structured_response``.

    Returns ``(mock_create_agent, captured_ainvoke_args)`` where
    ``captured_ainvoke_args`` accumulates every dict passed to ``agent.ainvoke``.
    """
    captured: list[dict[str, Any]] = []

    def _make_agent(finding: _VariableProbeFindingModel) -> MagicMock:
        agent = MagicMock()

        async def _ainvoke(state: dict[str, Any]) -> dict[str, Any]:
            captured.append(state)
            return {
                "messages": list(state.get("messages", [])),
                "structured_response": finding,
            }

        agent.ainvoke = AsyncMock(side_effect=_ainvoke)
        return agent

    mock_create = mocker.patch(
        "harness_stata.subgraphs.probe_subgraph.create_agent",
        side_effect=[_make_agent(f) for f in findings],
    )
    return mock_create, captured


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
    def test_empty_variables_list_skips_llm(self, mocker: Any) -> None:
        mock_create, _ = _wire_agent(mocker, findings=[])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=3
        )
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([])}))

        assert mock_create.call_count == 0
        assert result["queue_initialized"] is True
        assert result["variable_queue"] == []
        assert result["current_variable"] is None

        # F16: empty queue still initializes the downstream slices so HITL never KeyErrors
        assert result["probe_report"] == {
            "variable_results": [],
            "overall_status": "success",
            "failure_reason": None,
        }
        assert result["download_manifest"] == {"items": []}


# ---------------------------------------------------------------------------
# Single variable: create_agent called once with correct prompt and variable
# ---------------------------------------------------------------------------


class TestSingleVariableNaturalCompletion:
    def test_agent_receives_correct_prompt_and_variable(self, mocker: Any) -> None:
        mock_create, captured = _wire_agent(mocker, findings=[_found()])

        graph = build_probe_subgraph(
            tools=[csmar_probe, csmar_schema],
            prompt="SYS-PROBE",
            per_variable_max_calls=3,
        )
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([_var("ROA")])}))

        # Exactly one agent created
        assert mock_create.call_count == 1

        # system_prompt passed to create_agent contains the user-supplied prompt
        call_kwargs = mock_create.call_args[1]
        assert "SYS-PROBE" in call_kwargs.get("system_prompt", "")

        # ainvoke received a HumanMessage about ROA
        assert len(captured) == 1
        msgs = captured[0]["messages"]
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)
        assert "ROA" in msgs[0].content

        # Queue drained
        assert result["variable_queue"] == []


# ---------------------------------------------------------------------------
# Single variable: budget exhausted -> treated as not_found by result_handler
# ---------------------------------------------------------------------------


class TestSingleVariableBudgetExhaustion:
    def test_budget_exhausted_treated_as_not_found(self, mocker: Any) -> None:
        """ToolCallLimitMiddleware exhausts the per-variable budget; the outer
        subgraph treats a missing/not_found finding as not_found for soft variables."""
        mock_create, _ = _wire_agent(mocker, findings=[_not_found()])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=2
        )
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([_var("SIZE")])}))

        assert mock_create.call_count == 1
        vr = result["probe_report"]["variable_results"][0]
        assert vr["variable_name"] == "SIZE"
        assert vr["status"] == "not_found"


# ---------------------------------------------------------------------------
# Two variables: create_agent recreated per variable; HumanMessage has correct content
# ---------------------------------------------------------------------------


class TestTwoVariables:
    def test_agent_recreated_and_humamessage_scoped_per_variable(self, mocker: Any) -> None:
        mock_create, captured = _wire_agent(
            mocker, findings=[_found(field="V1"), _found(field="V2")]
        )

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=5
        )
        result = asyncio.run(
            graph.ainvoke({"empirical_spec": _spec([_var("V1"), _var("V2")])})
        )

        # create_agent called once per variable
        assert mock_create.call_count == 2

        # Each ainvoke got the right variable in its HumanMessage
        assert "V1" in captured[0]["messages"][0].content
        assert "V2" in captured[1]["messages"][0].content

        # Queue drained; current_variable is the last processed
        assert result["variable_queue"] == []
        assert result["current_variable"] == _var("V2")


# ---------------------------------------------------------------------------
# F16: result_handler branches
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
        # Routed straight to END after first hard failure: never processed LEV
        assert len(report["variable_results"]) == 1
        assert report["variable_results"][0]["variable_name"] == "ROA"
        assert report["variable_results"][0]["status"] == "not_found"
        # Manifest stays empty for hard failure
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
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "substituted"
        assert vr["source"] == {"database": "CSMAR", "table": "TRD", "field": "ROA"}
        trace = vr["substitution_trace"]
        assert trace is not None
        assert trace["original"] == "ROE"
        assert trace["substitute"] == "ROA"
        assert trace["reason"] == "similar economic meaning"

        # EmpiricalSpec writeback: ROA replaces ROE in variables list
        spec_vars = [v["name"] for v in result["empirical_spec"]["variables"]]
        assert "ROA" in spec_vars
        assert "ROE" not in spec_vars

        # Manifest now contains the substitute under its new name
        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["variable_names"] == ["ROA"]
        assert items[0]["variable_fields"] == ["ROA"]


class TestSoftSubstituteFailure:
    def test_substitute_chain_terminates_after_one_attempt(self, mocker: Any) -> None:
        finding1 = _not_found(
            substitute_name="ROA",
            substitute_description="d",
            substitute_reason="r",
        )
        finding2 = _not_found()  # no further substitute suggestion
        mock_create, _ = _wire_agent(mocker, findings=[finding1, finding2])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="p", per_variable_max_calls=3
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe])}))

        report = result["probe_report"]
        # Substitute itself failed: status=not_found, recorded under ORIGINAL name
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "not_found"
        # Soft does not block the workflow
        assert report["overall_status"] == "success"
        assert "workflow_status" not in result

        # Exactly two agent creations — no third substitute generation
        assert mock_create.call_count == 2

        # Empty manifest since nothing was found
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
        # key_fields deduped after merge
        assert items[0]["key_fields"] == ["stkcd", "year"]


# ---------------------------------------------------------------------------
# F26: available_databases injection
# ---------------------------------------------------------------------------


class TestAvailableDatabasesInjection:
    def test_humanmessage_contains_available_databases(self, mocker: Any) -> None:
        _, captured = _wire_agent(mocker, findings=[_found()])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=3
        )
        databases_text = (
            'Returned 2 purchased databases.\n{"databases": ["CSMAR", "RESSET"]}'
        )
        asyncio.run(
            graph.ainvoke(
                {
                    "empirical_spec": _spec([_var("ROA")]),
                    "available_databases": databases_text,
                }
            )
        )

        human = captured[0]["messages"][0]
        assert isinstance(human, HumanMessage)
        assert "Purchased databases" in human.content
        assert "CSMAR" in human.content
        assert "RESSET" in human.content
