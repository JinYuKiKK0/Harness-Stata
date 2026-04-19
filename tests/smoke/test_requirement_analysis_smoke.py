"""Smoke test: requirement_analysis node end-to-end contract.

Validates the state-shape contract at the node boundary (UserRequest →
empirical_spec). LLM is mocked so this runs in default pytest and acts as a
regression guard for plumbing changes (state keys, node signature, dump shape).
Internals and mock interactions belong in tests/nodes/.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from harness_stata.nodes.requirement_analysis import (
    _EmpiricalSpecModel,
    _VariableDefinitionModel,
    requirement_analysis,
)
from harness_stata.state import UserRequest, WorkflowState


def _realistic_spec() -> _EmpiricalSpecModel:
    return _EmpiricalSpecModel(
        variables=[
            _VariableDefinitionModel(
                name="ROA",
                description="总资产收益率",
                contract_type="hard",
                role="dependent",
            ),
            _VariableDefinitionModel(
                name="GOV",
                description="公司治理指数",
                contract_type="hard",
                role="independent",
            ),
            _VariableDefinitionModel(
                name="SIZE",
                description="企业规模（总资产对数）",
                contract_type="soft",
                role="control",
            ),
            _VariableDefinitionModel(
                name="LEV",
                description="资产负债率",
                contract_type="soft",
                role="control",
            ),
        ],
        sample_scope="A股上市公司",
        time_range_start="2018",
        time_range_end="2022",
        data_frequency="yearly",
        analysis_granularity="公司-年度",
    )


def test_requirement_analysis_produces_valid_empirical_spec(
    mocker: Any,
    make_user_request: Callable[..., UserRequest],
) -> None:
    mock_model = mocker.MagicMock()
    mock_model.with_structured_output.return_value.invoke.return_value = _realistic_spec()
    mocker.patch(
        "harness_stata.nodes.requirement_analysis.get_chat_model",
        return_value=mock_model,
    )

    state: WorkflowState = {"user_request": make_user_request()}
    result = requirement_analysis(state)

    assert "empirical_spec" in result
    spec = result["empirical_spec"]

    assert spec["topic"]
    assert spec["sample_scope"]
    assert spec["analysis_granularity"]
    assert spec["time_range_start"] == "2018"
    assert spec["time_range_end"] == "2022"
    assert spec["data_frequency"] == "yearly"

    assert len(spec["variables"]) >= 3
    roles = {v["role"] for v in spec["variables"]}
    assert {"dependent", "independent", "control"} <= roles
