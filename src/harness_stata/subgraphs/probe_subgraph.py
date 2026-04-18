"""Probe subgraph factory (F15 skeleton).

Three-node skeleton for the data-probe subgraph:

    variable_dispatcher -> variable_react -> result_handler

with ``per_variable_max_calls`` budget isolated per variable.

Key divergence from the generic ReAct factory (subgraphs/generic_react.py):
csmar_mcp is throttled per account per day, so budget must be reset between
variables rather than accumulated globally. The inner ReAct loop is therefore
written by hand inside ``variable_react`` (instead of reusing
``build_react_subgraph``), because the counter needs overwrite semantics rather
than ``operator.add`` reducer semantics.

Scope of F15:
  * graph topology and per-variable budget control
  * queue bootstrapping on first dispatcher entry
  * clean termination when the variable queue is drained

Left to F16:
  * Hard/Soft branch decisions inside ``result_handler``
  * Soft-substitute enqueueing and EmpiricalSpec/ModelPlan writeback
  * ProbeReport / DownloadManifest payload construction
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from harness_stata.clients.llm import get_chat_model
from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    VariableDefinition,
)


class ProbeState(TypedDict, total=False):
    """Internal state of the probe subgraph.

    Fields shared with the parent WorkflowState (read-in / write-back):
    ``empirical_spec``, ``model_plan``, ``probe_report``, ``download_manifest``.

    Fields private to the subgraph (do not leak to the parent):
    ``variable_queue``, ``current_variable``, ``per_variable_call_count``,
    ``messages``, ``queue_initialized``.
    """

    empirical_spec: EmpiricalSpec
    model_plan: ModelPlan
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    variable_queue: list[VariableDefinition]
    current_variable: VariableDefinition | None
    per_variable_call_count: int
    messages: list[BaseMessage]
    queue_initialized: bool


def build_probe_subgraph(
    tools: Sequence[BaseTool],
    prompt: str,
    per_variable_max_calls: int,
) -> CompiledStateGraph[ProbeState, ProbeState, ProbeState, ProbeState]:
    """Build a compiled probe subgraph bound to ``tools`` and ``prompt``.

    ``per_variable_max_calls`` caps the number of tool-executor rounds the
    inner ReAct may run for a single variable. Natural completion (LLM
    responds without ``tool_calls``) exits early without consuming the full
    budget.
    """
    if not tools:
        raise ValueError("tools must not be empty")
    if per_variable_max_calls < 1:
        raise ValueError("per_variable_max_calls must be >= 1")

    tools_by_name: dict[str, BaseTool] = {t.name: t for t in tools}
    bound_tools: list[BaseTool] = list(tools)

    def _variable_dispatcher(state: ProbeState) -> dict[str, Any]:
        """Pop the next variable off the queue and reset per-variable state.

        On the first entry (``queue_initialized`` absent / false) the queue is
        seeded from ``empirical_spec.variables``. Subsequent entries take the
        remaining queue from state. When the queue is empty the dispatcher
        sets ``current_variable`` to None so that ``variable_react`` can short
        circuit and ``result_handler`` can route to END.
        """
        if state.get("queue_initialized"):
            queue = list(state.get("variable_queue") or [])
        else:
            spec = state["empirical_spec"]  # type: ignore[reportTypedDictNotRequiredAccess]
            queue = list(spec["variables"])

        updates: dict[str, Any] = {"queue_initialized": True}
        if queue:
            updates["current_variable"] = queue[0]
            updates["variable_queue"] = queue[1:]
            updates["per_variable_call_count"] = 0
            updates["messages"] = []
        else:
            updates["current_variable"] = None
            updates["variable_queue"] = []
        return updates

    def _variable_react(state: ProbeState) -> dict[str, Any]:
        """Run the inner ReAct loop for the current variable.

        Short circuits with an empty update when ``current_variable`` is None
        (empty queue pass-through). Otherwise drives LLM <-> tools rounds
        until the LLM stops requesting tools (natural completion) or the
        per-variable budget is exhausted.
        """
        var = state.get("current_variable")
        if var is None:
            return {}

        messages: list[BaseMessage] = [
            SystemMessage(content=prompt),
            HumanMessage(
                content=(
                    f"Variable: {var['name']} - {var['description']} "
                    f"(contract: {var['contract_type']}, role: {var['role']})"
                )
            ),
        ]
        model = get_chat_model().bind_tools(bound_tools)  # pyright: ignore[reportUnknownMemberType]
        call_count = 0
        while True:
            response = model.invoke(messages)  # pyright: ignore[reportUnknownMemberType]
            assert isinstance(response, AIMessage)
            messages.append(response)
            if not response.tool_calls:
                break
            if call_count >= per_variable_max_calls:
                break
            for call in response.tool_calls:
                tool_obj = tools_by_name[call["name"]]
                output = tool_obj.invoke(call["args"])  # pyright: ignore[reportUnknownMemberType]
                messages.append(ToolMessage(content=str(output), tool_call_id=call["id"] or ""))
            call_count += 1
        return {"messages": messages, "per_variable_call_count": call_count}

    def _result_handler(state: ProbeState) -> dict[str, Any]:
        """F15 placeholder; Hard/Soft branching and payload writes land in F16."""
        del state
        return {}

    def _route_after_handler(
        state: ProbeState,
    ) -> Literal["variable_dispatcher", "__end__"]:
        if state.get("variable_queue"):
            return "variable_dispatcher"
        return "__end__"

    graph: StateGraph[ProbeState, ProbeState, ProbeState, ProbeState] = StateGraph(ProbeState)
    graph.add_node("variable_dispatcher", _variable_dispatcher)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("variable_react", _variable_react)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("result_handler", _result_handler)  # pyright: ignore[reportUnknownMemberType]
    graph.add_edge(START, "variable_dispatcher")
    graph.add_edge("variable_dispatcher", "variable_react")
    graph.add_edge("variable_react", "result_handler")
    graph.add_conditional_edges(
        "result_handler",
        _route_after_handler,
        {"variable_dispatcher": "variable_dispatcher", END: END},
    )
    return graph.compile()  # pyright: ignore[reportUnknownMemberType]
