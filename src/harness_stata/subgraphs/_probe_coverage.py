"""Coverage validation phase for the probe subgraph.

把覆盖率验证两个节点(``coverage_validator`` 调 ``csmar_probe_query`` 批量验证;
``coverage_validation_handler`` 把结果固化进 ProbeReport / DownloadManifest 并累积
substitute 候选)与其内部 helper(``_absorb_passing_outcome`` /
``_format_coverage_failure``)聚合到本模块,以保持 ``_probe_nodes.py`` 聚焦字段
发现流水线 0~4 阶段。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ProbeReport,
    VariableDefinition,
)
from harness_stata.subgraphs._probe_helpers import (
    CoverageEntry,
    CoverageOutcome,
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

if TYPE_CHECKING:
    from harness_stata.subgraphs._probe_nodes import ProbeNodeConfig
    from harness_stata.subgraphs.probe_subgraph import ProbeState


async def coverage_validator(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    """Batch-invoke csmar_probe_query for every queued field-level finding."""
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
        outcome = await run_probe_coverage(cfg.probe_tool, payload, ctx)
        outcomes.append(CoverageEntry(pending=pending, outcome=outcome))
    return {"coverage_outcomes": outcomes}


def coverage_validation_handler(state: ProbeState) -> dict[str, Any]:
    """Process coverage outcomes: write report/manifest on pass, route fail as not_found."""
    report = ensure_report(state.get("probe_report"))
    manifest = ensure_manifest(state.get("download_manifest"))
    spec = state.get("empirical_spec")
    plan = state.get("model_plan")
    sub_meta = dict(state.get("substitute_meta") or {})
    outcomes = list(state.get("coverage_outcomes") or [])
    substitute_queue = list(state.get("substitute_queue") or [])

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

        if current["contract_type"] == "hard":
            report["variable_results"].append(build_not_found_result(current["name"]))
            report["overall_status"] = "hard_failure"
            report["failure_reason"] = _format_coverage_failure(current["name"], outcome)
            out: dict[str, Any] = {
                "probe_report": report,
                "download_manifest": manifest,
                "workflow_status": "failed_hard_contract",
                "substitute_meta": sub_meta,
                "validation_queue": [],
                "coverage_outcomes": [],
                "substitute_queue": substitute_queue,
            }
            if spec is not None:
                out["empirical_spec"] = spec
            return out

        if is_substitute_task:
            meta = sub_meta.pop(current["name"])
            report["variable_results"].append(build_not_found_result(meta["original_name"]))
            continue

        cand = maybe_build_substitute(finding, current)
        if cand is not None and cand["name"] not in {v["name"] for v in substitute_queue}:
            substitute_queue.append(cand)
            sub_meta[cand["name"]] = SubstituteMeta(
                original_name=current["name"],
                reason=finding.candidate_substitute_reason or "",
            )
        else:
            report["variable_results"].append(build_not_found_result(current["name"]))

    out_final: dict[str, Any] = {
        "probe_report": report,
        "download_manifest": manifest,
        "validation_queue": [],
        "coverage_outcomes": [],
        "substitute_meta": sub_meta,
        "substitute_queue": substitute_queue,
    }
    if not substitute_queue and report["overall_status"] != "hard_failure":
        report["overall_status"] = "success"
        report["failure_reason"] = None
    if spec is not None:
        out_final["empirical_spec"] = spec
    if plan is not None:
        out_final["model_plan"] = replace_variable_in_model_plan(plan, report["variable_results"])
    return out_final


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
