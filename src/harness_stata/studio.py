"""LangSmith Studio entrypoint.

Exports a module-level compiled graph so ``langgraph dev`` can load the
workflow directly from ``langgraph.json``.

Note on observability: this entrypoint deliberately does **not** attach
:class:`harness_stata.observability.HarnessTracer`. ``langgraph dev``
loads ``graph`` exactly once at module import and reuses it across every
LangSmith Studio session — binding a single tracer instance would let
sessions overwrite each other's ``.harness/runs/<id>/`` directory. For
trace persistence go through the CLI (``harness-stata run`` or
``harness-stata node-run``); use Studio for interactive exploration only.
"""

from __future__ import annotations

from langgraph.graph.state import CompiledStateGraph

from harness_stata.config import apply_langsmith_env
from harness_stata.graph import build_graph
from harness_stata.state import WorkflowState

apply_langsmith_env()

graph: CompiledStateGraph[WorkflowState, None, WorkflowState, WorkflowState] = build_graph(
    use_checkpointer=False
)

__all__ = ["graph"]
