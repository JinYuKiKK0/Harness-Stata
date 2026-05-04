"""Typer CLI entry point for Harness-Stata.

Two commands:

* ``run`` — full workflow end-to-end with HITL prompts; trace persisted to
  ``.harness/runs/<id>/`` automatically.
* ``node-run`` — isolated single-node execution loaded from a fixture
  (``--from-run`` / ``--from-fixture`` / default = ``.harness/latest``);
  same trace target. CLI-runnable nodes whitelisted in
  ``observability.NODE_REGISTRY``.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

import typer
from langgraph.types import Command

from harness_stata.config import apply_langsmith_env, get_settings
from harness_stata.graph import build_graph
from harness_stata.observability import (
    NODE_REGISTRY,
    FixtureLoader,
    HarnessTracer,
    NodeRunner,
    RunStore,
)
from harness_stata.observability.models import RunMeta
from harness_stata.observability.store import generate_run_id
from harness_stata.state import UserRequest, WorkflowState

__all__ = ["app"]


def _config_summary() -> dict[str, str]:
    """Capture model + version into ``RunMeta.config`` so reading old
    runs across upgrades makes sense."""
    try:
        version = importlib.metadata.version("harness-stata")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return {
        "llm_model": get_settings().llm_model_name,
        "harness_version": version,
    }


class DataFrequency(StrEnum):
    YEARLY = "yearly"
    MONTHLY = "monthly"


app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def _main() -> None:
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
        return value
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
    """Run the graph to completion, handling hitl interrupts in-process.

    Trace is persisted to ``.harness/runs/<run_id>/`` via :class:`HarnessTracer`.
    Interrupt-resume reuses the same ``RunStore`` so a single run directory
    captures the full timeline (running → interrupted → running → success).
    """
    graph = build_graph()
    config: Any = {"configurable": {"thread_id": thread_id}}

    project_root = Path.cwd()
    meta: RunMeta = {
        "run_id": generate_run_id(),
        "status": "running",
        "mode": "full",
        "config": _config_summary(),  # type: ignore[typeddict-item]
    }
    store = RunStore.create(project_root, meta)
    tracer = HarnessTracer(store)

    typer.echo(f"[harness-stata] trace -> {store.run_dir}")

    try:
        result = await tracer.run(graph, initial, config=config)
        while (payload := _interrupt_payload(result)) is not None:
            tracer.mark_status("interrupted")
            decision = _prompt_hitl_decision(payload)
            tracer.append_timeline(node="hitl", event="resume")
            result = await tracer.run(graph, Command(resume=decision), config=config)
        snapshot = await graph.aget_state(config)
    except BaseException as exc:
        tracer.append_timeline(node="<root>", event="error", error=str(exc))
        tracer.mark_status("failed")
        raise

    final_status = snapshot.values.get("workflow_status", "success")
    tracer.mark_status(final_status)
    return snapshot.values


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
        "data_frequency": data_frequency.value,
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


# ---------------------------------------------------------------------------
# node-run command
# ---------------------------------------------------------------------------


@app.command(name="node-run")
def node_run(
    node: str = typer.Argument(
        ...,
        help=f"Target node. Whitelisted: {sorted(NODE_REGISTRY.keys())}",
    ),
    from_run: str | None = typer.Option(
        None,
        "--from-run",
        help="Load fixture from .harness/runs/<run_id>/nodes/<node>/input.json",
    ),
    from_fixture: str | None = typer.Option(
        None,
        "--from-fixture",
        help="Load fixture from downloads/fixtures/<subdir>/input_state.json",
    ),
) -> None:
    """Run a single node in isolation, capturing trace to .harness/runs/<id>/."""
    if node not in NODE_REGISTRY:
        valid = sorted(NODE_REGISTRY.keys())
        typer.secho(
            f"unknown node {node!r}; CLI-runnable nodes: {valid}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if from_run is not None and from_fixture is not None:
        typer.secho(
            "--from-run and --from-fixture are mutually exclusive",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    project_root = Path.cwd()
    loader = FixtureLoader(project_root)
    if from_fixture is not None:
        state, source = loader.load_from_fixture(from_fixture, node)
    elif from_run is not None:
        state, source = loader.load_from_run(from_run, node)
    else:
        state, source = loader.load_latest(node)

    if apply_langsmith_env():
        typer.echo("[harness-stata] LangSmith tracing enabled")
    typer.echo(f"[harness-stata] node-run {node!r} fixture={source}")

    runner = NodeRunner(project_root, node)
    final, store = asyncio.run(
        runner.run(state, fixture_source=source, config_summary=_config_summary())
    )

    typer.echo(f"[harness-stata] trace -> {store.run_dir}")
    typer.echo(f"[harness-stata] result keys: {sorted(final.keys())}")
