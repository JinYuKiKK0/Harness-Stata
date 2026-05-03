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
from typing import Any, NamedTuple, TypedDict, cast

from harness_stata.state import (
    DownloadManifest,
    DownloadTask,
    EmpiricalSpec,
    ProbeMatchKind,
    ProbeReport,
    VariableDefinition,
    VariableMapping,
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


_MATCH_KINDS: set[ProbeMatchKind] = {
    "direct_field",
    "semantic_equivalent",
    "derived",
}


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
    table_names: dict[str, str]
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
    table_names: dict[str, str] = {}
    failed: list[str] = []
    if not isinstance(raw, dict):
        return BulkSchemaResult(
            schema_dict=schema_dict, table_names=table_names, failed_table_codes=failed
        )
    items = raw.get("items")
    if not isinstance(items, list):
        return BulkSchemaResult(
            schema_dict=schema_dict, table_names=table_names, failed_table_codes=failed
        )

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
        name = _str_or_empty(item.get("table_name"))
        if name:
            table_names[table_code] = name

    return BulkSchemaResult(
        schema_dict=schema_dict, table_names=table_names, failed_table_codes=failed
    )


def format_schema_for_prompt(table_code: str, fields: list[dict[str, Any]]) -> str:
    """Render a single table's schema as a compact markdown pipe table for prompts.

    渲染 3 列: ``code | label | key``。``key`` 取自 CSMAR 上游 ``field_key``
    (典型值 ``Code`` = 主键, ``Date`` = 时间维),空值留空。
    标题里的 N 是渲染后实际行数,不是入参 fields 的原始长度。
    """
    rows: list[str] = []
    for f in fields:
        code = _str_or_empty(f.get("field_code"))
        if not code:
            continue
        label = _cell(f.get("field_label"))
        key = _cell(f.get("field_key"))
        rows.append(f"| {code} | {label} | {key} |")
    header = [
        f"### Table `{table_code}` ({len(rows)} fields)",
        "| code | label | key |",
        "| --- | --- | --- |",
    ]
    return "\n".join(header + rows)


def _cell(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.replace("|", "\\|").replace("\n", " ").strip()


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
        if finding.status != "found":
            continue
        schema = schema_dict.get(bucket_key.table, [])
        normalized = _normalize_bucket_found(bucket_key, finding, schema)
        if normalized is None:
            continue
        return normalized
    return None


def _normalize_bucket_found(
    bucket_key: BucketKey,
    finding: BucketVariableFinding,
    schema: list[dict[str, Any]],
) -> VariableProbeFindingModel | None:
    valid_fields = _valid_schema_fields(schema)
    source_fields = _source_fields_for_finding(finding)
    if not source_fields or any(field not in valid_fields for field in source_fields):
        return None

    key_fields = _key_fields_for_finding(finding)
    if any(field not in valid_fields for field in key_fields):
        return None

    match_kind = _match_kind_for_finding(finding)

    return VariableProbeFindingModel(
        status="found",
        database=bucket_key.database,
        table=bucket_key.table,
        field=source_fields[0],
        match_kind=match_kind,
        source_fields=source_fields,
        evidence=finding.evidence,
        key_fields=key_fields or None,
        filters=dict(finding.filters or {}) or None,
    )


def _valid_schema_fields(schema: list[dict[str, Any]]) -> set[str]:
    return {
        code.strip()
        for f in schema
        if isinstance(code := f.get("field_code"), str) and code.strip()
    }


def _dedupe_nonempty(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def _source_fields_for_finding(
    finding: BucketVariableFinding | VariableProbeFindingModel,
) -> list[str]:
    raw_fields = list(finding.source_fields or [])
    if not raw_fields and finding.field:
        raw_fields.append(finding.field)
    return _dedupe_nonempty(raw_fields)


def _key_fields_for_finding(
    finding: BucketVariableFinding | VariableProbeFindingModel,
) -> list[str]:
    return _dedupe_nonempty(list(finding.key_fields or []))


def _match_kind_for_finding(
    finding: BucketVariableFinding | VariableProbeFindingModel,
) -> ProbeMatchKind:
    match_kind = finding.match_kind or "direct_field"
    if match_kind in _MATCH_KINDS:
        return match_kind
    return "direct_field"


def finding_mapping_failure_reason(finding: VariableProbeFindingModel) -> str | None:
    """Return why a found finding cannot be safely mapped, or None if usable.

    Verification findings are normalized against schema before reaching coverage.
    Fallback findings come directly from a ReAct agent, so coverage calls this
    guard before asking CSMAR to validate raw columns.
    """
    source_fields = _source_fields_for_finding(finding)
    if not source_fields:
        return "found finding has no source_fields or field"
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


def _copy_variable_mappings(raw: object) -> list[VariableMapping]:
    if not isinstance(raw, list):
        return []
    copied: list[VariableMapping] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        variable_name = item.get("variable_name")
        source_fields = item.get("source_fields")
        match_kind = item.get("match_kind")
        if (
            not isinstance(variable_name, str)
            or not isinstance(source_fields, list)
            or match_kind not in _MATCH_KINDS
        ):
            continue
        typed_match_kind = cast(ProbeMatchKind, match_kind)
        mapping = VariableMapping(
            variable_name=variable_name,
            source_fields=[f for f in source_fields if isinstance(f, str)],
            match_kind=typed_match_kind,
        )
        evidence = item.get("evidence")
        if evidence is None or isinstance(evidence, str):
            mapping["evidence"] = evidence
        copied.append(mapping)
    return copied


def ensure_manifest(existing: DownloadManifest | None) -> DownloadManifest:
    """Return a fresh DownloadManifest, defensively deep-copying any prior tasks."""
    if existing is None:
        return DownloadManifest(items=[])
    items: list[DownloadTask] = []
    for item in existing["items"]:
        copied = DownloadTask(
            database=item["database"],
            table=item["table"],
            key_fields=list(item["key_fields"]),
            variable_fields=list(item["variable_fields"]),
            variable_names=list(item["variable_names"]),
            filters=dict(item["filters"]),
        )
        mappings = _copy_variable_mappings(item.get("variable_mappings"))
        if mappings:
            copied["variable_mappings"] = mappings
        items.append(copied)
    return DownloadManifest(items=items)


def _build_variable_mapping(
    var: VariableDefinition,
    finding: VariableProbeFindingModel,
) -> VariableMapping:
    mapping = VariableMapping(
        variable_name=var["name"],
        source_fields=_source_fields_for_finding(finding),
        match_kind=_match_kind_for_finding(finding),
    )
    mapping["evidence"] = finding.evidence
    return mapping


def _upsert_variable_mapping(task: DownloadTask, mapping: VariableMapping) -> None:
    mappings = _copy_variable_mappings(task.get("variable_mappings"))
    for idx, existing in enumerate(mappings):
        if existing["variable_name"] == mapping["variable_name"]:
            mappings[idx] = mapping
            task["variable_mappings"] = mappings
            return
    mappings.append(mapping)
    task["variable_mappings"] = mappings


def build_found_result(
    var: VariableDefinition,
    finding: VariableProbeFindingModel,
    *,
    record_count: int | None,
) -> VariableProbeResult:
    source_fields = _source_fields_for_finding(finding)
    result = VariableProbeResult(
        variable_name=var["name"],
        status="found",
        source=VariableSource(
            database=finding.database or "",
            table=finding.table or "",
            field=source_fields[0] if source_fields else finding.field or "",
        ),
        record_count=record_count,
    )
    result["match_kind"] = _match_kind_for_finding(finding)
    result["source_fields"] = source_fields
    result["evidence"] = finding.evidence
    return result


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
    var_name = current["name"]
    source_fields = _source_fields_for_finding(finding)
    key_fields = _key_fields_for_finding(finding)
    filters_typed = build_download_filters(spec, finding.filters)
    mapping = _build_variable_mapping(current, finding)

    for item in manifest["items"]:
        if item["database"] == database and item["table"] == table:
            for field in source_fields:
                if field not in item["variable_fields"]:
                    item["variable_fields"].append(field)
            if var_name and var_name not in item["variable_names"]:
                item["variable_names"].append(var_name)
            for kf in key_fields:
                if kf not in item["key_fields"]:
                    item["key_fields"].append(kf)
            for k, v in filters_typed.items():
                item["filters"][k] = v
            _upsert_variable_mapping(item, mapping)
            return

    task = DownloadTask(
        database=database,
        table=table,
        key_fields=key_fields,
        variable_fields=source_fields,
        variable_names=[var_name] if var_name else [],
        filters=filters_typed,
    )
    task["variable_mappings"] = [mapping]
    manifest["items"].append(task)


# ---------------------------------------------------------------------------
# Section 5: probe_query payload + 响应解码
# ---------------------------------------------------------------------------


def build_probe_query_payload(
    spec: EmpiricalSpec, finding: VariableProbeFindingModel
) -> dict[str, object]:
    """Produce the kwargs for ``csmar_probe_query`` from a finding + spec.

    ``columns`` 合并 finding 的 key_fields 与 source_fields,顺序保留并去重。
    时间范围由 spec 推导,condition 走 finding.filters.condition(若有)。
    """
    table_code = finding.table or ""
    columns_raw: list[str] = _key_fields_for_finding(finding)
    for field in _source_fields_for_finding(finding):
        if field not in columns_raw:
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
