"""Pure helpers for the CSMAR probe subgraph (报告/manifest/coverage 部分)。

本模块只保留与 **单变量结果固化** 相关的纯逻辑:

- 时间归一化与 download_manifest 的 filters 构造
- 变量替换(EmpiricalSpec / ModelPlan 同步改写)
- 单变量探测的结构化输出 schema (:class:`VariableProbeFindingModel`)
- ProbeReport / DownloadManifest 的构造与合并 helper
- probe_query (覆盖率验证)阶段的 payload 构造 / 响应解码 / 异步执行入口

**批量字段发现流水线**(Planning Agent / Bulk Schema / Verification 分桶 / 桶级合并)
独立放在 :mod:`harness_stata.subgraphs._probe_pipeline`,以保持每个文件聚焦单一职责。
"""

from __future__ import annotations

import calendar
import re
from typing import Any, Literal, TypedDict, cast

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from harness_stata.state import (
    CoreHypothesis,
    DownloadManifest,
    DownloadTask,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    SubstitutionTrace,
    VariableDefinition,
    VariableProbeResult,
    VariableSource,
)

# ---------------------------------------------------------------------------
# Time / filter normalization
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")
_YEAR_RE = re.compile(r"^\d{4}$")
_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$", re.IGNORECASE)
_TOKEN_LEFT = r"(?<![A-Za-z0-9_])"
_TOKEN_RIGHT = r"(?![A-Za-z0-9_])"


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


def replace_variable_in_spec(
    spec: EmpiricalSpec, original_name: str, substitute: VariableDefinition
) -> EmpiricalSpec:
    new_vars: list[VariableDefinition] = [
        substitute if v["name"] == original_name else v for v in spec["variables"]
    ]
    return EmpiricalSpec(
        topic=spec["topic"],
        variables=new_vars,
        sample_scope=spec["sample_scope"],
        time_range_start=spec["time_range_start"],
        time_range_end=spec["time_range_end"],
        data_frequency=spec["data_frequency"],
        analysis_granularity=spec["analysis_granularity"],
    )


def replace_variable_in_model_plan(
    plan: ModelPlan,
    variable_results: list[VariableProbeResult],
) -> ModelPlan:
    replacements: list[tuple[str, str]] = []
    for result in variable_results:
        trace = result["substitution_trace"]
        if result["status"] == "substituted" and trace is not None:
            replacements.append((trace["original"], trace["substitute"]))

    if not replacements:
        return plan

    equation = plan["equation"]
    requirements = list(plan["data_structure_requirements"])
    hypothesis = CoreHypothesis(**plan["core_hypothesis"])
    for original, substitute in replacements:
        equation = _replace_token(equation, original, substitute)
        requirements = [_replace_token(req, original, substitute) for req in requirements]
        if hypothesis["variable_name"] == original:
            hypothesis["variable_name"] = substitute

    return ModelPlan(
        model_type=plan["model_type"],
        equation=equation,
        core_hypothesis=hypothesis,
        data_structure_requirements=requirements,
    )


def _replace_token(text: str, original_name: str, substitute_name: str) -> str:
    pattern = re.compile(f"{_TOKEN_LEFT}{re.escape(original_name)}{_TOKEN_RIGHT}")
    return pattern.sub(substitute_name, text)


# ---------------------------------------------------------------------------
# Structured-output schema (consumed by create_agent response_format)
# ---------------------------------------------------------------------------

OUTPUT_SPEC = """你的探测结论必须直接按给定 schema 的字段填写,不要输出自然语言总结。字段规则:

- status="found" 要求 database / table / field 三字段非空;key_fields 填写主键/时间键列名。
- status="not_found" 时 source/key_fields/filters 保持 null 或空。
- soft 变量若没找到,但你在探测中发现了合理的替代变量,填写
  candidate_substitute_name / candidate_substitute_description / candidate_substitute_reason;
  否则三者留空。hard 变量不要填 substitute 字段。
- filters 不要写时间范围;运行时会从 EmpiricalSpec.time_range_start/end
  自动生成 start_date/end_date。若 CSMAR 需要额外样本筛选,只允许填写
  {"condition": "..."}。
- 不要编造探测未覆盖的信息;不确定就留 null。
- record_count 留 null 即可:行数与覆盖率验证由后续阶段以代码完成,你不需要在此估算。
"""


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
    candidate_substitute_name: str | None = Field(
        default=None, description="Soft+not_found only: candidate substitute variable name"
    )
    candidate_substitute_description: str | None = Field(
        default=None, description="Soft+not_found only: candidate substitute description"
    )
    candidate_substitute_reason: str | None = Field(
        default=None, description="Soft+not_found only: why this substitute fits"
    )


