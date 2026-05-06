"""基准回归节点(F22)——驱动 ReAct agent 编写 Stata do 代码并执行。

Agent 严格按 ``model_plan.equation`` 的方程结构跑回归,不自主调整方程结构、不
扫描稳健性变体。终止后由 LLM 看回归输出表填 ``sign_check``(核心解释变量的
实际系数符号 vs ``model_plan.core_hypothesis.expected_sign``)。

节点层后置校验:核心解释变量名必须出现在最终成功的 commands 中。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, TypedDict

import pandas as pd
from pydantic import BaseModel, Field

from harness_stata.nodes._stata_agent import resolve_stata_workspace, run_stata_agent
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    EmpiricalSpec,
    MergedDataset,
    ModelPlan,
    RegressionResult,
    SignCheck,
    VariableDefinition,
    WorkflowState,
)

_ITER_CAP = 10
_PREVIEW_ROWS = 3
_RTF_FILENAME = "02_regression.rtf"


class _SignCheckOutput(BaseModel):
    """LLM 子结构:对核心解释变量的符号一致性判定。"""

    variable_name: str = Field(description="核心解释变量名,与 model_plan 中的命名一致。")
    expected_sign: str = Field(
        description='预期符号,取自 model_plan,值 ∈ {"+", "-", "ambiguous"}。'
    )
    actual_sign: str = Field(
        description='回归输出表中该变量的实际系数符号,值 ∈ {"+", "-"};系数为 0 视为与预期不一致并记 "+"。'
    )
    consistent: bool = Field(
        description="actual_sign 是否与 expected_sign 一致(ambiguous 视为一致)。"
    )


class _RegressionOutput(BaseModel):
    """LLM-facing structured-output schema for the regression terminal step."""

    summary: str = Field(description="基准回归核心结果总结,含主要系数与显著性观察。")
    sign_check: _SignCheckOutput


class RegressionOutput(TypedDict):
    regression_result: RegressionResult
    workflow_status: Literal["success"]


def _validate(state: WorkflowState) -> str | None:
    if state.get("empirical_spec") is None:
        return "state.empirical_spec is missing"
    if state.get("model_plan") is None:
        return "state.model_plan is missing"
    merged = state.get("merged_dataset")
    if merged is None or not merged.get("file_path"):
        return "state.merged_dataset.file_path is missing"
    return None


def _format_variables(variables: list[VariableDefinition]) -> str:
    return "\n".join(
        f"- `{v['name']}` ({v['role']}, {v['contract_type']}): {v['description']}"
        for v in variables
    )


def _format_columns(columns: list[str]) -> str:
    return "\n".join(f"- `{c}`" for c in columns)


def _format_csv_preview(file_path: str, n: int = _PREVIEW_ROWS) -> str:
    try:
        df = pd.read_csv(file_path, nrows=n)
    except (FileNotFoundError, pd.errors.ParserError, OSError) as exc:
        return f"(preview unavailable: {exc})"
    if df.empty:
        return "(empty)"
    return df.to_string(index=False)


def _format_data_structure(reqs: list[str]) -> str:
    if not reqs:
        return "(none)"
    return "\n".join(f"- {r}" for r in reqs)


def _build_human_prompt(
    spec: EmpiricalSpec, plan: ModelPlan, merged: MergedDataset, rtf_path: Path
) -> str:
    """渲染 HumanMessage:`<inputs>` (按决策依赖深度排) + `<reminder>`。"""
    file_path = merged["file_path"]
    columns_block = _format_columns(merged["columns"])
    preview_block = _format_csv_preview(file_path)
    variables_block = _format_variables(spec["variables"])
    hypothesis = plan["core_hypothesis"]
    return (
        "<inputs>\n\n"
        "## merged_dataset_path\n"
        f"`{file_path}` 是一份长格式面板 csv 的绝对路径,首行为列名。\n\n"
        f"## columns\n{columns_block}\n\n"
        f"## preview (first {_PREVIEW_ROWS} rows)\n{preview_block}\n\n"
        f"## variables\n{variables_block}\n\n"
        "## model\n"
        f"model_type: `{plan['model_type']}`\n"
        f"equation: `{plan['equation']}`\n\n"
        "## core_hypothesis\n"
        f"variable_name: `{hypothesis['variable_name']}`\n"
        f"expected_sign: `{hypothesis['expected_sign']}`\n"
        f"rationale: {hypothesis['rationale']}\n\n"
        "## data_structure_requirements\n"
        f"{_format_data_structure(plan['data_structure_requirements'])}\n\n"
        f"## analysis_granularity\n`{spec['analysis_granularity']}`\n\n"
        "## rtf_table_path\n"
        f'`{rtf_path!s}` 是 RTF 三线表的导出绝对路径,直接 `using "<rtf_table_path>"`,'
        "不要自造其他路径。\n\n"
        "</inputs>\n\n"
        "<reminder>\n"
        "终止前必须满足:do 代码严格按 `equation` 写出方程(自变量、控制变量、固定效应"
        '项与 equation 中标注的一致);最近一次执行 `status="succeeded"` 且 '
        "`result_text` 含可读的回归系数表;在系数表中读取 `core_hypothesis.variable_name` "
        "的实际系数符号,与 `expected_sign` 比对填入符号检查结果;`rtf_table_path` 已通过 "
        "`esttab using` 成功导出。\n"
        "满足后,调用结构化输出工具上报回归总结与符号检查终止。\n"
        "</reminder>"
    )


def _strip_stata_noncode(commands: str) -> str:
    """剥离 Stata 注释与双引号字符串字面量。与 descriptive_stats 同款实现。"""
    s = re.sub(r"/\*.*?\*/", " ", commands, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", " ", s)
    s = re.sub(r"^\s*\*[^\n]*", " ", s, flags=re.MULTILINE)
    s = re.sub(r'"[^"\n]*"', " ", s)
    return s


def _check_core_var_present(commands: str, plan: ModelPlan) -> None:
    """``model_plan.core_hypothesis.variable_name`` 必须出现在剥离后的 commands。"""
    var_name = plan["core_hypothesis"]["variable_name"]
    code = _strip_stata_noncode(commands)
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(var_name)}(?![A-Za-z0-9_])"
    if not re.search(pattern, code):
        msg = (
            f"regression: core_hypothesis.variable_name {var_name!r} not referenced"
            f" in final commands (after stripping comments/strings)"
        )
        raise ValueError(msg)


def _payload_to_sign_check(payload_sc: _SignCheckOutput) -> SignCheck:
    return {
        "variable_name": payload_sc.variable_name,
        "expected_sign": payload_sc.expected_sign,
        "actual_sign": payload_sc.actual_sign,
        "consistent": payload_sc.consistent,
    }


async def regression(state: WorkflowState) -> RegressionOutput:
    """对 MergedDataset 跑基准回归,产出 do/log/rtf 路径、符号检查与文字总结。"""
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]
    plan: ModelPlan = state["model_plan"]
    merged: MergedDataset = state["merged_dataset"]

    workspace = resolve_stata_workspace("regression")
    rtf_path = workspace / _RTF_FILENAME

    payload, do_path, log_path = await run_stata_agent(
        node_name="regression",
        workspace=workspace,
        system_prompt=load_prompt("regression"),
        human_message=_build_human_prompt(spec, plan, merged, rtf_path),
        output_schema=_RegressionOutput,
        iter_cap=_ITER_CAP,
        post_check_fn=lambda cmds: _check_core_var_present(cmds, plan),
    )
    return {
        "regression_result": {
            "do_file_path": do_path,
            "log_file_path": log_path,
            "rtf_table_path": str(rtf_path),
            "sign_check": _payload_to_sign_check(payload.sign_check),
            "summary": payload.summary,
        },
        "workflow_status": "success",
    }
