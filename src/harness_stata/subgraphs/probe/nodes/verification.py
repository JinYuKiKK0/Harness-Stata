"""Phase 3: Verification — 分桶 structured-output 单桶判定。"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage

from harness_stata.clients.llm import get_chat_model
from harness_stata.state import VariableDefinition
from harness_stata.subgraphs.probe.config import ProbeNodeConfig
from harness_stata.subgraphs.probe.pure import (
    BucketKey,
    PendingValidation,
    bucket_plans,
    build_not_found_result,
    ensure_manifest,
    ensure_report,
    format_schema_for_prompt,
    merge_bucket_results,
)
from harness_stata.subgraphs.probe.schemas import (
    BucketVerificationOutput,
    VariableProbeFindingModel,
)
from harness_stata.subgraphs.probe.state import ProbeState


async def verification_phase(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    plans = list(state.get("plans") or [])
    planning_queue = list(state.get("planning_queue") or [])
    schema_dict = dict(state.get("schema_dict") or {})
    validation_queue = list(state.get("validation_queue") or [])
    report = ensure_report(state.get("probe_report"))
    manifest = ensure_manifest(state.get("download_manifest"))

    if not planning_queue:
        return {
            "validation_queue": validation_queue,
            "pending_hard_fallbacks": [],
            "probe_report": report,
            "download_manifest": manifest,
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

    for var, finding in all_findings:
        if finding.status == "found":
            validation_queue.append(PendingValidation(variable=var, finding=finding))
            continue
        if var["contract_type"] == "hard":
            pending_hard_fallbacks.append(var)
            continue
        # soft not_found → 直接记录
        report["variable_results"].append(build_not_found_result(var["name"]))

    return {
        "validation_queue": validation_queue,
        "pending_hard_fallbacks": pending_hard_fallbacks,
        "probe_report": report,
        "download_manifest": manifest,
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
                f"{cfg.verification_prompt}\n\n"
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
