"""Data download node — fifth node in the workflow.

Pure-code node. Consumes DownloadManifest emitted by the probe subgraph,
invokes csmar-mcp tools (``csmar_probe_query`` -> ``csmar_materialize_query``)
sequentially for each DownloadTask, and records the resulting file paths in
``downloaded_files`` for the downstream data_cleaning node.

Failure is hard: any task whose probe returns ``can_materialize=False`` or
whose materialize call raises aborts the entire node with a RuntimeError;
partial-success semantics are deliberately not supported so that downstream
nodes can assume the full variable set is present.

Filters currently honoured: ``start_date`` / ``end_date`` only. Other keys in
``DownloadTask.filters`` are ignored for the MVP — revisit when F20 surfaces
a concrete case needing CSMAR condition-string passthrough.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from langchain_core.tools import BaseTool

from harness_stata.clients.csmar import get_csmar_tools
from harness_stata.config import get_settings
from harness_stata.state import (
    DownloadedFile,
    DownloadManifest,
    DownloadTask,
    WorkflowState,
)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

_PROBE_TOOL_NAME = "csmar_probe_query"
_MATERIALIZE_TOOL_NAME = "csmar_materialize_query"
_SESSION_TS_FORMAT = "%Y%m%dT%H%M%SZ"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(state: WorkflowState) -> str | None:
    manifest = state.get("download_manifest")
    if manifest is None:
        return "state.download_manifest is missing"
    if not manifest.get("items"):
        return "download_manifest.items must be a non-empty list"
    return None


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def _make_session_dir(downloads_root: Path) -> Path:
    ts = datetime.now(UTC).strftime(_SESSION_TS_FORMAT)
    session_dir = downloads_root / ts
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _make_task_dir(session_dir: Path, task: DownloadTask) -> Path:
    task_dir = session_dir / f"{task['database']}_{task['table']}"
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


# ---------------------------------------------------------------------------
# Tool input/output adapters
# ---------------------------------------------------------------------------


def _tools_by_name(tools: Sequence[BaseTool]) -> Mapping[str, BaseTool]:
    return {t.name: t for t in tools}


def _build_probe_payload(task: DownloadTask) -> dict[str, object]:
    columns = list(dict.fromkeys([*task["key_fields"], *task["variable_fields"]]))
    payload: dict[str, object] = {
        "table_code": task["table"],
        "columns": columns,
    }
    start_date = task["filters"].get("start_date")
    end_date = task["filters"].get("end_date")
    if isinstance(start_date, str) and start_date:
        payload["start_date"] = start_date
    if isinstance(end_date, str) and end_date:
        payload["end_date"] = end_date
    return payload


def _coerce_dict(raw: object, context: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        msg = f"{context}: expected dict response, got {type(raw).__name__}"
        raise RuntimeError(msg)
    return cast("dict[str, Any]", raw)


def _extract_validation_id(probe_result: Mapping[str, Any], task: DownloadTask) -> str:
    table = task["table"]
    if probe_result.get("can_materialize") is not True:
        invalid = cast("list[Any]", probe_result.get("invalid_columns") or [])
        msg = (
            f"csmar_probe_query refused materialize for table {table}: "
            f"can_materialize={probe_result.get('can_materialize')!r}, "
            f"invalid_columns={invalid!r}"
        )
        raise RuntimeError(msg)
    validation_id = probe_result.get("validation_id")
    if not isinstance(validation_id, str) or not validation_id:
        msg = f"csmar_probe_query did not return validation_id for table {table}"
        raise RuntimeError(msg)
    return validation_id


def _extract_file_paths(mat_result: Mapping[str, Any], task: DownloadTask) -> list[str]:
    files = mat_result.get("files")
    if not isinstance(files, list) or not files:
        msg = f"csmar_materialize_query returned no files for table {task['table']}"
        raise RuntimeError(msg)
    paths: list[str] = []
    for entry in cast("list[object]", files):
        if not isinstance(entry, str) or not entry:
            msg = (
                f"csmar_materialize_query returned non-string file entry for table {task['table']}"
            )
            raise RuntimeError(msg)
        paths.append(entry)
    return paths


def _make_downloaded_files(task: DownloadTask, files: Sequence[str]) -> list[DownloadedFile]:
    return [
        DownloadedFile(
            path=p,
            source_table=task["table"],
            key_fields=list(task["key_fields"]),
            variable_names=list(task["variable_names"]),
        )
        for p in files
    ]


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


async def data_download(state: WorkflowState) -> dict[str, Any]:
    """Batch-download every DownloadTask and record file paths in downloaded_files."""
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    manifest: DownloadManifest = state["download_manifest"]  # type: ignore[reportTypedDictNotRequiredAccess]
    session_dir = _make_session_dir(get_settings().downloads_root)
    collected: list[DownloadedFile] = []

    async with get_csmar_tools() as tools:
        by_name = _tools_by_name(tools)
        if _PROBE_TOOL_NAME not in by_name or _MATERIALIZE_TOOL_NAME not in by_name:
            missing = sorted({_PROBE_TOOL_NAME, _MATERIALIZE_TOOL_NAME} - set(by_name))
            msg = f"csmar-mcp is missing required tools: {missing}"
            raise RuntimeError(msg)
        probe_tool = by_name[_PROBE_TOOL_NAME]
        materialize_tool = by_name[_MATERIALIZE_TOOL_NAME]

        for task in manifest["items"]:
            probe_raw = await probe_tool.ainvoke(_build_probe_payload(task))  # pyright: ignore[reportUnknownMemberType]
            probe_result = _coerce_dict(
                probe_raw, f"csmar_probe_query response for table {task['table']}"
            )
            validation_id = _extract_validation_id(probe_result, task)

            task_dir = _make_task_dir(session_dir, task)
            mat_raw = await materialize_tool.ainvoke(  # pyright: ignore[reportUnknownMemberType]
                {"validation_id": validation_id, "output_dir": str(task_dir)}
            )
            mat_result = _coerce_dict(
                mat_raw, f"csmar_materialize_query response for table {task['table']}"
            )
            files = _extract_file_paths(mat_result, task)
            collected.extend(_make_downloaded_files(task, files))

    return {"downloaded_files": {"files": collected}}
