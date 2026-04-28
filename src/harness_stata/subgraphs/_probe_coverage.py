"""Coverage validation phase for the probe subgraph.

把覆盖率验证两个节点(``coverage_validator`` 调 ``csmar_probe_query`` 批量验证;
``coverage_validation_handler`` 把结果固化进 ProbeReport / DownloadManifest)与其
内部 helper(``_format_coverage_failure``)聚合到本模块,以保持 ``_probe_nodes.py``
聚焦字段发现流水线 0~4 阶段。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_stata.subgraphs._probe_helpers import (
    CoverageEntry,
    CoverageOutcome,
    build_found_result,
    build_not_found_result,
    build_probe_query_payload,
    ensure_manifest,
    ensure_report,
    merge_into_manifest,
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
    outcomes = list(state.get("coverage_outcomes") or [])

    for entry in outcomes:
        pending = entry["pending"]
        outcome = entry["outcome"]
        current = pending["variable"]
        finding = pending["finding"]

        if outcome["can_materialize"]:
            if spec is None:
                raise RuntimeError(
                    "probe_subgraph: empirical_spec is missing during coverage absorb"
                )
            report["variable_results"].append(
                build_found_result(current, finding, record_count=outcome["row_count"])
            )
            merge_into_manifest(manifest, current, finding, spec)
            continue

        if current["contract_type"] == "hard":
            report["variable_results"].append(build_not_found_result(current["name"]))
            report["overall_status"] = "hard_failure"
            report["failure_reason"] = _format_coverage_failure(current["name"], outcome)
            return {
                "probe_report": report,
                "download_manifest": manifest,
                "workflow_status": "failed_hard_contract",
                "validation_queue": [],
                "coverage_outcomes": [],
            }

        report["variable_results"].append(build_not_found_result(current["name"]))

    if report["overall_status"] != "hard_failure":
        report["overall_status"] = "success"
        report["failure_reason"] = None
    return {
        "probe_report": report,
        "download_manifest": manifest,
        "validation_queue": [],
        "coverage_outcomes": [],
    }


def _format_coverage_failure(variable_name: str, outcome: CoverageOutcome) -> str:
    base = f"Hard contract variable '{variable_name}' coverage check failed: can_materialize=false"
    if outcome["invalid_columns"]:
        base = f"{base}, invalid_columns={outcome['invalid_columns']!r}"
    if outcome["failure_reason"]:
        base = f"{base}; {outcome['failure_reason']}"
    return base
