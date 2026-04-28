"""Unit tests for the regression node — pre-LLM validation paths only.

按项目测试约定:不 mock LLM/MCP。sign_check 等纯逻辑应抽出为纯函数后单测,
本文件只覆盖 ``_validate`` 阶段的确定性错误。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from harness_stata.nodes.regression import regression
from harness_stata.state import EmpiricalSpec, ModelPlan, WorkflowState


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(regression(state))


def test_regression_missing_merged_dataset_raises(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "model_plan": make_model_plan(),
    }
    with pytest.raises(ValueError, match="merged_dataset"):
        _run(state)
