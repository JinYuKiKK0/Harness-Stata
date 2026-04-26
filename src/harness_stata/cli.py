"""Typer CLI entry point for Harness-Stata.

Single ``run`` command: accepts the 6 mandatory ``UserRequest`` fields,
drives the compiled workflow graph with ``asyncio.run``, captures the
``hitl_plan_review`` interrupt via same-process blocking prompts, and
resumes via ``Command(resume=...)``. Final state is printed to stdout
and (on success paths with a ``merged_dataset``) dumped next to the
session's artifacts as ``final_state.json``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import typer
from langgraph.types import Command

from harness_stata.config import apply_langsmith_env
from harness_stata.graph import build_graph
from harness_stata.state import UserRequest, WorkflowState

__all__ = ["app"]


class DataFrequency(StrEnum):
    YEARLY = "yearly"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"
    DAILY = "daily"


app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:  # pyright: ignore[reportUnusedFunction]
    """Harness-Stata: LangGraph-driven empirical analysis workflow."""


# ---------------------------------------------------------------------------
# Interrupt / resume helpers
# ---------------------------------------------------------------------------


_INTERRUPT_KEY = "__interrupt__"


def _interrupt_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the first interrupt payload from an ainvoke result, if any."""
    interrupts = result.get(_INTERRUPT_KEY)
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", None)
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    return None


def _prompt_hitl_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Print the plan and prompt the operator for approve/reject + notes."""
    typer.echo("=" * 72)
    typer.echo("HITL: research plan review")
    typer.echo("=" * 72)
    typer.echo(str(payload.get("plan", "<missing plan>")))
    if error := payload.get("error"):
        typer.secho(f"\n[retry] previous decision invalid: {error}", fg=typer.colors.YELLOW)
    typer.echo("=" * 72)

    approved = typer.confirm("Approve this plan?", default=True)
    if approved:
        notes = typer.prompt("Optional notes (Enter to skip)", default="", show_default=False)
        return {"approved": True, "user_notes": notes or None}

    notes = typer.prompt("Rejection reason (required)").strip()
    while not notes:
        notes = typer.prompt("Rejection reason cannot be empty").strip()
    return {"approved": False, "user_notes": notes}


# ---------------------------------------------------------------------------
# Final-state rendering
# ---------------------------------------------------------------------------


def _render_summary(state: dict[str, Any]) -> None:
    status = state.get("workflow_status", "unknown")
    typer.echo("\n" + "=" * 72)
    typer.echo(f"Workflow finished: status={status}")
    typer.echo("=" * 72)

    if status == "failed_hard_contract":
        report: dict[str, Any] = state.get("probe_report") or {}
        typer.secho(
            f"Hard-contract probe failed: {report.get('failure_reason')}",
            fg=typer.colors.RED,
        )
        return

    if status == "rejected":
        decision: dict[str, Any] = state.get("hitl_decision") or {}
        typer.secho(
            f"Plan rejected by operator. Notes: {decision.get('user_notes')}",
            fg=typer.colors.YELLOW,
        )
        return

    reg: dict[str, Any] = state.get("regression_result") or {}
    desc: dict[str, Any] = state.get("desc_stats_report") or {}
    merged: dict[str, Any] = state.get("merged_dataset") or {}

    typer.secho("Regression summary:", fg=typer.colors.GREEN, bold=True)
    typer.echo(str(reg.get("summary", "<missing summary>")))
    typer.echo(f"\nSign check: {reg.get('sign_check')}")

    typer.echo("\nArtifacts:")
    if merged.get("file_path"):
        typer.echo(f"  merged_dataset : {merged['file_path']}")
    if desc.get("do_file_path"):
        typer.echo(f"  desc_stats do  : {desc['do_file_path']}")
    if desc.get("log_file_path"):
        typer.echo(f"  desc_stats log : {desc['log_file_path']}")
    if reg.get("do_file_path"):
        typer.echo(f"  regression do  : {reg['do_file_path']}")
    if reg.get("log_file_path"):
        typer.echo(f"  regression log : {reg['log_file_path']}")


def _dump_final_state(state: dict[str, Any]) -> Path | None:
    """Dump terminal state as JSON to session_dir (derived from merged_dataset path).

    Returns the written path, or None if no session_dir can be derived
    (e.g. hard_failure/rejected before data download).
    """
    merged: dict[str, Any] | None = state.get("merged_dataset")
    if not merged or not merged.get("file_path"):
        return None
    session_dir = Path(str(merged["file_path"])).resolve().parent
    target = session_dir / "final_state.json"
    target.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    typer.echo(f"\nfinal_state.json -> {target}")
    return target


# ---------------------------------------------------------------------------
# Graph driver
# ---------------------------------------------------------------------------


async def _drive_graph(initial: WorkflowState, thread_id: str) -> dict[str, Any]:
    """Run the graph to completion, handling hitl interrupts in-process."""
    graph = build_graph()
    config: Any = {"configurable": {"thread_id": thread_id}}

    result: dict[str, Any] = await graph.ainvoke(initial, config=config)  # pyright: ignore[reportUnknownMemberType]

    while (payload := _interrupt_payload(result)) is not None:
        decision = _prompt_hitl_decision(payload)
        result = await graph.ainvoke(Command(resume=decision), config=config)  # pyright: ignore[reportUnknownMemberType]

    snapshot = await graph.aget_state(config)  # pyright: ignore[reportUnknownMemberType]
    return cast("dict[str, Any]", snapshot.values)


# ---------------------------------------------------------------------------
# Typer command
# ---------------------------------------------------------------------------


@app.command()
def run(
    topic: str = typer.Option(..., "--topic", help="Research topic (one sentence summary of X→Y)."),
    x_variable: str = typer.Option(..., "--x-variable", help="Core explanatory variable."),
    y_variable: str = typer.Option(..., "--y-variable", help="Dependent variable."),
    sample_scope: str = typer.Option(..., "--sample-scope", help="Sample scope description."),
    time_range_start: str = typer.Option(..., "--time-range-start", help="Time range start."),
    time_range_end: str = typer.Option(..., "--time-range-end", help="Time range end."),
    data_frequency: DataFrequency = typer.Option(
        ..., "--data-frequency", help="Sampling frequency."
    ),
    thread_id: str | None = typer.Option(
        None, "--thread-id", help="Optional thread id; uuid4() if omitted."
    ),
) -> None:
    """Run the empirical analysis workflow end-to-end."""
    request: UserRequest = {
        "topic": topic,
        "x_variable": x_variable,
        "y_variable": y_variable,
        "sample_scope": sample_scope,
        "time_range_start": time_range_start,
        "time_range_end": time_range_end,
        "data_frequency": data_frequency.value,  # pyright: ignore[reportAssignmentType]
    }
    tid = thread_id or str(uuid.uuid4())
    if apply_langsmith_env():
        typer.echo("[harness-stata] LangSmith tracing enabled")
    typer.echo(f"[harness-stata] thread_id={tid}")

    final_state = asyncio.run(_drive_graph({"user_request": request}, tid))
    _render_summary(final_state)
    _dump_final_state(final_state)

    status = final_state.get("workflow_status")
    if status in ("failed_hard_contract", "rejected"):
        raise typer.Exit(code=1)
