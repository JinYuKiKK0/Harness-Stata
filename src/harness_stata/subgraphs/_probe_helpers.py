"""Pure helpers for the CSMAR probe subgraph."""

from __future__ import annotations

import calendar
import re

from harness_stata.state import (
    CoreHypothesis,
    EmpiricalSpec,
    ModelPlan,
    VariableDefinition,
    VariableProbeResult,
)

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
