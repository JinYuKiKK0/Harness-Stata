"""Probe subgraph factory.

Five-node, two-phase topology::

    variable_dispatcher -> variable_react -> field_existence_handler
                                                     │
                                ┌── discovery_queue 非空 ─┤
                                │                        │
                                ▼                        │
                       (back to dispatcher)              │
                                                         │
                                discovery_queue 空 + validation_queue 非空
                                                         │
                                                         ▼
                                              coverage_validator
                                                         │
                                                         ▼
                                          coverage_validation_handler
                                                         │
                                  ┌── 触发 substitute 重新入 discovery_queue ─┤
                                  │                                          │
                                  ▼                                          │
                         (back to dispatcher)                                │
                                                                             │
                                  hard_failure / 全部完成 → END

阶段一(字段发现): Agent 读 schema 判断字段是否存在,只确认 (database, table,
field, key_fields) 与可选的 filters.condition;不再让 Agent 跑 ``probe_query``。

阶段二(覆盖率验证): 对 ``validation_queue`` 里每条候选,代码批量调用
``csmar_probe_query`` 取 ``can_materialize`` / ``invalid_columns``。通过则写
``probe_report`` + ``download_manifest``;失败则视作 ``not_found`` 走与字段未找
到完全一致的 hard/soft 路由(hard → ``failed_hard_contract`` 终止;soft 主任务
→ 触发 substitute 候选重入 discovery_queue;substitute 任务 → 链终止)。
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

from harness_stata.clients.llm import get_chat_model
from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    VariableDefinition,
    WorkflowStatus,
)
from harness_stata.subgraphs._probe_helpers import (
    OUTPUT_SPEC,
    CoverageEntry,
    CoverageOutcome,
    PendingValidation,
    SubstituteMeta,
    VariableProbeFindingModel,
    build_found_result,
    build_not_found_result,
    build_probe_query_payload,
    build_substituted_result,
    ensure_manifest,
    ensure_report,
    maybe_build_substitute,
    merge_into_manifest,
    replace_variable_in_model_plan,
    replace_variable_in_spec,
    run_probe_coverage,
)

# ---------------------------------------------------------------------------
# Subgraph state
# ---------------------------------------------------------------------------


class ProbeState(TypedDict, total=False):
    """Internal state of the probe subgraph.

    Slices shared with the parent ``WorkflowState`` (read-in / write-back):
    ``empirical_spec``, ``model_plan``, ``probe_report``, ``download_manifest``,
    ``workflow_status``.

    Slices private to this subgraph (do not leak to the parent graph):
    ``discovery_queue`` / ``validation_queue`` / ``coverage_outcomes``,
    ``current_variable`` / ``messages`` / ``queue_initialized`` /
    ``substitute_meta`` / ``available_databases`` / ``variable_finding``.
    """

    empirical_spec: EmpiricalSpec
    model_plan: ModelPlan
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    workflow_status: WorkflowStatus
    discovery_queue: list[VariableDefinition]
    validation_queue: list[PendingValidation]
    coverage_outcomes: list[CoverageEntry]
    current_variable: VariableDefinition | None
    messages: list[BaseMessage]
    queue_initialized: bool
    substitute_meta: dict[str, SubstituteMeta]
    available_databases: str
    variable_finding: VariableProbeFindingModel | None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_probe_subgraph(
    tools: Sequence[BaseTool],
    probe_tool: BaseTool,
    prompt: str,
    per_variable_max_calls: int,
) -> CompiledStateGraph[ProbeState, ProbeState, ProbeState, ProbeState]:
    """Build a compiled probe subgraph wired to ``tools`` (Agent) + ``probe_tool``.

    ``tools`` 是子图 Agent 在字段发现阶段可用的 LangChain 工具(白名单由调用方
    在 ``data_probe`` 节点决定)。``probe_tool`` 是 ``csmar_probe_query`` 工具,
    专供覆盖率验证阶段调用,**不会**绑定给 Agent。``per_variable_max_calls``
    限制 Agent 一轮内的工具调用次数(:class:`ToolCallLimitMiddleware`)。
    """
    if not tools:
        raise ValueError("tools must not be empty")
    if per_variable_max_calls < 1:
        raise ValueError("per_variable_max_calls must be >= 1")

    bound_tools: list[BaseTool] = list(tools)
    system_prompt = f"{prompt}\n\n---\n\n{OUTPUT_SPEC}"

    def _variable_dispatcher(state: ProbeState) -> dict[str, Any]:
        """Pop the next variable off the discovery queue and reset per-variable state."""
        if state.get("queue_initialized"):
            queue = list(state.get("discovery_queue") or [])
        else:
            spec = state["empirical_spec"]  # type: ignore[reportTypedDictNotRequiredAccess]
            queue = list(spec["variables"])

        updates: dict[str, Any] = {"queue_initialized": True}
        if queue:
            updates["current_variable"] = queue[0]
            updates["discovery_queue"] = queue[1:]
            updates["messages"] = []
            updates["variable_finding"] = None
        else:
            updates["current_variable"] = None
            updates["discovery_queue"] = []
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
            response_format=ToolStrategy(VariableProbeFindingModel),
        )
        result: dict[str, Any] = await agent.ainvoke({"messages": [human]})  # type: ignore[reportUnknownMemberType]
        finding = result.get("structured_response")
        messages = result.get("messages", [])
        return {"messages": messages, "variable_finding": finding}

    def _field_existence_handler(state: ProbeState) -> dict[str, Any]:
        """Decide per-variable: push to validation_queue / hard fail / substitute / not_found.

        阶段一只判断字段是否存在,**不写 manifest**——manifest 推迟到覆盖率验证
        通过后由 ``_coverage_validation_handler`` 统一构造,避免回滚成本。
        """
        report = ensure_report(state.get("probe_report"))
        manifest = ensure_manifest(state.get("download_manifest"))
        sub_meta = dict(state.get("substitute_meta") or {})
        discovery_queue = list(state.get("discovery_queue") or [])
        validation_queue = list(state.get("validation_queue") or [])

        current = state.get("current_variable")
        if current is None:
            # 空变量集兜底:保证下游消费者拿到形状正确的 probe_report / download_manifest。
            return {"probe_report": report, "download_manifest": manifest}

        finding = state.get("variable_finding")
        if finding is None:
            finding = VariableProbeFindingModel(status="not_found")

        is_substitute_task = current["name"] in sub_meta

        if finding.status == "found":
            validation_queue.append(
                PendingValidation(
                    variable=current,
                    finding=finding,
                    is_substitute_task=is_substitute_task,
                )
            )
            return {
                "probe_report": report,
                "download_manifest": manifest,
                "discovery_queue": discovery_queue,
                "validation_queue": validation_queue,
                "substitute_meta": sub_meta,
            }

        # 字段未找到 — 与原状态机分支保持一致
        if current["contract_type"] == "hard":
            report["variable_results"].append(build_not_found_result(current["name"]))
            report["overall_status"] = "hard_failure"
            report["failure_reason"] = (
                f"Hard contract variable '{current['name']}' not found in CSMAR"
            )
            return {
                "probe_report": report,
                "download_manifest": manifest,
                "workflow_status": "failed_hard_contract",
                "substitute_meta": sub_meta,
                "discovery_queue": discovery_queue,
                "validation_queue": validation_queue,
            }

        if is_substitute_task:
            meta = sub_meta.pop(current["name"])
            report["variable_results"].append(build_not_found_result(meta["original_name"]))
        else:
            cand = maybe_build_substitute(finding, current)
            if cand is not None:
                discovery_queue.append(cand)
                sub_meta[cand["name"]] = SubstituteMeta(
                    original_name=current["name"],
                    reason=finding.candidate_substitute_reason or "",
                )
            else:
                report["variable_results"].append(build_not_found_result(current["name"]))

        return {
            "probe_report": report,
            "download_manifest": manifest,
            "discovery_queue": discovery_queue,
            "validation_queue": validation_queue,
            "substitute_meta": sub_meta,
        }

    async def _coverage_validator(state: ProbeState) -> dict[str, Any]:
        """Batch-invoke csmar_probe_query for every pending field-level finding.

        每条 PendingValidation 独立解码为 :class:`CoverageOutcome`;调用失败也只是
        outcome 标记 ``can_materialize=False``,不抛异常。本节点本身始终成功,
        路由由 ``_coverage_validation_handler`` 决定。
        """
        validation_queue = list(state.get("validation_queue") or [])
        if not validation_queue:
            return {"coverage_outcomes": []}

        spec = state.get("empirical_spec")
        if spec is None:
            raise RuntimeError("probe_subgraph: empirical_spec is missing during coverage check")

        outcomes: list[CoverageEntry] = []
        for pending in validation_queue:
            payload = build_probe_query_payload(spec, pending["finding"])
            ctx = (
                f"coverage check for variable '{pending['variable']['name']}'"
                f" on table {pending['finding'].table!r}"
            )
            outcome = await run_probe_coverage(probe_tool, payload, ctx)
            outcomes.append(CoverageEntry(pending=pending, outcome=outcome))
        return {"coverage_outcomes": outcomes}

    def _coverage_validation_handler(state: ProbeState) -> dict[str, Any]:
        """Process coverage outcomes: write report/manifest on pass, route fail as not_found."""
        report = ensure_report(state.get("probe_report"))
        manifest = ensure_manifest(state.get("download_manifest"))
        spec = state.get("empirical_spec")
        plan = state.get("model_plan")
        sub_meta = dict(state.get("substitute_meta") or {})
        discovery_queue = list(state.get("discovery_queue") or [])
        outcomes = list(state.get("coverage_outcomes") or [])

        for entry in outcomes:
            pending = entry["pending"]
            outcome = entry["outcome"]
            current = pending["variable"]
            finding = pending["finding"]
            is_substitute_task = pending["is_substitute_task"]

            if outcome["can_materialize"]:
                spec = _absorb_passing_outcome(
                    report=report,
                    manifest=manifest,
                    spec=spec,
                    sub_meta=sub_meta,
                    current=current,
                    finding=finding,
                    outcome=outcome,
                    is_substitute_task=is_substitute_task,
                )
                continue

            # Coverage failed → 等同 not_found,沿用 hard/soft 路由
            if current["contract_type"] == "hard":
                report["variable_results"].append(build_not_found_result(current["name"]))
                report["overall_status"] = "hard_failure"
                report["failure_reason"] = _format_coverage_failure(current["name"], outcome)
                out: dict[str, Any] = {
                    "probe_report": report,
                    "download_manifest": manifest,
                    "workflow_status": "failed_hard_contract",
                    "substitute_meta": sub_meta,
                    "discovery_queue": discovery_queue,
                    "validation_queue": [],
                    "coverage_outcomes": [],
                }
                if spec is not None:
                    out["empirical_spec"] = spec
                return out

            if is_substitute_task:
                meta = sub_meta.pop(current["name"])
                report["variable_results"].append(build_not_found_result(meta["original_name"]))
                continue

            cand = maybe_build_substitute(finding, current)
            if cand is not None:
                discovery_queue.append(cand)
                sub_meta[cand["name"]] = SubstituteMeta(
                    original_name=current["name"],
                    reason=finding.candidate_substitute_reason or "",
                )
            else:
                report["variable_results"].append(build_not_found_result(current["name"]))

        if not discovery_queue and report["overall_status"] != "hard_failure":
            report["overall_status"] = "success"
            report["failure_reason"] = None

        out_final: dict[str, Any] = {
            "probe_report": report,
            "download_manifest": manifest,
            "discovery_queue": discovery_queue,
            "validation_queue": [],
            "coverage_outcomes": [],
            "substitute_meta": sub_meta,
        }
        if spec is not None:
            out_final["empirical_spec"] = spec
        if plan is not None:
            out_final["model_plan"] = replace_variable_in_model_plan(
                plan, report["variable_results"]
            )
        return out_final

    def _route_after_field_existence(
        state: ProbeState,
    ) -> Literal["variable_dispatcher", "coverage_validator", "__end__"]:
        report = state.get("probe_report")
        if report is not None and report.get("overall_status") == "hard_failure":
            return "__end__"
        if state.get("discovery_queue"):
            return "variable_dispatcher"
        if state.get("validation_queue"):
            return "coverage_validator"
        return "__end__"

    def _route_after_coverage_handler(
        state: ProbeState,
    ) -> Literal["variable_dispatcher", "__end__"]:
        report = state.get("probe_report")
        if report is not None and report.get("overall_status") == "hard_failure":
            return "__end__"
        if state.get("discovery_queue"):
            return "variable_dispatcher"
        return "__end__"

    graph: StateGraph[ProbeState, ProbeState, ProbeState, ProbeState] = StateGraph(ProbeState)
    graph.add_node("variable_dispatcher", _variable_dispatcher)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("variable_react", _variable_react)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("field_existence_handler", _field_existence_handler)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("coverage_validator", _coverage_validator)  # pyright: ignore[reportUnknownMemberType]
    graph.add_node("coverage_validation_handler", _coverage_validation_handler)  # pyright: ignore[reportUnknownMemberType]
    graph.add_edge(START, "variable_dispatcher")
    graph.add_edge("variable_dispatcher", "variable_react")
    graph.add_edge("variable_react", "field_existence_handler")
    graph.add_conditional_edges(
        "field_existence_handler",
        _route_after_field_existence,
        {
            "variable_dispatcher": "variable_dispatcher",
            "coverage_validator": "coverage_validator",
            END: END,
        },
    )
    graph.add_edge("coverage_validator", "coverage_validation_handler")
    graph.add_conditional_edges(
        "coverage_validation_handler",
        _route_after_coverage_handler,
        {"variable_dispatcher": "variable_dispatcher", END: END},
    )
    return graph.compile()  # pyright: ignore[reportUnknownMemberType]


# ---------------------------------------------------------------------------
# Internal helpers (only used by the closure above; module-level for clarity)
# ---------------------------------------------------------------------------


def _absorb_passing_outcome(
    *,
    report: ProbeReport,
    manifest: DownloadManifest,
    spec: EmpiricalSpec | None,
    sub_meta: dict[str, SubstituteMeta],
    current: VariableDefinition,
    finding: VariableProbeFindingModel,
    outcome: CoverageOutcome,
    is_substitute_task: bool,
) -> EmpiricalSpec | None:
    """Write a passing outcome into report+manifest; return possibly-rewritten spec."""
    if spec is None:
        raise RuntimeError("probe_subgraph: empirical_spec is missing during coverage absorb")

    if is_substitute_task:
        meta = sub_meta.pop(current["name"])
        report["variable_results"].append(
            build_substituted_result(meta, current, finding, record_count=outcome["row_count"])
        )
        spec = replace_variable_in_spec(spec, meta["original_name"], current)
    else:
        report["variable_results"].append(
            build_found_result(current, finding, record_count=outcome["row_count"])
        )

    merge_into_manifest(manifest, current, finding, spec)
    return spec


def _format_coverage_failure(variable_name: str, outcome: CoverageOutcome) -> str:
    base = f"Hard contract variable '{variable_name}' coverage check failed: can_materialize=false"
    if outcome["invalid_columns"]:
        base = f"{base}, invalid_columns={outcome['invalid_columns']!r}"
    if outcome["failure_reason"]:
        base = f"{base}; {outcome['failure_reason']}"
    return base
