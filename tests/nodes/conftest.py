"""Shared fixtures for node-level tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from harness_stata.state import (
    CoreHypothesis,
    DownloadManifest,
    DownloadTask,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    VariableDefinition,
    VariableProbeResult,
    VariableSource,
)


# ---------------------------------------------------------------------------
# State slice factories (reusable for F17+ downstream node tests)
# ---------------------------------------------------------------------------


def _default_variables() -> list[VariableDefinition]:
    return [
        {
            "name": "ROA",
            "description": "总资产收益率",
            "contract_type": "hard",
            "role": "dependent",
        },
        {
            "name": "DIGITAL",
            "description": "企业数字化转型程度",
            "contract_type": "hard",
            "role": "independent",
        },
        {
            "name": "SIZE",
            "description": "企业规模 (取对数)",
            "contract_type": "soft",
            "role": "control",
        },
    ]


@pytest.fixture()
def make_empirical_spec() -> Callable[..., EmpiricalSpec]:
    """Factory building a complete :class:`EmpiricalSpec` with sensible defaults."""

    def _make(**overrides: Any) -> EmpiricalSpec:
        spec: EmpiricalSpec = {
            "topic": "数字化转型对企业盈利能力的影响",
            "variables": _default_variables(),
            "sample_scope": "A股上市公司",
            "time_range_start": "2015",
            "time_range_end": "2022",
            "data_frequency": "yearly",
            "analysis_granularity": "公司-年度",
        }
        spec.update(overrides)  # pyright: ignore[reportCallIssue]
        return spec

    return _make


@pytest.fixture()
def make_model_plan() -> Callable[..., ModelPlan]:
    """Factory building a complete :class:`ModelPlan` with sensible defaults."""

    def _make(**overrides: Any) -> ModelPlan:
        hypothesis: CoreHypothesis = {
            "variable_name": "DIGITAL",
            "expected_sign": "+",
            "rationale": "数字化转型通过降低信息不对称与交易成本提升企业盈利能力",
        }
        plan: ModelPlan = {
            "model_type": "双向固定效应面板模型",
            "equation": "ROA_it = a + b*DIGITAL_it + g*SIZE_it + mu_i + lambda_t + e_it",
            "core_hypothesis": hypothesis,
            "data_structure_requirements": ["面板结构", "至少两期"],
        }
        plan.update(overrides)  # pyright: ignore[reportCallIssue]
        return plan

    return _make


@pytest.fixture()
def make_probe_report() -> Callable[..., ProbeReport]:
    """Factory building :class:`ProbeReport`.

    Args:
        missing_counts: If True, every ``record_count`` is ``None``.
    """

    def _default_source(table: str, field: str) -> VariableSource:
        return {"database": "CSMAR", "table": table, "field": field}

    def _make(
        missing_counts: bool = False,
        **overrides: Any,
    ) -> ProbeReport:
        results: list[VariableProbeResult] = [
            {
                "variable_name": "ROA",
                "status": "found",
                "source": _default_source("FS_COMINS", "ROA"),
                "record_count": None if missing_counts else 38000,
            },
            {
                "variable_name": "DIGITAL",
                "status": "found",
                "source": _default_source("DIG_TRANSFORM", "DIG_INDEX"),
                "record_count": None if missing_counts else 35000,
            },
            {
                "variable_name": "SIZE",
                "status": "found",
                "source": _default_source("FS_COMBAS", "A001000000"),
                "record_count": None if missing_counts else 37000,
            },
        ]
        report: ProbeReport = {
            "variable_results": results,
            "overall_status": "success",
            "failure_reason": None,
        }
        report.update(overrides)  # pyright: ignore[reportCallIssue]
        return report

    return _make


@pytest.fixture()
def make_download_manifest() -> Callable[..., DownloadManifest]:
    """Factory building a :class:`DownloadManifest` with sensible defaults.

    Args:
        tasks: Optional explicit list of :class:`DownloadTask`; when omitted, a
            single default task targeting CSMAR.FS_COMBAS is used.
    """

    def _default_tasks() -> list[DownloadTask]:
        return [
            {
                "database": "CSMAR",
                "table": "FS_COMBAS",
                "key_fields": ["SYMBOL", "ACCYEAR"],
                "variable_fields": ["A001000000", "A002100000"],
                "variable_names": ["SIZE", "DEBT_RATIO"],
                "filters": {"start_date": "2015-01-01", "end_date": "2022-12-31"},
            }
        ]

    def _make(
        tasks: list[DownloadTask] | None = None,
        **overrides: Any,
    ) -> DownloadManifest:
        manifest: DownloadManifest = {
            "items": tasks if tasks is not None else _default_tasks()
        }
        manifest.update(overrides)  # pyright: ignore[reportCallIssue]
        return manifest

    return _make
