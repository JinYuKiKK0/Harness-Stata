"""Unit tests for the probe-subgraph pure helpers (deterministic post-processing).

Coverage scope follows CLAUDE.md test conventions:

- 时间归一化与 probe_query 响应解码(纯逻辑,无 LLM)
- bulk_schema 响应解码与失败 table 提取
- (variable, candidate_table) 笛卡尔展开与分桶
- 多桶 verification 输出合并(任一 found,否则 not_found)
"""

from __future__ import annotations

from harness_stata.state import EmpiricalSpec, VariableDefinition
from harness_stata.subgraphs.probe.pure import (
    BucketKey,
    bucket_plans,
    build_probe_query_payload,
    ensure_manifest,
    finding_mapping_failure_reason,
    format_schema_for_prompt,
    merge_bucket_results,
    merge_into_manifest,
    normalize_time_bound,
    parse_bulk_schema_response,
    parse_probe_query_response,
)
from harness_stata.subgraphs.probe.schemas import (
    BucketVariableFinding,
    BucketVerificationOutput,
    VariablePlan,
    VariableProbeFindingModel,
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


def _spec(variables: list[VariableDefinition]) -> EmpiricalSpec:
    return EmpiricalSpec(
        topic="t",
        variables=variables,
        sample_scope="A股上市公司",
        time_range_start="2010",
        time_range_end="2020",
        data_frequency="yearly",
        analysis_granularity="公司-年度",
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
                        {"field_code": "Stkcd", "field_label": "证券代码"},
                        {"field_code": "ROA", "field_label": "总资产收益率"},
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
                    "fields": [{"field_code": "F1"}],
                    "error": None,
                },
            ],
            "cache_hits": 1,
            "live_calls": 2,
            "failures": 1,
        }
        result = parse_bulk_schema_response(raw)
        assert set(result.schema_dict.keys()) == {"T1", "T3"}
        assert [f["field_code"] for f in result.schema_dict["T1"]] == ["Stkcd", "ROA"]
        assert result.failed_table_codes == ["T2"]

    def test_non_dict_returns_empty(self) -> None:
        result = parse_bulk_schema_response("not a dict")
        assert result.schema_dict == {}
        assert result.failed_table_codes == []

    def test_skips_items_without_table_code(self) -> None:
        raw = {"items": [{"source": "live", "fields": [{"field_code": "F"}]}]}
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
            "T1": [{"field_code": "Stkcd"}, {"field_code": "ROA"}],
            "T2": [{"field_code": "Stkcd"}, {"field_code": "ROA"}],
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
        schema_dict = {"T1": [{"field_code": "Stkcd"}]}  # ROA 不在
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

    def test_derived_source_field_valid_counts_as_found(self) -> None:
        age = _var("Age", description="企业年龄")
        schema_dict = {
            "T1": [
                {"field_code": "Stkcd"},
                {"field_code": "AccYear"},
                {"field_code": "EstablishDate"},
            ]
        }
        bucket_outputs = [
            (
                BucketKey("DB", "T1"),
                BucketVerificationOutput(
                    results=[
                        BucketVariableFinding(
                            variable_name="Age",
                            status="found",
                            field="EstablishDate",
                            source_fields=["EstablishDate"],
                            match_kind="derived",
                            transform={"op": "firm_age", "date_field": "EstablishDate"},
                            key_fields=["Stkcd", "AccYear"],
                            evidence="企业年龄可由成立日期和样本年份构造",
                        )
                    ]
                ),
            )
        ]
        results = merge_bucket_results(bucket_outputs, [age], schema_dict)
        _, finding = results[0]
        assert finding.status == "found"
        assert finding.match_kind == "derived"
        assert finding.source_fields == ["EstablishDate"]
        assert finding.field == "EstablishDate"
        assert finding.transform == {"op": "firm_age", "date_field": "EstablishDate"}

    def test_invalid_source_field_drops_to_not_found(self) -> None:
        age = _var("Age", description="企业年龄")
        schema_dict = {"T1": [{"field_code": "Stkcd"}, {"field_code": "AccYear"}]}
        bucket_outputs = [
            (
                BucketKey("DB", "T1"),
                BucketVerificationOutput(
                    results=[
                        BucketVariableFinding(
                            variable_name="Age",
                            status="found",
                            field="EstablishDate",
                            source_fields=["EstablishDate"],
                            match_kind="derived",
                            transform={"op": "firm_age", "date_field": "EstablishDate"},
                            key_fields=["Stkcd", "AccYear"],
                        )
                    ]
                ),
            )
        ]
        results = merge_bucket_results(bucket_outputs, [age], schema_dict)
        assert results[0][1].status == "not_found"

    def test_invalid_key_field_drops_to_not_found(self) -> None:
        roa = _var("ROA")
        schema_dict = {"T1": [{"field_code": "Stkcd"}, {"field_code": "ROA"}]}
        bucket_outputs = [
            (
                BucketKey("DB", "T1"),
                BucketVerificationOutput(
                    results=[
                        BucketVariableFinding(
                            variable_name="ROA",
                            status="found",
                            field="ROA",
                            source_fields=["ROA"],
                            match_kind="direct_field",
                            transform={"op": "pass_through"},
                            key_fields=["FakeKey"],
                        )
                    ]
                ),
            )
        ]
        results = merge_bucket_results(bucket_outputs, [roa], schema_dict)
        assert results[0][1].status == "not_found"

    def test_transform_reference_outside_source_fields_drops_to_not_found(self) -> None:
        age = _var("Age", description="企业年龄")
        schema_dict = {
            "T1": [{"field_code": "Stkcd"}, {"field_code": "EstablishDate"}]
        }
        bucket_outputs = [
            (
                BucketKey("DB", "T1"),
                BucketVerificationOutput(
                    results=[
                        BucketVariableFinding(
                            variable_name="Age",
                            status="found",
                            field="EstablishDate",
                            source_fields=["EstablishDate"],
                            match_kind="derived",
                            transform={"op": "firm_age", "date_field": "FakeDate"},
                            key_fields=["Stkcd"],
                        )
                    ]
                ),
            )
        ]
        results = merge_bucket_results(bucket_outputs, [age], schema_dict)
        assert results[0][1].status == "not_found"


