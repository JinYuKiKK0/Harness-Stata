"""Data cleaning node (F20) -- sixth node in the workflow.

ReAct-driven. Consumes DownloadedFiles (F18) + EmpiricalSpec (F09), binds one
persistent-namespace Python REPL tool to the generic ReAct subgraph (F19),
lets the LLM do cross-table join / wide->long / snake_case renaming, then
enforces post-conditions and writes ``merged_dataset``.

Tiered failure: primary-key duplication or ReAct truncation -> RuntimeError;
variable coverage below ``_COVERAGE_THRESHOLD`` -> ``MergedDataset.warnings``.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import traceback
from pathlib import Path
from typing import Any, cast

import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool, tool  # pyright: ignore[reportUnknownVariableType]

from harness_stata.prompts import load_prompt
from harness_stata.state import (
    DownloadedFile,
    EmpiricalSpec,
    MergedDataset,
    VariableDefinition,
    WorkflowState,
)
from harness_stata.subgraphs.generic_react import ReactState, build_react_subgraph

_MAX_ITERATIONS = 30
_MERGED_FILENAME = "merged.csv"
_COVERAGE_THRESHOLD = 0.8
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*?)\n```\s*$", re.DOTALL)


def _make_python_tool() -> BaseTool:
    """Build a fresh ``run_python`` tool with a private persistent namespace."""
    namespace: dict[str, Any] = {"pd": pd, "Path": Path}

    @tool  # pyright: ignore[reportUntypedFunctionDecorator, reportUnknownVariableType, reportUnknownArgumentType]
    def run_python(code: str) -> str:
        """Execute Python in a persistent namespace pre-loaded with pd and Path.

        State persists across calls within one node invocation. Use ``print``
        to surface values; returns captured stdout or ``ERROR: ...`` on raise.
        """
        stdout_buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buffer):
                exec(code, namespace)
        except Exception as exc:
            captured = stdout_buffer.getvalue()
            tb = "".join(traceback.format_exception_only(type(exc), exc))
            return f"{captured}ERROR: {tb}".strip()
        captured = stdout_buffer.getvalue()
        return captured if captured else "(no output)"

    return run_python


def _validate(state: WorkflowState) -> str | None:
    downloaded = state.get("downloaded_files")
    if downloaded is None or not downloaded.get("files"):
        return "state.downloaded_files.files is missing or empty"
    if state.get("empirical_spec") is None:
        return "state.empirical_spec is missing"
    return None


def _derive_output_path(files: list[DownloadedFile]) -> Path:
    """Place merged CSV alongside F18's session dir: ``<session>/merged.csv``."""
    first = Path(files[0]["path"]).resolve()
    return first.parents[1] / _MERGED_FILENAME


def _format_variables(variables: list[VariableDefinition]) -> str:
    return "\n".join(
        f"- {v['name']} ({v['role']}, {v['contract_type']}): {v['description']}" for v in variables
    )


def _format_files(files: list[DownloadedFile]) -> str:
    return "\n".join(
        f"{i}. path={f['path']}\n"
        f"   source_table={f['source_table']}\n"
        f"   key_fields={f['key_fields']}\n"
        f"   variable_names={f['variable_names']}"
        for i, f in enumerate(files, start=1)
    )


def _build_human_prompt(spec: EmpiricalSpec, files: list[DownloadedFile], output_path: Path) -> str:
    return (
        f"## topic\n{spec['topic']}\n\n"
        f"## analysis_granularity\n{spec['analysis_granularity']}\n\n"
        f"## sample / time / frequency\n"
        f"sample_scope: {spec['sample_scope']}\n"
        f"time_range: {spec['time_range_start']} - {spec['time_range_end']}\n"
        f"data_frequency: {spec['data_frequency']}\n\n"
        f"## variables (EmpiricalSpec.variables)\n"
        f"{_format_variables(spec['variables'])}\n\n"
        f"## source CSV files\n{_format_files(files)}\n\n"
        f"## output_path\n{output_path}\n\n"
        "Merge the above source CSVs into one long-format CSV saved at"
        " output_path, then emit the terminating JSON as specified."
    )


def _extract_final_json(content: str) -> dict[str, Any]:
    text = content.strip()
    match = _FENCE_RE.match(text)
    if match is not None:
        text = match.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"data_cleaning: cannot parse final AIMessage content as JSON: {exc}"
        raise RuntimeError(msg) from exc
    if not isinstance(obj, dict):
        msg = f"data_cleaning: final JSON must be object, got {type(obj).__name__}"
        raise RuntimeError(msg)
    return cast("dict[str, Any]", obj)


