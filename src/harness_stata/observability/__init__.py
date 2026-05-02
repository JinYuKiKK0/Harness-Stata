"""Observability infrastructure for Harness-Stata.

Non-invasive overlay layer that captures node IO, LLM/tool events to
``.harness/runs/<run_id>/`` as JSONL + JSON, and provides a CLI-driven
``NodeRunner`` for per-node isolation. See plan
``langgraph-agent-llm-agent-agent-token-t-glistening-panda.md`` for the
architectural rationale.

Public API surface kept intentionally small; importers should depend
only on the names re-exported here.
"""

from harness_stata.observability.loader import FixtureLoader
from harness_stata.observability.models import (
    NodeIOPayload,
    RunMeta,
    RunMode,
    TimelineEvent,
    TraceEventSummary,
)
from harness_stata.observability.registry import NODE_REGISTRY, REQUIRED_FIELDS
from harness_stata.observability.runner import NodeRunner
from harness_stata.observability.store import RunStore, namespace_path_segments
from harness_stata.observability.tracer import HarnessTracer

__all__ = [
    "NODE_REGISTRY",
    "REQUIRED_FIELDS",
    "FixtureLoader",
    "HarnessTracer",
    "NodeIOPayload",
    "NodeRunner",
    "RunMeta",
    "RunMode",
    "RunStore",
    "TimelineEvent",
    "TraceEventSummary",
    "namespace_path_segments",
]
