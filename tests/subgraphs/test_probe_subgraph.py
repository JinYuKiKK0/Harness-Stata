"""Unit tests for the probe_subgraph factory (F15 skeleton + F16 branching)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
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


def _wire_models(
    mocker: Any,
    *,
    react_responses: list[AIMessage] | None = None,
    extractor_findings: list[_VariableProbeFindingModel] | None = None,
) -> tuple[AsyncMock, AsyncMock]:
    """Patch get_chat_model so .bind_tools(...).ainvoke and .with_structured_output(...).ainvoke
    return canned responses in order. Returns (bound_react_ainvoke, extractor_ainvoke).

    Subgraph nodes are ``async def`` and use ``await model.ainvoke(...)`` /
    ``await structured.ainvoke(...)``; MagicMock auto-attrs are not awaitable,
    so we wire AsyncMock explicitly.
    """
    model = MagicMock()
    bound = MagicMock()
    bound.ainvoke = AsyncMock(side_effect=react_responses or [])
    model.bind_tools.return_value = bound
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=extractor_findings or [])
    model.with_structured_output.return_value = structured
    mocker.patch(
        "harness_stata.subgraphs.probe_subgraph.get_chat_model",
        return_value=model,
    )
    return bound.ainvoke, structured.ainvoke


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


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
        bound, structured = _wire_models(mocker)

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=3
        )
        initial: ProbeState = {"empirical_spec": _spec([])}
        result = asyncio.run(graph.ainvoke(initial))

        # Neither react nor extractor was called
        assert bound.call_count == 0
        assert structured.call_count == 0

        # Dispatcher initialised but left queue empty, current_variable cleared
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
# Single variable: natural completion (LLM emits no tool_calls on first turn)
# ---------------------------------------------------------------------------


class TestSingleVariableNaturalCompletion:
    def test_no_tools_on_first_turn(self, mocker: Any) -> None:
        ai_final = AIMessage(content="variable resolved: CSMAR.TRD.ROA")
        bound, _ = _wire_models(
            mocker,
            react_responses=[ai_final],
            extractor_findings=[_found()],
        )

        graph = build_probe_subgraph(
            tools=[csmar_probe, csmar_schema],
            prompt="SYS-PROBE",
            per_variable_max_calls=3,
        )
        initial: ProbeState = {"empirical_spec": _spec([_var("ROA")])}
        result = asyncio.run(graph.ainvoke(initial))

        # Exactly one LLM call, zero tool rounds consumed
        assert bound.call_count == 1
        assert result["per_variable_call_count"] == 0

        # Messages contain the injected SystemMessage and the LLM response
        msgs = result["messages"]
        assert isinstance(msgs[0], SystemMessage)
        assert msgs[0].content == "SYS-PROBE"
        assert isinstance(msgs[1], HumanMessage)
        assert "ROA" in msgs[1].content
        assert isinstance(msgs[-1], AIMessage)
        assert not msgs[-1].tool_calls

        # Queue drained, graph exited cleanly
        assert result["variable_queue"] == []


# ---------------------------------------------------------------------------
# Single variable: budget exhaustion (LLM always requests tools)
# ---------------------------------------------------------------------------


class TestSingleVariableBudgetExhaustion:
    def test_truncates_at_per_variable_max_calls(self, mocker: Any) -> None:
        def _ai(idx: int) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[_tool_call("csmar_probe", {"table": f"t{idx}"}, f"c{idx}")],
            )

        budget = 2
        # Need at least budget + 1 responses: loop invokes LLM once more than it executes tools
        responses = [_ai(i) for i in range(budget + 5)]
        bound, _ = _wire_models(
            mocker,
            react_responses=responses,
            extractor_findings=[_not_found()],  # extractor sees no clear conclusion
        )

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=budget
        )
        initial: ProbeState = {"empirical_spec": _spec([_var("SIZE")])}
        result = asyncio.run(graph.ainvoke(initial))

        # Counter reached the cap; tools executed exactly ``budget`` times
        assert result["per_variable_call_count"] == budget
        # LLM called budget + 1 times: the final call produced tool_calls that
        # were rejected by the budget guard instead of executed
        assert bound.call_count == budget + 1

        # Final AIMessage still carries tool_calls (proves truncation, not natural end)
        final_msg = result["messages"][-1]
        assert isinstance(final_msg, AIMessage)
        assert final_msg.tool_calls


# ---------------------------------------------------------------------------
# Two variables: dispatcher cycles back, per-variable counter resets between them
# ---------------------------------------------------------------------------


class TestTwoVariables:
    def test_budget_and_messages_reset_between_variables(self, mocker: Any) -> None:
        # Variable 1: two tool rounds, then natural completion
        v1_tool_a = AIMessage(
            content="",
            tool_calls=[_tool_call("csmar_probe", {"table": "v1_a"}, "c_v1_a")],
        )
        v1_tool_b = AIMessage(
            content="",
            tool_calls=[_tool_call("csmar_probe", {"table": "v1_b"}, "c_v1_b")],
        )
        v1_final = AIMessage(content="v1 resolved")
        # Variable 2: immediate natural completion
        v2_final = AIMessage(content="v2 resolved")

        bound, _ = _wire_models(
            mocker,
            react_responses=[v1_tool_a, v1_tool_b, v1_final, v2_final],
            extractor_findings=[
                _found(field="V1"),
                _found(field="V2"),
            ],
        )

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=5
        )
        initial: ProbeState = {
            "empirical_spec": _spec([_var("V1"), _var("V2")]),
        }
        result = asyncio.run(graph.ainvoke(initial))

        # Four total LLM invocations across both variables
        assert bound.call_count == 4

        # Counter reflects variable 2's run only (dispatcher reset it to 0)
        assert result["per_variable_call_count"] == 0

        # messages holds variable 2's transcript only (dispatcher cleared on reentry).
        # Expect exactly 3 messages: System, Human, AIMessage(no tools).
        msgs = result["messages"]
        assert len(msgs) == 3
        assert isinstance(msgs[0], SystemMessage)
        assert isinstance(msgs[1], HumanMessage)
        assert "V2" in msgs[1].content
        assert isinstance(msgs[2], AIMessage)
        assert msgs[2].content == "v2 resolved"

        # Queue drained
        assert result["variable_queue"] == []
        assert result["current_variable"] == _var("V2")


# ---------------------------------------------------------------------------
# F16: result_handler branches
# ---------------------------------------------------------------------------


class TestFoundSingleVariable:
    def test_found_writes_manifest_and_report(self, mocker: Any) -> None:
        ai_final = AIMessage(content="found at CSMAR.TRD.ROA")
        finding = _found(database="CSMAR", table="TRD", field="ROA", record_count=12345)
        _wire_models(mocker, react_responses=[ai_final], extractor_findings=[finding])

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
        ai_final = AIMessage(content="cannot find ROA in any csmar table")
        _wire_models(
            mocker,
            react_responses=[ai_final],
            extractor_findings=[_not_found()],
        )

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
        # Round 1: original ROE not found, suggest ROA
        ai_round1 = AIMessage(content="ROE not in csmar; suggest ROA")
        finding1 = _not_found(
            substitute_name="ROA",
            substitute_description="ROA proxies ROE",
            substitute_reason="similar economic meaning",
        )
        # Round 2: ROA found
        ai_round2 = AIMessage(content="ROA found at CSMAR.TRD.ROA")
        finding2 = _found(database="CSMAR", table="TRD", field="ROA")
        _wire_models(
            mocker,
            react_responses=[ai_round1, ai_round2],
            extractor_findings=[finding1, finding2],
        )

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
        ai_round1 = AIMessage(content="ROE missing; suggest ROA")
        finding1 = _not_found(
            substitute_name="ROA",
            substitute_description="d",
            substitute_reason="r",
        )
        ai_round2 = AIMessage(content="ROA also missing")
        finding2 = _not_found()  # no further substitute suggestion
        bound, structured = _wire_models(
            mocker,
            react_responses=[ai_round1, ai_round2],
            extractor_findings=[finding1, finding2],
        )

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

        # Exactly two react/extractor rounds — no third substitute generation
        assert bound.call_count == 2
        assert structured.call_count == 2
        # Empty manifest since nothing was found
        assert result["download_manifest"]["items"] == []


class TestMultiVariableSameTable:
    def test_two_variables_same_table_merge_into_one_task(self, mocker: Any) -> None:
        ai1 = AIMessage(content="ROA at CSMAR.TRD.ROA")
        ai2 = AIMessage(content="LEV at CSMAR.TRD.LEV")
        finding1 = _found(database="CSMAR", table="TRD", field="ROA")
        finding2 = _found(database="CSMAR", table="TRD", field="LEV")
        _wire_models(
            mocker,
            react_responses=[ai1, ai2],
            extractor_findings=[finding1, finding2],
        )

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
# F26: available_databases 注入与 list_databases 工具过滤
# ---------------------------------------------------------------------------


@tool
def csmar_list_databases() -> str:
    """Stub list_databases tool; must be filtered out of ReAct binding by name."""
    return "should not be called"


class TestAvailableDatabasesInjection:
    def test_humanmessage_contains_available_databases(self, mocker: Any) -> None:
        ai_final = AIMessage(content="resolved using injected list")
        bound, _ = _wire_models(
            mocker,
            react_responses=[ai_final],
            extractor_findings=[_found()],
        )

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=3
        )
        databases_text = (
            'Returned 2 purchased databases.\n{"databases": ["CSMAR", "RESSET"]}'
        )
        result = asyncio.run(
            graph.ainvoke(
                {
                    "empirical_spec": _spec([_var("ROA")]),
                    "available_databases": databases_text,
                }
            )
        )

        msgs = result["messages"]
        human_msg = next(m for m in msgs if isinstance(m, HumanMessage))
        assert "Purchased databases" in human_msg.content
        assert "CSMAR" in human_msg.content
        assert "RESSET" in human_msg.content
        assert "Do NOT call any list_databases tool" in human_msg.content

    def test_humanmessage_falls_back_when_available_databases_missing(
        self, mocker: Any
    ) -> None:
        ai_final = AIMessage(content="resolved")
        _wire_models(
            mocker,
            react_responses=[ai_final],
            extractor_findings=[_found()],
        )

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=3
        )
        # 不注入 available_databases,应回落到 (unavailable) 字面量
        result = asyncio.run(
            graph.ainvoke({"empirical_spec": _spec([_var("ROA")])})
        )

        msgs = result["messages"]
        human_msg = next(m for m in msgs if isinstance(m, HumanMessage))
        assert "(unavailable)" in human_msg.content


class TestToolFiltering:
    def test_bound_tools_exclude_list_databases(self, mocker: Any) -> None:
        ai_final = AIMessage(content="done")
        _wire_models(
            mocker,
            react_responses=[ai_final],
            extractor_findings=[_found()],
        )
        # 取到 mocked model 以便捕获 bind_tools 调用
        from harness_stata.subgraphs import probe_subgraph as mod

        captured_model = mod.get_chat_model()  # type: ignore[reportUnknownVariableType]

        graph = build_probe_subgraph(
            tools=[csmar_probe, csmar_list_databases, csmar_schema],
            prompt="sys",
            per_variable_max_calls=3,
        )
        asyncio.run(
            graph.ainvoke(
                {
                    "empirical_spec": _spec([_var("ROA")]),
                    "available_databases": "CSMAR",
                }
            )
        )

        # bind_tools 被调用时传入的工具列表不应含 list_databases
        bind_call = captured_model.bind_tools.call_args  # type: ignore[reportUnknownMemberType]
        passed_tools = bind_call.args[0] if bind_call.args else bind_call.kwargs["tools"]
        names = [t.name for t in passed_tools]
        assert "csmar_list_databases" not in names
        assert "csmar_probe" in names
        assert "csmar_schema" in names

    def test_subgraph_rejects_only_list_databases(self) -> None:
        # 工具集只含 list_databases → 过滤后 bound_tools 为空,应 ValueError
        with pytest.raises(ValueError, match="non-list_databases"):
            build_probe_subgraph(
                tools=[csmar_list_databases],
                prompt="p",
                per_variable_max_calls=1,
            )
