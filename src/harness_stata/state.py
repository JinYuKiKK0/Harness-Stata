"""Shared workflow state definitions.

See docs/state.md for the design rationale. All slices are TypedDicts;
WorkflowState composes them flatly with incremental population semantics
(``total=False``).
所有针对本项目状态定义的修改都必须同步更新 docs/state.md 中的设计文档,以保持文档与代码的一致性。
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------


class UserRequest(TypedDict):
    """User-provided empirical analysis requirements (7 mandatory fields)."""

    topic: str
    x_variable: str
    y_variable: str
    sample_scope: str
    time_range_start: str
    time_range_end: str
    data_frequency: Literal["yearly", "quarterly", "monthly", "daily"]


class VariableDefinition(TypedDict):
    name: str
    description: str
    contract_type: Literal["hard", "soft"]
    role: Literal["dependent", "independent", "control"]


class CoreHypothesis(TypedDict):
    variable_name: str
    expected_sign: Literal["+", "-", "ambiguous"]
    rationale: str


class VariableSource(TypedDict):
    database: str
    table: str
    field: str


ProbeMatchKind = Literal["direct_field", "semantic_equivalent", "derived"]


class VariableMapping(TypedDict):
    variable_name: str
    source_fields: list[str]
    match_kind: ProbeMatchKind
    transform: dict[str, object] | None
    evidence: NotRequired[str | None]


class VariableProbeResult(TypedDict):
    variable_name: str
    status: Literal["found", "not_found"]
    source: VariableSource | None
    record_count: int | None
    match_kind: NotRequired[ProbeMatchKind | None]
    source_fields: NotRequired[list[str]]
    transform: NotRequired[dict[str, object] | None]
    evidence: NotRequired[str | None]


class DownloadTask(TypedDict):
    database: str
    table: str
    key_fields: list[str]
    variable_fields: list[str]
    variable_names: list[str]
    variable_mappings: NotRequired[list[VariableMapping]]
    filters: dict[str, object]


class DownloadedFile(TypedDict):
    path: str
    source_table: str
    key_fields: list[str]
    variable_names: list[str]
    variable_mappings: NotRequired[list[VariableMapping]]


class SignCheck(TypedDict):
    variable_name: str
    expected_sign: str
    actual_sign: str
    consistent: bool


# ---------------------------------------------------------------------------
# State slices
# ---------------------------------------------------------------------------


class EmpiricalSpec(TypedDict):
    topic: str
    variables: list[VariableDefinition]
    sample_scope: str
    time_range_start: str
    time_range_end: str
    data_frequency: Literal["yearly", "quarterly", "monthly", "daily"]
    analysis_granularity: str


class ModelPlan(TypedDict):
    model_type: str
    equation: str
    core_hypothesis: CoreHypothesis
    data_structure_requirements: list[str]


class ProbeReport(TypedDict):
    variable_results: list[VariableProbeResult]
    overall_status: Literal["success", "hard_failure"]
    failure_reason: str | None


class DownloadManifest(TypedDict):
    items: list[DownloadTask]


class HitlDecision(TypedDict):
    approved: bool
    user_notes: str | None


class DownloadedFiles(TypedDict):
    files: list[DownloadedFile]


class MergedDataset(TypedDict):
    file_path: str
    row_count: int
    columns: list[str]
    warnings: list[str]


class DescStatsReport(TypedDict):
    do_file_path: str
    log_file_path: str
    summary: str


class RegressionResult(TypedDict):
    do_file_path: str
    log_file_path: str
    sign_check: SignCheck
    summary: str


# ---------------------------------------------------------------------------
# Workflow state
# ---------------------------------------------------------------------------


WorkflowStatus = Literal[
    "running",
    "success",
    "failed_hard_contract",
    "rejected",
]


class WorkflowState(TypedDict, total=False):
    user_request: UserRequest
    empirical_spec: EmpiricalSpec
    model_plan: ModelPlan
    probe_report: ProbeReport
    download_manifest: DownloadManifest
    hitl_decision: HitlDecision
    downloaded_files: DownloadedFiles
    merged_dataset: MergedDataset
    desc_stats_report: DescStatsReport
    regression_result: RegressionResult
    workflow_status: WorkflowStatus
