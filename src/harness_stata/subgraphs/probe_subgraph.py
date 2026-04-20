"""Probe subgraph factory.

Three-node topology: ``variable_dispatcher -> variable_react -> result_handler``
with ``per_variable_max_calls`` budget isolated per variable. Soft+not_found
appends a substitute ``VariableDefinition`` to the queue so the next dispatcher
round runs with an independent budget; hard+not_found flips overall_status to
``hard_failure`` and routes straight to END. The inner ReAct loop is hand-written
(not the generic_react factory) because csmar_mcp budget needs overwrite
semantics rather than ``operator.add`` reducer semantics.
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

# ---------------------------------------------------------------------------
# Extractor prompt and pydantic model (LLM structured-output schema)
# ---------------------------------------------------------------------------

_EXTRACTOR_PROMPT = """You are a structured-output extractor for a data-probe agent.

Input: a single variable's definition + the agent's exploration trace
(messages between the agent and the csmar tools).

Output the structured finding under these rules:

- status="found" requires non-null database, table, field. key_fields should
  contain the primary/time keys the agent saw in the table schema.
- status="not_found" leaves source / key_fields / filters as null.
- For soft variables that were not found, if the agent's final summary
  proposed a candidate substitute, copy the three fields verbatim into
  candidate_substitute_name / candidate_substitute_description /
  candidate_substitute_reason. Otherwise leave them null.
- Never invent data the agent did not produce. Set null when unsure.
- filters captures time/sample restrictions the agent confirmed (e.g.
  {"year": "2010-2020"}). Use empty/null if the agent did not state any.
"""


class _SubstituteMeta(TypedDict):
    """Bookkeeping for a substitute task enqueued for soft+not_found."""

    original_name: str
    reason: str


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


# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class ProbeState(TypedDict, total=False):
    """Internal state of the probe subgraph.

    Fields shared with the parent WorkflowState (read-in / write-back):
    ``empirical_spec``, ``model_plan``, ``probe_report``, ``download_manifest``,
    ``workflow_status``.

    Fields private to the subgraph (do not leak to the parent):
    ``variable_queue``, ``current_variable``, ``per_variable_call_count``,
    ``messages``, ``queue_initialized``, ``substitute_meta``.
    """

    empirical_spec: EmpiricalSpec
    model_plan: ModelPlan
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    workflow_status: WorkflowStatus
    variable_queue: list[VariableDefinition]
    current_variable: VariableDefinition | None
    per_variable_call_count: int
    messages: list[BaseMessage]
    queue_initialized: bool
    substitute_meta: dict[str, _SubstituteMeta]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


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
            updates["per_variable_call_count"] = 0
            updates["messages"] = []
        else:
            updates["current_variable"] = None
            updates["variable_queue"] = []
        return updates

    async def _variable_react(state: ProbeState) -> dict[str, Any]:
        """Run the inner ReAct loop for the current variable."""
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
            response = await model.ainvoke(messages)  # pyright: ignore[reportUnknownMemberType]
            assert isinstance(response, AIMessage)
            messages.append(response)
            if not response.tool_calls:
                break
            if call_count >= per_variable_max_calls:
                break
            for call in response.tool_calls:
                tool_obj = tools_by_name[call["name"]]
                output = await tool_obj.ainvoke(call["args"])  # pyright: ignore[reportUnknownMemberType]
                messages.append(ToolMessage(content=str(output), tool_call_id=call["id"] or ""))
            call_count += 1
        return {"messages": messages, "per_variable_call_count": call_count}

    async def _result_handler(state: ProbeState) -> dict[str, Any]:
        """Interpret the react trace; route Hard/Soft and update report/manifest."""
        report = _ensure_report(state.get("probe_report"))
        manifest = _ensure_manifest(state.get("download_manifest"))
        spec = state.get("empirical_spec")
        sub_meta = dict(state.get("substitute_meta") or {})
        queue = list(state.get("variable_queue") or [])

        current = state.get("current_variable")
        if current is None:
            return {"probe_report": report, "download_manifest": manifest}

        messages = state.get("messages") or []
        finding = await _extract_finding(messages, current)

        is_substitute_task = current["name"] in sub_meta

        if finding.status == "found":
            if is_substitute_task:
                meta = sub_meta.pop(current["name"])
                report["variable_results"].append(_build_substituted_result(meta, current, finding))
                if spec is not None:
                    spec = _replace_variable_in_spec(spec, meta["original_name"], current)
            else:
                report["variable_results"].append(_build_found_result(current, finding))
            _merge_into_manifest(manifest, current, finding)
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


async def _extract_finding(
    messages: list[BaseMessage], variable: VariableDefinition
) -> _VariableProbeFindingModel:
    """Run a structured-output extraction over the react trace."""
    user_msg = (
        f"Variable: name={variable['name']}, description={variable['description']}, "
        f"contract_type={variable['contract_type']}, role={variable['role']}\n\n"
        f"Agent exploration trace:\n{_format_trace(messages)}"
    )
    structured = get_chat_model().with_structured_output(_VariableProbeFindingModel)  # pyright: ignore[reportUnknownMemberType]
    result = await structured.ainvoke(  # pyright: ignore[reportUnknownMemberType]
        [SystemMessage(content=_EXTRACTOR_PROMPT), HumanMessage(content=user_msg)]
    )
    assert isinstance(result, _VariableProbeFindingModel)
    return result


def _format_trace(messages: list[BaseMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        content = str(m.content)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        if isinstance(m, HumanMessage):
            parts.append(f"[user] {content}")
        elif isinstance(m, AIMessage):
            if m.tool_calls:
                tc = "; ".join(f"{c['name']}({c['args']})" for c in m.tool_calls)
                parts.append(f"[assistant tool_calls={tc}] {content}")
            else:
                parts.append(f"[assistant] {content}")
        elif isinstance(m, ToolMessage):
            parts.append(f"[tool:{m.tool_call_id}] {content}")
    return "\n".join(parts)


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
) -> None:
    """Append a new DownloadTask or merge into an existing one by (database, table)."""
    database = finding.database or ""
    table = finding.table or ""
    field = finding.field or ""
    var_name = current["name"]
    key_fields = list(finding.key_fields or [])
    raw_filters = finding.filters or {}

    for item in manifest["items"]:
        if item["database"] == database and item["table"] == table:
            if field and field not in item["variable_fields"]:
                item["variable_fields"].append(field)
            if var_name and var_name not in item["variable_names"]:
                item["variable_names"].append(var_name)
            for kf in key_fields:
                if kf not in item["key_fields"]:
                    item["key_fields"].append(kf)
            for k, v in raw_filters.items():
                item["filters"][k] = v
            return

    filters_typed: dict[str, object] = {}
    for k, v in raw_filters.items():
        filters_typed[k] = v
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


def _replace_variable_in_spec(
    spec: EmpiricalSpec, original_name: str, substitute: VariableDefinition
) -> EmpiricalSpec:
    new_vars: list[VariableDefinition] = [
        substitute if v["name"] == original_name else v for v in spec["variables"]
    ]
    return EmpiricalSpec(
        topic=spec["topic"],
        variables=new_vars,
        sample_scope=spec["sample_scope"],
        time_range_start=spec["time_range_start"],
        time_range_end=spec["time_range_end"],
        data_frequency=spec["data_frequency"],
        analysis_granularity=spec["analysis_granularity"],
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
