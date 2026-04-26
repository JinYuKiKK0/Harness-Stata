"""LangSmith Studio entrypoint.

Exports a module-level compiled graph so ``langgraph dev`` can load the
workflow directly from ``langgraph.json``.
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
