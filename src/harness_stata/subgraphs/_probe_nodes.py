"""Node implementations for the batch probe subgraph (字段发现阶段 1~4)。

4 个节点(planning_agent / bulk_schema / verification / fallback_react)
设计要点:

- 节点函数签名统一为 ``(state, cfg) -> dict``,工厂层用 ``functools.partial`` 绑定 cfg
- ``ProbeNodeConfig`` 为 frozen dataclass,工具/prompt/预算配置不可变
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool

from harness_stata.clients.llm import get_chat_model
from harness_stata.state import VariableDefinition
from harness_stata.subgraphs._probe_helpers import (
    PendingValidation,
    SubstituteMeta,
    VariableProbeFindingModel,
    build_not_found_result,
    ensure_manifest,
    ensure_report,
    maybe_build_substitute,
)
from harness_stata.subgraphs._probe_pipeline import (
    VERIFICATION_OUTPUT_SPEC,
    BucketKey,
    BucketVerificationOutput,
    PlanningOutput,
    VariablePlan,
    bucket_plans,
    format_schema_for_prompt,
    merge_bucket_results,
    parse_bulk_schema_response,
)

if TYPE_CHECKING:
    from harness_stata.subgraphs.probe_subgraph import ProbeState


@dataclass(frozen=True)
class ProbeNodeConfig:
    """Immutable bundle of dependencies passed into every probe node."""

    planning_tools: Sequence[BaseTool]
    fallback_tools: Sequence[BaseTool]
    bulk_schema_tool: BaseTool
    probe_tool: BaseTool
    planning_system_prompt: str
    verification_prompt: str
    fallback_full_prompt: str
    planning_agent_max_calls: int
    fallback_react_max_calls: int


# ---------------------------------------------------------------------------
# Phase 1: Planning Agent — variables → (target_database, candidate_tables)
# ---------------------------------------------------------------------------


async def planning_agent(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    """Plan candidate tables; also performs round bookkeeping at entry.

    首轮(``pipeline_initialized`` 未置位):从 ``empirical_spec.variables`` 取队列。
    后续 substitute 重试:从 ``substitute_queue`` 取队列,``substitute_round`` 自增。
    队列空 → 直接返回 shaped report/manifest,路由层会落到 END。
    """
    spec = state["empirical_spec"]
    if not state.get("pipeline_initialized"):
        queue: list[VariableDefinition] = list(spec["variables"])
        substitute_round = 0
    else:
        queue = list(state.get("substitute_queue") or [])
        substitute_round = state.get("substitute_round", 0) + 1

    if not queue:
        return {
            "pipeline_initialized": True,
            "substitute_round": substitute_round,
            "pending_variables": [],
            "planning_queue": [],
            "substitute_queue": [],
            "plans": [],
            "schema_dict": {},
            "pending_hard_fallbacks": [],
            "probe_report": ensure_report(state.get("probe_report")),
            "download_manifest": ensure_manifest(state.get("download_manifest")),
        }

    db_block = state.get("available_databases", "")
    var_lines = [
        f"- name=`{v['name']}`, contract={v['contract_type']}, role={v['role']},"
        f" description={v['description']}"
        for v in queue
    ]
    human = HumanMessage(
        content=(
            f"Variables awaiting candidate-table planning ({len(queue)} total):\n"
            + "\n".join(var_lines)
            + f"\n\nSample scope: {spec['sample_scope']}"
            + f"\nTime range: {spec['time_range_start']} to {spec['time_range_end']}"
            + f"\nData frequency: {spec['data_frequency']}"
            + f"\n\nPurchased databases:\n{db_block}"
        )
    )
    agent = create_agent(
        model=get_chat_model(),
        tools=list(cfg.planning_tools),
        system_prompt=cfg.planning_system_prompt,
        middleware=[
            ToolCallLimitMiddleware(
                run_limit=cfg.planning_agent_max_calls,
                exit_behavior="end",
            ),
        ],
        response_format=ToolStrategy(PlanningOutput),
    )
    result: dict[str, Any] = await agent.ainvoke({"messages": [human]})
    planning = result.get("structured_response")
    plans: list[VariablePlan] = list(planning.plans) if isinstance(planning, PlanningOutput) else []
    return {
        "pipeline_initialized": True,
        "substitute_round": substitute_round,
        "pending_variables": queue,
        "planning_queue": queue,
        "plans": plans,
        "schema_dict": {},
        "pending_hard_fallbacks": [],
        "substitute_queue": [],
        "messages": result.get("messages", []),
    }


# ---------------------------------------------------------------------------
# Phase 2: bulk_schema — 拉回所有候选表的 schema
# ---------------------------------------------------------------------------


async def bulk_schema_phase(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    plans = list(state.get("plans") or [])
    candidates: list[str] = []
    seen: set[str] = set()
    for plan in plans:
        for code in plan.candidate_table_codes:
            if code and code not in seen:
                seen.add(code)
                candidates.append(code)
    if not candidates:
        return {"schema_dict": {}}
    try:
        msg: Any = await cfg.bulk_schema_tool.ainvoke(
            {
                "name": cfg.bulk_schema_tool.name,
                "args": {"table_codes": candidates},
                "id": "probe-bulk-schema",
                "type": "tool_call",
            }
        )
    except Exception:
        return {"schema_dict": {}}
    artifact = getattr(msg, "artifact", None)
    payload: object = None
    if isinstance(artifact, dict) and "structured_content" in artifact:
        payload = artifact["structured_content"]
    result = parse_bulk_schema_response(payload)
    return {"schema_dict": result.schema_dict}


# ---------------------------------------------------------------------------
# Phase 3: Verification — 分桶 structured-output 单桶判定
# ---------------------------------------------------------------------------


async def verification_phase(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    plans = list(state.get("plans") or [])
    planning_queue = list(state.get("planning_queue") or [])
    schema_dict = dict(state.get("schema_dict") or {})
    validation_queue = list(state.get("validation_queue") or [])
    sub_meta = dict(state.get("substitute_meta") or {})
    report = ensure_report(state.get("probe_report"))
    manifest = ensure_manifest(state.get("download_manifest"))

    if not planning_queue:
        return {
            "validation_queue": validation_queue,
            "pending_hard_fallbacks": [],
            "probe_report": report,
            "download_manifest": manifest,
            "substitute_meta": sub_meta,
        }

    variables_by_name = {v["name"]: v for v in planning_queue}
    buckets, unplanned = bucket_plans(plans, variables_by_name, schema_dict)

    # 兜住:planning_queue 里有但 plans 完全没列出的变量(planning agent 漏掉)
    unplanned_names = {v["name"] for v in unplanned}
    planned_names = {plan.variable_name for plan in plans}
    for name, var in variables_by_name.items():
        if name not in planned_names and name not in unplanned_names:
            unplanned.append(var)
            unplanned_names.add(name)

    bucket_outputs = await _run_verification_buckets(buckets, schema_dict, cfg)
    merged = merge_bucket_results(bucket_outputs, planning_queue, schema_dict)

    merged_names = {v["name"] for v, _ in merged}
    unplanned_findings: list[tuple[VariableDefinition, VariableProbeFindingModel]] = [
        (v, VariableProbeFindingModel(status="not_found"))
        for v in unplanned
        if v["name"] not in merged_names
    ]
    all_findings = merged + unplanned_findings

    pending_hard_fallbacks: list[VariableDefinition] = []
    substitute_queue_now = list(state.get("substitute_queue") or [])
    sub_queue_names = {v["name"] for v in substitute_queue_now}

    for var, finding in all_findings:
        if finding.status == "found":
            validation_queue.append(
                PendingValidation(
                    variable=var,
                    finding=finding,
                    is_substitute_task=var["name"] in sub_meta,
                )
            )
            continue
        if var["contract_type"] == "hard":
            pending_hard_fallbacks.append(var)
            continue
        # soft not_found
        if var["name"] in sub_meta:
            # 上一轮的 substitute,本轮再 not_found → 终止链路,记原变量名
            meta = sub_meta.pop(var["name"])
            report["variable_results"].append(build_not_found_result(meta["original_name"]))
            continue
        cand = maybe_build_substitute(finding, var)
        if cand is None or cand["name"] in sub_queue_names:
            report["variable_results"].append(build_not_found_result(var["name"]))
            continue
        substitute_queue_now.append(cand)
        sub_queue_names.add(cand["name"])
        sub_meta[cand["name"]] = SubstituteMeta(
            original_name=var["name"],
            reason=finding.candidate_substitute_reason or "",
        )

    return {
        "validation_queue": validation_queue,
        "pending_hard_fallbacks": pending_hard_fallbacks,
        "probe_report": report,
        "download_manifest": manifest,
        "substitute_meta": sub_meta,
        "substitute_queue": substitute_queue_now,
    }


async def _run_verification_buckets(
    buckets: dict[BucketKey, list[VariableDefinition]],
    schema_dict: dict[str, list[dict[str, Any]]],
    cfg: ProbeNodeConfig,
) -> list[tuple[BucketKey, BucketVerificationOutput]]:
    chat = get_chat_model().with_structured_output(
        BucketVerificationOutput, method="function_calling"
    )
    bucket_outputs: list[tuple[BucketKey, BucketVerificationOutput]] = []
    for bucket_key, vars_in_bucket in buckets.items():
        schema_block = format_schema_for_prompt(
            bucket_key.table, schema_dict.get(bucket_key.table, [])
        )
        var_lines = [
            f"- name=`{v['name']}`, contract={v['contract_type']},"
            f" role={v['role']}, description={v['description']}"
            for v in vars_in_bucket
        ]
        human = HumanMessage(
            content=(
                f"{cfg.verification_prompt}\n\n---\n\n{VERIFICATION_OUTPUT_SPEC}\n\n"
                f"Bucket: database=`{bucket_key.database}`, table=`{bucket_key.table}`\n\n"
                f"{schema_block}\n\n"
                f"Variables to judge ({len(vars_in_bucket)} total):\n" + "\n".join(var_lines)
            )
        )
        try:
            raw_out: Any = await chat.ainvoke([human])
        except Exception:
            raw_out = BucketVerificationOutput(results=[])
        if isinstance(raw_out, BucketVerificationOutput):
            bucket_outputs.append((bucket_key, raw_out))
        else:
            bucket_outputs.append((bucket_key, BucketVerificationOutput(results=[])))
    return bucket_outputs


# ---------------------------------------------------------------------------
# Phase 4: Fallback — 单变量 ReAct 兜底 (hard not_found)
# ---------------------------------------------------------------------------


async def fallback_react_phase(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    fallbacks = list(state.get("pending_hard_fallbacks") or [])
    if not fallbacks:
        return {"pending_hard_fallbacks": []}

    spec = state["empirical_spec"]
    db_block = state.get("available_databases", "")
    validation_queue = list(state.get("validation_queue") or [])
    report = ensure_report(state.get("probe_report"))
    manifest = ensure_manifest(state.get("download_manifest"))
    sub_meta = dict(state.get("substitute_meta") or {})

    for var in fallbacks:
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
            tools=list(cfg.fallback_tools),
            system_prompt=cfg.fallback_full_prompt,
            middleware=[
                ToolCallLimitMiddleware(
                    run_limit=cfg.fallback_react_max_calls,
                    exit_behavior="end",
                ),
            ],
            response_format=ToolStrategy(VariableProbeFindingModel),
        )
        result: dict[str, Any] = await agent.ainvoke({"messages": [human]})
        finding = result.get("structured_response")
        if not isinstance(finding, VariableProbeFindingModel):
            finding = VariableProbeFindingModel(status="not_found")

        if finding.status == "found":
            validation_queue.append(
                PendingValidation(
                    variable=var,
                    finding=finding,
                    is_substitute_task=var["name"] in sub_meta,
                )
            )
            continue

        # hard not_found 兜底 → 整体硬失败
        report["variable_results"].append(build_not_found_result(var["name"]))
        report["overall_status"] = "hard_failure"
        report["failure_reason"] = (
            f"Hard contract variable '{var['name']}' not found in CSMAR (fallback)"
        )
        return {
            "validation_queue": [],
            "pending_hard_fallbacks": [],
            "probe_report": report,
            "download_manifest": manifest,
            "substitute_meta": sub_meta,
            "workflow_status": "failed_hard_contract",
        }

    return {
        "validation_queue": validation_queue,
        "pending_hard_fallbacks": [],
        "probe_report": report,
        "download_manifest": manifest,
        "substitute_meta": sub_meta,
    }
