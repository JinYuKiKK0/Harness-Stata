"""End-to-end smoke test for ``NodeRunner`` using a pure-function node.

This test does NOT mock LLM/MCP — instead it injects a deterministic
echo node into ``NODE_REGISTRY`` via ``monkeypatch``, exercising the
LangGraph minimal-graph assembly + tracer wiring without touching
external services.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from harness_stata.observability.registry import NODE_REGISTRY, REQUIRED_FIELDS
from harness_stata.observability.runner import NodeRunner


async def _echo_node(state: dict) -> dict[str, Any]:
    return {
        "merged_dataset": {
            "file_path": "/tmp/echo.csv",
            "row_count": 1,
            "columns": ["a"],
            "warnings": [],
        }
    }


def test_node_runner_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(NODE_REGISTRY, "echo_smoke", _echo_node)
    monkeypatch.setitem(REQUIRED_FIELDS, "echo_smoke", ("user_request",))

    runner = NodeRunner(tmp_path, "echo_smoke")
    initial_state: dict = {"user_request": {"topic": "smoke"}}
    final, store = asyncio.run(runner.run(initial_state, fixture_source="test/smoke"))

    # Tracer dumped the IO triple
    node_dir = store.run_dir / "nodes" / "echo_smoke"
    assert (node_dir / "input.json").is_file()
    assert (node_dir / "update.json").is_file()
    assert (node_dir / "output.json").is_file()

    update_payload = json.loads((node_dir / "update.json").read_text("utf-8"))
    assert "merged_dataset" in update_payload["state"]

    # Final state contains the merged dict
    assert "merged_dataset" in final

    # Meta and pointer present
    meta = json.loads((store.run_dir / "meta.json").read_text("utf-8"))
    assert meta["mode"] == "node-run"
    assert meta["entry_node"] == "echo_smoke"
    assert meta["fixture_source"] == "test/smoke"
    assert meta["status"] == "success"

    timeline = (store.run_dir / "timeline.jsonl").read_text("utf-8").splitlines()
    assert len(timeline) >= 1


def test_node_runner_unknown_node_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown node"):
        NodeRunner(tmp_path, "no_such_node")


def test_node_runner_propagates_node_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(state: dict) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setitem(NODE_REGISTRY, "boom_node", boom)
    monkeypatch.setitem(REQUIRED_FIELDS, "boom_node", ("user_request",))

    runner = NodeRunner(tmp_path, "boom_node")
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(runner.run({"user_request": {}}, fixture_source="t"))

    # status should be marked failed even when the node raised
    runs_root = tmp_path / ".harness" / "runs"
    [run_dir] = list(runs_root.iterdir())
    meta = json.loads((run_dir / "meta.json").read_text("utf-8"))
    assert meta["status"] == "failed"
