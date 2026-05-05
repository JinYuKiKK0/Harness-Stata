"""Phase 4: Fallback — 单变量 ReAct 兜底 (hard not_found)。"""

from __future__ import annotations

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import HumanMessage

from harness_stata.clients.llm import get_chat_model
from harness_stata.subgraphs.probe.config import ProbeNodeConfig
from harness_stata.subgraphs.probe.pure import (
    PendingValidation,
    build_not_found_result,
    ensure_manifest,
    ensure_report,
)
from harness_stata.subgraphs.probe.schemas import VariableProbeFindingModel
from harness_stata.subgraphs.probe.state import ProbeState


async def fallback_react_phase(state: ProbeState, cfg: ProbeNodeConfig) -> dict[str, Any]:
    fallbacks = list(state.get("pending_hard_fallbacks") or [])
    if not fallbacks:
        return {"pending_hard_fallbacks": []}

    db_block = state.get("available_databases", "")
    validation_queue = list(state.get("validation_queue") or [])
    report = ensure_report(state.get("probe_report"))
    manifest = ensure_manifest(state.get("download_manifest"))

    for var in fallbacks:
        human = HumanMessage(
            content=(
                "<inputs>\n"
                f"变量: `{var['name']}` (contract={var['contract_type']}, role={var['role']})\n"
                f"description: {var['description']}\n\n"
                f"已购数据库:\n{db_block}\n"
                "</inputs>\n\n"
                "<reminder>\n"
                "找到明确可得性结论或两轮工具调用后仍不确定时,立即调用结构化输出工具下结论。\n"
                "found 时 database / table / field / source_fields / key_fields / match_kind 必填,"
                "且必须逐字来自工具返回。\n"
                "</reminder>"
            )
        )
        agent = create_agent(
            model=get_chat_model(),
            tools=list(cfg.fallback_tools),
            system_prompt=cfg.fallback_prompt,
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
            validation_queue.append(PendingValidation(variable=var, finding=finding))
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
            "workflow_status": "failed_hard_contract",
        }

    return {
        "validation_queue": validation_queue,
        "pending_hard_fallbacks": [],
        "probe_report": report,
        "download_manifest": manifest,
    }
