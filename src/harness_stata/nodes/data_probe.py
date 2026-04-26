"""Data probe node — third node in the workflow.

Pure-code wrapper around :func:`build_probe_subgraph`. 在节点入口先调用一次
``csmar_list_databases`` 把已购数据库清单作为共享上下文注入子图(每个变量的
Agent 共用),然后把 csmar-mcp 的工具按照白名单切片,**只把字段发现工具集**
(``csmar_search_field`` / ``csmar_list_tables`` / ``csmar_bulk_schema`` /
``csmar_get_table_schema``)绑定给 Agent;``csmar_probe_query`` 单独提取作为
``probe_tool`` 透传给子图,由其覆盖率验证阶段以代码方式批量调用。
``csmar_materialize_query`` / ``csmar_refresh_cache`` 不在本节点暴露范围内。

Node 与子图均为 async,通过 ``await subgraph.ainvoke(...)`` 走 MCP stdio,
满足 ``langgraph dev`` 的 blockbuster 检测与 LangGraph 部署对纯异步的要求。

硬失败不在本层抛错。子图把 ``workflow_status`` 翻为 ``"failed_hard_contract"``
时,本节点透传该字段;主图的条件边负责路由到 END。
"""

from __future__ import annotations

from typing import Literal, TypedDict, cast

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

# 字段发现阶段允许暴露给 Agent 的工具白名单:
# - csmar_search_field    本地 cache 搜索(零远程调用,首选)
# - csmar_list_tables     列出某数据库下的表(search_field 空命中时回退)
# - csmar_bulk_schema     批量拉多张候选表的 schema(优于循环 get_table_schema)
# - csmar_get_table_schema 单张表的 schema 精读
# 显式排除:
# - csmar_list_databases   节点入口已调一次,作为共享上下文注入,无需 Agent 再调
# - csmar_probe_query      由子图覆盖率验证阶段以代码调用,不暴露给 Agent
# - csmar_materialize_query / csmar_refresh_cache  与本节点职责无关
ALLOWED_REACT_TOOLS: frozenset[str] = frozenset(
    {
        "csmar_search_field",
        "csmar_list_tables",
        "csmar_bulk_schema",
        "csmar_get_table_schema",
    }
)
_LIST_DATABASES_TOOL = "csmar_list_databases"
_PROBE_QUERY_TOOL = "csmar_probe_query"


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
    empirical_spec: EmpiricalSpec
    model_plan: ModelPlan
    workflow_status: Literal["failed_hard_contract"]


async def data_probe(state: WorkflowState) -> DataProbeOutput:
    """Probe variable availability in CSMAR; emit probe_report + download_manifest."""
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]  # type: ignore[reportTypedDictNotRequiredAccess]
    model_plan: ModelPlan = state["model_plan"]  # type: ignore[reportTypedDictNotRequiredAccess]
    settings = get_settings()

    async with get_csmar_tools() as tools:
        by_name = {t.name: t for t in tools}
        missing = [
            name for name in (_LIST_DATABASES_TOOL, _PROBE_QUERY_TOOL) if name not in by_name
        ]
        if missing:
            msg = f"csmar-mcp is missing required tools: {missing}"
            raise RuntimeError(msg)

        list_tool = by_name[_LIST_DATABASES_TOOL]
        probe_tool = by_name[_PROBE_QUERY_TOOL]
        raw_databases = await list_tool.ainvoke({})  # pyright: ignore[reportUnknownMemberType]
        available_databases_text = str(raw_databases)

        react_tools = [t for t in tools if t.name in ALLOWED_REACT_TOOLS]
        if not react_tools:
            msg = (
                f"csmar-mcp 暴露的工具与白名单不交叉,无法构建探针子图;"
                f" 期望至少一个 {sorted(ALLOWED_REACT_TOOLS)}"
            )
            raise RuntimeError(msg)

        subgraph = build_probe_subgraph(
            tools=react_tools,
            probe_tool=probe_tool,
            prompt=load_prompt("data_probe"),
            per_variable_max_calls=settings.per_variable_max_calls,
        )
        initial: ProbeState = {
            "empirical_spec": spec,
            "model_plan": model_plan,
            "available_databases": available_databases_text,
        }
        raw_final = await subgraph.ainvoke(initial)  # pyright: ignore[reportUnknownMemberType]
        final = cast("ProbeState", raw_final)

    result: DataProbeOutput = {
        "probe_report": final["probe_report"],  # type: ignore[reportTypedDictNotRequiredAccess]
        "download_manifest": final["download_manifest"],  # type: ignore[reportTypedDictNotRequiredAccess]
    }
    # soft-substitute 成功时子图会重建 empirical_spec / model_plan,此处仅在确实变更时回传
    final_spec = final.get("empirical_spec")
    if final_spec is not None and final_spec is not spec:
        result["empirical_spec"] = final_spec
    final_plan = final.get("model_plan")
    if final_plan is not None and final_plan is not model_plan:
        result["model_plan"] = final_plan
    if final.get("workflow_status") == "failed_hard_contract":
        result["workflow_status"] = "failed_hard_contract"
    return result
