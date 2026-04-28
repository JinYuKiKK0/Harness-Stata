"""Probe subgraph factory — 批量字段发现流水线 + 覆盖率验证。

六节点拓扑::

    planning_agent
        │  (planning_queue 非空)
        ▼
    bulk_schema_phase
        │
        ▼
    verification_phase
        │  (pending_hard_fallbacks 非空)
        ▼
    fallback_react_phase
        │  (hard_failure 时直接 END)
        ▼
    coverage_validator
        │
        ▼
    coverage_validation_handler ──→ END

详见 ``docs/empirical-analysis-workflow.md``。本文件只承载:
- :class:`ProbeState` 子图状态 schema
- :func:`build_probe_subgraph` 工厂(组装 ``ProbeNodeConfig`` + 节点 partial 绑定 + 路由 + 装配)

节点函数实现位于 :mod:`harness_stata.subgraphs._probe_nodes`,纯流水线 helper 位于
:mod:`harness_stata.subgraphs._probe_pipeline`,报告/manifest/coverage 解码位于
:mod:`harness_stata.subgraphs._probe_helpers`。
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    VariableDefinition,
    WorkflowStatus,
)
from harness_stata.subgraphs._probe_coverage import (
    coverage_validation_handler,
    coverage_validator,
)
from harness_stata.subgraphs._probe_helpers import (
    CoverageEntry,
    PendingValidation,
)
from harness_stata.subgraphs._probe_nodes import (
    ProbeNodeConfig,
    bulk_schema_phase,
    fallback_react_phase,
    planning_agent,
    verification_phase,
)
from harness_stata.subgraphs._probe_pipeline import (
    PLANNING_OUTPUT_SPEC,
    VariablePlan,
)


class ProbeState(TypedDict, total=False):
    empirical_spec: EmpiricalSpec
    model_plan: ModelPlan
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    workflow_status: WorkflowStatus
    available_databases: str
    pending_variables: list[VariableDefinition]
    planning_queue: list[VariableDefinition]
    plans: list[VariablePlan]
    schema_dict: dict[str, list[dict[str, Any]]]
    pending_hard_fallbacks: list[VariableDefinition]
    validation_queue: list[PendingValidation]
    coverage_outcomes: list[CoverageEntry]
    messages: list[BaseMessage]


def build_probe_subgraph(
    *,
    planning_tools: Sequence[BaseTool],
    fallback_tools: Sequence[BaseTool],
    bulk_schema_tool: BaseTool,
    probe_tool: BaseTool,
    planning_prompt: str,
    verification_prompt: str,
    fallback_prompt: str,
    planning_agent_max_calls: int,
    fallback_react_max_calls: int,
) -> CompiledStateGraph[ProbeState, ProbeState, ProbeState, ProbeState]:
    """Build a compiled probe subgraph wired to the given tools and prompts.

    ``planning_tools`` 是 Planning Agent 在第一阶段可用的工具集(仅
    ``csmar_list_tables``)。``fallback_tools`` 是兜底单变量 ReAct 可用的工具集
    (``csmar_list_tables`` + ``csmar_bulk_schema`` + ``csmar_get_table_schema``)。
    ``bulk_schema_tool`` 由代码层在中间环节批量调用,同时也作为 fallback Agent 工具;
    ``probe_tool`` 仅在 coverage_validator 里使用,不绑给任何 Agent。

    ``planning_agent_max_calls`` 限制 Planning Agent 一轮内的工具调用次数,
    ``fallback_react_max_calls`` 限制每个兜底单变量 ReAct 的预算。
    """
    if not planning_tools:
        raise ValueError("planning_tools must not be empty")
    if not fallback_tools:
        raise ValueError("fallback_tools must not be empty")
    if planning_agent_max_calls < 1:
        raise ValueError("planning_agent_max_calls must be >= 1")
    if fallback_react_max_calls < 1:
        raise ValueError("fallback_react_max_calls must be >= 1")

    cfg = ProbeNodeConfig(
        planning_tools=list(planning_tools),
        fallback_tools=list(fallback_tools),
        bulk_schema_tool=bulk_schema_tool,
        probe_tool=probe_tool,
        planning_system_prompt=f"{planning_prompt}\n\n---\n\n{PLANNING_OUTPUT_SPEC}",
        verification_prompt=verification_prompt,
        fallback_full_prompt=(
            f"{fallback_prompt}\n\n---\n\n"
            "你的探测结论必须按 VariableProbeFindingModel schema 输出。"
            "status 只能是 found / not_found;found 时 database / table / field / key_fields 必填。"
        ),
        planning_agent_max_calls=planning_agent_max_calls,
        fallback_react_max_calls=fallback_react_max_calls,
    )

    def _route_after_planning(
        state: ProbeState,
    ) -> Literal["bulk_schema_phase", "__end__"]:
        if state.get("planning_queue"):
            return "bulk_schema_phase"
        return "__end__"

    def _route_after_verification(
        state: ProbeState,
    ) -> Literal["fallback_react_phase", "coverage_validator"]:
        if state.get("pending_hard_fallbacks"):
            return "fallback_react_phase"
        return "coverage_validator"

    def _route_after_fallback(
        state: ProbeState,
    ) -> Literal["coverage_validator", "__end__"]:
        report = state.get("probe_report")
        if report is not None and report.get("overall_status") == "hard_failure":
            return "__end__"
        return "coverage_validator"

    graph: StateGraph[ProbeState, ProbeState, ProbeState, ProbeState] = StateGraph(ProbeState)
    graph.add_node("planning_agent", partial(planning_agent, cfg=cfg))
    graph.add_node("bulk_schema_phase", partial(bulk_schema_phase, cfg=cfg))
    graph.add_node("verification_phase", partial(verification_phase, cfg=cfg))
    graph.add_node("fallback_react_phase", partial(fallback_react_phase, cfg=cfg))
    graph.add_node("coverage_validator", partial(coverage_validator, cfg=cfg))
    graph.add_node("coverage_validation_handler", coverage_validation_handler)

    graph.add_edge(START, "planning_agent")
    graph.add_conditional_edges(
        "planning_agent",
        _route_after_planning,
        {
            "bulk_schema_phase": "bulk_schema_phase",
            END: END,
        },
    )
    graph.add_edge("bulk_schema_phase", "verification_phase")
    graph.add_conditional_edges(
        "verification_phase",
        _route_after_verification,
        {
            "fallback_react_phase": "fallback_react_phase",
            "coverage_validator": "coverage_validator",
        },
    )
    graph.add_conditional_edges(
        "fallback_react_phase",
        _route_after_fallback,
        {"coverage_validator": "coverage_validator", END: END},
    )
    graph.add_edge("coverage_validator", "coverage_validation_handler")
    graph.add_edge("coverage_validation_handler", END)
    return graph.compile()
