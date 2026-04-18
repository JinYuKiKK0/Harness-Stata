"""Smoke test: model_construction node end-to-end contract.

Validates the state-shape contract at the node boundary (EmpiricalSpec →
model_plan). LLM is mocked so this runs in default pytest and acts as a
regression guard for plumbing changes (state keys, node signature, dump shape).
Internals and mock interactions belong in tests/nodes/.
"""

from __future__ import annotations

from typing import Any

from harness_stata.nodes.model_construction import (
    _CoreHypothesisModel,
    _ModelPlanModel,
    model_construction,
)
from harness_stata.state import EmpiricalSpec, WorkflowState


def _realistic_empirical_spec() -> EmpiricalSpec:
    return {
        "topic": "公司治理质量对财务绩效的影响研究",
        "variables": [
            {
                "name": "ROA",
                "description": "总资产收益率",
                "contract_type": "hard",
                "role": "dependent",
            },
            {
                "name": "GOV",
                "description": "公司治理质量指数",
                "contract_type": "hard",
                "role": "independent",
            },
            {
                "name": "SIZE",
                "description": "企业规模（总资产对数）",
                "contract_type": "soft",
                "role": "control",
            },
            {
                "name": "LEV",
                "description": "资产负债率",
                "contract_type": "soft",
                "role": "control",
            },
        ],
        "sample_scope": "A股上市公司",
        "time_range_start": "2018",
        "time_range_end": "2022",
        "data_frequency": "yearly",
        "analysis_granularity": "公司-年度",
    }


def _realistic_model_plan() -> _ModelPlanModel:
    return _ModelPlanModel(
        model_type="双向固定效应面板模型",
        equation="ROA_it = α + β₁GOV_it + γ'Z_it + μ_i + λ_t + ε_it",
        core_hypothesis=_CoreHypothesisModel(
            variable_name="GOV",
            expected_sign="+",
            rationale="代理成本理论下, 高治理质量降低代理冲突, 提升资产配置效率与盈利能力",
        ),
        data_structure_requirements=["面板结构", "至少两期", "允许非平衡面板"],
    )


def test_model_construction_produces_valid_model_plan(mocker: Any) -> None:
    mock_model = mocker.MagicMock()
    mock_model.with_structured_output.return_value.invoke.return_value = _realistic_model_plan()
    mocker.patch(
        "harness_stata.nodes.model_construction.get_chat_model",
        return_value=mock_model,
    )

    state: WorkflowState = {"empirical_spec": _realistic_empirical_spec()}
    result = model_construction(state)

    assert "model_plan" in result
    plan = result["model_plan"]

    assert set(plan.keys()) >= {
        "model_type",
        "equation",
        "core_hypothesis",
        "data_structure_requirements",
    }
    assert plan["model_type"]
    assert plan["equation"]
    assert isinstance(plan["data_structure_requirements"], list)
    assert len(plan["data_structure_requirements"]) >= 1

    ch = plan["core_hypothesis"]
    assert set(ch.keys()) == {"variable_name", "expected_sign", "rationale"}
    assert ch["variable_name"]
    assert ch["expected_sign"] in {"+", "-", "ambiguous"}
    assert ch["rationale"]
