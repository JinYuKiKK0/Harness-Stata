"""Descriptive statistics node (F21).

ReAct-driven. Consumes MergedDataset (F20) + EmpiricalSpec (F09), binds the
stata-executor MCP tool set via :func:`get_stata_tools`, lets the LLM write
and execute a do file that runs descriptive statistics, missing/outlier scans
and logic checks, then reports key findings.

The node enforces post-conditions (final JSON schema, do/log files on disk)
and assembles :class:`DescStatsReport`. As a non-terminal node it does NOT
write ``workflow_status`` -- the graph continues toward the regression node.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from langchain_core.messages import AIMessage, HumanMessage

from harness_stata.clients.stata import get_stata_tools
from harness_stata.nodes._writes import awrites_to
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    DescStatsReport,
    EmpiricalSpec,
    MergedDataset,
    WorkflowState,
)
from harness_stata.subgraphs.generic_react import ReactState, build_react_subgraph

_MAX_ITERATIONS = 15
_DO_FILENAME = "descriptive_stats.do"
_LOG_FILENAME = "descriptive_stats.log"
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _validate(state: WorkflowState) -> str | None:
    if state.get("merged_dataset") is None:
        return "state.merged_dataset is missing"
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
    merged: MergedDataset,
    do_path: Path,
    log_path: Path,
) -> str:
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
        msg = f"descriptive_stats: cannot parse final AIMessage content as JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(obj, dict):
        msg = f"descriptive_stats: final JSON must be object, got {type(obj).__name__}"
        raise RuntimeError(msg)
    return cast("dict[str, Any]", obj)


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        msg = f"descriptive_stats: final JSON field {key!r} must be a non-empty string"
        raise RuntimeError(msg)
    return value


def _validate_payload(payload: dict[str, Any]) -> tuple[str, str, str]:
    do_file_path = _require_str(payload, "do_file_path")
    log_file_path = _require_str(payload, "log_file_path")
    summary = _require_str(payload, "summary")
    return do_file_path, log_file_path, summary


def _assert_file_exists(path_str: str, role: str) -> None:
    path = Path(path_str)
    if not path.exists():
        msg = f"descriptive_stats: LLM claimed {role} at {path_str!r} but file does not exist"
        raise RuntimeError(msg)


@awrites_to("desc_stats_report")
async def descriptive_stats(state: WorkflowState) -> DescStatsReport:
    """Run descriptive statistics and produce DescStatsReport.

    Drives a generic ReAct subgraph bound to the stata-executor MCP tools.
    The LLM writes + runs a do file; this node validates the terminating JSON
    and confirms do/log exist on disk. Non-terminal: does not set
    workflow_status, so the graph advances to the regression node next.
    """
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    merged: MergedDataset = state["merged_dataset"]  # pyright: ignore[reportTypedDictNotRequiredAccess]

    session_dir = _derive_session_dir(merged["file_path"])
    do_path = session_dir / _DO_FILENAME
    log_path = session_dir / _LOG_FILENAME

    prompt = load_prompt("descriptive_stats")

    async with get_stata_tools() as tools:
        subgraph = build_react_subgraph(tools=tools, prompt=prompt, max_iterations=_MAX_ITERATIONS)
        initial: ReactState = {
            "messages": [
                HumanMessage(content=_build_human_prompt(spec, merged, do_path, log_path))
            ],
            "iteration_count": 0,
        }
        result = await subgraph.ainvoke(initial)  # pyright: ignore[reportUnknownMemberType]

    messages = cast("list[Any]", result.get("messages", []))
    if not messages:
        raise RuntimeError("descriptive_stats: ReAct subgraph returned no messages")
    last = messages[-1]
    if not isinstance(last, AIMessage):
        msg = f"descriptive_stats: final message is not AIMessage (got {type(last).__name__})"
        raise RuntimeError(msg)
    if last.tool_calls:
        raise RuntimeError(
            "descriptive_stats: ReAct reached max_iterations without a terminal AIMessage"
        )

    content_attr = cast("Any", last.content)  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(content_attr, str):
        msg = f"descriptive_stats: AIMessage.content must be str, got {type(content_attr).__name__}"
        raise RuntimeError(msg)
    payload = _extract_final_json(content_attr)
    do_file_path, log_file_path, summary = _validate_payload(payload)

    _assert_file_exists(do_file_path, "do_file_path")
    _assert_file_exists(log_file_path, "log_file_path")

    return {
        "do_file_path": do_file_path,
        "log_file_path": log_file_path,
        "summary": summary,
    }
