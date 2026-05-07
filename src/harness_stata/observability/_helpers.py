"""Stateless helpers used by :class:`HarnessTracer`.

Kept in a sibling module so ``tracer.py`` itself stays under the
file-size budget. Nothing here touches :class:`RunStore` — the helpers
are pure functions on LangChain / LangGraph payload shapes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

PREVIEW_LIMIT = 200
TOOL_PREVIEW_LIMIT = 800
INTERRUPT_KEY = "__interrupt__"
TERMINAL_STATUSES = frozenset({"success", "failed", "failed_hard_contract", "rejected"})


def preview(value: object, limit: int = PREVIEW_LIMIT) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def coerce_namespace(value: object) -> tuple[str, ...]:
    """LangGraph emits ``()`` for the root graph and ``("parent:id",)`` for
    subgraph scopes; coerce to a stable tuple of strings."""
    if not value:
        return ()
    if isinstance(value, tuple):
        return tuple(str(s) for s in value)
    if isinstance(value, list):
        return tuple(str(s) for s in value)
    return ()


def attribution_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> tuple[tuple[str, ...], str] | None:
    """Derive (namespace, node) for LLM/tool event attribution.

    LangGraph injects two metadata fields per node-level chain:

    * ``langgraph_node`` — current node's name
    * ``checkpoint_ns`` — pipe-separated parent path with the same
      ``<parent>:<task_id>`` segment format the stream channel uses;
      empty / absent on root-graph nodes.

    Returns ``None`` if attribution cannot be determined (rare; non-
    LangGraph chains).
    """
    if not metadata:
        return None
    node = metadata.get("langgraph_node")
    if not isinstance(node, str) or not node:
        return None
    ckpt_ns = metadata.get("checkpoint_ns")
    namespace = tuple(ckpt_ns.split("|")) if isinstance(ckpt_ns, str) and ckpt_ns else ()
    if namespace and namespace[-1].split(":", 1)[0] == node:
        # ReAct subgraph init phase: langgraph_node == last namespace parent
        # name. Stripping prevents nodes/<X>/sub_nodes/<X>/ double-counting.
        namespace = namespace[:-1]
    return (namespace, node)


def model_name(metadata: Mapping[str, Any] | None) -> str | None:
    if not metadata:
        return None
    for key in ("ls_model_name", "ls_model", "model_name", "model"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def extract_token_usage(response_dump: Mapping[str, Any]) -> dict[str, int]:
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


_SEMANTIC_FAILURE_MARKERS = ('"status": "failed"', '"status":"failed"')


def is_semantic_tool_failure(result_text: str) -> bool:
    """Detect tool returns that completed without raising but report failure.

    Stata-Executor's ``ExecutionResult`` and DuckDB ``run_sql`` errors both
    surface as JSON with ``"status": "failed"`` plus a non-null ``error_kind``.
    Substring scan over ``result_preview`` is sufficient since the relevant
    keys appear in the first 800 chars by construction.
    """
    if any(m in result_text for m in _SEMANTIC_FAILURE_MARKERS):
        return True
    return '"error_kind":' in result_text and '"error_kind": null' not in result_text


def coerce_jsonable(value: Any) -> Any:
    """Best-effort JSON normalization for tool outputs."""
    try:
        json.dumps(value, default=str)
        return value
    except (TypeError, ValueError):
        return str(value)
