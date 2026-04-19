"""Requirement analysis node — first node in the workflow.

Single-turn LLM call that parses user requirements into a structured
:class:`~harness_stata.state.EmpiricalSpec`.
"""

from __future__ import annotations

from typing import Literal, TypedDict, cast

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from harness_stata.clients.llm import get_chat_model
from harness_stata.prompts import load_prompt
from harness_stata.state import EmpiricalSpec, UserRequest, WorkflowState

# ---------------------------------------------------------------------------
# Pydantic models (node-private, used for with_structured_output schema)
# ---------------------------------------------------------------------------


class _VariableDefinitionModel(BaseModel):
    name: str = Field(description="变量英文缩写, e.g. ROA")
    description: str = Field(description="变量中文含义, e.g. 总资产收益率")
    contract_type: Literal["hard", "soft"] = Field(description="hard=用户指定, soft=LLM拟定")
    role: Literal["dependent", "independent", "control"] = Field(
        description="dependent=被解释变量Y, independent=核心解释变量X, control=控制变量"
    )


class _EmpiricalSpecModel(BaseModel):
    """Schema for the LLM structured output.

    ``topic`` is NOT produced by the LLM — it is passed through verbatim from
    ``UserRequest.topic`` and merged into the final EmpiricalSpec in the node body.
    """

    variables: list[_VariableDefinitionModel] = Field(description="变量清单: Y + X + 控制变量")
    sample_scope: str = Field(description="样本范围, 直接取自用户输入")
    time_range_start: str = Field(description="起始年份, 直接取自用户输入")
    time_range_end: str = Field(description="结束年份, 直接取自用户输入")
    data_frequency: Literal["yearly", "quarterly", "monthly", "daily"] = Field(
        description="数据频率, 直接取自用户输入"
    )
    analysis_granularity: str = Field(description="分析粒度, e.g. 公司-年度")


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


class RequirementAnalysisOutput(TypedDict):
    empirical_spec: EmpiricalSpec


def requirement_analysis(state: WorkflowState) -> RequirementAnalysisOutput:
    """Parse user requirements into a structured EmpiricalSpec."""
    # user_request is guaranteed to be present: it is set as initial state by the CLI
    user_req: UserRequest = state["user_request"]  # type: ignore[reportTypedDictNotRequiredAccess]

    system_prompt = load_prompt("requirement_analysis")
    user_message = _format_user_message(user_req)

    model = get_chat_model()
    structured = model.with_structured_output(_EmpiricalSpecModel)

    result = structured.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
    )

    assert isinstance(result, _EmpiricalSpecModel)
    spec = cast("EmpiricalSpec", {"topic": user_req["topic"], **result.model_dump()})
    return {"empirical_spec": spec}


def _format_user_message(user_req: UserRequest) -> str:
    """Format UserRequest fields into a human-readable message for the LLM."""
    freq_map: dict[str, str] = {
        "yearly": "年度",
        "quarterly": "季度",
        "monthly": "月度",
        "daily": "日度",
    }
    freq_label = freq_map.get(user_req["data_frequency"], user_req["data_frequency"])

    return (
        f"研究选题: {user_req['topic']}\n"
        f"核心解释变量 X: {user_req['x_variable']}\n"
        f"被解释变量 Y: {user_req['y_variable']}\n"
        f"样本范围: {user_req['sample_scope']}\n"
        f"时间范围: {user_req['time_range_start']} - {user_req['time_range_end']}\n"
        f"数据频率: {freq_label}"
    )
