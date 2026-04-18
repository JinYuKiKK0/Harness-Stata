"""Unit tests for the model_construction node."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

from harness_stata.nodes.model_construction import (
    _CoreHypothesisModel,
    _ModelPlanModel,
    _format_empirical_spec,
    model_construction,
)
from harness_stata.state import EmpiricalSpec, WorkflowState


def _make_empirical_spec() -> EmpiricalSpec:
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


def _make_fake_model_plan() -> _ModelPlanModel:
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


def _wire_mock_chain(mock_model: MagicMock, fake_plan: _ModelPlanModel) -> MagicMock:
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = fake_plan
    mock_model.with_structured_output.return_value = mock_chain
    return mock_chain


# ---------------------------------------------------------------------------
# _format_empirical_spec (pure function)
# ---------------------------------------------------------------------------


class TestFormatEmpiricalSpec:
    def test_top_level_fields_present(self) -> None:
        msg = _format_empirical_spec(_make_empirical_spec())
        assert "公司治理质量对财务绩效的影响研究" in msg
        assert "A股上市公司" in msg
        assert "2018" in msg
        assert "2022" in msg
        assert "yearly" in msg
        assert "公司-年度" in msg

    def test_variables_table_covers_all_variables(self) -> None:
        msg = _format_empirical_spec(_make_empirical_spec())
        for v in _make_empirical_spec()["variables"]:
            assert v["name"] in msg
            assert v["description"] in msg

    def test_variables_table_exposes_role_and_contract_type(self) -> None:
        msg = _format_empirical_spec(_make_empirical_spec())
        assert "independent" in msg
        assert "dependent" in msg
        assert "control" in msg
        assert "hard" in msg
        assert "soft" in msg


# ---------------------------------------------------------------------------
# model_construction node function
# ---------------------------------------------------------------------------


class TestModelConstruction:
    def test_returns_model_plan(
        self,
        mock_chat_model_for: Callable[[str], MagicMock],
    ) -> None:
        model = mock_chat_model_for("model_construction")
        _wire_mock_chain(model, _make_fake_model_plan())

        state: WorkflowState = {"empirical_spec": _make_empirical_spec()}
        result = model_construction(state)

        assert "model_plan" in result
        plan = result["model_plan"]
        assert plan["model_type"] == "双向固定效应面板模型"
        assert "β₁" in plan["equation"]
        assert plan["data_structure_requirements"] == [
            "面板结构",
            "至少两期",
            "允许非平衡面板",
        ]

    def test_core_hypothesis_structure(
        self,
        mock_chat_model_for: Callable[[str], MagicMock],
    ) -> None:
        model = mock_chat_model_for("model_construction")
        _wire_mock_chain(model, _make_fake_model_plan())

        state: WorkflowState = {"empirical_spec": _make_empirical_spec()}
        plan = model_construction(state)["model_plan"]

        ch = plan["core_hypothesis"]
        assert set(ch.keys()) == {"variable_name", "expected_sign", "rationale"}
        assert ch["variable_name"] == "GOV"
        assert ch["expected_sign"] in {"+", "-", "ambiguous"}
        assert ch["rationale"]

    def test_calls_with_structured_output_once(
        self,
        mock_chat_model_for: Callable[[str], MagicMock],
    ) -> None:
        model = mock_chat_model_for("model_construction")
        _wire_mock_chain(model, _make_fake_model_plan())

        state: WorkflowState = {"empirical_spec": _make_empirical_spec()}
        model_construction(state)

        model.with_structured_output.assert_called_once_with(_ModelPlanModel)

    def test_system_prompt_injected(
        self,
        mock_chat_model_for: Callable[[str], MagicMock],
    ) -> None:
        model = mock_chat_model_for("model_construction")
        mock_chain = _wire_mock_chain(model, _make_fake_model_plan())

        state: WorkflowState = {"empirical_spec": _make_empirical_spec()}
        model_construction(state)

        messages = mock_chain.invoke.call_args[0][0]
        assert len(messages) == 2
        # SystemMessage 应来自 prompts/model_construction.md, 包含关键短语
        assert "模型类型" in messages[0].content
