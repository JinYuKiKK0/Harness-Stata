"""Unit tests for the requirement_analysis node."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import MagicMock

from harness_stata.nodes.requirement_analysis import (
    _EmpiricalSpecModel,
    _VariableDefinitionModel,
    _format_user_message,
    requirement_analysis,
)
from harness_stata.state import UserRequest, WorkflowState

# ---------------------------------------------------------------------------
# _format_user_message (pure function)
# ---------------------------------------------------------------------------


class TestFormatUserMessage:
    def test_all_fields_present(self, make_user_request: Callable[..., UserRequest]) -> None:
        msg = _format_user_message(make_user_request())
        assert "公司治理质量对财务绩效的影响研究" in msg
        assert "公司治理质量" in msg
        assert "ROA" in msg
        assert "A股上市公司" in msg
        assert "2018" in msg
        assert "2022" in msg
        assert "年度" in msg

    def test_frequency_mapping(self, make_user_request: Callable[..., UserRequest]) -> None:
        for freq, label in [
            ("yearly", "年度"),
            ("quarterly", "季度"),
            ("monthly", "月度"),
            ("daily", "日度"),
        ]:
            msg = _format_user_message(make_user_request(data_frequency=freq))
            assert label in msg, f"{freq} should map to {label}"


# ---------------------------------------------------------------------------
# requirement_analysis node function
# ---------------------------------------------------------------------------


def _make_fake_spec() -> _EmpiricalSpecModel:
    """Build a realistic _EmpiricalSpecModel for mock returns."""
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
                description="公司治理质量指数",
                contract_type="hard",
                role="independent",
            ),
            _VariableDefinitionModel(
                name="SIZE",
                description="企业规模（总资产对数）",
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


def _wire_mock_chain(mock_model: MagicMock, fake_spec: _EmpiricalSpecModel) -> MagicMock:
    """Wire: model.with_structured_output(...).invoke(...) -> fake_spec."""
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = fake_spec
    mock_model.with_structured_output.return_value = mock_chain
    return mock_chain


class TestRequirementAnalysis:
    def test_returns_empirical_spec(
        self,
        mock_chat_model: MagicMock,
        make_user_request: Callable[..., UserRequest],
    ) -> None:
        fake_spec = _make_fake_spec()
        _wire_mock_chain(mock_chat_model, fake_spec)

        state: WorkflowState = {"user_request": make_user_request()}
        result = requirement_analysis(state)

        assert "empirical_spec" in result
        spec = result["empirical_spec"]
        assert spec["topic"] == "公司治理质量对财务绩效的影响研究"
        assert len(spec["variables"]) == 3
        assert spec["analysis_granularity"] == "公司-年度"

    def test_calls_with_structured_output(
        self,
        mock_chat_model: MagicMock,
        make_user_request: Callable[..., UserRequest],
    ) -> None:
        fake_spec = _make_fake_spec()
        _wire_mock_chain(mock_chat_model, fake_spec)

        state: WorkflowState = {"user_request": make_user_request()}
        requirement_analysis(state)

        mock_chat_model.with_structured_output.assert_called_once_with(_EmpiricalSpecModel)

    def test_passes_system_prompt(
        self,
        mock_chat_model: MagicMock,
        make_user_request: Callable[..., UserRequest],
    ) -> None:
        fake_spec = _make_fake_spec()
        mock_chain = _wire_mock_chain(mock_chat_model, fake_spec)

        state: WorkflowState = {"user_request": make_user_request()}
        requirement_analysis(state)

        messages = mock_chain.invoke.call_args[0][0]
        assert len(messages) == 2
        # System prompt should contain key phrase from requirement_analysis.md
        assert "实证分析" in messages[0].content
