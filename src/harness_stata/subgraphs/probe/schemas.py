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

PLANNING_OUTPUT_SPEC = """你的输出必须严格按 PlanningOutput schema 填写,不要输出自然语言。规则:

- plans 必须为输入中**全部**待处理变量给出一条记录。
- target_database 取自工具返回的已购数据库名,不要自行拼写或翻译。
- candidate_table_codes 必须**完全来自 csmar_list_tables 工具返回的 table_code**,
  禁止盲猜或缩写。一个变量可以给 1~N 张候选表(N 不要超过 3),按相关度从高到低排序。
- 不要在本阶段判定字段是否存在 — 只负责"该变量大概率位于哪些表"。
- 若你确实推断不出某变量的候选表,把 candidate_table_codes 留空 list 并照常输出该变量。
"""

VERIFICATION_OUTPUT_SPEC = """你的输出必须严格按 BucketVerificationOutput schema 填写。
本桶对应一张表(database / table_code 已在 prompt 中给出),你的任务是判定本桶里
每个变量是否能在给定 schema 中找到对应字段。规则:

- results 必须为输入中**全部**变量给出一条记录,顺序保持一致。
- field 必须**严格出自给定 schema 的 field_code**,绝不能创造 schema 之外的列名。
- key_fields 同样从 schema 中挑选,通常是主键 + 时间键。
- filters 不要写时间范围,只在 CSMAR 需要额外样本筛选时填 {"condition": "..."}。
- 不要输出 database / table — 由代码层从 bucket key 回填。
"""

FALLBACK_OUTPUT_SPEC = """你的探测结论必须按 VariableProbeFindingModel schema 输出。
status 只能是 found / not_found;found 时 database / table / field / key_fields 必填。
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
    record_count: int | None = Field(
        default=None, description="Record count if reported by the agent"
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
    key_fields: list[str] | None = None
    filters: dict[str, str] | None = None


class BucketVerificationOutput(BaseModel):
    """Verification Agent 单桶一次调用的结构化输出。"""

    results: list[BucketVariableFinding] = []
