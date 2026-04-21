"""Regression node (F22) -- terminal product node.

Driven by :func:`langchain.agents.create_agent`. Consumes MergedDataset (F20),
ModelPlan (F11) and EmpiricalSpec (F09), binds the stata-executor MCP tool set
via :func:`get_stata_tools`, lets the LLM write and execute a do file that runs
the baseline regression, then reports the core coefficient sign.

The node enforces post-conditions (structured response schema, do/log files on
disk, ``actual_sign`` within ``{"+", "-", "0"}``) and assembles
:class:`RegressionResult` with a structured :class:`SignCheck`. Sign mismatch
vs ``ModelPlan.core_hypothesis.expected_sign`` is **not** an error -- it is
recorded as ``sign_check.consistent=False`` and ``workflow_status=success``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from harness_stata.clients.llm import get_chat_model
from harness_stata.clients.stata import get_stata_tools
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    EmpiricalSpec,
    MergedDataset,
    ModelPlan,
    RegressionResult,
    SignCheck,
    WorkflowState,
)

_MAX_ITERATIONS = 20
_DO_FILENAME = "regression.do"
_LOG_FILENAME = "regression.log"


class _RegressionOutput(BaseModel):
    """LLM-facing structured-output schema for the regression terminal step."""

    do_file_path: str = Field(description="Absolute path of the .do file written to disk.")
    log_file_path: str = Field(description="Absolute path of the .log file produced by Stata.")
    actual_sign: Literal["+", "-", "0"] = Field(
        description="Sign of the core explanatory variable's coefficient."
    )
    summary: str = Field(description="Natural-language summary of regression output.")


def _validate(state: WorkflowState) -> str | None:
    if state.get("merged_dataset") is None:
        return "state.merged_dataset is missing"
    if state.get("model_plan") is None:
        return "state.model_plan is missing"
    if state.get("empirical_spec") is None:
        return "state.empirical_spec is missing"
    return None


def _derive_session_dir(merged_path: str) -> Path:
    """Place do/log alongside merged.csv (F20 session_dir convention).

    ``merged_path`` is guaranteed absolute by F20. We avoid ``Path.resolve()``
    because on Windows it calls ``os.getcwd()`` inside the event loop, which
    blockbuster intercepts under ``langgraph dev``.
    """
    return Path(merged_path).parent


def _build_human_prompt(
    spec: EmpiricalSpec,
    plan: ModelPlan,
    merged: MergedDataset,
    do_path: Path,
    log_path: Path,
) -> str:
    hyp = plan["core_hypothesis"]
    warnings_block = (
        "\n".join(f"- {w}" for w in merged["warnings"]) if merged["warnings"] else "(none)"
    )
    return (
        f"## research topic\n{spec['topic']}\n\n"
        f"## sample / time / frequency\n"
        f"sample_scope: {spec['sample_scope']}\n"
        f"time_range: {spec['time_range_start']} - {spec['time_range_end']}\n"
        f"data_frequency: {spec['data_frequency']}\n"
        f"analysis_granularity: {spec['analysis_granularity']}\n\n"
        f"## model\n"
        f"model_type: {plan['model_type']}\n"
        f"equation: {plan['equation']}\n"
        f"data_structure_requirements: {plan['data_structure_requirements']}\n\n"
        f"## core hypothesis (baseline sign you will compare against)\n"
        f"variable_name: {hyp['variable_name']}\n"
        f"expected_sign: {hyp['expected_sign']}\n"
        f"rationale: {hyp['rationale']}\n\n"
        f"## merged dataset\n"
        f"file_path: {merged['file_path']}\n"
        f"row_count: {merged['row_count']}\n"
        f"columns: {merged['columns']}\n"
        f"data_cleaning warnings:\n{warnings_block}\n\n"
        f"## output paths (write both files, absolute paths required)\n"
        f"do_file_path: {do_path}\n"
        f"log_file_path: {log_path}\n\n"
        "Write the do file to do_file_path, execute it (log must be produced at"
        " log_file_path via `log using`), then return the structured response"
        " (do_file_path, log_file_path, actual_sign, summary)."
    )


def _assert_file_exists(path_str: str, role: str) -> None:
    path = Path(path_str)
    if not path.exists():
        msg = f"regression: LLM claimed {role} at {path_str!r} but file does not exist"
        raise RuntimeError(msg)


def _compute_sign_check(plan: ModelPlan, actual_sign: str) -> SignCheck:
    hyp = plan["core_hypothesis"]
    expected = hyp["expected_sign"]
    consistent = expected == "ambiguous" or expected == actual_sign
    return {
        "variable_name": hyp["variable_name"],
        "expected_sign": expected,
        "actual_sign": actual_sign,
        "consistent": consistent,
    }


class RegressionOutput(TypedDict):
    regression_result: RegressionResult
    workflow_status: Literal["success"]


async def regression(state: WorkflowState) -> RegressionOutput:
    """Run the baseline regression and produce the terminal RegressionResult.

    Drives a :func:`create_agent` bound to the stata-executor MCP tools. The LLM
    writes + runs a do file; this node validates the structured response,
    confirms do/log exist on disk, and assembles RegressionResult with a
    SignCheck against ModelPlan.core_hypothesis.expected_sign.
    """
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    plan: ModelPlan = state["model_plan"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    spec: EmpiricalSpec = state["empirical_spec"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    merged: MergedDataset = state["merged_dataset"]  # pyright: ignore[reportTypedDictNotRequiredAccess]

    session_dir = _derive_session_dir(merged["file_path"])
    do_path = session_dir / _DO_FILENAME
    log_path = session_dir / _LOG_FILENAME

    async with get_stata_tools() as tools:
        agent = create_agent(
            model=get_chat_model(),
            tools=tools,  # type: ignore[arg-type]
            system_prompt=load_prompt("regression"),
            middleware=[
                ModelCallLimitMiddleware(run_limit=_MAX_ITERATIONS, exit_behavior="error"),
            ],
            response_format=_RegressionOutput,
        )
        initial = {
            "messages": [
                HumanMessage(content=_build_human_prompt(spec, plan, merged, do_path, log_path))
            ]
        }
        try:
            result: dict[str, Any] = await agent.ainvoke(initial)  # type: ignore[reportUnknownMemberType]
        except ModelCallLimitExceededError as exc:
            raise RuntimeError(
                f"regression: ReAct reached max_iterations ({_MAX_ITERATIONS})"
                f" without a terminal response"
            ) from exc

    payload = result.get("structured_response")
    if not isinstance(payload, _RegressionOutput):
        raise RuntimeError(
            f"regression: agent did not produce a structured response"
            f" (got {type(payload).__name__})"
        )
    if not payload.do_file_path:
        raise RuntimeError("regression: structured_response.do_file_path is empty")
    if not payload.log_file_path:
        raise RuntimeError("regression: structured_response.log_file_path is empty")
    if not payload.summary:
        raise RuntimeError("regression: structured_response.summary is empty")

    _assert_file_exists(payload.do_file_path, "do_file_path")
    _assert_file_exists(payload.log_file_path, "log_file_path")

    sign_check = _compute_sign_check(plan, payload.actual_sign)
    regression_result: RegressionResult = {
        "do_file_path": payload.do_file_path,
        "log_file_path": payload.log_file_path,
        "sign_check": sign_check,
        "summary": payload.summary,
    }
    return {
        "regression_result": regression_result,
        "workflow_status": "success",
    }