def _extract_primary_key(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("primary_key")
    if not isinstance(raw, list) or not raw:
        msg = "data_cleaning: final JSON must include non-empty 'primary_key' list"
        raise RuntimeError(msg)
    keys: list[str] = []
    for item in cast("list[object]", raw):
        if not isinstance(item, str) or not item:
            msg = "data_cleaning: primary_key entries must be non-empty strings"
            raise RuntimeError(msg)
        keys.append(item)
    return keys


def _find_variable_column(var_name: str, columns: list[str]) -> str | None:
    target = var_name.lower().replace("_", "")
    for col in columns:
        if col.lower().replace("_", "") == target:
            return col
    return None


def _check_post_conditions(
    csv_path: Path, spec: EmpiricalSpec, primary_key: list[str]
) -> tuple[int, list[str], list[str]]:
    df = pd.read_csv(csv_path)  # pyright: ignore[reportUnknownMemberType]
    row_count = len(df)
    columns = [str(c) for c in cast("list[Any]", list(df.columns))]

    missing_keys = [k for k in primary_key if k not in columns]
    if missing_keys:
        msg = (
            f"data_cleaning: primary_key columns {missing_keys!r} not present"
            f" in merged CSV columns {columns!r}"
        )
        raise RuntimeError(msg)
    dup_count = int(cast("Any", df.duplicated(subset=primary_key).sum()))  # pyright: ignore[reportUnknownMemberType]
    if dup_count > 0:
        msg = (
            f"data_cleaning: merged CSV has {dup_count} duplicate rows"
            f" on primary_key {primary_key!r}"
        )
        raise RuntimeError(msg)

    warnings: list[str] = []
    for var in spec["variables"]:
        col = _find_variable_column(var["name"], columns)
        if col is None:
            warnings.append(f"variable {var['name']!r} not found in merged CSV columns")
            continue
        if row_count == 0:
            warnings.append(f"variable {var['name']!r} column exists but CSV is empty")
            continue
        non_null = int(cast("Any", df[col].notna().sum()))  # pyright: ignore[reportUnknownMemberType]
        coverage = non_null / row_count
        if coverage < _COVERAGE_THRESHOLD:
            warnings.append(
                f"variable {var['name']!r} (column {col!r}) coverage"
                f" {coverage:.2%} < threshold {_COVERAGE_THRESHOLD:.0%}"
            )
    return row_count, columns, warnings


async def data_cleaning(state: WorkflowState) -> dict[str, Any]:
    """Merge DownloadedFiles into a single long-format CSV.

    Drives a generic ReAct subgraph bound to one ``run_python`` tool. The LLM
    performs joins / reshape / renaming; this node enforces post-conditions
    (primary-key uniqueness hard, variable coverage soft) and writes
    ``merged_dataset``.
    """
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    spec: EmpiricalSpec = state["empirical_spec"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    files = state["downloaded_files"]["files"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    output_path = _derive_output_path(files)

    python_tool = _make_python_tool()
    prompt = load_prompt("data_cleaning")
    subgraph = build_react_subgraph(
        tools=[python_tool], prompt=prompt, max_iterations=_MAX_ITERATIONS
    )

    initial: ReactState = {
        "messages": [HumanMessage(content=_build_human_prompt(spec, files, output_path))],
        "iteration_count": 0,
    }
    result = await subgraph.ainvoke(initial)  # pyright: ignore[reportUnknownMemberType]

    messages = cast("list[Any]", result.get("messages", []))
    if not messages:
        raise RuntimeError("data_cleaning: ReAct subgraph returned no messages")
    last = messages[-1]
    if not isinstance(last, AIMessage):
        msg = f"data_cleaning: final message is not AIMessage (got {type(last).__name__})"
        raise RuntimeError(msg)
    if last.tool_calls:
        raise RuntimeError(
            "data_cleaning: ReAct reached max_iterations without a terminal AIMessage"
        )

    content_attr = cast("Any", last.content)  # pyright: ignore[reportUnknownMemberType]
    if not isinstance(content_attr, str):
        msg = f"data_cleaning: AIMessage.content must be str, got {type(content_attr).__name__}"
        raise RuntimeError(msg)
    payload = _extract_final_json(content_attr)
    primary_key = _extract_primary_key(payload)

    if not output_path.exists():
        msg = f"data_cleaning: LLM finished but output file {output_path!s} does not exist"
        raise RuntimeError(msg)

    row_count, columns, warnings = _check_post_conditions(output_path, spec, primary_key)
    merged: MergedDataset = {
        "file_path": str(output_path),
        "row_count": row_count,
        "columns": columns,
        "warnings": warnings,
    }
    return {"merged_dataset": merged}
