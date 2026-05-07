"""Hydrate :class:`WorkflowState` for a single-node run from two sources.

* ``--from-run <run_id>`` reads ``.harness/runs/<id>/nodes/<node>/input.json``
  which is :class:`NodeIOPayload` (carries ``state`` under a ``state`` key)
* ``--from-fixture <subdir>`` reads ``downloads/fixtures/<subdir>/input_state.json``
  which is a plain :class:`WorkflowState` dict (user-authored, no wrapper)
* ``--from-run latest`` resolves through ``.harness/latest`` (plain text
  pointer; symlinks are unreliable on Windows so we use a file)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from harness_stata.observability.registry import REQUIRED_FIELDS
from harness_stata.observability.store import DEFAULT_HARNESS_DIR, LATEST_FILE_NAME
from harness_stata.state import WorkflowState


class FixtureLoader:
    """Load + validate a node's input ``WorkflowState`` from disk."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root

    # ------------------------------------------------------------------
    # Public sources
    # ------------------------------------------------------------------

    def load_from_run(self, run_id: str, node: str) -> tuple[WorkflowState, str]:
        """Load node's ``input.json`` from ``.harness/runs/<run_id>/...``.

        Returns ``(state, source_label)`` where source_label is suitable
        for ``RunMeta.fixture_source``.
        """
        path = (
            self.project_root
            / DEFAULT_HARNESS_DIR
            / "runs"
            / run_id
            / "nodes"
            / node
            / "input.json"
        )
        if not path.is_file():
            raise FileNotFoundError(
                f"no input.json for node {node!r} in run {run_id!r} (expected at {path})"
            )
        wrapper = self._load_json(path)
        state = wrapper.get("state") if isinstance(wrapper, dict) else None
        if not isinstance(state, dict):
            raise ValueError(f"malformed NodeIOPayload at {path}: missing or non-dict 'state' key")
        self.validate_for_node(state, node)
        return cast("WorkflowState", state), f"runs/{run_id}/{node}"

    def load_from_fixture(self, subdir: str, node: str) -> tuple[WorkflowState, str]:
        """Load ``downloads/fixtures/<subdir>/input_state.json`` (plain state)."""
        path = self.project_root / "downloads" / "fixtures" / subdir / "input_state.json"
        if not path.is_file():
            raise FileNotFoundError(
                f"no input_state.json under downloads/fixtures/{subdir}/ (expected at {path})"
            )
        state = self._load_json(path)
        if not isinstance(state, dict):
            raise ValueError(f"malformed input_state.json at {path}: top-level must be a dict")
        self.validate_for_node(state, node)
        return cast("WorkflowState", state), f"fixtures/{subdir}"

    def load_latest(self, node: str) -> tuple[WorkflowState, str]:
        latest_file = self.project_root / DEFAULT_HARNESS_DIR / LATEST_FILE_NAME
        if not latest_file.is_file():
            raise FileNotFoundError(
                f"no '{DEFAULT_HARNESS_DIR}/{LATEST_FILE_NAME}' pointer found;"
                f" '{LATEST_FILE_NAME}' is updated only by full-mode runs."
                " run the full workflow once, or pass --from-run <id> / --from-fixture <subdir> explicitly"
            )
        run_id = latest_file.read_text("utf-8").strip()
        if not run_id:
            raise ValueError(f"'{DEFAULT_HARNESS_DIR}/{LATEST_FILE_NAME}' is empty")
        return self.load_from_run(run_id, node)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def validate_for_node(state: dict[str, Any], node: str) -> None:
        required = REQUIRED_FIELDS.get(node)
        if required is None:
            raise ValueError(
                f"unknown node {node!r}; registry knows {sorted(REQUIRED_FIELDS.keys())!r}"
            )
        missing = [f for f in required if not state.get(f)]
        if missing:
            raise ValueError(f"fixture missing fields {missing} required by node {node!r}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"failed to parse JSON at {path}: {exc}") from exc
