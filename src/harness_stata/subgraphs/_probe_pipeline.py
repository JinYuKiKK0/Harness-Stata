"""Pure helpers for the batch field-discovery pipeline (probe subgraph).

本模块只承载 **批量字段发现** 流水线相关的纯逻辑:

- Planning Agent / Verification 单桶的结构化输出 schema 与 prompt 输出规约
- ``csmar_bulk_schema`` 响应解码
- ``(variable, candidate_table)`` 笛卡尔展开 / 分桶
- 多桶 verification 输出合并(任一 found / substitute 兜底)
- prompt 用 schema 块格式化

所有依赖 :class:`VariableProbeFindingModel` 等公共 schema 的入口都从
``_probe_helpers`` 复用,不重复定义。子图工厂 ``build_probe_subgraph`` 通过本模块
完成所有"代码主导"的批量加工,LLM 节点只在工厂闭包里调用。
"""

from __future__ import annotations

from typing import Any, Literal, NamedTuple, cast

from pydantic import BaseModel

from harness_stata.state import VariableDefinition
from harness_stata.subgraphs._probe_helpers import VariableProbeFindingModel

# ---------------------------------------------------------------------------
# Prompt 输出规约 (供工厂闭包拼接 system prompt)
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
- field 必须**严格出自给定 schema 的 field_name**,绝不能创造 schema 之外的列名。
- key_fields 同样从 schema 中挑选,通常是主键 + 时间键。
- filters 不要写时间范围,只在 CSMAR 需要额外样本筛选时填 {"condition": "..."}。
- substitute 候选只在 status="not_found" + 该变量是 soft 契约时才允许填写;
  hard 变量绝对不要填 substitute 字段。
