"""Regression node (F22) -- terminal product node.

ReAct-driven. Consumes MergedDataset (F20) + ModelPlan (F11) + EmpiricalSpec
(F09), binds the stata-executor MCP tool set via :func:`get_stata_tools`,
lets the LLM write and execute a do file that runs the baseline regression,
then reports the core coefficient sign.

The node enforces post-conditions (final JSON schema, do/log files on disk,
``actual_sign`` within ``{"+", "-", "0"}``) and assembles
:class:`RegressionResult` with a structured :class:`SignCheck`. Sign mismatch
vs ``ModelPlan.core_hypothesis.expected_sign`` is **not** an error -- it is
recorded as ``sign_check.consistent=False`` and ``workflow_status=success``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage

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
from harness_stata.subgraphs.generic_react import ReactState, build_react_subgraph

_MAX_ITERATIONS = 20
_DO_FILENAME = "regression.do"
_LOG_FILENAME = "regression.log"
_VALID_ACTUAL_SIGNS = {"+", "-", "0"}
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _validate(state: WorkflowState) -> str | None:
    if state.get("merged_dataset") is None:
        return "state.merged_dataset is missing"
    if state.get("model_plan") is None:
        return "state.model_plan is missing"
    if state.get("empirical_spec") is None:
        return "state.empirical_spec is missing"
    return None


def _derive_session_dir(merged_path: str) -> Path:
    """Place do/log alongside merged.csv (F20 session_dir convention)."""
    return Path(merged_path).resolve().parent


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
        " log_file_path via `log using`), then emit the terminating JSON as"
        " specified by the system prompt."
    )


def _extract_final_json(content: str) -> dict[str, Any]:
    text = content.strip()
    match = _FENCE_RE.match(text)
    if match is not None:
        text = match.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"regression: cannot parse final AIMessage content as JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(obj, dict):
        msg = f"regression: final JSON must be object, got {type(obj).__name__}"
        raise RuntimeError(msg)
    return cast("dict[str, Any]", obj)


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        msg = f"regression: final JSON field {key!r} must be a non-empty string"
        raise RuntimeError(msg)
    return value


def _validate_payload(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    do_file_path = _require_str(payload, "do_file_path")
    log_file_path = _require_str(payload, "log_file_path")
    actual_sign = _require_str(payload, "actual_sign")
    summary = _require_str(payload, "summary")
    if actual_sign not in _VALID_ACTUAL_SIGNS:
        msg = (
            f"regression: actual_sign must be one of {sorted(_VALID_ACTUAL_SIGNS)!r},"
            f" got {actual_sign!r}"
        )
        raise RuntimeError(msg)
    return do_file_path, log_file_path, actual_sign, summary


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


async def regression(state: WorkflowState) -> dict[str, Any]:
    """Run the baseline regression and produce the terminal RegressionResult.

    Drives a generic ReAct subgraph bound to the stata-executor MCP tools.
    The LLM writes + runs a do file; this node validates the terminating JSON,
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

    prompt = load_prompt("regression")

    async with get_stata_tools() as tools:
        subgraph = build_react_subgraph(tools=tools, prompt=prompt, max_iterations=_MAX_ITERATIONS)
        initial: ReactState = {
            "messages": [
                HumanMessage(content=_build_human_prompt(spec, plan, merged, do_path, log_path))
            ],
            "iteration_count": 0,
        }
        result = await subgraph.ainvoke(initial)  # pyright: ignore[reportUnknownMemberType]

    messages = cast("list[Any]", result.get("messages", []))
    if not messages:
        raise RuntimeError("regression: ReAct subgraph returned no messages")
    last = messages[-1]
    if not isinstance(last, AIMessage):
        msg = f"regression: final message is not AIMessage (got {type(last).__name__})"
        raise RuntimeError(msg)
    if last.tool_calls:
        raise RuntimeError("regression: ReAct reached max_iterations without a terminal AIMessage")

    content_attr = cast("Any", last.content)  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(content_attr, str):
        msg = f"regression: AIMessage.content must be str, got {type(content_attr).__name__}"
        raise RuntimeError(msg)
    payload = _extract_final_json(content_attr)
    do_file_path, log_file_path, actual_sign, summary = _validate_payload(payload)

    _assert_file_exists(do_file_path, "do_file_path")
    _assert_file_exists(log_file_path, "log_file_path")

    sign_check = _compute_sign_check(plan, actual_sign)
    regression_result: RegressionResult = {
        "do_file_path": do_file_path,
        "log_file_path": log_file_path,
        "sign_check": sign_check,
        "summary": summary,
    }
    return {
        "regression_result": regression_result,
        "workflow_status": "success",
    }
