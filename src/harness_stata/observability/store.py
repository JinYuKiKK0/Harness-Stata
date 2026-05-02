"""Run-scoped filesystem store under ``.harness/runs/<run_id>/``.

Each ``RunStore`` instance owns one run directory exclusively. Concurrent
runs from different processes must use distinct ``run_id`` values (the
default factory guarantees this via timestamp + hex tag).

Layout produced::

    .harness/
      runs/
        <run_id>/
          meta.json                  ← live status; rewritten on each transition
          timeline.jsonl             ← node-level enter/exit/error/resume
          nodes/
            <root>/                  ← root-graph node
              input.json             ← state seen at node entry
              update.json            ← partial delta returned by node
              output.json            ← post-reducer state at node exit
              events.jsonl           ← LLM/tool summary lines
              sub_nodes/
                <child>/...          ← subgraph node, recursive
          raw/
            evt_<6-digit>.json       ← full LLM messages / tool args+result
      latest                         ← plain text file containing latest run_id
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_stata.observability.models import (
        NodeIOPayload,
        RunMeta,
        TimelineEvent,
        TraceEventSummary,
    )

DEFAULT_HARNESS_DIR = ".harness"
LATEST_FILE_NAME = "latest"


def generate_run_id() -> str:
    """Return ``YYYYMMDDTHHMMSSZ-<8 hex>`` UTC-anchored unique id.

    8 hex chars = 4 random bytes ≈ 4.3e9 namespace, so multiple runs in
    the same wall-clock second collide with negligible probability.
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(4)}"


def namespace_path_segments(namespace: tuple[str, ...], node: str) -> list[str]:
    """Translate LangGraph ``(namespace, node_key)`` into a relative path.

    LangGraph emits namespace tuples like ``("data_probe:<task_id>",)`` for
    subgraph scopes; the actual subgraph node name comes from the
    ``updates`` chunk's payload key. This helper builds the corresponding
    on-disk path under ``nodes/`` so subgraph nesting is reflected by
    ``sub_nodes/<child>/`` directories.
    """
    parts: list[str] = ["nodes"]
    for seg in namespace:
        parent_name = seg.split(":", 1)[0]
        parts.append(parent_name)
        parts.append("sub_nodes")
    parts.append(node)
    return parts


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunStore:
    """Owns one ``.harness/runs/<run_id>/`` directory for a single run."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id
        self.run_dir = root / "runs" / run_id
        self._next_event_seq = 1
        self._next_timeline_seq = 1

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        project_root: Path,
        meta: RunMeta,
        *,
        harness_dir_name: str = DEFAULT_HARNESS_DIR,
    ) -> RunStore:
        """Create a fresh run directory and write the initial ``meta.json``.

        ``meta["run_id"]`` is the canonical id; the directory name matches.
        ``latest`` plain-text pointer is updated atomically last so that
        partial directories are not advertised.
        """
        root = project_root / harness_dir_name
        run_dir = root / "runs" / meta["run_id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "nodes").mkdir(exist_ok=True)
        (run_dir / "raw").mkdir(exist_ok=True)
        store = cls(root=root, run_id=meta["run_id"])
        store.write_meta(meta)
        store._update_latest_pointer()
        return store

    # ------------------------------------------------------------------
    # Meta / latest
    # ------------------------------------------------------------------

    def write_meta(self, meta: RunMeta) -> None:
        path = self.run_dir / "meta.json"
        path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def read_meta(self) -> RunMeta:
        path = self.run_dir / "meta.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _update_latest_pointer(self) -> None:
        latest = self.root / LATEST_FILE_NAME
        latest.write_text(self.run_id, encoding="utf-8")

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    def append_timeline(self, event: TimelineEvent) -> None:
        path = self.run_dir / "timeline.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str))
            f.write("\n")

    def next_timeline_seq(self) -> int:
        seq = self._next_timeline_seq
        self._next_timeline_seq += 1
        return seq

    # ------------------------------------------------------------------
    # Node IO (input/update/output JSON)
    # ------------------------------------------------------------------

    def node_dir(self, namespace: tuple[str, ...], node: str) -> Path:
        rel = namespace_path_segments(namespace, node)
        target = self.run_dir.joinpath(*rel)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def write_node_io(self, payload: NodeIOPayload) -> Path:
        """Write a node IO payload to its kind-specific filename."""
        ns_tuple = tuple(payload["namespace"])
        target_dir = self.node_dir(ns_tuple, payload["node"])
        target = target_dir / f"{payload['kind']}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return target

    def append_node_event(
        self,
        namespace: tuple[str, ...],
        node: str,
        summary: TraceEventSummary,
    ) -> None:
        target_dir = self.node_dir(namespace, node)
        path = target_dir / "events.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False, default=str))
            f.write("\n")

    # ------------------------------------------------------------------
    # Raw payloads
    # ------------------------------------------------------------------

    def next_event_id(self) -> str:
        seq = self._next_event_seq
        self._next_event_seq += 1
        return f"evt_{seq:06d}"

    def write_raw(self, event_id: str, payload: dict[str, object]) -> Path:
        target = self.run_dir / "raw" / f"{event_id}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return target
