"""Probe subgraph 节点共享依赖。

把跨节点的工具/prompt/预算配置 (:class:`ProbeNodeConfig`) 集中到本模块,工厂层负责
装配并通过 partial 注入到每个节点。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class ProbeNodeConfig:
    """Immutable bundle of dependencies passed into every probe node."""

    planning_tools: Sequence[BaseTool]
    fallback_tools: Sequence[BaseTool]
    bulk_schema_tool: BaseTool
    probe_tool: BaseTool
    planning_prompt: str
    verification_prompt: str
    fallback_prompt: str
    planning_agent_max_calls: int
    fallback_react_max_calls: int
