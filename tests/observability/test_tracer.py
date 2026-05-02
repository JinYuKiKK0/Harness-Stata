"""Pure-logic tests for ``HarnessTracer`` stream chunk dispatch.

The LLM/tool callback paths are exercised end-to-end with real services;
unit tests here only validate the deterministic stream-driven node IO
capture: namespace tuple → directory layout, ``input/update/output``
sequencing, and interrupt detection.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness_stata.observability.models import RunMeta
from harness_stata.observability.store import RunStore
from harness_stata.observability.tracer import HarnessTracer


def _meta(run_id: str = "20260502T120000Z-tttt") -> RunMeta:
    return {
        "run_id": run_id,
        "status": "running",
        "mode": "node-run",
        "config": {"harness_version": "0.0.0"},
    }


def _tracer(tmp_path: Path) -> HarnessTracer:
    store = RunStore.create(tmp_path, _meta())
    return HarnessTracer(store)


# ---------------------------------------------------------------------------
# Root-graph node IO sequencing
# ---------------------------------------------------------------------------


class TestRootNodeIO:
    def test_input_update_output_written(self, tmp_path: Path) -> None:
        tracer = _tracer(tmp_path)
        # Root init values -> node update -> post-update values
        tracer._handle_chunk(((), "values", {"user_request": {"topic": "x"}}))
        tracer._handle_chunk(
            ((), "updates", {"data_cleaning": {"merged_dataset": {"row_count": 100}}})
        )
        tracer._handle_chunk(
            ((), "values", {"user_request": {"topic": "x"}, "merged_dataset": {"row_count": 100}})
        )

        node_dir = tracer.store.run_dir / "nodes" / "data_cleaning"
        assert (node_dir / "input.json").is_file()
        assert (node_dir / "update.json").is_file()
        assert (node_dir / "output.json").is_file()

        input_state = json.loads((node_dir / "input.json").read_text("utf-8"))
        update_state = json.loads((node_dir / "update.json").read_text("utf-8"))
        output_state = json.loads((node_dir / "output.json").read_text("utf-8"))

        assert input_state["state"] == {"user_request": {"topic": "x"}}
        assert update_state["state"] == {"merged_dataset": {"row_count": 100}}
        assert output_state["state"] == {
            "user_request": {"topic": "x"},
            "merged_dataset": {"row_count": 100},
        }

    def test_timeline_records_exit(self, tmp_path: Path) -> None:
        tracer = _tracer(tmp_path)
        tracer._handle_chunk(((), "values", {"a": 1}))
        tracer._handle_chunk(((), "updates", {"node_a": {"a": 2}}))
        tracer._handle_chunk(((), "values", {"a": 2}))

        timeline = (tracer.store.run_dir / "timeline.jsonl").read_text("utf-8").splitlines()
        assert len(timeline) == 1
        line = json.loads(timeline[0])
        assert line["node"] == "node_a"
        assert line["event"] == "exit"
        assert line["seq"] == 1


# ---------------------------------------------------------------------------
# Subgraph nesting
# ---------------------------------------------------------------------------


class TestSubgraphNesting:
    def test_subgraph_node_path(self, tmp_path: Path) -> None:
        tracer = _tracer(tmp_path)
        # init root values
        tracer._handle_chunk(((), "values", {"empirical_spec": {"topic": "y"}}))
        # subgraph entry snapshot
        tracer._handle_chunk((("data_probe:abc",), "values", {"empirical_spec": {"topic": "y"}}))
        # planning_agent completes inside subgraph
        tracer._handle_chunk((("data_probe:abc",), "updates", {"planning_agent": {"plan": ["Q1"]}}))
        tracer._handle_chunk(
            (
                ("data_probe:abc",),
                "values",
                {"empirical_spec": {"topic": "y"}, "plan": ["Q1"]},
            )
        )

        nested = tracer.store.run_dir / "nodes" / "data_probe" / "sub_nodes" / "planning_agent"
        assert (nested / "input.json").is_file()
        assert (nested / "update.json").is_file()
        assert (nested / "output.json").is_file()

        input_state = json.loads((nested / "input.json").read_text("utf-8"))
        # input is the namespace's last seen values BEFORE this update
        assert input_state["state"] == {"empirical_spec": {"topic": "y"}}
        assert input_state["namespace"] == ["data_probe:abc"]

    def test_timeline_namespaces_node(self, tmp_path: Path) -> None:
        tracer = _tracer(tmp_path)
        tracer._handle_chunk((("data_probe:abc",), "values", {}))
        tracer._handle_chunk((("data_probe:abc",), "updates", {"planning_agent": {"plan": []}}))
        line = json.loads((tracer.store.run_dir / "timeline.jsonl").read_text("utf-8").strip())
        # subgraph node names get parent prefix in timeline for context
        assert line["node"] == "data_probe.planning_agent"


# ---------------------------------------------------------------------------
# Interrupt
# ---------------------------------------------------------------------------


class TestInterrupt:
    def test_interrupt_captured_not_treated_as_node(self, tmp_path: Path) -> None:
        tracer = _tracer(tmp_path)
        tracer._handle_chunk(((), "values", {"x": 1}))
        sentinel = ("INTERRUPT_PAYLOAD",)
        tracer._handle_chunk(((), "updates", {"__interrupt__": sentinel}))

        # interrupt should not create a node directory
        node_dir = tracer.store.run_dir / "nodes" / "__interrupt__"
        assert not node_dir.exists()

        # tracer remembers interrupt
        assert tracer._last_interrupt == sentinel

        # timeline records interrupt event
        line = json.loads((tracer.store.run_dir / "timeline.jsonl").read_text("utf-8").strip())
        assert line["event"] == "interrupt"


# ---------------------------------------------------------------------------
# Multiple updates in one chunk (parallel branches)
# ---------------------------------------------------------------------------


class TestParallelUpdates:
    def test_two_nodes_in_single_chunk_both_dumped(self, tmp_path: Path) -> None:
        tracer = _tracer(tmp_path)
        tracer._handle_chunk(((), "values", {"k": 0}))
        tracer._handle_chunk(((), "updates", {"a": {"k": 1}, "b": {"k": 2}}))
        tracer._handle_chunk(((), "values", {"k": 12}))

        for name in ("a", "b"):
            node_dir = tracer.store.run_dir / "nodes" / name
            assert (node_dir / "update.json").is_file()
            assert (node_dir / "output.json").is_file()


# ---------------------------------------------------------------------------
# mark_status
# ---------------------------------------------------------------------------


class TestMarkStatus:
    def test_mark_status_rewrites_meta(self, tmp_path: Path) -> None:
        tracer = _tracer(tmp_path)
        tracer.mark_status("interrupted")
        meta = tracer.store.read_meta()
        assert meta["status"] == "interrupted"
        tracer.mark_status("success")
        assert tracer.store.read_meta()["status"] == "success"
