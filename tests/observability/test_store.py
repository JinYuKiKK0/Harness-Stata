"""Pure-logic tests for ``RunStore`` and namespace path helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from harness_stata.observability.models import (
    NodeIOPayload,
    RunMeta,
    TimelineEvent,
    TraceEventSummary,
)
from harness_stata.observability.store import (
    LATEST_FILE_NAME,
    RunStore,
    generate_run_id,
    namespace_path_segments,
    utc_now_iso,
)


def _meta(run_id: str, mode: str = "node-run") -> RunMeta:
    return {
        "run_id": run_id,
        "status": "running",
        "mode": mode,  # type: ignore[typeddict-item]
        "config": {},
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestPureHelpers:
    def test_run_id_format(self) -> None:
        run_id = generate_run_id()
        assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{8}", run_id), run_id

    def test_run_id_unique(self) -> None:
        ids = {generate_run_id() for _ in range(50)}
        # randomness from token_hex(4) → 4.3e9 space, 50 draws collision negligible
        assert len(ids) == 50

    def test_utc_iso_format(self) -> None:
        ts = utc_now_iso()
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts), ts

    @pytest.mark.parametrize(
        ("namespace", "node", "expected"),
        [
            ((), "data_cleaning", ["nodes", "data_cleaning"]),
            (
                ("data_probe:abc123",),
                "planning_agent",
                ["nodes", "data_probe", "sub_nodes", "planning_agent"],
            ),
            (
                ("a:1", "b:2"),
                "c",
                ["nodes", "a", "sub_nodes", "b", "sub_nodes", "c"],
            ),
            ((), "node-with-dash", ["nodes", "node-with-dash"]),
        ],
    )
    def test_namespace_path_segments(
        self, namespace: tuple[str, ...], node: str, expected: list[str]
    ) -> None:
        assert namespace_path_segments(namespace, node) == expected


# ---------------------------------------------------------------------------
# RunStore filesystem behavior
# ---------------------------------------------------------------------------


class TestRunStoreCreate:
    def test_creates_layout(self, tmp_path: Path) -> None:
        meta = _meta("20260502T103500Z-aaaa")
        store = RunStore.create(tmp_path, meta)

        assert store.run_dir == tmp_path / ".harness" / "runs" / meta["run_id"]
        assert store.run_dir.is_dir()
        assert (store.run_dir / "nodes").is_dir()
        assert (store.run_dir / "raw").is_dir()
        assert (store.run_dir / "meta.json").is_file()

    def test_writes_initial_meta(self, tmp_path: Path) -> None:
        meta = _meta("20260502T103500Z-aaaa", mode="full")
        store = RunStore.create(tmp_path, meta)

        loaded = json.loads((store.run_dir / "meta.json").read_text("utf-8"))
        assert loaded["run_id"] == meta["run_id"]
        assert loaded["mode"] == "full"
        assert loaded["status"] == "running"

    def test_updates_latest_pointer(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        latest = tmp_path / ".harness" / LATEST_FILE_NAME
        assert latest.read_text("utf-8") == store.run_id

    def test_second_run_overrides_latest(self, tmp_path: Path) -> None:
        RunStore.create(tmp_path, _meta("20260502T100000Z-aaaa"))
        store2 = RunStore.create(tmp_path, _meta("20260502T110000Z-bbbb"))

        latest = (tmp_path / ".harness" / LATEST_FILE_NAME).read_text("utf-8")
        assert latest == store2.run_id

    def test_concurrent_runs_isolated(self, tmp_path: Path) -> None:
        a = RunStore.create(tmp_path, _meta("20260502T100000Z-aaaa"))
        b = RunStore.create(tmp_path, _meta("20260502T110000Z-bbbb"))
        assert a.run_dir != b.run_dir
        assert a.run_dir.is_dir() and b.run_dir.is_dir()


class TestRunStoreMetaRoundtrip:
    def test_write_then_read_meta(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        updated: RunMeta = {
            "run_id": store.run_id,
            "status": "success",
            "mode": "full",
            "config": {},
        }
        store.write_meta(updated)
        assert store.read_meta()["status"] == "success"


class TestRunStoreTimeline:
    def test_appends_jsonl_lines(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        e1: TimelineEvent = {
            "ts": utc_now_iso(),
            "node": "data_cleaning",
            "event": "resume",
            "seq": store.next_timeline_seq(),
        }
        e2: TimelineEvent = {
            "ts": utc_now_iso(),
            "node": "data_cleaning",
            "event": "exit",
            "seq": store.next_timeline_seq(),
            "duration_ms": 1234,
        }
        store.append_timeline(e1)
        store.append_timeline(e2)

        lines = (store.run_dir / "timeline.jsonl").read_text("utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["seq"] == 1
        assert json.loads(lines[1])["seq"] == 2
        assert json.loads(lines[1])["duration_ms"] == 1234

    def test_seq_monotonic_independent(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        seqs = [store.next_timeline_seq() for _ in range(5)]
        assert seqs == [1, 2, 3, 4, 5]


class TestRunStoreNodeIO:
    def test_root_node_io(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        payload: NodeIOPayload = {
            "namespace": [],
            "node": "data_cleaning",
            "kind": "input",
            "state": {"empirical_spec": {"topic": "x"}},
        }
        path = store.write_node_io(payload)
        assert path == store.run_dir / "nodes" / "data_cleaning" / "input.json"
        loaded = json.loads(path.read_text("utf-8"))
        assert loaded["state"]["empirical_spec"]["topic"] == "x"

    def test_subgraph_node_io_nested(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        payload: NodeIOPayload = {
            "namespace": ["data_probe:abc"],
            "node": "planning_agent",
            "kind": "output",
            "state": {"variable_results": []},
        }
        path = store.write_node_io(payload)
        assert path == (
            store.run_dir / "nodes" / "data_probe" / "sub_nodes" / "planning_agent" / "output.json"
        )
        assert path.is_file()

    def test_three_kinds_coexist(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        for kind in ("input", "update", "output"):
            store.write_node_io(
                {
                    "namespace": [],
                    "node": "data_cleaning",
                    "kind": kind,  # type: ignore[typeddict-item]
                    "state": {"k": kind},
                }
            )
        for kind in ("input", "update", "output"):
            target = store.run_dir / "nodes" / "data_cleaning" / f"{kind}.json"
            assert target.is_file()

    def test_append_node_event(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        summary: TraceEventSummary = {
            "ts": utc_now_iso(),
            "kind": "llm",
            "name": "qwen-max",
            "raw_id": "evt_000001",
            "tokens_in": 100,
            "tokens_out": 50,
        }
        store.append_node_event((), "data_cleaning", summary)
        path = store.run_dir / "nodes" / "data_cleaning" / "events.jsonl"
        line = path.read_text("utf-8").strip()
        assert json.loads(line)["raw_id"] == "evt_000001"


class TestRunStoreRaw:
    def test_event_id_monotonic_and_padded(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        ids = [store.next_event_id() for _ in range(3)]
        assert ids == ["evt_000001", "evt_000002", "evt_000003"]

    def test_write_raw_to_correct_path(self, tmp_path: Path) -> None:
        store = RunStore.create(tmp_path, _meta("20260502T103500Z-aaaa"))
        eid = store.next_event_id()
        path = store.write_raw(eid, {"messages": [{"role": "user", "content": "hi"}]})
        assert path == store.run_dir / "raw" / f"{eid}.json"
        assert path.is_file()
        loaded = json.loads(path.read_text("utf-8"))
        assert loaded["messages"][0]["content"] == "hi"
