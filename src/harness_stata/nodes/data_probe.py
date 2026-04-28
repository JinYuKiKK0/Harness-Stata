"""Data probe node — third node in the workflow.

Pure-code wrapper around :func:`build_probe_subgraph`. 在节点入口先调用一次
``csmar_list_databases`` 把已购数据库清单作为共享上下文注入子图,然后把
csmar-mcp 的工具按白名单切片,分别交给 Planning 阶段(仅 ``csmar_list_tables``)
与兜底单变量 ReAct 阶段(``csmar_list_tables`` + ``csmar_bulk_schema`` +
``csmar_get_table_schema``)。
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict, cast

from harness_stata.clients.csmar import get_csmar_tools
from harness_stata.config import get_settings
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    WorkflowState,
)
from harness_stata.subgraphs.probe_subgraph import ProbeState, build_probe_subgraph

# ---------------------------------------------------------------------------
# Tool exposure policy
# ---------------------------------------------------------------------------

# Planning Agent (阶段一) 的工具白名单:只允许 list_tables。
# - csmar_list_tables     列出某数据库下的表(候选 table_code 必须出自此处)
PLANNING_TOOLS: frozenset[str] = frozenset({"csmar_list_tables"})

# Fallback 单变量 ReAct (阶段三兜底) 的工具白名单:仅列表 + 单表 schema 拉取。
# csmar_bulk_schema 是 bulk_schema_phase 的代码节点专属(走 structuredContent
# artifact),不暴露给 LLM。
FALLBACK_TOOLS: frozenset[str] = frozenset(
    {
        "csmar_list_tables",
        "csmar_get_table_schema",
    }
)

# 兼容旧入口(测试白名单不变性):字段发现层允许暴露给任意 Agent 的工具集合
ALLOWED_REACT_TOOLS: frozenset[str] = FALLBACK_TOOLS

_LIST_DATABASES_TOOL = "csmar_list_databases"
_BULK_SCHEMA_TOOL = "csmar_bulk_schema"
_PROBE_QUERY_TOOL = "csmar_probe_query"


def _format_databases(raw: Any) -> str:
    """Extract `databases` list from csmar_list_databases content blocks → 顿号分隔。"""
    blocks = raw if isinstance(raw, list) else [raw]
    for block in blocks:
        if not isinstance(block, dict):
            continue
        text_value = block.get("text")
        if not isinstance(text_value, str):
            continue
        try:
            payload = json.loads(text_value)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        dbs_value = payload.get("databases")
        if isinstance(dbs_value, list):
            return "、".join(str(d) for d in dbs_value)
    return str(raw)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(state: WorkflowState) -> str | None:
    spec = state.get("empirical_spec")
    if spec is None:
        return "state.empirical_spec is missing"
    if not spec.get("variables"):
        return "empirical_spec.variables must be a non-empty list"
    if state.get("model_plan") is None:
        return "state.model_plan is missing"
    return None


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


class DataProbeOutput(TypedDict, total=False):
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    workflow_status: Literal["failed_hard_contract"]


async def data_probe(state: WorkflowState) -> DataProbeOutput:
    """Probe variable availability in CSMAR; emit probe_report + download_manifest."""
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]
    model_plan: ModelPlan = state["model_plan"]
    settings = get_settings()

    async with get_csmar_tools() as tools:
        by_name = {t.name: t for t in tools}
        required = (_LIST_DATABASES_TOOL, _BULK_SCHEMA_TOOL, _PROBE_QUERY_TOOL)
        missing = [name for name in required if name not in by_name]
        if missing:
            msg = f"csmar-mcp is missing required tools: {missing}"
            raise RuntimeError(msg)

        list_tool = by_name[_LIST_DATABASES_TOOL]
        bulk_schema_tool = by_name[_BULK_SCHEMA_TOOL]
        probe_tool = by_name[_PROBE_QUERY_TOOL]
        raw_databases = await list_tool.ainvoke({})
        available_databases_text = _format_databases(raw_databases)

        planning_tools = [t for t in tools if t.name in PLANNING_TOOLS]
        fallback_tools = [t for t in tools if t.name in FALLBACK_TOOLS]
        if not planning_tools:
            msg = (
                f"csmar-mcp 暴露的工具与 Planning 白名单不交叉,无法构建 Planning Agent;"
                f" 期望至少一个 {sorted(PLANNING_TOOLS)}"
            )
            raise RuntimeError(msg)
        if not fallback_tools:
            msg = (
                f"csmar-mcp 暴露的工具与 Fallback 白名单不交叉,无法构建兜底子流程;"
                f" 期望至少一个 {sorted(FALLBACK_TOOLS)}"
            )
            raise RuntimeError(msg)

        subgraph = build_probe_subgraph(
            planning_tools=planning_tools,
            fallback_tools=fallback_tools,
            bulk_schema_tool=bulk_schema_tool,
            probe_tool=probe_tool,
            planning_prompt=load_prompt("data_probe_planning"),
            verification_prompt=load_prompt("data_probe_verification"),
            fallback_prompt=load_prompt("data_probe_fallback"),
            planning_agent_max_calls=settings.planning_agent_max_calls,
            fallback_react_max_calls=settings.fallback_react_max_calls,
        )
        initial: ProbeState = {
            "empirical_spec": spec,
            "model_plan": model_plan,
            "available_databases": available_databases_text,
        }
        raw_final = await subgraph.ainvoke(initial)
        final = cast("ProbeState", raw_final)

    result: DataProbeOutput = {
        "probe_report": final["probe_report"],
        "download_manifest": final["download_manifest"],
    }
    if final.get("workflow_status") == "failed_hard_contract":
        result["workflow_status"] = "failed_hard_contract"
    return result
