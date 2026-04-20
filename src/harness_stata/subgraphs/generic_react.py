"""Generic ReAct subgraph factory (F19).

Compiles a reusable two-node ReAct loop (``agent`` + ``tool_executor``) with a
``max_iterations`` safety cap. Consumed by data_cleaning / descriptive_stats /
regression nodes; each caller supplies its own tools and system prompt.

Loop semantics (docs/empirical-analysis-workflow.md:60-71):

* ``agent`` produces an ``AIMessage``; if it carries ``tool_calls``, the
  condition edge routes to ``tool_executor``; otherwise the subgraph ends
  (natural completion).
* ``tool_executor`` dispatches each tool call, appends one ``ToolMessage`` per
  call, increments ``iteration_count`` by 1, then hands control back to
  ``agent``.
* Once ``iteration_count >= max_iterations`` the condition edge forces ``END``
  even if the last ``AIMessage`` still carries ``tool_calls``.

Caller contract: invoke the compiled graph with
``{"messages": [HumanMessage(...)], "iteration_count": 0}``. The final
``AIMessage`` in the returned ``messages`` list is the business output the
caller must parse.
"""

from __future__ import annotations

import operator
from collections.abc import Sequence
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from harness_stata.clients.llm import get_chat_model


class ReactState(TypedDict):
    """Internal state of the generic ReAct subgraph."""

    messages: Annotated[list[BaseMessage], add_messages]
    iteration_count: Annotated[int, operator.add]


def build_react_subgraph(
    tools: Sequence[BaseTool],
    prompt: str,
    max_iterations: int,
) -> CompiledStateGraph[ReactState, ReactState, ReactState, ReactState]:
    """Build a compiled ReAct subgraph bound to ``tools`` and ``prompt``."""
    if not tools:
        raise ValueError("tools must not be empty")
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")

    tools_by_name: dict[str, BaseTool] = {t.name: t for t in tools}
    bound_tools: list[BaseTool] = list(tools)

    async def _agent(state: ReactState) -> dict[str, list[BaseMessage]]:
        msgs = list(state["messages"])
        new_messages: list[BaseMessage] = []
        if not msgs or not isinstance(msgs[0], SystemMessage):
            sys_msg = SystemMessage(content=prompt)
            msgs = [sys_msg, *msgs]
            new_messages.append(sys_msg)
        model = get_chat_model().bind_tools(bound_tools)  # pyright: ignore[reportUnknownMemberType]
        response = await model.ainvoke(msgs)  # pyright: ignore[reportUnknownMemberType]
        assert isinstance(response, AIMessage)
        new_messages.append(response)
        return {"messages": new_messages}

    async def _tool_executor(state: ReactState) -> dict[str, Any]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        tool_msgs: list[BaseMessage] = []
        for call in last.tool_calls:
            tool_obj = tools_by_name[call["name"]]
            output = await tool_obj.ainvoke(call["args"])  # pyright: ignore[reportUnknownMemberType]
            tool_msgs.append(ToolMessage(content=str(output), tool_call_id=call["id"] or ""))
        return {"messages": tool_msgs, "iteration_count": 1}

    def _should_continue(state: ReactState) -> Literal["tool_executor", "__end__"]:
        last = state["messages"][-1]
        if not isinstance(last, AIMessage) or not last.tool_calls:
            return "__end__"
        if state["iteration_count"] >= max_iterations:
            return "__end__"
        return "tool_executor"

    graph: StateGraph[ReactState, ReactState, ReactState, ReactState] = StateGraph(ReactState)
    graph.add_node("agent", _agent)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("tool_executor", _tool_executor)  # pyright: ignore[reportUnknownMemberType]
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        _should_continue,
        {"tool_executor": "tool_executor", END: END},
    )
    graph.add_edge("tool_executor", "agent")
    return graph.compile()  # pyright: ignore[reportUnknownMemberType]
