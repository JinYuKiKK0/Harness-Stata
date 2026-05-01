"""Phase 5: Coverage validation — 批量 csmar_probe_query + 报告/manifest 固化。

单节点完成两件事:① 对 validation_queue 里每条 finding 跑一次 csmar_probe_query;
② 按 outcome 把结果落进 ProbeReport / DownloadManifest,can_materialize=false 的
hard 变量直接写 hard_failure 终态。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from harness_stata.clients.mcp import call_structured_mcp_tool
from harness_stata.subgraphs.probe.config import ProbeNodeConfig
from harness_stata.subgraphs.probe.pure import (
    CoverageOutcome,
    build_found_result,
    build_not_found_result,
    build_probe_query_payload,
    ensure_manifest,
    ensure_report,
    finding_mapping_failure_reason,
    merge_into_manifest,
    parse_probe_query_response,
)
from harness_stata.subgraphs.probe.state import ProbeState


async def coverage_phase(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    """Run probe_query for each pending finding, then absorb outcomes into report/manifest."""
    validation_queue = list(state.get("validation_queue") or [])
    report = ensure_report(state.get("probe_report"))
    manifest = ensure_manifest(state.get("download_manifest"))
    spec = state["empirical_spec"]

    for pending in validation_queue:
        current = pending["variable"]
        finding = pending["finding"]
        ctx = f"coverage check for variable '{current['name']}' on table {finding.table!r}"
        mapping_failure = finding_mapping_failure_reason(finding)
        if mapping_failure is not None:
            outcome = CoverageOutcome(
                can_materialize=False,
                invalid_columns=[],
                validation_id=None,
                row_count=None,
                failure_reason=f"{ctx}: {mapping_failure}",
            )
        else:
            payload = build_probe_query_payload(spec, finding)
            outcome = await run_probe_coverage(cfg.probe_tool, payload, ctx)

        if outcome["can_materialize"]:
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
            }

        report["variable_results"].append(build_not_found_result(current["name"]))

    if report["overall_status"] != "hard_failure":
        report["overall_status"] = "success"
        report["failure_reason"] = None
    return {
        "probe_report": report,
        "download_manifest": manifest,
        "validation_queue": [],
    }


async def run_probe_coverage(
    probe_tool: BaseTool, payload: dict[str, object], context: str
) -> CoverageOutcome:
    """Invoke the probe_query tool and decode the response into CoverageOutcome.

    任何调用抛出的异常都在本函数捕获,转写为 ``can_materialize=False`` 的 outcome。
    调用方据此走 hard/soft 路由,不再抛 RuntimeError。
    """
    try:
        raw = await call_structured_mcp_tool(probe_tool, payload, context)
    except Exception as exc:
        return CoverageOutcome(
            can_materialize=False,
            invalid_columns=[],
            validation_id=None,
            row_count=None,
            failure_reason=f"{context}: probe_query call failed: {exc}",
        )
    return parse_probe_query_response(raw, context)


def _format_coverage_failure(variable_name: str, outcome: CoverageOutcome) -> str:
    base = f"Hard contract variable '{variable_name}' coverage check failed: can_materialize=false"
    if outcome["invalid_columns"]:
        base = f"{base}, invalid_columns={outcome['invalid_columns']!r}"
    if outcome["failure_reason"]:
        base = f"{base}; {outcome['failure_reason']}"
    return base
