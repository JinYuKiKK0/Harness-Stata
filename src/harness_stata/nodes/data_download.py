"""Data download node — fifth node in the workflow.

Pure-code node. Consumes DownloadManifest emitted by the probe subgraph,
invokes csmar-mcp tools (``csmar_probe_query`` -> ``csmar_materialize_query``)
sequentially for each DownloadTask, and records the resulting file paths in
``downloaded_files`` for the downstream data_cleaning node.
``variable_fields`` are raw source fields; variable construction rules are
carried through as ``variable_mappings`` and interpreted by data_cleaning.

Failure is hard: any task whose probe returns ``can_materialize=False`` or
whose materialize call raises aborts the entire node with a RuntimeError;
partial-success semantics are deliberately not supported so that downstream
nodes can assume the full variable set is present.

Filters are a strict contract: every task must carry ``start_date`` and
``end_date`` in ``YYYY-MM-DD`` form. Optional ``condition`` is passed through to
CSMAR's probe tool.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool

from harness_stata.clients.csmar import get_csmar_tools
from harness_stata.clients.mcp import call_structured_mcp_tool
from harness_stata.config import get_settings
from harness_stata.nodes._writes import awrites_to
from harness_stata.state import (
    DownloadedFile,
    DownloadedFiles,
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
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# CSMAR 下发包内除数据文件外还会携带 [DES] 字段字典 .txt 等附属物，
# 这里按后缀白名单只保留可被 data_cleaning 登记到 DuckDB 的数据文件。
_DATA_FILE_SUFFIXES = frozenset({".csv", ".xlsx", ".xls"})


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
        "start_date": _require_date_filter(task, "start_date"),
        "end_date": _require_date_filter(task, "end_date"),
    }
    condition = task["filters"].get("condition")
    if isinstance(condition, str) and condition.strip():
        payload["condition"] = condition.strip()
    return payload


def _require_date_filter(task: DownloadTask, key: str) -> str:
    value = task["filters"].get(key)
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        msg = f"DownloadTask for table {task['table']} must include filters.{key} as YYYY-MM-DD"
        raise RuntimeError(msg)
    return value


def _extract_validation_id(probe_result: Mapping[str, Any], task: DownloadTask) -> str:
    table = task["table"]
    if probe_result.get("can_materialize") is not True:
        invalid = probe_result.get("invalid_columns") or []
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
    for entry in files:
        if not isinstance(entry, str) or not entry:
            msg = (
                f"csmar_materialize_query returned non-string file entry for table {task['table']}"
            )
            raise RuntimeError(msg)
        if Path(entry).suffix.lower() not in _DATA_FILE_SUFFIXES:
            continue
        paths.append(entry)
    if not paths:
        msg = (
            f"csmar_materialize_query returned no data files (suffix in {sorted(_DATA_FILE_SUFFIXES)!r})"
            f" for table {task['table']}; got {files!r}"
        )
        raise RuntimeError(msg)
    return paths


def _make_downloaded_files(task: DownloadTask, files: Sequence[str]) -> list[DownloadedFile]:
    downloaded: list[DownloadedFile] = []
    variable_mappings = task.get("variable_mappings")
    for p in files:
        item = DownloadedFile(
            path=p,
            source_table=task["table"],
            key_fields=list(task["key_fields"]),
            variable_names=list(task["variable_names"]),
        )
        if variable_mappings:
            item["variable_mappings"] = [
                {
                    "variable_name": m["variable_name"],
                    "source_fields": list(m["source_fields"]),
                    "match_kind": m["match_kind"],
                    "transform": dict(m["transform"]) if m["transform"] is not None else None,
                    "evidence": m.get("evidence"),
                }
                for m in variable_mappings
            ]
        downloaded.append(item)
    return downloaded


# ---------------------------------------------------------------------------
# Node function
# ---------------------------------------------------------------------------


@awrites_to("downloaded_files")
async def data_download(state: WorkflowState) -> DownloadedFiles:
    """Batch-download every DownloadTask and record file paths in downloaded_files."""
    err = _validate(state)
    if err is not None:
        raise ValueError(err)

    manifest: DownloadManifest = state["download_manifest"]
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
            probe_ctx = f"csmar_probe_query response for table {task['table']}"
            probe_result = await call_structured_mcp_tool(
                probe_tool, _build_probe_payload(task), probe_ctx
            )
            validation_id = _extract_validation_id(probe_result, task)

            task_dir = _make_task_dir(session_dir, task)
            mat_ctx = f"csmar_materialize_query response for table {task['table']}"
            mat_result = await call_structured_mcp_tool(
                materialize_tool,
                {"validation_id": validation_id, "output_dir": str(task_dir)},
                mat_ctx,
            )
            files = _extract_file_paths(mat_result, task)
            collected.extend(_make_downloaded_files(task, files))

    return {"files": collected}
