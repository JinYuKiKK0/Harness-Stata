"""Probe subgraph factory — 批量字段发现流水线 + 覆盖率验证。

五节点拓扑::

    planning_agent
        │  (empirical_spec.variables 非空)
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
    coverage_phase ──→ END

详见 ``docs/empirical-analysis-workflow.md``。本文件只承载路由函数与
:func:`build_probe_subgraph` 工厂(组装 ``ProbeNodeConfig`` + 节点 partial 绑定 +
路由 + 装配)。节点函数实现位于 :mod:`harness_stata.subgraphs.probe.nodes`,纯逻辑
位于 :mod:`harness_stata.subgraphs.probe.pure`。
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Literal

from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from harness_stata.subgraphs.probe.config import (
    ProbeNodeConfig,
    compose_fallback_prompt,
    compose_planning_prompt,
    compose_verification_prompt,
)
from harness_stata.subgraphs.probe.nodes import (
    bulk_schema_phase,
    coverage_phase,
    fallback_react_phase,
    planning_agent,
    verification_phase,
)
from harness_stata.subgraphs.probe.state import ProbeState


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
    ``probe_tool`` 仅在 coverage_phase 里使用,不绑给任何 Agent。

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
        planning_system_prompt=compose_planning_prompt(planning_prompt),
        verification_prompt=compose_verification_prompt(verification_prompt),
        fallback_full_prompt=compose_fallback_prompt(fallback_prompt),
        planning_agent_max_calls=planning_agent_max_calls,
        fallback_react_max_calls=fallback_react_max_calls,
    )

    def _route_after_planning(
        state: ProbeState,
    ) -> Literal["bulk_schema_phase", "__end__"]:
        if state["empirical_spec"]["variables"]:
            return "bulk_schema_phase"
        return "__end__"

    def _route_after_verification(
        state: ProbeState,
    ) -> Literal["fallback_react_phase", "coverage_phase"]:
        if state.get("pending_hard_fallbacks"):
            return "fallback_react_phase"
        return "coverage_phase"

    def _route_after_fallback(
        state: ProbeState,
    ) -> Literal["coverage_phase", "__end__"]:
        report = state.get("probe_report")
        if report is not None and report.get("overall_status") == "hard_failure":
            return "__end__"
        return "coverage_phase"

    graph: StateGraph[ProbeState, ProbeState, ProbeState, ProbeState] = StateGraph(ProbeState)
    graph.add_node("planning_agent", partial(planning_agent, cfg=cfg))
    graph.add_node("bulk_schema_phase", partial(bulk_schema_phase, cfg=cfg))
    graph.add_node("verification_phase", partial(verification_phase, cfg=cfg))
    graph.add_node("fallback_react_phase", partial(fallback_react_phase, cfg=cfg))
    graph.add_node("coverage_phase", partial(coverage_phase, cfg=cfg))

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
            "coverage_phase": "coverage_phase",
        },
    )
    graph.add_conditional_edges(
        "fallback_react_phase",
        _route_after_fallback,
        {"coverage_phase": "coverage_phase", END: END},
    )
    graph.add_edge("coverage_phase", END)
    return graph.compile()
