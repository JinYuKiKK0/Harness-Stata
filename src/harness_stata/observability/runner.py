"""Single-node runner: assemble a minimal ``StateGraph`` and trace one node.

The minimal graph contains exactly the target node; ``START → node → END``.
LangGraph still drives the node through ``astream`` so :class:`HarnessTracer`
captures the same input/update/output triple it would in a full-workflow
run, just without the upstream work. Subgraph nodes (``data_probe``) are
exercised end-to-end inside the wrapper because their factory call lives
inside the wrapper function — the streaming machinery still picks up
internal ``planning_agent`` / ``verification_phase`` updates and routes
them under ``sub_nodes/`` automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from harness_stata.observability.models import RunMeta
from harness_stata.observability.registry import NODE_REGISTRY
from harness_stata.observability.store import RunStore, generate_run_id
from harness_stata.observability.tracer import HarnessTracer
from harness_stata.state import WorkflowState

if TYPE_CHECKING:
    from collections.abc import Mapping


class NodeRunner:
    """Owns one node-isolation run from fixture load to status mark."""

    def __init__(self, project_root: Path, node: str) -> None:
        if node not in NODE_REGISTRY:
            valid = sorted(NODE_REGISTRY.keys())
            raise ValueError(f"unknown node {node!r}; CLI-runnable nodes: {valid}")
        self.project_root = project_root
        self.node = node

    async def run(
        self,
        input_state: WorkflowState,
        *,
        fixture_source: str,
        config_summary: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], RunStore]:
        """Drive the minimal graph end-to-end and return (final_state, store)."""
        run_id = generate_run_id()
        meta: RunMeta = {
            "run_id": run_id,
            "status": "running",
            "mode": "node-run",
            "entry_node": self.node,
            "fixture_source": fixture_source,
            "config": dict(config_summary or {}),  # type: ignore[typeddict-item]
        }
        store = RunStore.create(self.project_root, meta)
        tracer = HarnessTracer(store)
        compiled = self._compile_minimal_graph()

        try:
            final = await tracer.run(compiled, input_state)
        except BaseException as exc:
            tracer.append_timeline(node=self.node, event="error", error=str(exc))
            tracer.mark_status("failed")
            raise
        tracer.mark_status("success")
        return final, store

    def _compile_minimal_graph(self) -> Any:
        builder = StateGraph(WorkflowState)
        builder.add_node(self.node, NODE_REGISTRY[self.node])
        builder.add_edge(START, self.node)
        builder.add_edge(self.node, END)
        return builder.compile()
