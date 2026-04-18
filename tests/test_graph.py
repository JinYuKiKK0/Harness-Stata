"""Topology and routing tests for the main workflow graph."""

from __future__ import annotations

from typing import cast

from langgraph.graph import END

from harness_stata.graph import build_graph, route_after_hitl, route_after_probe
from harness_stata.state import (
    HitlDecision,
    ProbeReport,
    WorkflowState,
    WorkflowStatus,
)


# ---------------------------------------------------------------------------
# build_graph — topology
# ---------------------------------------------------------------------------


def test_build_graph_compiles_and_exposes_eight_business_nodes() -> None:
    graph = build_graph()
    node_names = set(graph.get_graph().nodes)
    expected = {
        "requirement_analysis",
        "model_construction",
        "data_probe",
        "hitl",
        "data_download",
        "data_cleaning",
        "descriptive_stats",
        "regression",
    }
    assert expected <= node_names


def test_build_graph_has_checkpointer_bound() -> None:
    graph = build_graph()
    assert graph.checkpointer is not None


def test_build_graph_returns_fresh_instance_each_call() -> None:
    a = build_graph()
    b = build_graph()
    assert a is not b
    assert a.checkpointer is not b.checkpointer


# ---------------------------------------------------------------------------
# route_after_probe
# ---------------------------------------------------------------------------


def _probe_report(status: str) -> ProbeReport:
    return cast(
        "ProbeReport",
        {
            "variable_results": [],
            "overall_status": status,
            "failure_reason": None,
        },
    )


def test_route_after_probe_hard_failure_via_workflow_status() -> None:
    state: WorkflowState = {"workflow_status": cast("WorkflowStatus", "failed_hard_contract")}
    assert route_after_probe(state) == "hard_failure"


def test_route_after_probe_hard_failure_via_report_fallback() -> None:
    state: WorkflowState = {"probe_report": _probe_report("hard_failure")}
    assert route_after_probe(state) == "hard_failure"


def test_route_after_probe_success_when_status_running_and_report_success() -> None:
    state: WorkflowState = {
        "workflow_status": cast("WorkflowStatus", "running"),
        "probe_report": _probe_report("success"),
    }
    assert route_after_probe(state) == "success"


def test_route_after_probe_success_when_state_empty() -> None:
    assert route_after_probe(cast("WorkflowState", {})) == "success"


# ---------------------------------------------------------------------------
# route_after_hitl
# ---------------------------------------------------------------------------


def _hitl_decision(approved: bool) -> HitlDecision:
    return cast("HitlDecision", {"approved": approved, "user_notes": None})


def test_route_after_hitl_rejected_via_workflow_status() -> None:
    state: WorkflowState = {"workflow_status": cast("WorkflowStatus", "rejected")}
    assert route_after_hitl(state) == "rejected"


def test_route_after_hitl_rejected_via_decision_fallback() -> None:
    state: WorkflowState = {"hitl_decision": _hitl_decision(approved=False)}
    assert route_after_hitl(state) == "rejected"


def test_route_after_hitl_approved_when_decision_true() -> None:
    state: WorkflowState = {"hitl_decision": _hitl_decision(approved=True)}
    assert route_after_hitl(state) == "approved"


# ---------------------------------------------------------------------------
# Conditional-edge wiring: END reachable from data_probe and hitl
# ---------------------------------------------------------------------------


def test_graph_edges_include_end_branches_from_probe_and_hitl() -> None:
    drawable = build_graph().get_graph()
    edges_by_source: dict[str, set[str]] = {}
    for edge in drawable.edges:
        edges_by_source.setdefault(edge.source, set()).add(edge.target)

    # probe gate: both hitl and END reachable
    assert "hitl" in edges_by_source["data_probe"]
    assert END in edges_by_source["data_probe"]

    # hitl gate: both data_download and END reachable
    assert "data_download" in edges_by_source["hitl"]
    assert END in edges_by_source["hitl"]

    # regression is the terminal business node
    assert edges_by_source["regression"] == {END}
