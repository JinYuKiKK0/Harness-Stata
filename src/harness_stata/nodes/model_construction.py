"""Model construction node — second node in the workflow.

Single-turn LLM call that translates :class:`~harness_stata.state.EmpiricalSpec`
into a structured :class:`~harness_stata.state.ModelPlan` containing the model
form, estimation equation, expected sign baseline for the core regressor, and
data-structure requirements.
"""

from __future__ import annotations

from typing import Literal, TypedDict, cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from harness_stata.clients.llm import get_chat_model
from harness_stata.prompts import load_prompt
from harness_stata.state import EmpiricalSpec, ModelPlan, VariableDefinition, WorkflowState

# ---------------------------------------------------------------------------
# Pydantic models (node-private, used for with_structured_output schema)
# ---------------------------------------------------------------------------


class _CoreHypothesisModel(BaseModel):
    variable_name: str = Field(
        description="核心解释变量名, 必须与 variables 中 role=independent 的 name 完全一致"
    )
    expected_sign: Literal["+", "-", "ambiguous"] = Field(
        description="基于经济学理论与实证文献共识的预期符号"
    )
    rationale: str = Field(description="一句中文说明经济学依据, 30-80 字")


class _ModelPlanModel(BaseModel):
    """Schema for the LLM structured output."""

    model_type: str = Field(
        description=(
            "模型类型, 优先取自: 双向固定效应面板模型 / 单向固定效应面板模型 / "
            "OLS 截面回归 / Logit 模型 / 时间序列模型"
        )
    )
    equation: str = Field(
        description="数学方程式字符串, 使用标准计量经济学数学符号, 具体格式见 system prompt"
    )
    core_hypothesis: _CoreHypothesisModel
    data_structure_requirements: list[str] = Field(
        description="模型对数据结构的要求, 自然语言字符串列表, 3-5 条"
    )


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


class ModelConstructionOutput(TypedDict):
    model_plan: ModelPlan


def model_construction(state: WorkflowState) -> ModelConstructionOutput:
    """Translate EmpiricalSpec into a structured ModelPlan."""
    spec: EmpiricalSpec = state["empirical_spec"]  # type: ignore[reportTypedDictNotRequiredAccess]

    system_prompt = load_prompt("model_construction")
    user_message = _format_empirical_spec(spec)

    model = get_chat_model()
    structured = model.with_structured_output(_ModelPlanModel)

    result = structured.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
    )

    assert isinstance(result, _ModelPlanModel)
    return {"model_plan": cast("ModelPlan", result.model_dump())}


def _format_empirical_spec(spec: EmpiricalSpec) -> str:
    """Render EmpiricalSpec into a human-readable message for the LLM."""
    variables_table = _render_variables_table(spec["variables"])
    return (
        f"研究选题: {spec['topic']}\n"
        f"样本范围: {spec['sample_scope']}\n"
        f"时间范围: {spec['time_range_start']} - {spec['time_range_end']}\n"
        f"数据频率: {spec['data_frequency']}\n"
        f"分析粒度: {spec['analysis_granularity']}\n\n"
        f"变量清单:\n{variables_table}"
    )


def _render_variables_table(variables: list[VariableDefinition]) -> str:
    """Render the variables list as a pipe-separated text table."""
    header = "| name | description | role | contract_type |"
    sep = "| --- | --- | --- | --- |"
    rows = [
        f"| {v['name']} | {v['description']} | {v['role']} | {v['contract_type']} |"
        for v in variables
    ]
    return "\n".join([header, sep, *rows])
