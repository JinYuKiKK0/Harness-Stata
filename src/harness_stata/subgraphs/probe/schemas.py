"""Pydantic schemas + LLM-facing 输出规约字符串。

本文件只放 **Pydantic BaseModel + 给 LLM 看的输出规约字符串**:

- :class:`VariableProbeFindingModel` — 单变量字段定位的结构化输出 (Fallback 与内部合并结果共用)
- :class:`VariablePlan` / :class:`PlanningOutput` — Planning Agent 的结构化输出
- :class:`BucketVariableFinding` / :class:`BucketVerificationOutput` — Verification 单桶判定的结构化输出
- ``PLANNING_OUTPUT_SPEC`` / ``VERIFICATION_OUTPUT_SPEC`` / ``FALLBACK_OUTPUT_SPEC`` — 三段拼接进 system prompt 的 LLM 输出规约

NamedTuple 类(:class:`BucketKey` / :class:`BulkSchemaResult`)是纯逻辑函数的返回类型,
留在 :mod:`harness_stata.subgraphs.probe.pure` 不进本文件。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# LLM 输出规约 (供 config 层拼接进 system prompt)
# ---------------------------------------------------------------------------

PLANNING_OUTPUT_SPEC = """语义约束(schema 之外):

- 每个输入变量必须输出一条 plan;variable_name 不得改写。
- target_database 必须来自已购数据库清单;candidate_table_codes 必须来自 csmar_list_tables 返回。
- 每个变量最多 3 张候选表;不确定时输出空 database 和空 list。
"""

VERIFICATION_OUTPUT_SPEC = """语义约束(schema 之外):

- 每个输入变量必须输出一条 result;variable_name 不得改写。
- found 表示变量可由 CSMAR 字段直接取得、语义等价字段取得,或可由确定性派生规则构造。
- found 时 match_kind 必须是 direct_field / semantic_equivalent / derived 之一。
- source_fields / field / key_fields 必须严格来自给定 schema 的 field_code；field 是兼容字段,应等于 source_fields[0]。
- direct_field / semantic_equivalent 使用 {"op": "pass_through"}；derived 必须写出确定性 transform。
- 近似代理或口径不一致不算 found；不确定时输出 not_found。
- filters 不要写时间范围;仅在必须附加样本筛选时填 {"condition": "..."}。
"""

FALLBACK_OUTPUT_SPEC = """语义约束(schema 之外):

- found 表示变量可由 CSMAR 字段直接取得、语义等价字段取得,或可由确定性派生规则构造。
- found 时 database / table / field / source_fields / key_fields / match_kind 必填,且字段必须来自输入或工具结果。
- field 是兼容字段,应等于 source_fields[0]。
- direct_field / semantic_equivalent 使用 {"op": "pass_through"}；derived 必须写出确定性 transform。
- 近似代理或口径不一致不算 found。
- 不确定就返回 not_found;不要猜测库、表或字段。
"""


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


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
    transform: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Deterministic construction rule, e.g. {'op':'pass_through'} "
            "or {'op':'firm_age','date_field':'EstablishDate'}"
        ),
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
    transform: dict[str, Any] | None = None
    evidence: str | None = None
    key_fields: list[str] | None = None
    filters: dict[str, str] | None = None


class BucketVerificationOutput(BaseModel):
    """Verification Agent 单桶一次调用的结构化输出。"""

    results: list[BucketVariableFinding] = []