- 不要输出 database / table — 由代码层从 bucket key 回填。
"""


# ---------------------------------------------------------------------------
# 结构化输出 schema
# ---------------------------------------------------------------------------


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
    candidate_substitute_name: str | None = None
    candidate_substitute_description: str | None = None
    candidate_substitute_reason: str | None = None


class BucketVerificationOutput(BaseModel):
    """Verification Agent 单桶一次调用的结构化输出。"""

    results: list[BucketVariableFinding] = []


class BucketKey(NamedTuple):
    database: str
    table: str


class BulkSchemaResult(NamedTuple):
    schema_dict: dict[str, list[dict[str, Any]]]
    failed_table_codes: list[str]


# ---------------------------------------------------------------------------
# bulk_schema 解析
# ---------------------------------------------------------------------------


def _str_or_empty(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_bulk_schema_response(raw: object) -> BulkSchemaResult:
    """Decode csmar_bulk_schema response into a table_code → fields dictionary.

    Accepts the dict shape produced by langchain-mcp-adapters wrapping
    ``BulkSchemaOutput``. Items with ``error != null`` or missing ``fields`` are
    moved to ``failed_table_codes``;调用方据此从候选清单中剔除。
    """
    schema_dict: dict[str, list[dict[str, Any]]] = {}
    failed: list[str] = []
    if not isinstance(raw, dict):
        return BulkSchemaResult(schema_dict=schema_dict, failed_table_codes=failed)
    payload = cast("dict[str, Any]", raw)
    items = payload.get("items")
    if not isinstance(items, list):
        return BulkSchemaResult(schema_dict=schema_dict, failed_table_codes=failed)

    for item in cast("list[Any]", items):
        if not isinstance(item, dict):
            continue
        item_dict = cast("dict[str, Any]", item)
        table_code = _str_or_empty(item_dict.get("table_code"))
        if not table_code:
            continue
        fields_raw = item_dict.get("fields")
        if item_dict.get("error") is not None or not isinstance(fields_raw, list):
            failed.append(table_code)
            continue
        fields: list[dict[str, Any]] = []
        for f in cast("list[Any]", fields_raw):
            if isinstance(f, dict):
                fields.append(cast("dict[str, Any]", f))
        schema_dict[table_code] = fields

    return BulkSchemaResult(schema_dict=schema_dict, failed_table_codes=failed)


def format_schema_for_prompt(table_code: str, fields: list[dict[str, Any]]) -> str:
    """Render a single table's schema as a compact markdown block for prompts."""
    lines = [f"### Table `{table_code}` ({len(fields)} fields)"]
    for f in fields:
        name = f.get("field_name") or ""
        if not name:
            continue
        label = f.get("field_label") or ""
        dtype = f.get("data_type") or ""
        desc = f.get("field_description") or ""
        suffix_parts: list[str] = []
        if label:
            suffix_parts.append(label)
        if dtype:
            suffix_parts.append(f"type={dtype}")
        if desc:
            suffix_parts.append(desc)
        suffix = f" — {' | '.join(suffix_parts)}" if suffix_parts else ""
        lines.append(f"- `{name}`{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 分桶 / 合并
# ---------------------------------------------------------------------------


def bucket_plans(
    plans: list[VariablePlan],
    variables_by_name: dict[str, VariableDefinition],
    schema_dict: dict[str, list[dict[str, Any]]],
) -> tuple[
    dict[BucketKey, list[VariableDefinition]],
    list[VariableDefinition],
]:
    """Cartesian-explode (variable, candidate_table) pairs into bucket buckets.

    Returns ``(buckets, unplanned)`` where:

    - ``buckets[(db, table)]`` 是一组变量定义,代表该桶 LLM 调用要判定的变量
    - ``unplanned`` 是 plan 完全没给候选表(或所有候选表都不在 schema_dict 中)的变量
    """
    buckets: dict[BucketKey, list[VariableDefinition]] = {}
    unplanned: list[VariableDefinition] = []

    for plan in plans:
        var = variables_by_name.get(plan.variable_name)
        if var is None:
            continue
        candidates = [c for c in plan.candidate_table_codes if c in schema_dict]
        if not candidates:
            unplanned.append(var)
            continue
        for table_code in candidates:
            key = BucketKey(database=plan.target_database, table=table_code)
            buckets.setdefault(key, []).append(var)

    return buckets, unplanned


def merge_bucket_results(
    bucket_outputs: list[tuple[BucketKey, BucketVerificationOutput]],
    planned_variables: list[VariableDefinition],
    schema_dict: dict[str, list[dict[str, Any]]],
) -> list[tuple[VariableDefinition, VariableProbeFindingModel]]:
    """Collapse per-bucket outputs into a single finding per variable.

    优先级:
    1. 任一桶判定 found(且 field 在该桶 schema 中存在)→ 取第一个有效 found
    2. 全部 not_found → 取首个携带 substitute 候选的桶,合成 not_found+substitute
    3. 否则 → 纯 not_found
    """
    by_var_name = {v["name"]: v for v in planned_variables}
    per_var_findings: dict[str, list[tuple[BucketKey, BucketVariableFinding]]] = {
        v["name"]: [] for v in planned_variables
    }

    for bucket_key, output in bucket_outputs:
        for finding in output.results:
            if finding.variable_name in per_var_findings:
                per_var_findings[finding.variable_name].append((bucket_key, finding))

    results: list[tuple[VariableDefinition, VariableProbeFindingModel]] = []
    for name, var in by_var_name.items():
        bucket_findings = per_var_findings.get(name, [])
        chosen_found = _pick_first_valid_found(bucket_findings, schema_dict)
        if chosen_found is not None:
            results.append((var, chosen_found))
            continue
        results.append((var, _build_not_found_with_substitute(bucket_findings, var)))
    return results


def _pick_first_valid_found(
    bucket_findings: list[tuple[BucketKey, BucketVariableFinding]],
    schema_dict: dict[str, list[dict[str, Any]]],
) -> VariableProbeFindingModel | None:
    for bucket_key, finding in bucket_findings:
        if finding.status != "found" or not finding.field:
            continue
        schema = schema_dict.get(bucket_key.table, [])
        valid_fields = {
            cast("str", f["field_name"]).strip()
            for f in schema
            if isinstance(f.get("field_name"), str)
        }
        if finding.field.strip() not in valid_fields:
            continue
        return VariableProbeFindingModel(
            status="found",
            database=bucket_key.database,
            table=bucket_key.table,
            field=finding.field.strip(),
            key_fields=list(finding.key_fields or []) or None,
            filters=dict(finding.filters or {}) or None,
        )
    return None


def _build_not_found_with_substitute(
    bucket_findings: list[tuple[BucketKey, BucketVariableFinding]],
    var: VariableDefinition,
) -> VariableProbeFindingModel:
    if var["contract_type"] == "soft":
        for _, finding in bucket_findings:
            if finding.candidate_substitute_name and finding.candidate_substitute_description:
                return VariableProbeFindingModel(
                    status="not_found",
                    candidate_substitute_name=finding.candidate_substitute_name,
                    candidate_substitute_description=finding.candidate_substitute_description,
                    candidate_substitute_reason=finding.candidate_substitute_reason,
                )
    return VariableProbeFindingModel(status="not_found")