# ---------------------------------------------------------------------------
# manifest + probe payload
# ---------------------------------------------------------------------------


class TestManifestAndProbePayload:
    def test_manifest_merges_source_fields_and_variable_mappings(self) -> None:
        age = _var("Age", description="企业年龄")
        cashflow = _var("CashFlow", description="经营活动现金流净额与总资产之比")
        manifest = ensure_manifest(None)
        spec = _spec([age, cashflow])

        merge_into_manifest(
            manifest,
            age,
            VariableProbeFindingModel(
                status="found",
                database="CSMAR",
                table="T1",
                field="EstablishDate",
                source_fields=["EstablishDate"],
                match_kind="derived",
                transform={"op": "firm_age", "date_field": "EstablishDate"},
                key_fields=["Stkcd", "AccYear"],
                evidence="企业年龄可由成立日期构造",
            ),
            spec,
        )
        merge_into_manifest(
            manifest,
            cashflow,
            VariableProbeFindingModel(
                status="found",
                database="CSMAR",
                table="T1",
                field="CashRecoveryRate",
                source_fields=["CashRecoveryRate"],
                match_kind="semantic_equivalent",
                transform={"op": "pass_through"},
                key_fields=["Stkcd", "AccYear"],
                evidence="字段定义与经营现金流/总资产口径一致",
            ),
            spec,
        )

        assert len(manifest["items"]) == 1
        task = manifest["items"][0]
        assert task["variable_fields"] == ["EstablishDate", "CashRecoveryRate"]
        assert task["variable_names"] == ["Age", "CashFlow"]
        assert task["key_fields"] == ["Stkcd", "AccYear"]
        assert task["filters"] == {"start_date": "2010-01-01", "end_date": "2020-12-31"}
        assert task["variable_mappings"] == [
            {
                "variable_name": "Age",
                "source_fields": ["EstablishDate"],
                "match_kind": "derived",
                "transform": {"op": "firm_age", "date_field": "EstablishDate"},
                "evidence": "企业年龄可由成立日期构造",
            },
            {
                "variable_name": "CashFlow",
                "source_fields": ["CashRecoveryRate"],
                "match_kind": "semantic_equivalent",
                "transform": {"op": "pass_through"},
                "evidence": "字段定义与经营现金流/总资产口径一致",
            },
        ]

    def test_probe_payload_contains_key_and_source_fields(self) -> None:
        spec = _spec([_var("Age")])
        finding = VariableProbeFindingModel(
            status="found",
            database="CSMAR",
            table="T1",
            field="EstablishDate",
            source_fields=["EstablishDate"],
            match_kind="derived",
            transform={"op": "firm_age", "date_field": "EstablishDate"},
            key_fields=["Stkcd", "AccYear"],
        )

        payload = build_probe_query_payload(spec, finding)

        assert payload["table_code"] == "T1"
        assert payload["columns"] == ["Stkcd", "AccYear", "EstablishDate"]
        assert payload["start_date"] == "2010-01-01"
        assert payload["end_date"] == "2020-12-31"

    def test_finding_mapping_failure_reason_rejects_bad_transform_reference(self) -> None:
        finding = VariableProbeFindingModel(
            status="found",
            database="CSMAR",
            table="T1",
            field="EstablishDate",
            source_fields=["EstablishDate"],
            match_kind="derived",
            transform={"op": "firm_age", "date_field": "FakeDate"},
            key_fields=["Stkcd"],
        )

        reason = finding_mapping_failure_reason(finding)

        assert reason is not None
        assert "unusable transform" in reason


# ---------------------------------------------------------------------------
# format_schema_for_prompt
# ---------------------------------------------------------------------------


class TestFormatSchemaForPrompt:
    def test_renders_field_lines_with_optional_metadata(self) -> None:
        block = format_schema_for_prompt(
            "T1",
            [
                {"field_code": "Stkcd", "field_label": "证券代码", "field_key": "Code"},
                {"field_code": "Trddt", "field_label": "交易日期", "field_key": "Date"},
                {"field_code": "ROA", "field_label": None},
                {"field_code": ""},  # 空 field_code 应被跳过
            ],
        )
        # 标题 N 是渲染后行数,空 field_code 被跳过 → 3 行
        assert "### Table `T1` (3 fields)" in block
        assert "| code | label | key |" in block
        assert "| --- | --- | --- |" in block
        assert "| Stkcd | 证券代码 | Code |" in block
        assert "| Trddt | 交易日期 | Date |" in block
        # field_label / field_key 缺失时渲染为空 cell
        assert "| ROA |  |  |" in block
        # 数据行恰好 3 条(空 field_code 跳过);整块共 5 行 pipe 行 = header + sep + 3 数据
        assert block.count("\n| ") == 5
