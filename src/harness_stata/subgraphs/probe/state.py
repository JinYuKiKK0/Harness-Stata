"""Probe subgraph state schema.

只承载 :class:`ProbeState` 子图状态 schema。跨节点流转的 TypedDict 三件套
(:class:`PendingValidation` / :class:`CoverageOutcome` / :class:`CoverageEntry`)
本质是 pure 函数的输入/返回类型,与 pure 函数 colocate 在
:mod:`harness_stata.subgraphs.probe.pure`,本文件只 re-export ProbeState 用到的引用。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.messages import BaseMessage

from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    VariableDefinition,
    WorkflowStatus,
)
from harness_stata.subgraphs.probe.pure import CoverageEntry, PendingValidation
from harness_stata.subgraphs.probe.schemas import VariablePlan


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
