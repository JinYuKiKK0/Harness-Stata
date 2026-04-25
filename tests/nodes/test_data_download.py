"""Unit tests for the data_download node (F18)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from harness_stata.nodes.data_download import data_download
from harness_stata.state import DownloadManifest, DownloadTask, WorkflowState

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_tool(name: str, *, ainvoke: AsyncMock) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.ainvoke = ainvoke
    return tool


def _patch_csmar(
    mocker: MockerFixture,
    *,
    probe: AsyncMock,
    materialize: AsyncMock,
) -> tuple[MagicMock, MagicMock]:
    probe_tool = _make_tool("csmar_probe_query", ainvoke=probe)
    materialize_tool = _make_tool("csmar_materialize_query", ainvoke=materialize)

    @asynccontextmanager
    async def _cm() -> AsyncIterator[list[MagicMock]]:
        yield [probe_tool, materialize_tool]

    mocker.patch(
        "harness_stata.nodes.data_download.get_csmar_tools",
        side_effect=_cm,
    )
    return probe_tool, materialize_tool


def _patch_settings(mocker: MockerFixture, downloads_root: Path) -> None:
    fake = MagicMock()
    fake.downloads_root = downloads_root
    mocker.patch("harness_stata.nodes.data_download.get_settings", return_value=fake)


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_download(state))


def _second_task() -> DownloadTask:
    return {
        "database": "CSMAR",
        "table": "TRD_YEAR",
        "key_fields": ["SYMBOL"],
        "variable_fields": ["Yretwd"],
        "variable_names": ["annual_return"],
        "filters": {"start_date": "2015-01-01", "end_date": "2022-12-31"},
    }


# ---------------------------------------------------------------------------
# Success paths
# ---------------------------------------------------------------------------


def test_data_download_single_task_success(
    mocker: MockerFixture,
    tmp_path: Path,
    make_download_manifest: Callable[..., DownloadManifest],
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock(return_value={"can_materialize": True, "validation_id": "v1"})
    materialize = AsyncMock(
        return_value={"files": [str(tmp_path / "a.csv")], "output_dir": str(tmp_path)}
    )
    probe_tool, mat_tool = _patch_csmar(mocker, probe=probe, materialize=materialize)

    manifest = make_download_manifest()
    manifest["items"][0]["filters"]["condition"] = "Markettype in (1,4)"
    state: WorkflowState = {"download_manifest": manifest}

    out = _run(state)

    assert out == {
        "downloaded_files": {
            "files": [
                {
                    "path": str(tmp_path / "a.csv"),
                    "source_table": "FS_COMBAS",
                    "key_fields": ["SYMBOL", "ACCYEAR"],
                    "variable_names": ["SIZE", "DEBT_RATIO"],
                }
            ]
        }
    }
    assert probe_tool.ainvoke.call_count == 1
    probe_payload: dict[str, Any] = probe_tool.ainvoke.call_args.args[0]
    assert probe_payload["table_code"] == "FS_COMBAS"
    assert set(probe_payload["columns"]) == {"SYMBOL", "ACCYEAR", "A001000000", "A002100000"}
    assert probe_payload["start_date"] == "2015-01-01"
    assert probe_payload["end_date"] == "2022-12-31"
    assert probe_payload["condition"] == "Markettype in (1,4)"
    assert mat_tool.ainvoke.call_count == 1
    mat_payload: dict[str, Any] = mat_tool.ainvoke.call_args.args[0]
    assert mat_payload["validation_id"] == "v1"
    assert mat_payload["output_dir"].startswith(str(tmp_path))


def test_data_download_multi_tasks_success(
    mocker: MockerFixture,
    tmp_path: Path,
    make_download_manifest: Callable[..., DownloadManifest],
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock(
        side_effect=[
            {"can_materialize": True, "validation_id": "v1"},
            {"can_materialize": True, "validation_id": "v2"},
        ]
    )
    materialize = AsyncMock(
        side_effect=[
            {"files": [str(tmp_path / "f1.csv")]},
            {"files": [str(tmp_path / "f2.csv")]},
        ]
    )
    probe_tool, mat_tool = _patch_csmar(mocker, probe=probe, materialize=materialize)

    manifest = make_download_manifest(tasks=[*make_download_manifest()["items"], _second_task()])
    state: WorkflowState = {"download_manifest": manifest}

    out = _run(state)

    files = out["downloaded_files"]["files"]
    assert len(files) == 2
    assert files[0]["path"].endswith("f1.csv")
    assert files[0]["source_table"] == "FS_COMBAS"
    assert files[1]["path"].endswith("f2.csv")
    assert files[1]["source_table"] == "TRD_YEAR"
    assert files[1]["variable_names"] == ["annual_return"]
    assert probe_tool.ainvoke.call_count == 2
    assert mat_tool.ainvoke.call_count == 2


def test_data_download_materialize_returns_multi_files(
    mocker: MockerFixture,
    tmp_path: Path,
    make_download_manifest: Callable[..., DownloadManifest],
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock(return_value={"can_materialize": True, "validation_id": "v1"})
    materialize = AsyncMock(
        return_value={"files": [str(tmp_path / "part1.csv"), str(tmp_path / "part2.csv")]}
    )
    _patch_csmar(mocker, probe=probe, materialize=materialize)

    state: WorkflowState = {"download_manifest": make_download_manifest()}

    out = _run(state)

    files = out["downloaded_files"]["files"]
    assert len(files) == 2
    assert {Path(f["path"]).name for f in files} == {"part1.csv", "part2.csv"}
    for f in files:
        assert f["source_table"] == "FS_COMBAS"
        assert f["variable_names"] == ["SIZE", "DEBT_RATIO"]


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_data_download_probe_cannot_materialize_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_download_manifest: Callable[..., DownloadManifest],
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock(
        return_value={"can_materialize": False, "invalid_columns": ["A001000000"]}
    )
    materialize = AsyncMock()
    _patch_csmar(mocker, probe=probe, materialize=materialize)

    state: WorkflowState = {"download_manifest": make_download_manifest()}

    with pytest.raises(RuntimeError, match="A001000000"):
        _run(state)
    assert materialize.await_count == 0


def test_data_download_materialize_raises_propagates(
    mocker: MockerFixture,
    tmp_path: Path,
    make_download_manifest: Callable[..., DownloadManifest],
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock(return_value={"can_materialize": True, "validation_id": "v1"})
    materialize = AsyncMock(side_effect=RuntimeError("network unavailable"))
    probe_tool, mat_tool = _patch_csmar(mocker, probe=probe, materialize=materialize)

    state: WorkflowState = {"download_manifest": make_download_manifest()}

    with pytest.raises(RuntimeError, match="network unavailable"):
        _run(state)
    assert probe_tool.ainvoke.call_count == 1
    assert mat_tool.ainvoke.call_count == 1


def test_data_download_empty_manifest_raises(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock()
    materialize = AsyncMock()
    _patch_csmar(mocker, probe=probe, materialize=materialize)

    state: WorkflowState = {"download_manifest": {"items": []}}

    with pytest.raises(ValueError, match="non-empty"):
        _run(state)
    assert probe.await_count == 0
    assert materialize.await_count == 0


def test_data_download_missing_required_date_filter_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_download_manifest: Callable[..., DownloadManifest],
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock()
    materialize = AsyncMock()
    _patch_csmar(mocker, probe=probe, materialize=materialize)
    manifest = make_download_manifest()
    del manifest["items"][0]["filters"]["start_date"]

    with pytest.raises(RuntimeError, match=r"filters\.start_date"):
        _run({"download_manifest": manifest})
    assert probe.await_count == 0
    assert materialize.await_count == 0


def test_data_download_invalid_date_filter_raises(
    mocker: MockerFixture,
    tmp_path: Path,
    make_download_manifest: Callable[..., DownloadManifest],
) -> None:
    _patch_settings(mocker, tmp_path)
    probe = AsyncMock()
    materialize = AsyncMock()
    _patch_csmar(mocker, probe=probe, materialize=materialize)
    manifest = make_download_manifest()
    manifest["items"][0]["filters"]["end_date"] = "2022"

    with pytest.raises(RuntimeError, match=r"filters\.end_date"):
        _run({"download_manifest": manifest})
    assert probe.await_count == 0
    assert materialize.await_count == 0
