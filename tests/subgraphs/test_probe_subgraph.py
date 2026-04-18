"""Unit tests for the probe_subgraph factory (F15 skeleton)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool

from harness_stata.state import EmpiricalSpec, VariableDefinition
from harness_stata.subgraphs.probe_subgraph import ProbeState, build_probe_subgraph

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


def _wire_model(mocker: Any, responses: list[AIMessage]) -> MagicMock:
    """Patch get_chat_model so that .bind_tools(...).invoke(...) returns responses in order."""
    model = MagicMock()
    bound = MagicMock()
    bound.invoke.side_effect = responses
    model.bind_tools.return_value = bound
    mocker.patch(
        "harness_stata.subgraphs.probe_subgraph.get_chat_model",
        return_value=model,
    )
    return bound


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
# Empty queue: no LLM invocation, graph exits cleanly
# ---------------------------------------------------------------------------


class TestEmptyQueue:
    def test_empty_variables_list_skips_llm(self, mocker: Any) -> None:
        bound = _wire_model(mocker, [])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=3
        )
        initial: ProbeState = {"empirical_spec": _spec([])}
        result = graph.invoke(initial)

        # LLM was never called
        assert bound.invoke.call_count == 0

        # Dispatcher initialised but left queue empty, current_variable cleared
        assert result["queue_initialized"] is True
        assert result["variable_queue"] == []
        assert result["current_variable"] is None


# ---------------------------------------------------------------------------
# Single variable: natural completion (LLM emits no tool_calls on first turn)
# ---------------------------------------------------------------------------


class TestSingleVariableNaturalCompletion:
    def test_no_tools_on_first_turn(self, mocker: Any) -> None:
        ai_final = AIMessage(content="variable resolved: CSMAR.TRD.ROA")
        bound = _wire_model(mocker, [ai_final])

        graph = build_probe_subgraph(
            tools=[csmar_probe, csmar_schema],
            prompt="SYS-PROBE",
            per_variable_max_calls=3,
        )
        initial: ProbeState = {"empirical_spec": _spec([_var("ROA")])}
        result = graph.invoke(initial)

        # Exactly one LLM call, zero tool rounds consumed
        assert bound.invoke.call_count == 1
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
        bound = _wire_model(mocker, responses)

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=budget
        )
        initial: ProbeState = {"empirical_spec": _spec([_var("SIZE")])}
        result = graph.invoke(initial)

        # Counter reached the cap; tools executed exactly ``budget`` times
        assert result["per_variable_call_count"] == budget
        # LLM called budget + 1 times: the final call produced tool_calls that
        # were rejected by the budget guard instead of executed
        assert bound.invoke.call_count == budget + 1

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

        bound = _wire_model(mocker, [v1_tool_a, v1_tool_b, v1_final, v2_final])

        graph = build_probe_subgraph(
            tools=[csmar_probe], prompt="sys", per_variable_max_calls=5
        )
        initial: ProbeState = {
            "empirical_spec": _spec([_var("V1"), _var("V2")]),
        }
        result = graph.invoke(initial)

        # Four total LLM invocations across both variables
        assert bound.invoke.call_count == 4

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
