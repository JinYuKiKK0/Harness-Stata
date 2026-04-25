"""Probe subgraph factory.

Three-node topology: ``variable_dispatcher -> variable_react -> result_handler``
with ``per_variable_max_calls`` budget isolated per variable. Soft+not_found
appends a substitute ``VariableDefinition`` to the queue so the next dispatcher
round runs with an independent budget; hard+not_found flips overall_status to
``hard_failure`` and routes straight to END.

The inner ReAct loop is built with :func:`langchain.agents.create_agent`, which
produces the :class:`_VariableProbeFindingModel` finding directly via its
``response_format`` — replacing the previous two-phase "ReAct trace + structured
extractor" pipeline that accounted for the :class:`AssertionError` documented in
``docs/bug.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, TypedDict

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from harness_stata.clients.llm import get_chat_model
from harness_stata.state import (
    DownloadManifest,
    DownloadTask,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    SubstitutionTrace,
    VariableDefinition,
    VariableProbeResult,
    VariableSource,
    WorkflowStatus,
)
from harness_stata.subgraphs._probe_helpers import (
    build_download_filters,
    replace_variable_in_model_plan,
    replace_variable_in_spec,
)

# ---------------------------------------------------------------------------
# Structured-output schema (used as create_agent response_format)
# ---------------------------------------------------------------------------

_OUTPUT_SPEC = """你的探测结论必须直接按给定 schema 的字段填写,不要输出自然语言总结。字段规则:

- status="found" 要求 database / table / field 三字段非空;key_fields 填写主键/时间键列名。
- status="not_found" 时 source/key_fields/filters 保持 null 或空。
- soft 变量若没找到,但你在探测中发现了合理的替代变量,填写
  candidate_substitute_name / candidate_substitute_description / candidate_substitute_reason;
  否则三者留空。hard 变量不要填 substitute 字段。
- filters 不要写时间范围;运行时会从 EmpiricalSpec.time_range_start/end
  自动生成 start_date/end_date。若 CSMAR 需要额外样本筛选,只允许填写
  {"condition": "..."}。
- 不要编造探测未覆盖的信息;不确定就留 null。
"""


class _VariableProbeFindingModel(BaseModel):
    """LLM-facing structured-output schema for one variable's probe finding."""

    status: Literal["found", "not_found"] = Field(
        description="found if the agent located a usable data source, otherwise not_found"
    )
    database: str | None = Field(default=None, description="Source database name (found only)")
    table: str | None = Field(default=None, description="Source table name (found only)")
    field: str | None = Field(default=None, description="Variable column name (found only)")
    record_count: int | None = Field(
        default=None, description="Record count if reported by the agent"
    )
    key_fields: list[str] | None = Field(
        default=None, description="Primary/time key columns for the source table"
    )
    filters: dict[str, str] | None = Field(
        default=None, description="Confirmed time/sample filters keyed by column"
    )
    candidate_substitute_name: str | None = Field(
        default=None, description="Soft+not_found only: candidate substitute variable name"
    )
    candidate_substitute_description: str | None = Field(
        default=None, description="Soft+not_found only: candidate substitute description"
    )
    candidate_substitute_reason: str | None = Field(
        default=None, description="Soft+not_found only: why this substitute fits"
    )


class _SubstituteMeta(TypedDict):
    """Bookkeeping for a substitute task enqueued for soft+not_found."""

    original_name: str
    reason: str


# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class ProbeState(TypedDict, total=False):
    """Internal state of the probe subgraph.

    Fields shared with the parent WorkflowState (read-in / write-back):
    ``empirical_spec``, ``model_plan``, ``probe_report``, ``download_manifest``,
    ``workflow_status``.

    Fields private to the subgraph (do not leak to the parent):
    ``variable_queue``, ``current_variable``, ``messages``, ``queue_initialized``,
    ``substitute_meta``, ``available_databases``, ``variable_finding``.
    """

    empirical_spec: EmpiricalSpec
    model_plan: ModelPlan
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    workflow_status: WorkflowStatus
    variable_queue: list[VariableDefinition]
    current_variable: VariableDefinition | None
    messages: list[BaseMessage]
    queue_initialized: bool
    substitute_meta: dict[str, _SubstituteMeta]
    available_databases: str
    variable_finding: _VariableProbeFindingModel | None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_probe_subgraph(
    tools: Sequence[BaseTool],
    prompt: str,
    per_variable_max_calls: int,
) -> CompiledStateGraph[ProbeState, ProbeState, ProbeState, ProbeState]:
    """Build a compiled probe subgraph bound to ``tools`` and ``prompt``.

    ``per_variable_max_calls`` caps the number of tool calls the inner agent may
    make for a single variable (enforced by :class:`ToolCallLimitMiddleware`).
    """
    if not tools:
        raise ValueError("tools must not be empty")
    if per_variable_max_calls < 1:
        raise ValueError("per_variable_max_calls must be >= 1")

    bound_tools: list[BaseTool] = list(tools)
    system_prompt = f"{prompt}\n\n---\n\n{_OUTPUT_SPEC}"

    def _variable_dispatcher(state: ProbeState) -> dict[str, Any]:
        """Pop the next variable off the queue and reset per-variable state."""
        if state.get("queue_initialized"):
            queue = list(state.get("variable_queue") or [])
        else:
            spec = state["empirical_spec"]  # type: ignore[reportTypedDictNotRequiredAccess]
            queue = list(spec["variables"])

        updates: dict[str, Any] = {"queue_initialized": True}
        if queue:
            updates["current_variable"] = queue[0]
            updates["variable_queue"] = queue[1:]
            updates["messages"] = []
            updates["variable_finding"] = None
        else:
            updates["current_variable"] = None
            updates["variable_queue"] = []
        return updates

    async def _variable_react(state: ProbeState) -> dict[str, Any]:
        """Run the inner agent for the current variable via create_agent."""
        var = state.get("current_variable")
        if var is None:
            return {}

        db_block = state.get("available_databases", "")
        spec = state["empirical_spec"]  # type: ignore[reportTypedDictNotRequiredAccess]
        human = HumanMessage(
            content=(
                f"Variable: {var['name']} - {var['description']} "
                f"(contract: {var['contract_type']}, role: {var['role']})\n\n"
                f"Sample scope: {spec['sample_scope']}\n"
                f"Time range: {spec['time_range_start']} to {spec['time_range_end']}\n"
                f"Data frequency: {spec['data_frequency']}\n\n"
                f"Purchased databases:\n{db_block}"
            )
        )
        agent = create_agent(
            model=get_chat_model(),
            tools=bound_tools,  # type: ignore[arg-type]
            system_prompt=system_prompt,
            middleware=[
                ToolCallLimitMiddleware(
                    run_limit=per_variable_max_calls,
                    exit_behavior="end",
                ),
            ],
            response_format=ToolStrategy(_VariableProbeFindingModel),
        )
        result: dict[str, Any] = await agent.ainvoke({"messages": [human]})  # type: ignore[reportUnknownMemberType]
        finding = result.get("structured_response")
        messages = result.get("messages", [])
        return {"messages": messages, "variable_finding": finding}

    def _result_handler(state: ProbeState) -> dict[str, Any]:
        """Interpret the finding; route Hard/Soft and update report/manifest."""
        report = _ensure_report(state.get("probe_report"))
        manifest = _ensure_manifest(state.get("download_manifest"))
        spec = state.get("empirical_spec")
        sub_meta = dict(state.get("substitute_meta") or {})
        queue = list(state.get("variable_queue") or [])

        current = state.get("current_variable")
        if current is None:
            return {"probe_report": report, "download_manifest": manifest}

        finding = state.get("variable_finding")
        if finding is None:
            finding = _VariableProbeFindingModel(status="not_found")

        is_substitute_task = current["name"] in sub_meta

        if finding.status == "found":
            if is_substitute_task:
                meta = sub_meta.pop(current["name"])
                report["variable_results"].append(_build_substituted_result(meta, current, finding))
                if spec is not None:
                    spec = replace_variable_in_spec(spec, meta["original_name"], current)
            else:
                report["variable_results"].append(_build_found_result(current, finding))
            if spec is None:
                raise RuntimeError("probe_subgraph: empirical_spec is missing")
            _merge_into_manifest(manifest, current, finding, spec)
        elif current["contract_type"] == "hard":
            report["variable_results"].append(_build_not_found_result(current["name"]))
            report["overall_status"] = "hard_failure"
            report["failure_reason"] = (
                f"Hard contract variable '{current['name']}' not found in CSMAR"
            )
            updates: dict[str, Any] = {
                "probe_report": report,
                "download_manifest": manifest,
                "workflow_status": "failed_hard_contract",
                "substitute_meta": sub_meta,
                "variable_queue": queue,
            }
            if spec is not None:
                updates["empirical_spec"] = spec
            return updates
        elif is_substitute_task:
            meta = sub_meta.pop(current["name"])
            report["variable_results"].append(_build_not_found_result(meta["original_name"]))
        else:
            cand = _maybe_build_substitute(finding, current)
            if cand is not None:
                queue.append(cand)
                sub_meta[cand["name"]] = _SubstituteMeta(
                    original_name=current["name"],
                    reason=finding.candidate_substitute_reason or "",
                )
            else:
                report["variable_results"].append(_build_not_found_result(current["name"]))

        if not queue:
            report["overall_status"] = "success"
            report["failure_reason"] = None

        out: dict[str, Any] = {
            "probe_report": report,
            "download_manifest": manifest,
            "variable_queue": queue,
            "substitute_meta": sub_meta,
        }
        if spec is not None:
            out["empirical_spec"] = spec
        plan = state.get("model_plan")
        if plan is not None:
            out["model_plan"] = replace_variable_in_model_plan(plan, report["variable_results"])
        return out

    def _route_after_handler(
        state: ProbeState,
    ) -> Literal["variable_dispatcher", "__end__"]:
        report = state.get("probe_report")
        if report is not None and report.get("overall_status") == "hard_failure":
            return "__end__"
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


