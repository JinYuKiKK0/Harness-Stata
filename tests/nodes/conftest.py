"""Shared fixtures for node-level tests."""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any
from unittest.mock import MagicMock

import pytest

from harness_stata.state import (
    CoreHypothesis,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    SubstitutionTrace,
    VariableDefinition,
    VariableProbeResult,
    VariableSource,
)


@pytest.fixture()
def mock_chat_model(mocker: Any) -> Generator[MagicMock]:
    """Patch get_chat_model at requirement_analysis's import site.

    Kept for F09 unit tests. New node tests should prefer :func:`mock_chat_model_for`.
    """
    mock_model = MagicMock()
    mocker.patch(
        "harness_stata.nodes.requirement_analysis.get_chat_model",
        return_value=mock_model,
    )
    yield mock_model


@pytest.fixture()
def mock_chat_model_for(mocker: Any) -> Callable[[str], MagicMock]:
    """Factory: pass a node module short name, get a patched mock chat model.

    Usage::

        def test_something(mock_chat_model_for: Callable[[str], MagicMock]):
            model = mock_chat_model_for("model_construction")
            model.with_structured_output.return_value.invoke.return_value = ...
    """

    def _make(node_module: str) -> MagicMock:
        m = MagicMock()
        mocker.patch(f"harness_stata.nodes.{node_module}.get_chat_model", return_value=m)
        return m

    return _make


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
        substituted: If True, the control variable (SIZE) is marked substituted
            with a :class:`SubstitutionTrace`; otherwise all three variables are
            "found".
        missing_counts: If True, every ``record_count`` is ``None``.
    """

    def _default_source(table: str, field: str) -> VariableSource:
        return {"database": "CSMAR", "table": table, "field": field}

    def _make(
        substituted: bool = False,
        missing_counts: bool = False,
        **overrides: Any,
    ) -> ProbeReport:
        results: list[VariableProbeResult] = [
            {
                "variable_name": "ROA",
                "status": "found",
                "source": _default_source("FS_COMINS", "ROA"),
                "record_count": None if missing_counts else 38000,
                "substitution_trace": None,
            },
            {
                "variable_name": "DIGITAL",
                "status": "found",
                "source": _default_source("DIG_TRANSFORM", "DIG_INDEX"),
                "record_count": None if missing_counts else 35000,
                "substitution_trace": None,
            },
        ]
        if substituted:
            trace: SubstitutionTrace = {
                "original": "SIZE_RAW",
                "reason": "CSMAR 未提供原始总资产字段",
                "substitute": "SIZE",
                "substitute_description": "总资产取对数",
            }
            results.append(
                {
                    "variable_name": "SIZE",
                    "status": "substituted",
                    "source": _default_source("FS_COMBAS", "A001000000"),
                    "record_count": None if missing_counts else 37000,
                    "substitution_trace": trace,
                }
            )
        else:
            results.append(
                {
                    "variable_name": "SIZE",
                    "status": "found",
                    "source": _default_source("FS_COMBAS", "A001000000"),
                    "record_count": None if missing_counts else 37000,
                    "substitution_trace": None,
                }
            )
        report: ProbeReport = {
            "variable_results": results,
            "overall_status": "success",
            "failure_reason": None,
        }
        report.update(overrides)  # pyright: ignore[reportCallIssue]
        return report

    return _make
