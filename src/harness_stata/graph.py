"""Main workflow graph assembly.

Topology (8 nodes, 2 conditional edges):

    START
      -> requirement_analysis -> model_construction -> data_probe
      [probe gate]
        failed_hard_contract -> END
        otherwise            -> hitl
      [hitl gate]
        rejected   -> END
        otherwise  -> data_download -> data_cleaning
                      -> descriptive_stats -> regression -> END

Compiled with an ``InMemorySaver`` checkpointer so that ``hitl`` node's
``interrupt()`` can pause and later resume via ``Command(resume=...)``.
"""

from __future__ import annotations

from typing import Literal

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from harness_stata.nodes.data_cleaning import data_cleaning
from harness_stata.nodes.data_download import data_download
from harness_stata.nodes.data_probe import data_probe
from harness_stata.nodes.descriptive_stats import descriptive_stats
from harness_stata.nodes.hitl import hitl
from harness_stata.nodes.model_construction import model_construction
from harness_stata.nodes.regression import regression
from harness_stata.nodes.requirement_analysis import requirement_analysis
from harness_stata.state import WorkflowState

__all__ = ["build_graph", "route_after_hitl", "route_after_probe"]


def route_after_probe(state: WorkflowState) -> Literal["hard_failure", "success"]:
    """Conditional router after ``data_probe``.

    Hard failure is signaled by either ``workflow_status="failed_hard_contract"``
    (written by the node) or ``probe_report.overall_status="hard_failure"``
    (written by the subgraph). The second check is a defensive fallback.
    """
    if state.get("workflow_status") == "failed_hard_contract":
        return "hard_failure"
    report = state.get("probe_report")
    if report is not None and report.get("overall_status") == "hard_failure":
        return "hard_failure"
    return "success"


def route_after_hitl(state: WorkflowState) -> Literal["rejected", "approved"]:
    """Conditional router after ``hitl``.

    Rejected path is signaled by ``workflow_status="rejected"`` (written by
    the node) or by ``hitl_decision.approved is False``.
    """
    if state.get("workflow_status") == "rejected":
        return "rejected"
    decision = state.get("hitl_decision")
    if decision is not None and not decision.get("approved", True):
        return "rejected"
    return "approved"


def build_graph(
    *, use_checkpointer: bool = True
) -> CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState]:
    """Assemble and compile the main workflow graph.

    When ``use_checkpointer`` is True (default, CLI path), bind an ``InMemorySaver``
    so ``hitl`` node's ``interrupt()`` can pause and resume. On LangGraph Platform
    (``langgraph dev`` / Studio), persistence is managed by the platform, so pass
    ``use_checkpointer=False`` to avoid the platform rejecting the custom saver.
    """
    builder = StateGraph(WorkflowState)

    builder.add_node("requirement_analysis", requirement_analysis)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("model_construction", model_construction)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("data_probe", data_probe)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("hitl", hitl)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("data_download", data_download)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("data_cleaning", data_cleaning)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("descriptive_stats", descriptive_stats)  # pyright: ignore[reportUnknownMemberType]
    builder.add_node("regression", regression)  # pyright: ignore[reportUnknownMemberType]

    builder.add_edge(START, "requirement_analysis")  # pyright: ignore[reportUnknownMemberType]
    builder.add_edge("requirement_analysis", "model_construction")  # pyright: ignore[reportUnknownMemberType]
    builder.add_edge("model_construction", "data_probe")  # pyright: ignore[reportUnknownMemberType]

    builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
        "data_probe",
        route_after_probe,
        {"hard_failure": END, "success": "hitl"},
    )
    builder.add_conditional_edges(  # pyright: ignore[reportUnknownMemberType]
        "hitl",
        route_after_hitl,
        {"rejected": END, "approved": "data_download"},
    )

    builder.add_edge("data_download", "data_cleaning")  # pyright: ignore[reportUnknownMemberType]
    builder.add_edge("data_cleaning", "descriptive_stats")  # pyright: ignore[reportUnknownMemberType]
    builder.add_edge("descriptive_stats", "regression")  # pyright: ignore[reportUnknownMemberType]
    builder.add_edge("regression", END)  # pyright: ignore[reportUnknownMemberType]

    if use_checkpointer:
        return builder.compile(checkpointer=InMemorySaver())  # pyright: ignore[reportUnknownMemberType]
    return builder.compile()  # pyright: ignore[reportUnknownMemberType]
