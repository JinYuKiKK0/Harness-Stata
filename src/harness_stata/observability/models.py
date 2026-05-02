"""Data shapes written to ``.harness/runs/<run_id>/``.

Schemas are kept narrow on purpose so JSONL lines stay <=1KB and
``Read``/``Grep`` output remains scannable. Wide payloads (full LLM
messages, full tool args/results) live in ``raw/<event_id>.json`` and
are referenced by ``raw_id`` from summary lines.
"""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

RunMode = Literal["full", "node-run"]
RunStatus = Literal[
    "running",
    "interrupted",
    "success",
    "failed_hard_contract",
    "rejected",
    "failed",
]
TimelineEventKind = Literal["enter", "exit", "error", "resume"]
TraceEventKind = Literal["llm", "tool"]


class RunConfigSummary(TypedDict, total=False):
    llm_model: str
    harness_version: str


class RunMeta(TypedDict):
    run_id: str
    status: RunStatus
    mode: RunMode
    entry_node: NotRequired[str]
    fixture_source: NotRequired[str]
    config: RunConfigSummary


class TimelineEvent(TypedDict):
    ts: str
    node: str
    event: TimelineEventKind
    seq: int
    duration_ms: NotRequired[int]
    summary: NotRequired[str]
    raw_id: NotRequired[str]
    error: NotRequired[str]


class TraceEventSummary(TypedDict):
    ts: str
    kind: TraceEventKind
    name: str
    duration_ms: NotRequired[int]
    model: NotRequired[str]
    tokens_in: NotRequired[int]
    tokens_out: NotRequired[int]
    args_preview: NotRequired[str]
    result_preview: NotRequired[str]
    error: NotRequired[str]
    raw_id: str


class NodeIOPayload(TypedDict):
    """Wrapper around a node-level state slice serialized to JSON.

    ``state`` carries the actual TypedDict-shaped dict. ``namespace`` and
    ``node`` are duplicated here for self-describing dumps so a single
    ``input.json``/``output.json``/``update.json`` is portable when copied
    out of the run tree.
    """

    namespace: list[str]
    node: str
    kind: Literal["input", "update", "output"]
    state: dict[str, object]
