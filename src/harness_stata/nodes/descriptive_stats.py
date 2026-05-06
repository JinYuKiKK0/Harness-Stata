"""描述性统计节点(F21)——驱动 ReAct agent 编写 Stata do 代码并执行。

Agent 严格定位为"do 代码作者 + Stata 报错修复者":不修数据、不改变量集合,
只对 ``empirical_spec.variables`` 中所有变量做描述性统计(sum/tab/misstable 等)。
节点层做 deterministic 后置校验:所有变量名必须出现在最终成功的 commands 中。
"""

from __future__ import annotations

import re

import pandas as pd
from pydantic import BaseModel, Field

from harness_stata.nodes._stata_agent import run_stata_agent
from harness_stata.nodes._writes import awrites_to
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    DescStatsReport,
    EmpiricalSpec,
    MergedDataset,
    VariableDefinition,
    WorkflowState,
)

_ITER_CAP = 6
_PREVIEW_ROWS = 3


class _DescOutput(BaseModel):
    """LLM-facing structured-output schema for the descriptive_stats terminal step."""

    summary: str = Field(description="对所有目标变量描述性统计核心观察的简明文字总结。")


def _validate(state: WorkflowState) -> str | None:
    if state.get("empirical_spec") is None:
        return "state.empirical_spec is missing"
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


def _build_human_prompt(spec: EmpiricalSpec, merged: MergedDataset) -> str:
    """渲染 HumanMessage:`<inputs>` (按决策依赖深度排) + `<reminder>`。"""
    file_path = merged["file_path"]
    columns_block = _format_columns(merged["columns"])
    preview_block = _format_csv_preview(file_path)
    variables_block = _format_variables(spec["variables"])
    return (
        "<inputs>\n\n"
        "## merged_dataset_path\n"
        f"`{file_path}` 是一份长格式面板 csv 的绝对路径,首行为列名。\n\n"
        f"## columns\n{columns_block}\n\n"
        f"## preview (first {_PREVIEW_ROWS} rows)\n{preview_block}\n\n"
        f"## variables\n{variables_block}\n\n"
        f"## analysis_granularity\n`{spec['analysis_granularity']}`\n\n"
        "## sample / time / frequency\n"
        f"sample_scope: `{spec['sample_scope']}`\n"
        f"time_range: `{spec['time_range_start']}` — `{spec['time_range_end']}`\n"
        f"data_frequency: `{spec['data_frequency']}`\n\n"
        "</inputs>\n\n"
        "<reminder>\n"
        "终止前必须满足:`variables` 列出的所有变量名都已出现在你编写的 do 代码"
        "中(被 `summarize` / `tabulate` / `misstable` 等命令命中);最近一次"
        '执行 `status="succeeded"` 且 `result_text` 包含可读的描述性统计输出。\n'
        "满足后,调用结构化输出工具上报核心观察总结终止。\n"
        "</reminder>"
    )


def _strip_stata_noncode(commands: str) -> str:
    """剥离 Stata 注释与双引号字符串字面量,留下命令位置的可执行片段。

    顺序:块注释 -> 行尾注释 -> 整行星号注释 -> 字符串字面量。
    """
    s = re.sub(r"/\*.*?\*/", " ", commands, flags=re.DOTALL)
    s = re.sub(r"//[^\n]*", " ", s)
    s = re.sub(r"^\s*\*[^\n]*", " ", s, flags=re.MULTILINE)
    s = re.sub(r'"[^"\n]*"', " ", s)
    return s


def _check_variables_covered(commands: str, spec: EmpiricalSpec) -> None:
    """所有 ``empirical_spec.variables[*].name`` 必须出现在剥离注释/字符串后的 commands。

    Stata 变量名严格区分大小写,匹配也保持 case-sensitive。
    """
    code = _strip_stata_noncode(commands)
    missing: list[str] = []
    for var in spec["variables"]:
        name = var["name"]
        pattern = rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])"
        if not re.search(pattern, code):
            missing.append(name)
    if missing:
        msg = (
            f"descriptive_stats: variables not referenced in final commands"
            f" (after stripping comments/strings): {missing!r}"
        )
        raise ValueError(msg)


@awrites_to("desc_stats_report")
async def descriptive_stats(state: WorkflowState) -> DescStatsReport:
    """对 MergedDataset 跑描述性统计,产出 do/log 路径与文字总结。"""
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]
    merged: MergedDataset = state["merged_dataset"]

    payload, do_path, log_path = await run_stata_agent(
        node_name="descriptive_stats",
        system_prompt=load_prompt("descriptive_stats"),
        human_message=_build_human_prompt(spec, merged),
        output_schema=_DescOutput,
        iter_cap=_ITER_CAP,
        post_check_fn=lambda cmds: _check_variables_covered(cmds, spec),
    )
    return {
        "do_file_path": do_path,
        "log_file_path": log_path,
        "summary": payload.summary,
    }
