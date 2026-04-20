"""Data probe node — third node in the workflow.

Pure-code wrapper around :func:`build_probe_subgraph`. Binds csmar-mcp tools via
``get_csmar_tools()`` (async contextmanager) and maps ``WorkflowState`` into the
subgraph's ``ProbeState``, then lifts the subgraph's final state back out.

Node and the inner ReAct loop are fully async: the compiled subgraph is invoked
via ``await subgraph.ainvoke(...)`` so MCP stdio IO never blocks the event loop
(required by ``langgraph dev`` blockbuster detection and LangGraph deployments).

Hard failure is not raised at this layer. When the subgraph flips
``workflow_status`` to ``"failed_hard_contract"``, the node simply passes it
through; the main graph's conditional edge after this node is responsible for
routing to END.
"""

from __future__ import annotations

from typing import Literal, TypedDict, cast

from harness_stata.clients.csmar import get_csmar_tools
from harness_stata.config import get_settings
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    WorkflowState,
)
from harness_stata.subgraphs.probe_subgraph import ProbeState, build_probe_subgraph

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(state: WorkflowState) -> str | None:
    spec = state.get("empirical_spec")
    if spec is None:
        return "state.empirical_spec is missing"
    if not spec.get("variables"):
        return "empirical_spec.variables must be a non-empty list"
    if state.get("model_plan") is None:
        return "state.model_plan is missing"
    return None


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


class DataProbeOutput(TypedDict, total=False):
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    empirical_spec: EmpiricalSpec
    workflow_status: Literal["failed_hard_contract"]


async def data_probe(state: WorkflowState) -> DataProbeOutput:
    """Probe variable availability in CSMAR; emit probe_report + download_manifest."""
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]  # type: ignore[reportTypedDictNotRequiredAccess]
    model_plan: ModelPlan = state["model_plan"]  # type: ignore[reportTypedDictNotRequiredAccess]
    settings = get_settings()

    async with get_csmar_tools() as tools:
        subgraph = build_probe_subgraph(
            tools=tools,
            prompt=load_prompt("data_probe"),
            per_variable_max_calls=settings.per_variable_max_calls,
        )
        initial: ProbeState = {
            "empirical_spec": spec,
            "model_plan": model_plan,
        }
        raw_final = await subgraph.ainvoke(initial)  # pyright: ignore[reportUnknownMemberType]
        final = cast("ProbeState", raw_final)

    result: DataProbeOutput = {
        "probe_report": final["probe_report"],  # type: ignore[reportTypedDictNotRequiredAccess]
        "download_manifest": final["download_manifest"],  # type: ignore[reportTypedDictNotRequiredAccess]
    }
    # soft-substitute 成功时子图会重建 empirical_spec,此处仅在确实变更时回传
    final_spec = final.get("empirical_spec")
    if final_spec is not None and final_spec is not spec:
        result["empirical_spec"] = final_spec
    if final.get("workflow_status") == "failed_hard_contract":
        result["workflow_status"] = "failed_hard_contract"
    return result
