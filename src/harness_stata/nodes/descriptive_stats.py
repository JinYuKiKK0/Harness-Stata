"""Descriptive statistics node (F21).

Driven by :func:`langchain.agents.create_agent`. Consumes MergedDataset (F20)
and EmpiricalSpec (F09), binds the stata-executor MCP tool set via
:func:`get_stata_tools`, lets the LLM write and execute a do file that runs
descriptive statistics, missing/outlier scans and logic checks, then reports
key findings.

The node enforces post-conditions (structured response schema, do/log files on
disk) and assembles :class:`DescStatsReport`. As a non-terminal node it does NOT
write ``workflow_status`` -- the graph continues toward the regression node.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.agents import create_agent  # pyright: ignore[reportUnknownVariableType]
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from harness_stata.clients.llm import get_chat_model
from harness_stata.clients.stata import get_stata_tools
from harness_stata.nodes._writes import awrites_to
from harness_stata.prompts import load_prompt
from harness_stata.state import (
    DescStatsReport,
    EmpiricalSpec,
    MergedDataset,
    WorkflowState,
)

_MAX_ITERATIONS = 15
_DO_FILENAME = "descriptive_stats.do"
_LOG_FILENAME = "descriptive_stats.log"


class _DescStatsOutput(BaseModel):
    """LLM-facing structured-output schema for the descriptive_stats terminal step."""

    do_file_path: str = Field(description="Absolute path of the .do file written to disk.")
    log_file_path: str = Field(description="Absolute path of the .log file produced by Stata.")
    summary: str = Field(description="Natural-language summary of key findings.")


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
        " log_file_path via `log using`), then return the structured response"
        " (do_file_path, log_file_path, summary)."
    )


def _assert_file_exists(path_str: str, role: str) -> None:
    path = Path(path_str)
    if not path.exists():
        msg = f"descriptive_stats: LLM claimed {role} at {path_str!r} but file does not exist"
        raise RuntimeError(msg)


@awrites_to("desc_stats_report")
async def descriptive_stats(state: WorkflowState) -> DescStatsReport:
    """Run descriptive statistics and produce DescStatsReport.

    Drives a :func:`create_agent` bound to the stata-executor MCP tools. The LLM
    writes + runs a do file; this node validates the structured response and
    confirms do/log exist on disk. Non-terminal: does not set ``workflow_status``,
    so the graph advances to the regression node next.
    """
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    merged: MergedDataset = state["merged_dataset"]  # pyright: ignore[reportTypedDictNotRequiredAccess]

    session_dir = _derive_session_dir(merged["file_path"])
    do_path = session_dir / _DO_FILENAME
    log_path = session_dir / _LOG_FILENAME

    async with get_stata_tools() as tools:
        agent = create_agent(
            model=get_chat_model(),
            tools=tools,  # type: ignore[arg-type]
            system_prompt=load_prompt("descriptive_stats"),
            middleware=[
                ModelCallLimitMiddleware(run_limit=_MAX_ITERATIONS, exit_behavior="error"),
            ],
            response_format=_DescStatsOutput,
        )
        initial = {
            "messages": [HumanMessage(content=_build_human_prompt(spec, merged, do_path, log_path))]
        }
        try:
            result: dict[str, Any] = await agent.ainvoke(initial)  # type: ignore[reportUnknownMemberType]
        except ModelCallLimitExceededError as exc:
            raise RuntimeError(
                f"descriptive_stats: ReAct reached max_iterations ({_MAX_ITERATIONS})"
                f" without a terminal response"
            ) from exc

    payload = result.get("structured_response")
    if not isinstance(payload, _DescStatsOutput):
        raise RuntimeError(
            f"descriptive_stats: agent did not produce a structured response"
            f" (got {type(payload).__name__})"
        )
    if not payload.do_file_path:
        raise RuntimeError("descriptive_stats: structured_response.do_file_path is empty")
    if not payload.log_file_path:
        raise RuntimeError("descriptive_stats: structured_response.log_file_path is empty")
    if not payload.summary:
        raise RuntimeError("descriptive_stats: structured_response.summary is empty")

    _assert_file_exists(payload.do_file_path, "do_file_path")
    _assert_file_exists(payload.log_file_path, "log_file_path")

    return {
        "do_file_path": payload.do_file_path,
        "log_file_path": payload.log_file_path,
        "summary": payload.summary,
    }
