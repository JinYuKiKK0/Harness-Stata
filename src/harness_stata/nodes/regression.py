"""Regression node (F22) — 待重写。"""

from __future__ import annotations

from typing import Literal, TypedDict

from harness_stata.state import RegressionResult, WorkflowState


class RegressionOutput(TypedDict):
    regression_result: RegressionResult
    workflow_status: Literal["success"]


async def regression(state: WorkflowState) -> RegressionOutput:
    raise NotImplementedError("regression: 待重写")
