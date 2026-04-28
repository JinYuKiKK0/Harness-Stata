"""Unit tests for the probe-subgraph pure helpers (deterministic post-processing).

Coverage scope follows CLAUDE.md test conventions:

- 时间归一化与 probe_query 响应解码(纯逻辑,无 LLM)
- bulk_schema 响应解码与失败 table 提取
- (variable, candidate_table) 笛卡尔展开与分桶
- 多桶 verification 输出合并(任一 found,否则 not_found)
"""

from __future__ import annotations

from harness_stata.state import VariableDefinition
from harness_stata.subgraphs._probe_helpers import (
    normalize_time_bound,
    parse_probe_query_response,
)
from harness_stata.subgraphs._probe_pipeline import (
    BucketKey,
    BucketVariableFinding,
    BucketVerificationOutput,
    VariablePlan,
    bucket_plans,
    format_schema_for_prompt,
    merge_bucket_results,
    parse_bulk_schema_response,
)


def _var(
    name: str,
    *,
    contract: str = "hard",
    role: str = "independent",
    description: str | None = None,
) -> VariableDefinition:
    return VariableDefinition(
        name=name,
        description=description or f"desc of {name}",
        contract_type=contract,  # type: ignore[typeddict-item]
        role=role,  # type: ignore[typeddict-item]
    )


# ---------------------------------------------------------------------------
# Time bound normalization
# ---------------------------------------------------------------------------


class TestTimeBounds:
    def test_year_month_date_quarter_round_trip(self) -> None:
        assert normalize_time_bound("2010", is_start=True) == "2010-01-01"
        assert normalize_time_bound("2010", is_start=False) == "2010-12-31"
        assert normalize_time_bound("2010-02", is_start=True) == "2010-02-01"
        assert normalize_time_bound("2010-02", is_start=False) == "2010-02-28"
        assert normalize_time_bound("2012-02-03", is_start=True) == "2012-02-03"
        assert normalize_time_bound("2012Q4", is_start=True) == "2012-10-01"
        assert normalize_time_bound("2012Q4", is_start=False) == "2012-12-31"


# ---------------------------------------------------------------------------
# probe_query response parsing
# ---------------------------------------------------------------------------


class TestProbeQueryResponseParsing:
    def test_non_dict_marks_failure(self) -> None:
        outcome = parse_probe_query_response("not a dict", "ctx")
        assert outcome["can_materialize"] is False
        assert outcome["validation_id"] is None
        assert outcome["failure_reason"] is not None
        assert "expected dict" in outcome["failure_reason"]

    def test_missing_validation_id_marks_failure(self) -> None:
        outcome = parse_probe_query_response(
            {"can_materialize": True, "row_count": 10}, "ctx"
        )
        assert outcome["can_materialize"] is False
        assert outcome["failure_reason"] is not None
        assert "validation_id" in outcome["failure_reason"]

    def test_passing_response_extracts_fields(self) -> None:
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

    def test_invalid_columns_blocks_materialization(self) -> None:
        outcome = parse_probe_query_response(
            {
                "can_materialize": False,
                "validation_id": "x",
                "row_count": 0,
                "invalid_columns": ["FOO", "BAR"],
            },
            "ctx",
        )
        assert outcome["can_materialize"] is False
        assert outcome["invalid_columns"] == ["FOO", "BAR"]


# ---------------------------------------------------------------------------
# parse_bulk_schema_response
# ---------------------------------------------------------------------------


class TestParseBulkSchemaResponse:
    def test_extracts_schema_dict_and_failures(self) -> None:
        raw = {
            "items": [
                {
                    "table_code": "T1",
                    "source": "live",
                    "fields": [
                        {"field_name": "Stkcd", "field_label": "证券代码"},
                        {"field_name": "ROA", "field_label": "总资产收益率"},
                    ],
                    "error": None,
                },
                {
                    "table_code": "T2",
                    "source": "live",
                    "fields": None,
                    "error": {"code": "not_found", "message": "missing", "hint": "x"},
                },
                {
                    "table_code": "T3",
                    "source": "cache",
                    "fields": [{"field_name": "F1"}],
                    "error": None,
                },
            ],
            "cache_hits": 1,
            "live_calls": 2,
            "failures": 1,
        }
        result = parse_bulk_schema_response(raw)
        assert set(result.schema_dict.keys()) == {"T1", "T3"}
        assert [f["field_name"] for f in result.schema_dict["T1"]] == ["Stkcd", "ROA"]
        assert result.failed_table_codes == ["T2"]

    def test_non_dict_returns_empty(self) -> None:
        result = parse_bulk_schema_response("not a dict")
        assert result.schema_dict == {}
        assert result.failed_table_codes == []

    def test_skips_items_without_table_code(self) -> None:
        raw = {"items": [{"source": "live", "fields": [{"field_name": "F"}]}]}
        result = parse_bulk_schema_response(raw)
        assert result.schema_dict == {}


