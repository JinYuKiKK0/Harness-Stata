"""Unit tests for the probe_subgraph factory — routing and state-machine logic."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import BaseTool, tool

from harness_stata.state import EmpiricalSpec, ModelPlan, VariableDefinition
from harness_stata.subgraphs._probe_helpers import (
    VariableProbeFindingModel,
    normalize_time_bound,
)
from harness_stata.subgraphs.probe_subgraph import build_probe_subgraph

# ---------------------------------------------------------------------------
# Fake react tool (no side effects) — Agent never calls it under create_agent mock
# ---------------------------------------------------------------------------


@tool
def csmar_search_field(keyword: str) -> str:
    """Return a canned discovery result (test double)."""
    return f"discover:{keyword}"


# ---------------------------------------------------------------------------
# Mock wiring helpers
# ---------------------------------------------------------------------------


def _wire_agent(
    mocker: Any,
    findings: list[VariableProbeFindingModel],
) -> MagicMock:
    """Patch ``create_agent``; each call consumes the next finding from the list."""

    def _make_agent(finding: VariableProbeFindingModel) -> MagicMock:
        agent = MagicMock()

        async def _ainvoke(state: dict[str, Any]) -> dict[str, Any]:
            return {"messages": [], "structured_response": finding}

        agent.ainvoke = AsyncMock(side_effect=_ainvoke)
        return agent

    return mocker.patch(
        "harness_stata.subgraphs.probe_subgraph.create_agent",
        side_effect=[_make_agent(f) for f in findings],
    )


def _ok_outcome(
    *,
    validation_id: str = "vid-1",
    row_count: int | None = 1234,
) -> dict[str, object]:
    return {
        "can_materialize": True,
        "validation_id": validation_id,
        "row_count": row_count,
        "invalid_columns": [],
    }


def _bad_outcome(
    *,
    invalid_columns: list[str] | None = None,
    row_count: int | None = 0,
) -> dict[str, object]:
    return {
        "can_materialize": False,
        "validation_id": None,
        "row_count": row_count,
        "invalid_columns": invalid_columns or [],
    }


def _make_probe_tool(
    *,
    side_effect: list[dict[str, object]] | None = None,
    return_value: dict[str, object] | None = None,
) -> BaseTool:
    """Build a probe_tool mock whose ``ainvoke`` returns canned dicts.

    Supply ``side_effect`` for per-call sequencing or ``return_value`` for a single
    canned response. The mock is typed as :class:`BaseTool` so the subgraph factory
    accepts it; the real subclass would be a langchain-mcp-adapters StructuredTool.
    """
    tool_mock: Any = MagicMock(spec=BaseTool)
    tool_mock.name = "csmar_probe_query"
    if side_effect is not None:
        tool_mock.ainvoke = AsyncMock(side_effect=list(side_effect))
    elif return_value is not None:
        tool_mock.ainvoke = AsyncMock(return_value=return_value)
    else:
        tool_mock.ainvoke = AsyncMock(return_value=_ok_outcome())
    return tool_mock


def _stub_probe_tool() -> BaseTool:
    """Probe tool stub for tests that should never reach coverage validation."""
    tool_mock: Any = MagicMock(spec=BaseTool)
    tool_mock.name = "csmar_probe_query"
    tool_mock.ainvoke = AsyncMock(side_effect=AssertionError("probe_tool unexpectedly called"))
    return tool_mock


def _var(name: str, role: str = "independent", contract: str = "hard") -> VariableDefinition:
    return VariableDefinition(
        name=name,
        description=f"desc of {name}",
        contract_type=contract,  # type: ignore[typeddict-item]
        role=role,  # type: ignore[typeddict-item]
    )


def _spec(variables: list[VariableDefinition]) -> EmpiricalSpec:
    return EmpiricalSpec(
        topic="t",
        variables=variables,
        sample_scope="s",
        time_range_start="2010",
        time_range_end="2020",
        data_frequency="yearly",
        analysis_granularity="firm-year",
    )


def _plan(variable_name: str = "ROE") -> ModelPlan:
    return {
        "model_type": "OLS",
        "equation": "Y = a + b*ROE + e",
        "core_hypothesis": {
            "variable_name": variable_name,
            "expected_sign": "+",
            "rationale": "r",
        },
        "data_structure_requirements": ["ROE must be available"],
    }


def _found(
    *,
    database: str = "CSMAR",
    table: str = "TRD",
    field: str = "ROA",
    record_count: int | None = None,
    key_fields: list[str] | None = None,
    filters: dict[str, str] | None = None,
) -> VariableProbeFindingModel:
    return VariableProbeFindingModel(
        status="found",
        database=database,
        table=table,
        field=field,
        record_count=record_count,
        key_fields=key_fields if key_fields is not None else ["stkcd", "year"],
        filters=filters if filters is not None else None,
    )


def _not_found(
    *,
    substitute_name: str | None = None,
    substitute_description: str | None = None,
    substitute_reason: str | None = None,
) -> VariableProbeFindingModel:
    return VariableProbeFindingModel(
        status="not_found",
        candidate_substitute_name=substitute_name,
        candidate_substitute_description=substitute_description,
        candidate_substitute_reason=substitute_reason,
    )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_empty_tools_rejected(self) -> None:
        with pytest.raises(ValueError, match="tools must not be empty"):
            build_probe_subgraph(
                tools=[],
                probe_tool=_stub_probe_tool(),
                prompt="p",
                per_variable_max_calls=1,
            )

    def test_non_positive_budget_rejected(self) -> None:
        with pytest.raises(ValueError, match="per_variable_max_calls must be >= 1"):
            build_probe_subgraph(
                tools=[csmar_search_field],
                probe_tool=_stub_probe_tool(),
                prompt="p",
                per_variable_max_calls=0,
            )


class TestTimeBounds:
    def test_normalize_year_month_date_and_quarter(self) -> None:
        assert normalize_time_bound("2010", is_start=True) == "2010-01-01"
        assert normalize_time_bound("2010", is_start=False) == "2010-12-31"
        assert normalize_time_bound("2010-02", is_start=True) == "2010-02-01"
        assert normalize_time_bound("2010-02", is_start=False) == "2010-02-28"
        assert normalize_time_bound("2012-02-03", is_start=True) == "2012-02-03"
        assert normalize_time_bound("2012Q4", is_start=True) == "2012-10-01"
        assert normalize_time_bound("2012Q4", is_start=False) == "2012-12-31"


# ---------------------------------------------------------------------------
# Empty queue: no LLM invocation, downstream slices initialized
# ---------------------------------------------------------------------------


class TestEmptyQueue:
    def test_empty_variables_list_skips_llm(self) -> None:
        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=_stub_probe_tool(),
            prompt="sys",
            per_variable_max_calls=3,
        )
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([])}))

        assert result["queue_initialized"] is True
        assert result["discovery_queue"] == []
        assert result["current_variable"] is None
        assert result["probe_report"] == {
            "variable_results": [],
            "overall_status": "success",
            "failure_reason": None,
        }
        assert result["download_manifest"] == {"items": []}


# ---------------------------------------------------------------------------
# Phase-1 (field existence) routing
# ---------------------------------------------------------------------------


class TestFoundSingleVariable:
    def test_found_writes_manifest_and_report(self, mocker: Any) -> None:
        finding = _found(database="CSMAR", table="TRD", field="ROA")
        _wire_agent(mocker, findings=[finding])
        probe_tool = _make_probe_tool(return_value=_ok_outcome(row_count=12345))

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=probe_tool,
            prompt="p",
            per_variable_max_calls=3,
        )
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([_var("ROA")])}))

        report = result["probe_report"]
        assert report["overall_status"] == "success"
        assert report["failure_reason"] is None
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROA"
        assert vr["status"] == "found"
        assert vr["source"] == {"database": "CSMAR", "table": "TRD", "field": "ROA"}
        assert vr["record_count"] == 12345  # 来自 probe_tool outcome,不是 finding
        assert vr["substitution_trace"] is None

        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["database"] == "CSMAR"
        assert items[0]["table"] == "TRD"
        assert items[0]["variable_fields"] == ["ROA"]
        assert items[0]["variable_names"] == ["ROA"]
        assert items[0]["key_fields"] == ["stkcd", "year"]
        assert items[0]["filters"] == {
            "start_date": "2010-01-01",
            "end_date": "2020-12-31",
        }

        # probe_tool 应该被调一次 (一个变量一条 PendingValidation)
        probe_tool.ainvoke.assert_awaited_once()  # type: ignore[attr-defined]


class TestHardFieldNotFound:
    def test_hard_not_found_routes_end_with_workflow_status(self, mocker: Any) -> None:
        # 字段未找到 → 路由直接终止,probe_tool 永远不该被调
        _wire_agent(mocker, findings=[_not_found()])

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=_stub_probe_tool(),
            prompt="p",
            per_variable_max_calls=3,
        )
        result = asyncio.run(
            graph.ainvoke({"empirical_spec": _spec([_var("ROA", contract="hard"), _var("LEV")])})
        )

        report = result["probe_report"]
        assert report["overall_status"] == "hard_failure"
        assert report["failure_reason"] is not None
        assert "ROA" in report["failure_reason"]
        assert result["workflow_status"] == "failed_hard_contract"
        # 路由直接 END:第二个变量 LEV 未被处理
        assert len(report["variable_results"]) == 1
        assert report["variable_results"][0]["variable_name"] == "ROA"
        assert report["variable_results"][0]["status"] == "not_found"
        assert result["download_manifest"]["items"] == []


class TestSoftSubstituteSuccess:
    def test_soft_substitute_writes_back_spec_and_manifest(self, mocker: Any) -> None:
        finding1 = _not_found(
            substitute_name="ROA",
            substitute_description="ROA proxies ROE",
            substitute_reason="similar economic meaning",
        )
        finding2 = _found(database="CSMAR", table="TRD", field="ROA")
        _wire_agent(mocker, findings=[finding1, finding2])
        probe_tool = _make_probe_tool(return_value=_ok_outcome(row_count=999))

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=probe_tool,
            prompt="p",
            per_variable_max_calls=3,
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe]), "model_plan": _plan()}))

        report = result["probe_report"]
        assert report["overall_status"] == "success"
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "substituted"
        assert vr["source"] == {"database": "CSMAR", "table": "TRD", "field": "ROA"}
        assert vr["record_count"] == 999  # 来自 probe_tool
        trace = vr["substitution_trace"]
        assert trace["original"] == "ROE"
        assert trace["substitute"] == "ROA"
        assert trace["reason"] == "similar economic meaning"

        # EmpiricalSpec 写回
        spec_vars = [v["name"] for v in result["empirical_spec"]["variables"]]
        assert "ROA" in spec_vars
        assert "ROE" not in spec_vars
        assert result["model_plan"]["equation"] == "Y = a + b*ROA + e"
        assert result["model_plan"]["core_hypothesis"]["variable_name"] == "ROA"
        assert result["model_plan"]["data_structure_requirements"] == ["ROA must be available"]

        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["variable_names"] == ["ROA"]


class TestSoftSubstituteFailure:
    def test_substitute_chain_terminates_after_one_attempt(self, mocker: Any) -> None:
        # 主任务给出 substitute → 替代任务再 not_found → 链终止
        finding1 = _not_found(
            substitute_name="ROA", substitute_description="d", substitute_reason="r"
        )
        finding2 = _not_found()
        mock_create = _wire_agent(mocker, findings=[finding1, finding2])

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=_stub_probe_tool(),  # 字段未找到 → probe_tool 永远不调
            prompt="p",
            per_variable_max_calls=3,
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe])}))

        report = result["probe_report"]
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "not_found"
        assert report["overall_status"] == "success"
        assert "workflow_status" not in result
        assert mock_create.call_count == 2
        assert result["download_manifest"]["items"] == []


class TestMultiVariableSameTable:
    def test_two_variables_same_table_merge_into_one_task(self, mocker: Any) -> None:
        finding1 = _found(database="CSMAR", table="TRD", field="ROA")
        finding2 = _found(database="CSMAR", table="TRD", field="LEV")
        _wire_agent(mocker, findings=[finding1, finding2])
        # 两条 PendingValidation,probe_tool 调两次,均通过
        probe_tool = _make_probe_tool(side_effect=[_ok_outcome(), _ok_outcome()])

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=probe_tool,
            prompt="p",
            per_variable_max_calls=3,
        )
        result = asyncio.run(
            graph.ainvoke({"empirical_spec": _spec([_var("ROA"), _var("LEV", role="control")])})
        )

        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["database"] == "CSMAR"
        assert items[0]["table"] == "TRD"
        assert sorted(items[0]["variable_fields"]) == ["LEV", "ROA"]
        assert sorted(items[0]["variable_names"]) == ["LEV", "ROA"]
        assert items[0]["key_fields"] == ["stkcd", "year"]


# ---------------------------------------------------------------------------
# Phase-2 (coverage validation) routing
# ---------------------------------------------------------------------------


class TestHardCoverageFailed:
    def test_hard_coverage_failed_routes_end_with_workflow_status(self, mocker: Any) -> None:
        # 字段发现是逐变量、批量结束后再统一覆盖率验证。所以两个变量都会被 Agent 处理,
        # 在 coverage_validation_handler 里命中第一个 hard 覆盖率失败时早返。
        finding1 = _found(database="CSMAR", table="TRD", field="ROA")
        finding2 = _found(database="CSMAR", table="OTHER", field="LEV")
        _wire_agent(mocker, findings=[finding1, finding2])
        # validation_queue 顺序 = spec.variables 顺序: ROA 先, LEV 后
        probe_tool = _make_probe_tool(
            side_effect=[_bad_outcome(invalid_columns=["ROA"]), _ok_outcome()]
        )

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=probe_tool,
            prompt="p",
            per_variable_max_calls=3,
        )
        result = asyncio.run(
            graph.ainvoke(
                {"empirical_spec": _spec([_var("ROA", contract="hard"), _var("LEV")])}
            )
        )

        report = result["probe_report"]
        assert report["overall_status"] == "hard_failure"
        assert report["failure_reason"] is not None
        assert "ROA" in report["failure_reason"]
        assert "coverage check failed" in report["failure_reason"]
        assert result["workflow_status"] == "failed_hard_contract"
        assert report["variable_results"][-1]["variable_name"] == "ROA"
        assert report["variable_results"][-1]["status"] == "not_found"
        # 早返之后 LEV 的 outcome 不会被吸收进 manifest / report
        assert all(
            vr["variable_name"] != "LEV" for vr in report["variable_results"]
        )
        assert result["download_manifest"]["items"] == []


class TestSoftCoverageFailedTriggersSubstitute:
    def test_soft_coverage_failed_enqueues_substitute(self, mocker: Any) -> None:
        # 主任务 finding="found" 但携带 substitute 候选(罕见但 schema 允许);
        # probe_query 拒绝 → 等同 not_found → 走 substitute 路径。
        finding1 = VariableProbeFindingModel(
            status="found",
            database="CSMAR",
            table="TRD",
            field="ROE",
            key_fields=["stkcd", "year"],
            candidate_substitute_name="ROA",
            candidate_substitute_description="ROA proxies ROE",
            candidate_substitute_reason="similar economic meaning",
        )
        # 替代变量探测仍走 Agent 一轮 → 字段找到 → coverage 通过
        finding2 = _found(database="CSMAR", table="TRD", field="ROA")
        _wire_agent(mocker, findings=[finding1, finding2])
        probe_tool = _make_probe_tool(
            side_effect=[_bad_outcome(invalid_columns=["ROE"]), _ok_outcome()]
        )

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=probe_tool,
            prompt="p",
            per_variable_max_calls=3,
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe])}))

        report = result["probe_report"]
        assert report["overall_status"] == "success"
        assert "workflow_status" not in result
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "substituted"
        assert vr["source"]["field"] == "ROA"
        items = result["download_manifest"]["items"]
        assert len(items) == 1
        assert items[0]["variable_names"] == ["ROA"]


class TestSoftCoverageFailedNoSubstitute:
    def test_soft_coverage_failed_without_substitute_records_not_found(
        self, mocker: Any
    ) -> None:
        # 生产中最常见的 soft 失败路径: Agent status="found" + 无 substitute 字段
        # (因 prompt 约定只有 not_found 时才填 substitute), coverage 拒绝
        # → 直接 not_found, 不再触发 substitute 重试
        finding = _found(database="CSMAR", table="TRD", field="ROE")
        # 显式置空 substitute 字段(_found 默认就是 None,这里强调语义)
        finding.candidate_substitute_name = None
        finding.candidate_substitute_description = None
        mock_create = _wire_agent(mocker, findings=[finding])
        probe_tool = _make_probe_tool(return_value=_bad_outcome(invalid_columns=["ROE"]))

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=probe_tool,
            prompt="p",
            per_variable_max_calls=3,
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe])}))

        report = result["probe_report"]
        assert report["overall_status"] == "success"
        assert "workflow_status" not in result
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"
        assert vr["status"] == "not_found"
        assert vr["substitution_trace"] is None
        assert result["download_manifest"]["items"] == []
        # Agent 只被调一次(没有 substitute 重试)
        assert mock_create.call_count == 1
        # probe_tool 调一次(只有 ROE 进了 validation_queue)
        probe_tool.ainvoke.assert_awaited_once()  # type: ignore[attr-defined]


class TestSubstituteCoverageFailure:
    def test_substitute_coverage_failed_terminates_as_not_found(self, mocker: Any) -> None:
        # 主任务 not_found + 给出替代候选 → 替代任务字段找到 → 但 coverage 拒绝
        # → 链终止,主变量记 not_found
        finding1 = _not_found(
            substitute_name="ROA", substitute_description="d", substitute_reason="r"
        )
        finding2 = _found(database="CSMAR", table="TRD", field="ROA")
        _wire_agent(mocker, findings=[finding1, finding2])
        probe_tool = _make_probe_tool(return_value=_bad_outcome(invalid_columns=["ROA"]))

        graph = build_probe_subgraph(
            tools=[csmar_search_field],
            probe_tool=probe_tool,
            prompt="p",
            per_variable_max_calls=3,
        )
        roe = _var("ROE", role="control", contract="soft")
        result = asyncio.run(graph.ainvoke({"empirical_spec": _spec([roe])}))

        report = result["probe_report"]
        assert report["overall_status"] == "success"
        assert "workflow_status" not in result
        assert len(report["variable_results"]) == 1
        vr = report["variable_results"][0]
        assert vr["variable_name"] == "ROE"  # 记原变量名
        assert vr["status"] == "not_found"
        assert vr["substitution_trace"] is None
        assert result["download_manifest"]["items"] == []


# ---------------------------------------------------------------------------
# Coverage validator helper coverage (via parse_probe_query_response)
# ---------------------------------------------------------------------------


class TestCoverageOutcomeParsing:
    def test_non_dict_response_marks_failure(self) -> None:
        from harness_stata.subgraphs._probe_helpers import parse_probe_query_response

        outcome = parse_probe_query_response("not a dict", "ctx")
        assert outcome["can_materialize"] is False
        assert outcome["validation_id"] is None
        assert outcome["failure_reason"] is not None
        assert "expected dict" in outcome["failure_reason"]

    def test_missing_validation_id_marks_failure(self) -> None:
        from harness_stata.subgraphs._probe_helpers import parse_probe_query_response

        outcome = parse_probe_query_response(
            {"can_materialize": True, "row_count": 10}, "ctx"
        )
        assert outcome["can_materialize"] is False
        assert outcome["failure_reason"] is not None
        assert "validation_id" in outcome["failure_reason"]

    def test_passing_response_extracts_fields(self) -> None:
        from harness_stata.subgraphs._probe_helpers import parse_probe_query_response

        outcome = parse_probe_query_response(
            {
                "can_materialize": True,
                "validation_id": "abc",
                "row_count": 42,
                "invalid_columns": [],
            },
            "ctx",
        )
        assert outcome["can_materialize"] is True
        assert outcome["validation_id"] == "abc"
        assert outcome["row_count"] == 42
        assert outcome["invalid_columns"] == []
        assert outcome["failure_reason"] is None
