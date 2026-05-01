"""HITL node — fourth node in the workflow.

Pure-code node. Uses ``langgraph.types.interrupt`` to pause graph execution
and surface the complete research plan (variables, equation, expected sign
baseline, sample size estimate) to the caller, collects the user's
approve/reject decision via ``Command(resume=...)``, and writes
``hitl_decision`` (plus ``workflow_status`` when rejected) to drive the
conditional edge after HITL defined in
docs/empirical-analysis-workflow.md.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.types import interrupt

from harness_stata.state import (
    EmpiricalSpec,
    HitlDecision,
    ModelPlan,
    ProbeReport,
    VariableDefinition,
    VariableProbeResult,
    WorkflowState,
)

# ---------------------------------------------------------------------------
# Module constants (stable contract for future CLI / Web resume adapters)
# ---------------------------------------------------------------------------

_INTERRUPT_TYPE = "hitl_plan_review"
_MAX_INTERRUPT_ATTEMPTS = 3

_SECTION_HEADERS: dict[str, str] = {
    "topic": "## 实证选题",
    "sample": "## 样本与时间范围",
    "equation": "## 模型方程",
    "variables": "## 变量定义表",
    "hypothesis": "## 预期符号基准线",
    "sample_size": "## 样本规模预估",
}

_ROLE_LABEL: dict[str, str] = {
    "dependent": "Y (被解释变量)",
    "independent": "X (核心解释变量)",
    "control": "控制变量",
}


# ---------------------------------------------------------------------------
# Formatting helpers (pure, side-effect free to stay safe under langgraph
# interrupt re-entry semantics — the node re-executes on resume).
# ---------------------------------------------------------------------------


def _format_topic_section(spec: EmpiricalSpec) -> str:
    return f"{_SECTION_HEADERS['topic']}\n\n{spec['topic']}"


def _format_sample_section(spec: EmpiricalSpec) -> str:
    return (
        f"{_SECTION_HEADERS['sample']}\n\n"
        f"- 样本范围: {spec['sample_scope']}\n"
        f"- 时间范围: {spec['time_range_start']} ~ {spec['time_range_end']}\n"
        f"- 数据频率: {spec['data_frequency']}\n"
        f"- 分析粒度: {spec['analysis_granularity']}"
    )


def _format_equation_section(plan: ModelPlan) -> str:
    return (
        f"{_SECTION_HEADERS['equation']}\n\n"
        f"- 模型类型: {plan['model_type']}\n"
        f"- 方程: {plan['equation']}"
    )


def _format_variable_source(probe: VariableProbeResult | None) -> str:
    if probe is None:
        return "N/A"
    if probe["status"] == "not_found":
        return "未找到"
    source = probe["source"]
    if source is None:
        return "N/A"
    source_fields = probe.get("source_fields") or [source["field"]]
    field_part = "+".join(source_fields)
    base = f"{source['database']}.{source['table']}.{field_part}"
    details: list[str] = []
    match_kind = probe.get("match_kind")
    if match_kind and match_kind != "direct_field":
        details.append(match_kind)
    evidence = probe.get("evidence")
    if evidence:
        details.append(f"依据: {evidence}")
    if not details:
        return base
    return f"{base} ({'; '.join(details)})"


def _format_variables_table(
    variables: list[VariableDefinition],
    report: ProbeReport,
) -> str:
    probe_by_name: dict[str, VariableProbeResult] = {
        r["variable_name"]: r for r in report["variable_results"]
    }
    lines: list[str] = [
        _SECTION_HEADERS["variables"],
        "",
        "| 变量 | 角色 | Hard/Soft | 描述 | 数据来源 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for var in variables:
        probe = probe_by_name.get(var["name"])
        source_str = _format_variable_source(probe)
        role_label = _ROLE_LABEL.get(var["role"], var["role"])
        contract_label = "Hard" if var["contract_type"] == "hard" else "Soft"
        lines.append(
            f"| {var['name']} | {role_label} | {contract_label} | "
            f"{var['description']} | {source_str} |"
        )
    return "\n".join(lines)


def _format_core_hypothesis(plan: ModelPlan) -> str:
    hyp = plan["core_hypothesis"]
    return (
        f"{_SECTION_HEADERS['hypothesis']}\n\n"
        f"- 核心解释变量: {hyp['variable_name']}\n"
        f"- 预期符号: {hyp['expected_sign']}\n"
        f"- 经济学依据: {hyp['rationale']}"
    )


def _format_sample_size(report: ProbeReport) -> str:
    counts = [
        r["record_count"] for r in report["variable_results"] if r["record_count"] is not None
    ]
    header = _SECTION_HEADERS["sample_size"]
    if not counts:
        return f"{header}\n\n无法根据探针估算 (所有变量 record_count 缺失)"
    if len(counts) == 1:
        return f"{header}\n\n预估 {counts[0]} 条 (基于 1 个变量探针记录数)"
    return (
        f"{header}\n\n预估 {min(counts)} ~ {max(counts)} 条 (基于 {len(counts)} 个变量探针记录数)"
    )


def _format_plan(
    spec: EmpiricalSpec,
    plan: ModelPlan,
    report: ProbeReport,
) -> str:
    sections = [
        _format_topic_section(spec),
        _format_sample_section(spec),
        _format_equation_section(plan),
        _format_variables_table(spec["variables"], report),
        _format_core_hypothesis(plan),
        _format_sample_size(report),
    ]
    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------------------
# Decision validation and interrupt loop
# ---------------------------------------------------------------------------


def _validate(raw: object) -> str | None:
    if not isinstance(raw, dict):
        return "decision must be a dict"
    data = raw
    approved = data.get("approved")
    if not isinstance(approved, bool):
        return "'approved' must be a bool"
    if approved is False:
        notes = data.get("user_notes")
        if not isinstance(notes, str) or not notes.strip():
            return "'user_notes' is required (non-empty string) when approved=False"
    return None


def _build_payload(plan_text: str, error: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": _INTERRUPT_TYPE,
        "plan": plan_text,
        "need": "approve_or_reject",
        "schema": {
            "approved": "bool (required)",
            "user_notes": ("str (optional if approved=True, required non-empty if approved=False)"),
        },
    }
    if error is not None:
        payload["error"] = error
    return payload


def _request_decision(plan_text: str) -> dict[str, Any]:
    """Call ``interrupt`` until a valid decision is resumed, or raise."""
    error: str | None = None
    for _ in range(_MAX_INTERRUPT_ATTEMPTS):
        payload = _build_payload(plan_text, error)
        raw = interrupt(payload)
        err = _validate(raw)
        if err is None:
            assert isinstance(raw, dict)
            return raw
        error = err
    raise ValueError(f"HITL decision validation failed after {_MAX_INTERRUPT_ATTEMPTS} attempts")


class HitlOutput(TypedDict, total=False):
    hitl_decision: HitlDecision
    workflow_status: Literal["rejected"]


def _build_return(decision: dict[str, Any]) -> HitlOutput:
    approved = bool(decision["approved"])
    notes = decision.get("user_notes")
    if approved:
        return {"hitl_decision": {"approved": True, "user_notes": notes}}
    return {
        "hitl_decision": {"approved": False, "user_notes": notes},
        "workflow_status": "rejected",
    }


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


def hitl(state: WorkflowState) -> HitlOutput:
    """Present research plan and collect approve/reject decision via interrupt."""
    spec: EmpiricalSpec = state["empirical_spec"]
    plan: ModelPlan = state["model_plan"]
    report: ProbeReport = state["probe_report"]

    plan_text = _format_plan(spec, plan, report)
    decision = _request_decision(plan_text)
    return _build_return(decision)
