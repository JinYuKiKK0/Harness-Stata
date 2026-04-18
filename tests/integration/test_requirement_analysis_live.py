"""Integration test: call real LLM for requirement_analysis.

Run with: pytest -m integration
Requires real DASHSCOPE_API_KEY environment variable.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from harness_stata.nodes.requirement_analysis import requirement_analysis
from harness_stata.state import UserRequest, WorkflowState

pytestmark = pytest.mark.integration


def test_live_llm_returns_valid_spec(make_user_request: Callable[..., UserRequest]) -> None:
    """Smoke test: real LLM call should return a well-formed EmpiricalSpec."""
    state: WorkflowState = {"user_request": make_user_request()}
    result = requirement_analysis(state)

    spec = result["empirical_spec"]
    assert "topic" in spec
    assert len(spec["variables"]) >= 3  # Y + X + at least 1 control
    assert spec["time_range"]["start"] == "2018"
    assert spec["data_frequency"] == "yearly"
