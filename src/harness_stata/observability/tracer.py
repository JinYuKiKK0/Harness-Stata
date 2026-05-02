"""HarnessTracer: dual-channel observability bound to one run.

Two complementary capture channels feed a single :class:`RunStore`:

* **Node IO via stream** — :meth:`run` wraps ``graph.astream(..., stream_mode=
  ["updates","values"], subgraphs=True)``; namespace tuples drive the
  ``nodes/<root>/sub_nodes/<child>/`` directory layout. ``input``,
  ``update``, ``output`` JSON are written per node.
* **LLM / tool events via callback** — inherits :class:`BaseCallbackHandler`;
  ``on_llm_*`` / ``on_tool_*`` write summary lines to
  ``nodes/<active>/events.jsonl`` plus full payloads to ``raw/<evt>.json``.
  Active-node attribution uses ``metadata.langgraph_node`` (best-effort —
  failures fall back to the root node).

The tracer instance is reusable across multiple ``run()`` invocations on
the same ``thread_id`` (e.g. interrupt-resume), but each run instance is
bound to exactly one :class:`RunStore`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig

from harness_stata.observability.store import RunStore, utc_now_iso

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import LLMResult

    from harness_stata.observability.models import (
        NodeIOPayload,
        RunStatus,
        TimelineEvent,
        TraceEventSummary,
    )

logger = logging.getLogger(__name__)

PREVIEW_LIMIT = 200
INTERRUPT_KEY = "__interrupt__"


def _preview(value: object, limit: int = PREVIEW_LIMIT) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _coerce_namespace(value: object) -> tuple[str, ...]:
    """LangGraph emits ``()`` for the root graph and ``("parent:id",)`` for
    subgraph scopes; coerce to a stable tuple of strings."""
    if not value:
        return ()
    if isinstance(value, tuple):
        return tuple(str(s) for s in value)
    if isinstance(value, list):
        return tuple(str(s) for s in value)
    return ()


def _attribution_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[tuple[str, ...], str] | None:
    """Derive (namespace, node) for LLM/tool event attribution.

    Best-effort — relies on ``metadata.langgraph_node`` set by LangGraph
    on each node-level Runnable. Subgraph nesting cannot be reliably
    recovered from callback metadata alone, so we attribute to the root
    node level; node-IO directory paths come from the stream channel and
    are authoritative.
    """
    if not metadata:
        return None
    node = metadata.get("langgraph_node")
    if not isinstance(node, str) or not node:
        return None
    return ((), node)


class HarnessTracer(BaseCallbackHandler):
    """Wraps a single graph run and persists trace artefacts."""

    def __init__(self, store: RunStore) -> None:
        self.store = store
        self._last_values: dict[tuple[str, ...], dict[str, Any]] = {}
        self._pending_outputs: list[tuple[tuple[str, ...], str]] = []
        self._last_interrupt: Any = None

        self._llm_starts: dict[UUID, dict[str, Any]] = {}
        self._tool_starts: dict[UUID, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Driver-facing API
    # ------------------------------------------------------------------

    async def run(
        self,
        graph: Any,
        input_state: Any,
        config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Drive ``graph.astream`` and capture node IO + emit final state.

        Returns the final root-graph state dict. If a LangGraph interrupt
        fired during the run, the returned dict carries ``__interrupt__``
        with the interrupt payload (mirroring ``ainvoke`` semantics) so
        the caller can prompt the user and resume with ``Command``.
        """
        self._last_interrupt = None
        merged_config = self._merge_callbacks(config)

        async for chunk in graph.astream(
            input_state,
            config=merged_config,
            stream_mode=["updates", "values"],
            subgraphs=True,
        ):
            self._handle_chunk(chunk)

        final = dict(self._last_values.get((), {}))
        if self._last_interrupt is not None:
            final[INTERRUPT_KEY] = self._last_interrupt
        return final

    def mark_status(self, status: RunStatus) -> None:
        meta = self.store.read_meta()
        meta["status"] = status
        self.store.write_meta(meta)

    def append_timeline(self, node: str, event: str, **extra: Any) -> None:
        line: TimelineEvent = {
            "ts": utc_now_iso(),
            "node": node,
            "event": event,  # type: ignore[typeddict-item]
            "seq": self.store.next_timeline_seq(),
            **extra,  # type: ignore[typeddict-item]
        }
        self.store.append_timeline(line)

    # ------------------------------------------------------------------
    # Stream chunk dispatch
    # ------------------------------------------------------------------

    def _merge_callbacks(self, config: Mapping[str, Any] | None) -> RunnableConfig:
        merged: dict[str, Any] = dict(config or {})
        cbs = list(merged.get("callbacks") or [])
        if self not in cbs:
            cbs.append(self)
        merged["callbacks"] = cbs
        return merged  # type: ignore[return-value]

    def _handle_chunk(self, chunk: Any) -> None:
        try:
            namespace, mode_str, payload = chunk
        except (ValueError, TypeError):
            logger.warning("HarnessTracer: malformed astream chunk %r", chunk)
            return
        ns = _coerce_namespace(namespace)

        if mode_str == "updates" and isinstance(payload, dict):
            for key, value in payload.items():
                if key == INTERRUPT_KEY:
                    self._last_interrupt = value
                    self.append_timeline(node="<interrupt>", event="interrupt")
                    continue
                if isinstance(value, dict):
                    self._on_node_update(ns, key, value)
        elif mode_str == "values" and isinstance(payload, dict):
            self._on_values(ns, payload)

    def _on_node_update(self, namespace: tuple[str, ...], node: str, delta: dict[str, Any]) -> None:
        input_state = dict(self._last_values.get(namespace, {}))
        ns_list = list(namespace)
        self._write_node_io(
            {"namespace": ns_list, "node": node, "kind": "input", "state": input_state}
        )
        self._write_node_io({"namespace": ns_list, "node": node, "kind": "update", "state": delta})
        self.append_timeline(
            node=self._timeline_node_name(namespace, node),
            event="exit",
            summary=_preview(list(delta.keys())),
        )
        self._pending_outputs.append((namespace, node))

    def _on_values(self, namespace: tuple[str, ...], values: dict[str, Any]) -> None:
        self._last_values[namespace] = values
        if not self._pending_outputs:
            return
        remaining: list[tuple[tuple[str, ...], str]] = []
        for ns_pending, node_pending in self._pending_outputs:
            if ns_pending == namespace:
                self._write_node_io(
                    {
                        "namespace": list(ns_pending),
                        "node": node_pending,
                        "kind": "output",
                        "state": values,
                    }
                )
            else:
                remaining.append((ns_pending, node_pending))
        self._pending_outputs = remaining

    def _write_node_io(self, payload: NodeIOPayload) -> None:
        try:
            self.store.write_node_io(payload)
        except OSError as exc:
            logger.warning("HarnessTracer: write_node_io failed: %s", exc)

    @staticmethod
    def _timeline_node_name(namespace: tuple[str, ...], node: str) -> str:
        if not namespace:
            return node
        parents = ".".join(seg.split(":", 1)[0] for seg in namespace)
        return f"{parents}.{node}"

    # ------------------------------------------------------------------
    # LangChain callback channel — LLM/tool events
    # ------------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._llm_starts[run_id] = {
            "started_at": time.monotonic(),
            "metadata": metadata,
            "messages": [[m.model_dump() for m in row] for row in messages],
            "model_name": (serialized or {}).get("name") or _model_name(metadata),
        }

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._llm_starts[run_id] = {
            "started_at": time.monotonic(),
            "metadata": metadata,
            "prompts": prompts,
            "model_name": (serialized or {}).get("name") or _model_name(metadata),
        }

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        start = self._llm_starts.pop(run_id, None)
        if start is None:
            return
        attribution = _attribution_from_metadata(start.get("metadata"))
        ns, node = attribution if attribution is not None else ((), "<root>")
        eid = self.store.next_event_id()
        try:
            response_dump = response.model_dump()
        except AttributeError:
            response_dump = {"generations": [str(response.generations)]}
        usage = _extract_token_usage(response_dump)
        duration_ms = int((time.monotonic() - start["started_at"]) * 1000)

        summary: TraceEventSummary = {
            "ts": utc_now_iso(),
            "kind": "llm",
            "name": start.get("model_name") or "llm",
            "duration_ms": duration_ms,
            "raw_id": eid,
        }
        if usage.get("input"):
            summary["tokens_in"] = usage["input"]
        if usage.get("output"):
            summary["tokens_out"] = usage["output"]
        if model := start.get("model_name"):
            summary["model"] = model

        self.store.write_raw(
            eid,
            {
                "kind": "llm",
                "messages": start.get("messages") or start.get("prompts"),
                "response": response_dump,
                "metadata": start.get("metadata"),
            },
        )
        self.store.append_node_event(ns, node, summary)

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._llm_starts.pop(run_id, None)
        if start is None:
            return
        attribution = _attribution_from_metadata(start.get("metadata"))
        ns, node = attribution if attribution is not None else ((), "<root>")
        eid = self.store.next_event_id()
        self.store.write_raw(
            eid,
            {"kind": "llm_error", "error": str(error), "metadata": start.get("metadata")},
        )
        self.store.append_node_event(
            ns,
            node,
            {
                "ts": utc_now_iso(),
                "kind": "llm",
                "name": start.get("model_name") or "llm",
                "error": str(error),
                "raw_id": eid,
            },
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._tool_starts[run_id] = {
            "started_at": time.monotonic(),
            "metadata": metadata,
            "name": (serialized or {}).get("name") or "tool",
            "input": inputs if inputs is not None else input_str,
        }

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        start = self._tool_starts.pop(run_id, None)
        if start is None:
            return
        attribution = _attribution_from_metadata(start.get("metadata"))
        ns, node = attribution if attribution is not None else ((), "<root>")
        eid = self.store.next_event_id()
        duration_ms = int((time.monotonic() - start["started_at"]) * 1000)
        self.store.write_raw(
            eid,
            {
                "kind": "tool",
                "name": start.get("name"),
                "input": start.get("input"),
                "output": _coerce_jsonable(output),
                "metadata": start.get("metadata"),
            },
        )
        self.store.append_node_event(
            ns,
            node,
            {
                "ts": utc_now_iso(),
                "kind": "tool",
                "name": start.get("name") or "tool",
                "duration_ms": duration_ms,
                "args_preview": _preview(start.get("input")),
                "result_preview": _preview(output),
                "raw_id": eid,
            },
        )

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._tool_starts.pop(run_id, None)
        if start is None:
            return
        attribution = _attribution_from_metadata(start.get("metadata"))
        ns, node = attribution if attribution is not None else ((), "<root>")
        eid = self.store.next_event_id()
        self.store.write_raw(
            eid,
            {
                "kind": "tool_error",
                "name": start.get("name"),
                "input": start.get("input"),
                "error": str(error),
            },
        )
        self.store.append_node_event(
            ns,
            node,
            {
                "ts": utc_now_iso(),
                "kind": "tool",
                "name": start.get("name") or "tool",
                "error": str(error),
                "raw_id": eid,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model_name(metadata: Mapping[str, Any] | None) -> str | None:
    if not metadata:
        return None
    for key in ("ls_model_name", "ls_model", "model_name", "model"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _extract_token_usage(response_dump: Mapping[str, Any]) -> dict[str, int]:
    """Extract input/output token counts from an LLMResult dump if present."""
    out: dict[str, int] = {}
    llm_output = response_dump.get("llm_output") or {}
    usage = (
        llm_output.get("token_usage")
        or llm_output.get("usage")
        or response_dump.get("usage_metadata")
        or {}
    )
    if isinstance(usage, dict):
        if (v := usage.get("input_tokens") or usage.get("prompt_tokens")) is not None:
            out["input"] = int(v)
        if (v := usage.get("output_tokens") or usage.get("completion_tokens")) is not None:
            out["output"] = int(v)
    return out


def _coerce_jsonable(value: Any) -> Any:
    """Best-effort JSON normalization for tool outputs."""
    try:
        json.dumps(value, default=str)
        return value
    except (TypeError, ValueError):
        return str(value)