class SubstituteMeta(TypedDict):
    """Bookkeeping for a substitute task enqueued for soft+not_found."""

    original_name: str
    reason: str


class PendingValidation(TypedDict):
    """A field-level finding waiting for the coverage-validation phase."""

    variable: VariableDefinition
    finding: VariableProbeFindingModel
    is_substitute_task: bool


class CoverageOutcome(TypedDict):
    """Decoded result of a single ``csmar_probe_query`` call."""

    can_materialize: bool
    invalid_columns: list[str]
    validation_id: str | None
    row_count: int | None
    failure_reason: str | None


class CoverageEntry(TypedDict):
    """Pairing of a pending validation with the probe outcome it produced."""

    pending: PendingValidation
    outcome: CoverageOutcome


# ---------------------------------------------------------------------------
# Report / manifest construction
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
    record_count: int | None = None,
) -> VariableProbeResult:
    rc = record_count if record_count is not None else finding.record_count
    return VariableProbeResult(
        variable_name=var["name"],
        status="found",
        source=VariableSource(
            database=finding.database or "",
            table=finding.table or "",
            field=finding.field or "",
        ),
        record_count=rc,
        substitution_trace=None,
    )


def build_substituted_result(
    meta: SubstituteMeta,
    sub_var: VariableDefinition,
    finding: VariableProbeFindingModel,
    *,
    record_count: int | None = None,
) -> VariableProbeResult:
    rc = record_count if record_count is not None else finding.record_count
    return VariableProbeResult(
        variable_name=meta["original_name"],
        status="substituted",
        source=VariableSource(
            database=finding.database or "",
            table=finding.table or "",
            field=finding.field or "",
        ),
        record_count=rc,
        substitution_trace=SubstitutionTrace(
            original=meta["original_name"],
            reason=meta["reason"],
            substitute=sub_var["name"],
            substitute_description=sub_var["description"],
        ),
    )


def build_not_found_result(variable_name: str) -> VariableProbeResult:
    return VariableProbeResult(
        variable_name=variable_name,
        status="not_found",
        source=None,
        record_count=None,
        substitution_trace=None,
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


def maybe_build_substitute(
    finding: VariableProbeFindingModel, current: VariableDefinition
) -> VariableDefinition | None:
    if not (finding.candidate_substitute_name and finding.candidate_substitute_description):
        return None
    return VariableDefinition(
        name=finding.candidate_substitute_name,
        description=finding.candidate_substitute_description,
        contract_type="soft",
        role=current["role"],
    )


# ---------------------------------------------------------------------------
# Coverage-validation phase: build payload, decode response, run probe_query
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
    payload = cast("dict[str, Any]", raw)

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


async def run_probe_coverage(
    probe_tool: BaseTool, payload: dict[str, object], context: str
) -> CoverageOutcome:
    """Invoke the probe_query tool and decode the response into CoverageOutcome.

    任何调用抛出的异常都在本函数捕获,转写为 ``can_materialize=False`` 的 outcome。
    上游 coverage_validation_handler 据此走 hard/soft 路由,不再抛 RuntimeError。
    """
    try:
        raw = await probe_tool.ainvoke(payload)  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        return CoverageOutcome(
            can_materialize=False,
            invalid_columns=[],
            validation_id=None,
            row_count=None,
            failure_reason=f"{context}: probe_query call failed: {exc}",
        )
    return parse_probe_query_response(raw, context)


def _coerce_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items = cast("list[Any]", value)
    return [item for item in items if isinstance(item, str)]


def _coerce_int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
