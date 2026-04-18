"""Unit tests for the generic_react subgraph factory (F19)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from harness_stata.subgraphs.generic_react import ReactState, build_react_subgraph

# ---------------------------------------------------------------------------
# Fake tools (no side effects, deterministic output)
# ---------------------------------------------------------------------------


@tool
def echo_a(x: str) -> str:
    """Prefix x with 'A:' (test double)."""
    return f"A:{x}"


@tool
def echo_b(x: str) -> str:
    """Prefix x with 'B:' (test double)."""
    return f"B:{x}"


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
        "harness_stata.subgraphs.generic_react.get_chat_model",
        return_value=model,
    )
    return bound


def _tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_empty_tools_rejected(self) -> None:
        with pytest.raises(ValueError, match="tools must not be empty"):
            build_react_subgraph(tools=[], prompt="p", max_iterations=1)

    def test_non_positive_max_iterations_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_iterations must be >= 1"):
            build_react_subgraph(tools=[echo_a], prompt="p", max_iterations=0)


# ---------------------------------------------------------------------------
# Normal completion path: tool -> tool-executor -> agent -> no-tool -> END
# ---------------------------------------------------------------------------


class TestNormalCompletion:
    def test_single_round_then_final(self, mocker: Any) -> None:
        ai_with_tool = AIMessage(
            content="",
            tool_calls=[_tool_call("echo_a", {"x": "hello"}, "call_1")],
        )
        ai_final = AIMessage(content="final answer")
        bound = _wire_model(mocker, [ai_with_tool, ai_final])

        graph = build_react_subgraph(
            tools=[echo_a, echo_b], prompt="system-prompt-X", max_iterations=5
        )
        initial: ReactState = {
            "messages": [HumanMessage(content="user input")],
            "iteration_count": 0,
        }
        result = graph.invoke(initial)

        # Final state ends with a tool-less AIMessage
        final_msg = result["messages"][-1]
        assert isinstance(final_msg, AIMessage)
        assert not final_msg.tool_calls
        assert final_msg.content == "final answer"

        # Exactly one tool round completed
        assert result["iteration_count"] == 1

        # Tool output appears in the transcript
        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "A:hello"
        assert tool_msgs[0].tool_call_id == "call_1"

        # LLM was invoked twice (two agent turns)
        assert bound.invoke.call_count == 2


# ---------------------------------------------------------------------------
# Forced truncation path: agent always requests tools until max_iterations hit
# ---------------------------------------------------------------------------


class TestForcedTruncation:
    def test_truncates_at_max_iterations(self, mocker: Any) -> None:
        # Every agent turn returns a tool-calling AIMessage.
        def _make_ai(idx: int) -> AIMessage:
            return AIMessage(
                content="",
                tool_calls=[_tool_call("echo_a", {"x": f"t{idx}"}, f"call_{idx}")],
            )

        responses = [_make_ai(i) for i in range(10)]
        bound = _wire_model(mocker, responses)

        max_iter = 2
        graph = build_react_subgraph(
            tools=[echo_a], prompt="sys", max_iterations=max_iter
        )
        initial: ReactState = {
            "messages": [HumanMessage(content="go")],
            "iteration_count": 0,
        }
        result = graph.invoke(initial)

        # Iteration counter reached the cap
        assert result["iteration_count"] == max_iter

        # Agent ran max_iter + 1 times (final call aborted by should_continue)
        assert bound.invoke.call_count == max_iter + 1

        # Final message still carries tool_calls (proves truncation, not natural end)
        final_msg = result["messages"][-1]
        assert isinstance(final_msg, AIMessage)
        assert final_msg.tool_calls


# ---------------------------------------------------------------------------
# SystemMessage injection on first turn
# ---------------------------------------------------------------------------


class TestSystemPromptInjection:
    def test_system_prompt_injected_once(self, mocker: Any) -> None:
        ai_final = AIMessage(content="done")
        bound = _wire_model(mocker, [ai_final])

        graph = build_react_subgraph(tools=[echo_a], prompt="PROMPT-X", max_iterations=3)
        initial: ReactState = {
            "messages": [HumanMessage(content="hi")],
            "iteration_count": 0,
        }
        result = graph.invoke(initial)

        # First message seen by the LLM is the injected SystemMessage
        first_call_msgs: list[Any] = bound.invoke.call_args_list[0][0][0]
        assert isinstance(first_call_msgs[0], SystemMessage)
        assert first_call_msgs[0].content == "PROMPT-X"

        # SystemMessage is persisted in state so subsequent turns don't re-inject
        sys_in_state = [m for m in result["messages"] if isinstance(m, SystemMessage)]
        assert len(sys_in_state) == 1
        assert sys_in_state[0].content == "PROMPT-X"


# ---------------------------------------------------------------------------
# Multiple tool calls in a single AIMessage
# ---------------------------------------------------------------------------


class TestParallelToolCalls:
    def test_multiple_tools_in_one_turn(self, mocker: Any) -> None:
        ai_with_tools = AIMessage(
            content="",
            tool_calls=[
                _tool_call("echo_a", {"x": "1"}, "c_a"),
                _tool_call("echo_b", {"x": "2"}, "c_b"),
            ],
        )
        ai_final = AIMessage(content="combined")
        _wire_model(mocker, [ai_with_tools, ai_final])

        graph = build_react_subgraph(
            tools=[echo_a, echo_b], prompt="sys", max_iterations=5
        )
        initial: ReactState = {
            "messages": [HumanMessage(content="go")],
            "iteration_count": 0,
        }
        result = graph.invoke(initial)

        tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2
        contents = {m.content for m in tool_msgs}
        assert contents == {"A:1", "B:2"}
        # iteration_count still 1: one tool_executor run produced two ToolMessages
        assert result["iteration_count"] == 1
