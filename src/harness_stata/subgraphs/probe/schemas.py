"""Pydantic schemas + LLM-facing 输出规约字符串。

本文件只放 **Pydantic BaseModel + 给 LLM 看的输出规约字符串**:

- :class:`VariableProbeFindingModel` — 单变量探测的结构化输出 (Planning/Fallback ReAct 共用)
- :class:`VariablePlan` / :class:`PlanningOutput` — Planning Agent 的结构化输出
- :class:`BucketVariableFinding` / :class:`BucketVerificationOutput` — Verification 单桶判定的结构化输出
- ``PLANNING_OUTPUT_SPEC`` / ``VERIFICATION_OUTPUT_SPEC`` / ``FALLBACK_OUTPUT_SPEC`` — 三段拼接进 system prompt 的 LLM 输出规约

NamedTuple 类(:class:`BucketKey` / :class:`BulkSchemaResult`)是纯逻辑函数的返回类型,
留在 :mod:`harness_stata.subgraphs.probe.pure` 不进本文件。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# LLM 输出规约 (供 config 层拼接进 system prompt)
# ---------------------------------------------------------------------------

PLANNING_OUTPUT_SPEC = """语义约束(schema 之外):

- candidate_table_codes 必须**完全来自 csmar_list_tables 返回的 table_code**,
  禁止盲猜或缩写;一个变量给 1~3 张,按相关度从高到低排序。
- 不要在本阶段判定字段是否存在 — 只负责"该变量大概率位于哪些表"。
- 推断不出候选表时,留空 list 并照常输出该变量。
"""

VERIFICATION_OUTPUT_SPEC = """语义约束(schema 之外):

- field / key_fields 必须**严格出自给定 schema 的 field_code**,
  绝不能创造 schema 之外的列名。
- filters 不要写时间范围;仅在 CSMAR 需要额外样本筛选时填 {"condition": "..."}。
- 不要输出 database / table — 由代码层从 bucket key 回填。
"""

FALLBACK_OUTPUT_SPEC = """语义约束(schema 之外):

- found 时 database / table / field / key_fields 必填;不确定就返回 not_found。
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
    key_fields: list[str] | None = None
    filters: dict[str, str] | None = None


class BucketVerificationOutput(BaseModel):
    """Verification Agent 单桶一次调用的结构化输出。"""

    results: list[BucketVariableFinding] = []
