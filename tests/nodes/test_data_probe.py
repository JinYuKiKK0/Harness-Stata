"""Unit tests for the data_probe node (F25).

Mock strategy: patch both ``get_csmar_tools`` (async contextmanager) and
``build_probe_subgraph`` at the node's import site. The compiled subgraph is
replaced by a ``MagicMock`` whose ``.invoke()`` returns a hand-crafted
``ProbeState``. We intentionally do not exercise the real subgraph — F15/F16
unit tests already cover that; this file verifies only the wrapper's field
mapping and error propagation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from harness_stata.nodes.data_probe import data_probe
from harness_stata.state import (
    DownloadManifest,
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _patch_csmar(mocker: MockerFixture) -> list[MagicMock]:
    """Replace get_csmar_tools with a no-op async contextmanager yielding tools."""
    probe_tool = MagicMock()
    probe_tool.name = "csmar_probe_query"
    tools: list[MagicMock] = [probe_tool]

    @asynccontextmanager
    async def _cm() -> AsyncIterator[list[MagicMock]]:
        yield tools

    mocker.patch("harness_stata.nodes.data_probe.get_csmar_tools", side_effect=_cm)
    return tools


def _patch_settings(mocker: MockerFixture, per_variable_max_calls: int = 4) -> None:
    fake = MagicMock()
    fake.per_variable_max_calls = per_variable_max_calls
    mocker.patch("harness_stata.nodes.data_probe.get_settings", return_value=fake)


def _patch_subgraph(mocker: MockerFixture, final_state: dict[str, Any]) -> MagicMock:
    """Replace build_probe_subgraph with a factory returning a mock compiled graph."""
    compiled = MagicMock()
    compiled.invoke.return_value = final_state
    factory = mocker.patch(
        "harness_stata.nodes.data_probe.build_probe_subgraph",
        return_value=compiled,
    )
    return factory


def _run(state: WorkflowState) -> dict[str, Any]:
    return asyncio.run(data_probe(state))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_data_probe_all_found_passes_through_minimal_fields(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    _patch_csmar(mocker)
    _patch_settings(mocker)
    spec = make_empirical_spec()
    plan = make_model_plan()
    report = make_probe_report()
    manifest: DownloadManifest = {
        "items": [
            {
                "database": "CSMAR",
                "table": "FS_COMBAS",
                "key_fields": ["SYMBOL"],
                "variable_fields": ["A001000000"],
                "variable_names": ["SIZE"],
                "filters": {},
            }
        ]
    }
    final_state: dict[str, Any] = {
        "empirical_spec": spec,  # 同一引用 → 节点不回传
        "model_plan": plan,
        "probe_report": report,
        "download_manifest": manifest,
    }
    factory = _patch_subgraph(mocker, final_state)

    state: WorkflowState = {"empirical_spec": spec, "model_plan": plan}

    out = _run(state)

    # 验证 subgraph factory 以正确参数调用 (tools + prompt + budget)
    assert factory.call_count == 1
    kwargs = factory.call_args.kwargs
    assert len(kwargs["tools"]) == 1
    assert kwargs["per_variable_max_calls"] == 4
    assert "数据可得性探针" in kwargs["prompt"]
    # 验证节点返回的最小字段集
    assert set(out.keys()) == {"probe_report", "download_manifest"}
    assert out["probe_report"] is report
    assert out["download_manifest"] is manifest


# ---------------------------------------------------------------------------
# Soft substitute: empirical_spec 被子图重建 → 节点必须回传
# ---------------------------------------------------------------------------


def test_data_probe_soft_substitute_rewrites_spec(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    _patch_csmar(mocker)
    _patch_settings(mocker)
    spec = make_empirical_spec()
    plan = make_model_plan()
    # 模拟子图 soft 替代: 返回一个字段不同的新 spec (dict 不是同一引用)
    rewritten_spec: EmpiricalSpec = dict(spec)  # type: ignore[assignment]
    rewritten_spec["topic"] = "经过替代回写后的课题"
    report = make_probe_report(substituted=True)
    manifest: DownloadManifest = {"items": []}
    final_state: dict[str, Any] = {
        "empirical_spec": rewritten_spec,
        "model_plan": plan,
        "probe_report": report,
        "download_manifest": manifest,
    }
    _patch_subgraph(mocker, final_state)

    state: WorkflowState = {"empirical_spec": spec, "model_plan": plan}

    out = _run(state)

    assert "empirical_spec" in out
    assert out["empirical_spec"] is rewritten_spec
    assert out["empirical_spec"]["topic"] == "经过替代回写后的课题"
    assert out["probe_report"] is report


# ---------------------------------------------------------------------------
# Hard failure: workflow_status 透传,不抛异常
# ---------------------------------------------------------------------------


def test_data_probe_hard_failure_propagates_workflow_status(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    _patch_csmar(mocker)
    _patch_settings(mocker)
    spec = make_empirical_spec()
    plan = make_model_plan()
    report: ProbeReport = {
        "variable_results": [
            {
                "variable_name": "ROA",
                "status": "not_found",
                "source": None,
                "record_count": None,
                "substitution_trace": None,
            }
        ],
        "overall_status": "hard_failure",
        "failure_reason": "Hard contract variable 'ROA' not found in CSMAR",
    }
    manifest: DownloadManifest = {"items": []}
    final_state: dict[str, Any] = {
        "empirical_spec": spec,
        "model_plan": plan,
        "probe_report": report,
        "download_manifest": manifest,
        "workflow_status": "failed_hard_contract",
    }
    _patch_subgraph(mocker, final_state)

    state: WorkflowState = {"empirical_spec": spec, "model_plan": plan}

    out = _run(state)

    assert out["workflow_status"] == "failed_hard_contract"
    assert out["probe_report"]["overall_status"] == "hard_failure"
    # empirical_spec 未变更 → 不在回传 dict 中
    assert "empirical_spec" not in out


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_data_probe_missing_empirical_spec_raises(
    mocker: MockerFixture,
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    _patch_csmar(mocker)
    _patch_settings(mocker)
    factory = _patch_subgraph(mocker, {})

    state: WorkflowState = {"model_plan": make_model_plan()}

    with pytest.raises(ValueError, match="empirical_spec"):
        _run(state)
    # 不应触达 subgraph 构造
    assert factory.call_count == 0


def test_data_probe_missing_model_plan_raises(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
) -> None:
    _patch_csmar(mocker)
    _patch_settings(mocker)
    factory = _patch_subgraph(mocker, {})

    state: WorkflowState = {"empirical_spec": make_empirical_spec()}

    with pytest.raises(ValueError, match="model_plan"):
        _run(state)
    assert factory.call_count == 0


def test_data_probe_empty_variables_raises(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    _patch_csmar(mocker)
    _patch_settings(mocker)
    _patch_subgraph(mocker, {})

    spec = make_empirical_spec(variables=[])
    state: WorkflowState = {"empirical_spec": spec, "model_plan": make_model_plan()}

    with pytest.raises(ValueError, match="non-empty"):
        _run(state)


# ---------------------------------------------------------------------------
# Subgraph 异常透传 (确保 csmar context manager 正确退出)
# ---------------------------------------------------------------------------


def test_data_probe_subgraph_invoke_exception_propagates(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
) -> None:
    _patch_csmar(mocker)
    _patch_settings(mocker)
    compiled = MagicMock()
    compiled.invoke.side_effect = RuntimeError("upstream MCP crashed")
    mocker.patch(
        "harness_stata.nodes.data_probe.build_probe_subgraph",
        return_value=compiled,
    )

    state: WorkflowState = {
        "empirical_spec": make_empirical_spec(),
        "model_plan": make_model_plan(),
    }

    with pytest.raises(RuntimeError, match="upstream MCP crashed"):
        _run(state)
