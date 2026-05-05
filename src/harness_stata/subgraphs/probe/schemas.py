"""Pydantic schemas — LLM-facing structured output models。

- :class:`VariableProbeFindingModel` — 单变量字段定位的结构化输出 (Fallback 与内部合并结果共用)
- :class:`VariablePlan` / :class:`PlanningOutput` — Planning Agent 的结构化输出
- :class:`BucketVariableFinding` / :class:`BucketVerificationOutput` — Verification 单桶判定的结构化输出

NamedTuple 类(:class:`BucketKey` / :class:`BulkSchemaResult`)是纯逻辑函数的返回类型,
留在 :mod:`harness_stata.subgraphs.probe.pure` 不进本文件。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VariableProbeFindingModel(BaseModel):
    """LLM-facing structured-output schema for one variable's probe finding."""

    status: Literal["found", "not_found"] = Field(
        description="found if the agent located a usable data source, otherwise not_found"
    )
    database: str | None = Field(default=None, description="Source database name (found only)")
    table: str | None = Field(default=None, description="Source table name (found only)")
    field: str | None = Field(default=None, description="Variable column name (found only)")
    match_kind: Literal["direct_field", "semantic_equivalent", "derived"] | None = Field(
        default=None,
        description=(
            "How the variable is available: direct_field, semantic_equivalent, or derived"
        ),
    )
    source_fields: list[str] | None = Field(
        default=None,
        description="Raw CSMAR columns needed to obtain or construct the variable",
    )
    evidence: str | None = Field(
        default=None,
        description="Short reason for the availability judgement, useful for review",
    )
    key_fields: list[str] | None = Field(
        default=None, description="Primary/time key columns for the source table"
    )
    filters: dict[str, str] | None = Field(
        default=None, description="Confirmed time/sample filters keyed by column"
    )


class VariablePlan(BaseModel):
    """Planning Agent 对单个变量给出的目标 database + 候选表。"""

    variable_name: str
    target_database: str = ""
    candidate_table_codes: list[str] = []


class PlanningOutput(BaseModel):
    """Planning Agent 一次调用的整体结构化输出。"""

    plans: list[VariablePlan] = []


class BucketVariableFinding(BaseModel):
    """Verification 阶段单桶内对单个变量的判定。"""

    variable_name: str
    status: Literal["found", "not_found"]
    field: str | None = None
    match_kind: Literal["direct_field", "semantic_equivalent", "derived"] | None = None
    source_fields: list[str] | None = None
    evidence: str | None = None
    key_fields: list[str] | None = None
    filters: dict[str, str] | None = None


class BucketVerificationOutput(BaseModel):
    """Verification Agent 单桶一次调用的结构化输出。"""

    results: list[BucketVariableFinding] = []
