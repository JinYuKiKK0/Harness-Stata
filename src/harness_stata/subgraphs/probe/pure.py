"""Pure helpers for the CSMAR probe subgraph (无 LangChain/LangGraph 依赖)。

按 5 个 section 组织,全部为无副作用、无外部依赖的纯函数:

1. 时间归一化与 download_manifest filters 构造
2. ``csmar_bulk_schema`` 响应解码 + prompt schema 块格式化
3. (variable, candidate_table) 笛卡尔分桶 + 多桶 verification 输出合并
4. ProbeReport / DownloadManifest 的构造与合并
5. ``csmar_probe_query`` payload 构造与响应解码

NamedTuple :class:`BucketKey` / :class:`BulkSchemaResult` 是分桶/解码函数的返回类型,
TypedDict :class:`PendingValidation` / :class:`CoverageOutcome` 是节点间流转/解码的
桥接类型,本质都是 pure 函数的输入/返回类型,与 pure 函数 colocate 在本文件。
``run_probe_coverage`` 含 await 调用,不属于纯逻辑,归
:mod:`harness_stata.subgraphs.probe.nodes.coverage`。
"""

from __future__ import annotations

import calendar
import re
from typing import Any, NamedTuple, TypedDict

from harness_stata.state import (
    DownloadManifest,
    DownloadTask,
    EmpiricalSpec,
    ProbeReport,
    VariableDefinition,
    VariableProbeResult,
    VariableSource,
)
from harness_stata.subgraphs.probe.schemas import (
    BucketVariableFinding,
    BucketVerificationOutput,
    VariablePlan,
    VariableProbeFindingModel,
)


class PendingValidation(TypedDict):
    """A field-level finding waiting for the coverage-validation phase."""

    variable: VariableDefinition
    finding: VariableProbeFindingModel


class CoverageOutcome(TypedDict):
    """Decoded result of a single ``csmar_probe_query`` call."""

    can_materialize: bool
    invalid_columns: list[str]
    validation_id: str | None
    row_count: int | None
    failure_reason: str | None


# ---------------------------------------------------------------------------
# Section 1: 时间归一化 + download filters
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
_YEAR_RE = re.compile(r"^\d{4}$")
_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$", re.IGNORECASE)


def normalize_time_bound(value: str, *, is_start: bool) -> str:
    raw = value.strip()
    if _DATE_RE.fullmatch(raw):
        return raw
    if _YEAR_RE.fullmatch(raw):
        return f"{raw}-01-01" if is_start else f"{raw}-12-31"
    if match := _YEAR_MONTH_RE.fullmatch(raw):
        year = int(match.group(1))
        month = int(match.group(2))
        if not 1 <= month <= 12:
            msg = f"invalid month in time bound {value!r}"
            raise ValueError(msg)
        day = 1 if is_start else calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-{day:02d}"
    if match := _QUARTER_RE.fullmatch(raw):
        year = int(match.group(1))
        quarter = int(match.group(2))
        start_month = (quarter - 1) * 3 + 1
        month = start_month if is_start else start_month + 2
        day = 1 if is_start else calendar.monthrange(year, month)[1]
        return f"{year:04d}-{month:02d}-{day:02d}"
    msg = f"unsupported time bound {value!r}; expected YYYY, YYYY-MM, YYYY-MM-DD, or YYYYQn"
    raise ValueError(msg)


def build_download_filters(
    spec: EmpiricalSpec, raw_filters: dict[str, str] | None
) -> dict[str, object]:
    filters: dict[str, object] = {
        "start_date": normalize_time_bound(spec["time_range_start"], is_start=True),
        "end_date": normalize_time_bound(spec["time_range_end"], is_start=False),
    }
    condition = (raw_filters or {}).get("condition")
    if isinstance(condition, str) and condition.strip():
        filters["condition"] = condition.strip()
    return filters


# ---------------------------------------------------------------------------
# Section 2: bulk_schema 响应解码 + prompt 渲染
# ---------------------------------------------------------------------------


class BucketKey(NamedTuple):
    database: str
    table: str


class BulkSchemaResult(NamedTuple):
    schema_dict: dict[str, list[dict[str, Any]]]
    failed_table_codes: list[str]


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
    items = raw.get("items")
    if not isinstance(items, list):
        return BulkSchemaResult(schema_dict=schema_dict, failed_table_codes=failed)

    for item in items:
        if not isinstance(item, dict):
            continue
        table_code = _str_or_empty(item.get("table_code"))
        if not table_code:
            continue
        fields_raw = item.get("fields")
        if item.get("error") is not None or not isinstance(fields_raw, list):
            failed.append(table_code)
            continue
        fields: list[dict[str, Any]] = [f for f in fields_raw if isinstance(f, dict)]
        schema_dict[table_code] = fields

    return BulkSchemaResult(schema_dict=schema_dict, failed_table_codes=failed)