# ---------------------------------------------------------------------------
# Helpers (module-level so they can be unit-tested if needed)
# ---------------------------------------------------------------------------


def _ensure_report(existing: ProbeReport | None) -> ProbeReport:
    if existing is None:
        return ProbeReport(variable_results=[], overall_status="success", failure_reason=None)
    return ProbeReport(
        variable_results=list(existing["variable_results"]),
        overall_status=existing["overall_status"],
        failure_reason=existing["failure_reason"],
    )


def _ensure_manifest(existing: DownloadManifest | None) -> DownloadManifest:
    if existing is None:
        return DownloadManifest(items=[])
    items: list[DownloadTask] = [
        DownloadTask(
            database=item["database"],
            table=item["table"],
            key_fields=list(item["key_fields"]),
            variable_fields=list(item["variable_fields"]),
            variable_names=list(item["variable_names"]),
            filters=dict(item["filters"]),
        )
        for item in existing["items"]
    ]
    return DownloadManifest(items=items)


def _build_found_result(
    var: VariableDefinition, finding: _VariableProbeFindingModel
) -> VariableProbeResult:
    return VariableProbeResult(
        variable_name=var["name"],
        status="found",
        source=VariableSource(
            database=finding.database or "",
            table=finding.table or "",
            field=finding.field or "",
        ),
        record_count=finding.record_count,
        substitution_trace=None,
    )


def _build_substituted_result(
    meta: _SubstituteMeta, sub_var: VariableDefinition, finding: _VariableProbeFindingModel
) -> VariableProbeResult:
    return VariableProbeResult(
        variable_name=meta["original_name"],
        status="substituted",
        source=VariableSource(
            database=finding.database or "",
            table=finding.table or "",
            field=finding.field or "",
        ),
        record_count=finding.record_count,
        substitution_trace=SubstitutionTrace(
            original=meta["original_name"],
            reason=meta["reason"],
            substitute=sub_var["name"],
            substitute_description=sub_var["description"],
        ),
    )


def _build_not_found_result(variable_name: str) -> VariableProbeResult:
    return VariableProbeResult(
        variable_name=variable_name,
        status="not_found",
        source=None,
        record_count=None,
        substitution_trace=None,
    )


def _merge_into_manifest(
    manifest: DownloadManifest,
    current: VariableDefinition,
    finding: _VariableProbeFindingModel,
    spec: EmpiricalSpec,
) -> None:
    """Append a new DownloadTask or merge into an existing one by (database, table)."""
    database = finding.database or ""
    table = finding.table or ""
    field = finding.field or ""
    var_name = current["name"]
    key_fields = list(finding.key_fields or [])
    filters_typed = build_download_filters(spec, finding.filters)

    for item in manifest["items"]:
        if item["database"] == database and item["table"] == table:
            if field and field not in item["variable_fields"]:
                item["variable_fields"].append(field)
            if var_name and var_name not in item["variable_names"]:
                item["variable_names"].append(var_name)
            for kf in key_fields:
                if kf not in item["key_fields"]:
                    item["key_fields"].append(kf)
            for k, v in filters_typed.items():
                item["filters"][k] = v
            return

    manifest["items"].append(
        DownloadTask(
            database=database,
            table=table,
            key_fields=key_fields,
            variable_fields=[field] if field else [],
            variable_names=[var_name] if var_name else [],
            filters=filters_typed,
        )
    )


def _maybe_build_substitute(
    finding: _VariableProbeFindingModel, current: VariableDefinition
) -> VariableDefinition | None:
    if not (finding.candidate_substitute_name and finding.candidate_substitute_description):
        return None
    return VariableDefinition(
        name=finding.candidate_substitute_name,
        description=finding.candidate_substitute_description,
        contract_type="soft",
        role=current["role"],
    )