# ---------------------------------------------------------------------------
# bucket_plans
# ---------------------------------------------------------------------------


class TestBucketPlans:
    def test_cartesian_explode_and_skip_missing_schema(self) -> None:
        roa = _var("ROA")
        lev = _var("LEV")
        plans = [
            VariablePlan(
                variable_name="ROA",
                target_database="TRD",
                candidate_table_codes=["T1", "T2"],
            ),
            VariablePlan(
                variable_name="LEV",
                target_database="TRD",
                candidate_table_codes=["T2", "T_MISSING"],
            ),
        ]
        schema_dict = {"T1": [], "T2": []}  # T_MISSING 不存在
        buckets, unplanned = bucket_plans(
            plans, {"ROA": roa, "LEV": lev}, schema_dict
        )
        assert unplanned == []
        assert set(buckets.keys()) == {
            BucketKey("TRD", "T1"),
            BucketKey("TRD", "T2"),
        }
        assert [v["name"] for v in buckets[BucketKey("TRD", "T1")]] == ["ROA"]
        assert [v["name"] for v in buckets[BucketKey("TRD", "T2")]] == ["ROA", "LEV"]

    def test_no_candidate_or_all_missing_marks_unplanned(self) -> None:
        roa = _var("ROA")
        plans = [
            VariablePlan(
                variable_name="ROA",
                target_database="TRD",
                candidate_table_codes=["T_MISSING"],
            )
        ]
        buckets, unplanned = bucket_plans(plans, {"ROA": roa}, {})
        assert buckets == {}
        assert unplanned == [roa]

    def test_unknown_variable_in_plans_is_ignored(self) -> None:
        roa = _var("ROA")
        plans = [
            VariablePlan(
                variable_name="UNKNOWN",
                target_database="TRD",
                candidate_table_codes=["T1"],
            )
        ]
        buckets, unplanned = bucket_plans(plans, {"ROA": roa}, {"T1": []})
        assert buckets == {}
        assert unplanned == []  # ROA 由调用方在外部检查 plans 覆盖率


# ---------------------------------------------------------------------------
# merge_bucket_results
# ---------------------------------------------------------------------------


class TestMergeBucketResults:
    def test_first_valid_found_wins(self) -> None:
        roa = _var("ROA")
        schema_dict = {
            "T1": [{"field_name": "ROA"}],
            "T2": [{"field_name": "ROA"}],
        }
        bucket_outputs = [
            (
                BucketKey("DB", "T1"),
                BucketVerificationOutput(
                    results=[
                        BucketVariableFinding(
                            variable_name="ROA",
                            status="found",
                            field="ROA",
                            key_fields=["Stkcd"],
                        )
                    ]
                ),
            ),
            (
                BucketKey("DB", "T2"),
                BucketVerificationOutput(
                    results=[
                        BucketVariableFinding(
                            variable_name="ROA",
                            status="found",
                            field="ROA",
                            key_fields=["Stkcd"],
                        )
                    ]
                ),
            ),
        ]
        results = merge_bucket_results(bucket_outputs, [roa], schema_dict)
        assert len(results) == 1
        var, finding = results[0]
        assert var["name"] == "ROA"
        assert finding.status == "found"
        assert finding.table == "T1"  # 第一个有效命中

    def test_field_not_in_schema_drops_to_not_found(self) -> None:
        roa = _var("ROA")
        schema_dict = {"T1": [{"field_name": "Stkcd"}]}  # ROA 不在
        bucket_outputs = [
            (
                BucketKey("DB", "T1"),
                BucketVerificationOutput(
                    results=[
                        BucketVariableFinding(
                            variable_name="ROA",
                            status="found",
                            field="ROA_FAKE",
                        )
                    ]
                ),
            )
        ]
        results = merge_bucket_results(bucket_outputs, [roa], schema_dict)
        assert results[0][1].status == "not_found"

# ---------------------------------------------------------------------------
# format_schema_for_prompt
# ---------------------------------------------------------------------------


class TestFormatSchemaForPrompt:
    def test_renders_field_lines_with_optional_metadata(self) -> None:
        block = format_schema_for_prompt(
            "T1",
            [
                {"field_name": "Stkcd", "field_label": "证券代码", "data_type": "varchar"},
                {"field_name": "ROA", "field_label": None, "data_type": None},
                {"field_name": ""},  # 空 field_name 应被跳过
            ],
        )
        assert "Table `T1` (3 fields)" in block
        assert "- `Stkcd` — 证券代码 | type=varchar" in block
        assert "- `ROA`" in block
        # 空 field_name 那行被跳过
        assert block.count("- `") == 2
