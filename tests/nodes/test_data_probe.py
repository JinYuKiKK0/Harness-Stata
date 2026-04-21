"""Unit tests for the data_probe node (F25) — input validation only.

Validation fires before the async CSMAR context manager is entered, so these
tests require no mocking.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from harness_stata.nodes.data_probe import data_probe
from harness_stata.state import EmpiricalSpec, ModelPlan, WorkflowState


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_probe(state))


def test_data_probe_missing_empirical_spec_raises(
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {"model_plan": make_model_plan()}
    with pytest.raises(ValueError, match="empirical_spec"):
        _run(state)


def test_data_probe_missing_model_plan_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    state: WorkflowState = {"empirical_spec": make_empirical_spec()}
    with pytest.raises(ValueError, match="model_plan"):
        _run(state)


def test_data_probe_empty_variables_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    spec = make_empirical_spec(variables=[])
    state: WorkflowState = {"empirical_spec": spec, "model_plan": make_model_plan()}
    with pytest.raises(ValueError, match="non-empty"):
        _run(state)
