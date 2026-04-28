"""Probe subgraph 节点共享依赖与 prompt 拼接。

把跨节点的工具/prompt/预算配置 (:class:`ProbeNodeConfig`) 与 system prompt 的拼接
helper (compose_*_prompt) 集中到本模块。工厂层只负责"调用 helper 拼好 prompt → 装配
config → 注入节点",节点函数本身不感知 OUTPUT_SPEC 字符串。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from harness_stata.subgraphs.probe.schemas import (
    FALLBACK_OUTPUT_SPEC,
    PLANNING_OUTPUT_SPEC,
    VERIFICATION_OUTPUT_SPEC,
)


@dataclass(frozen=True)
class ProbeNodeConfig:
    """Immutable bundle of dependencies passed into every probe node."""

    planning_tools: Sequence[BaseTool]
    fallback_tools: Sequence[BaseTool]
    bulk_schema_tool: BaseTool
    probe_tool: BaseTool
    planning_system_prompt: str
    verification_prompt: str
    fallback_full_prompt: str
    planning_agent_max_calls: int
    fallback_react_max_calls: int


def compose_planning_prompt(planning_prompt: str) -> str:
    return f"{planning_prompt}\n\n---\n\n{PLANNING_OUTPUT_SPEC}"


def compose_verification_prompt(verification_prompt: str) -> str:
    return f"{verification_prompt}\n\n---\n\n{VERIFICATION_OUTPUT_SPEC}"


def compose_fallback_prompt(fallback_prompt: str) -> str:
    return f"{fallback_prompt}\n\n---\n\n{FALLBACK_OUTPUT_SPEC}"