def format_schema_for_prompt(table_code: str, fields: list[dict[str, Any]]) -> str:
    """Render a single table's schema as a compact markdown block for prompts."""
    lines = [f"### Table `{table_code}` ({len(fields)} fields)"]
    for f in fields:
        name = f.get("field_code") or ""
        if not name:
            continue
        label = f.get("field_label") or ""
        suffix = f" — {label}" if label else ""
        lines.append(f"- `{name}`{suffix}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 3: 分桶 + 多桶结果合并
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
    2. 否则 → 纯 not_found
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
        results.append((var, VariableProbeFindingModel(status="not_found")))
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
            f["field_code"].strip() for f in schema if isinstance(f.get("field_code"), str)
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


# ---------------------------------------------------------------------------
# Section 4: ProbeReport / DownloadManifest 构造
# ---------------------------------------------------------------------------


def ensure_report(existing: ProbeReport | None) -> ProbeReport:
    """Return a fresh ProbeReport, defensively copying any prior variable_results."""
    if existing is None:
        return ProbeReport(variable_results=[], overall_status="success", failure_reason=None)
    return ProbeReport(
        variable_results=list(existing["variable_results"]),
        overall_status=existing["overall_status"],
        failure_reason=existing["failure_reason"],
    )


def ensure_manifest(existing: DownloadManifest | None) -> DownloadManifest:
    """Return a fresh DownloadManifest, defensively deep-copying any prior tasks."""
    if existing is None:
        return DownloadManifest(items=[])
    items: list[DownloadTask] = [
        DownloadTask(
            database=item["database"],
            table=item["table"],
            key_fields=list(item["key_fields"]),
            variable_fields=list(item["variable_fields"]),
            variable_names=list(item["variable_names"]),
            filters=dict(item["filters"]),
        )
        for item in existing["items"]
    ]
    return DownloadManifest(items=items)


def build_found_result(
    var: VariableDefinition,
    finding: VariableProbeFindingModel,
    *,
    record_count: int | None,
) -> VariableProbeResult:
    return VariableProbeResult(
        variable_name=var["name"],
        status="found",
        source=VariableSource(
            database=finding.database or "",
            table=finding.table or "",
            field=finding.field or "",
        ),
        record_count=record_count,
    )


def build_not_found_result(variable_name: str) -> VariableProbeResult:
    return VariableProbeResult(
        variable_name=variable_name,
        status="not_found",
        source=None,
        record_count=None,
    )


def merge_into_manifest(
    manifest: DownloadManifest,
    current: VariableDefinition,
    finding: VariableProbeFindingModel,
    spec: EmpiricalSpec,
) -> None:
    """Append a new DownloadTask or merge into an existing one by (database, table)."""
    database = finding.database or ""
    table = finding.table or ""
    field = finding.field or ""
    var_name = current["name"]
    key_fields = list(finding.key_fields or [])
    filters_typed = build_download_filters(spec, finding.filters)

    for item in manifest["items"]:
        if item["database"] == database and item["table"] == table:
            if field and field not in item["variable_fields"]:
                item["variable_fields"].append(field)
            if var_name and var_name not in item["variable_names"]:
                item["variable_names"].append(var_name)
            for kf in key_fields:
                if kf not in item["key_fields"]:
                    item["key_fields"].append(kf)
            for k, v in filters_typed.items():
                item["filters"][k] = v
            return

    manifest["items"].append(
        DownloadTask(
            database=database,
            table=table,
            key_fields=key_fields,
            variable_fields=[field] if field else [],
            variable_names=[var_name] if var_name else [],
            filters=filters_typed,
        )
    )


# ---------------------------------------------------------------------------
# Section 5: probe_query payload + 响应解码
# ---------------------------------------------------------------------------


def build_probe_query_payload(
    spec: EmpiricalSpec, finding: VariableProbeFindingModel
) -> dict[str, object]:
    """Produce the kwargs for ``csmar_probe_query`` from a finding + spec.

    ``columns`` 合并 finding 的 key_fields 与 field,顺序保留并去重。
    时间范围由 spec 推导,condition 走 finding.filters.condition(若有)。
    """
    table_code = finding.table or ""
    field = finding.field or ""
    columns_raw: list[str] = list(finding.key_fields or [])
    if field and field not in columns_raw:
        columns_raw.append(field)
    columns = list(dict.fromkeys(columns_raw))

    payload: dict[str, object] = {
        "table_code": table_code,
        "columns": columns,
        "start_date": normalize_time_bound(spec["time_range_start"], is_start=True),
        "end_date": normalize_time_bound(spec["time_range_end"], is_start=False),
    }
    if finding.filters:
        condition = finding.filters.get("condition")
        if isinstance(condition, str) and condition.strip():
            payload["condition"] = condition.strip()
    return payload


def parse_probe_query_response(raw: object, context: str) -> CoverageOutcome:
    """Decode the langchain-mcp-adapters return value of ``csmar_probe_query``.

    Failure is recorded into :class:`CoverageOutcome` with ``can_materialize=False``
    rather than raised; the calling handler routes hard/soft per the variable
    contract.
    """
    if not isinstance(raw, dict):
        return CoverageOutcome(
            can_materialize=False,
            invalid_columns=[],
            validation_id=None,
            row_count=None,
            failure_reason=f"{context}: expected dict response, got {type(raw).__name__}",
        )
    payload = raw

    invalid_columns = _coerce_string_list(payload.get("invalid_columns"))
    row_count = _coerce_int_or_none(payload.get("row_count"))

    if payload.get("can_materialize") is not True:
        return CoverageOutcome(
            can_materialize=False,
            invalid_columns=invalid_columns,
            validation_id=None,
            row_count=row_count,
            failure_reason=(
                f"{context}: can_materialize={payload.get('can_materialize')!r}, "
                f"invalid_columns={invalid_columns!r}"
            ),
        )

    validation_id = payload.get("validation_id")
    if not isinstance(validation_id, str) or not validation_id:
        return CoverageOutcome(
            can_materialize=False,
            invalid_columns=invalid_columns,
            validation_id=None,
            row_count=row_count,
            failure_reason=f"{context}: missing validation_id",
        )

    return CoverageOutcome(
        can_materialize=True,
        invalid_columns=[],
        validation_id=validation_id,
        row_count=row_count,
        failure_reason=None,
    )


def _coerce_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _coerce_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
