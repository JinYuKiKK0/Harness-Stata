"""Single source of truth for which nodes are CLI-runnable in isolation.

This file is the **ONLY** place observability infra reaches into
``harness_stata.nodes`` to acquire concrete node callables. New nodes
join the CLI ``node-run`` whitelist by adding entries here plus the
matching ``REQUIRED_FIELDS`` row.

Node value contract: any object accepted by
``StateGraph.add_node(name, value)`` — async function, sync function,
or pre-compiled subgraph (``CompiledStateGraph``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from harness_stata.nodes.data_cleaning import data_cleaning
from harness_stata.nodes.data_probe import data_probe
from harness_stata.state import WorkflowState

NodeRunnable = Callable[[WorkflowState], Awaitable[dict[str, Any]]]

NODE_REGISTRY: dict[str, NodeRunnable] = {
    "data_probe": data_probe,  # type: ignore[dict-item]
    "data_cleaning": data_cleaning,  # type: ignore[dict-item]
}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "data_probe": ("empirical_spec",),
    "data_cleaning": ("downloaded_files", "empirical_spec"),
}
