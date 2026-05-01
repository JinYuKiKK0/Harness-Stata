"""Probe subgraph state schema.

只承载 :class:`ProbeState` 子图状态 schema。跨节点流转的 TypedDict
(:class:`PendingValidation` / :class:`CoverageOutcome`) 本质是 pure 函数的
输入/返回类型,与 pure 函数 colocate 在 :mod:`harness_stata.subgraphs.probe.pure`,
本文件只 re-export ProbeState 用到的引用。
"""

from __future__ import annotations

from typing import Any, TypedDict

from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ProbeReport,
    VariableDefinition,
    WorkflowStatus,
)
from harness_stata.subgraphs.probe.pure import PendingValidation
from harness_stata.subgraphs.probe.schemas import VariablePlan


class ProbeState(TypedDict, total=False):
    empirical_spec: EmpiricalSpec
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    workflow_status: WorkflowStatus
    available_databases: str
    plans: list[VariablePlan]
    schema_dict: dict[str, list[dict[str, Any]]]
    table_names: dict[str, str]
    pending_hard_fallbacks: list[VariableDefinition]
    validation_queue: list[PendingValidation]
