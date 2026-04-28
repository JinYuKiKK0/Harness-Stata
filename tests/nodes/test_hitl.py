"""Unit tests for the HITL node (F17)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pytest_mock import MockerFixture

from harness_stata.nodes.hitl import (
    _format_plan,
    _format_sample_size,
    _SECTION_HEADERS,
    hitl,
)
from harness_stata.state import (
    EmpiricalSpec,
    ModelPlan,
    ProbeReport,
    WorkflowState,
)


# ---------------------------------------------------------------------------
# _format_plan / _format_sample_size — pure function coverage
# ---------------------------------------------------------------------------


def test_format_plan_full(
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    spec = make_empirical_spec()
    plan = make_model_plan()
    report = make_probe_report()

    text = _format_plan(spec, plan, report)

    assert "Hard" in text and "Soft" in text
    # all three variables appear as rows in the variables table
    for var in spec["variables"]:
        assert var["name"] in text
    # equation and hypothesis present
    assert plan["equation"] in text
    assert plan["core_hypothesis"]["rationale"] in text
    # core sections present
    assert _SECTION_HEADERS["variables"] in text
    assert _SECTION_HEADERS["hypothesis"] in text


def test_format_sample_size_all_counts(
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    report = make_probe_report()  # counts: 38000, 35000, 37000

    text = _format_sample_size(report)

    assert "预估 35000 ~ 38000 条" in text
    assert "基于 3 个变量探针记录数" in text


def test_format_sample_size_partial_counts(
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    report = make_probe_report()
    # blank out one count
    report["variable_results"][1]["record_count"] = None

    text = _format_sample_size(report)

    assert "预估 37000 ~ 38000 条" in text
    assert "基于 2 个变量探针记录数" in text


def test_format_sample_size_all_none(
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    report = make_probe_report(missing_counts=True)

    text = _format_sample_size(report)

    assert "无法根据探针估算" in text
    assert "record_count 缺失" in text


# ---------------------------------------------------------------------------
# hitl() node — decision handling via mocked interrupt
# ---------------------------------------------------------------------------


def _make_state(
    spec: EmpiricalSpec,
    plan: ModelPlan,
    report: ProbeReport,
) -> WorkflowState:
    return {"empirical_spec": spec, "model_plan": plan, "probe_report": report}


def test_hitl_approved_with_notes(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    fake_interrupt = mocker.patch(
        "harness_stata.nodes.hitl.interrupt",
        return_value={"approved": True, "user_notes": "looks good"},
    )
    state = _make_state(make_empirical_spec(), make_model_plan(), make_probe_report())

    out = hitl(state)

    assert out == {"hitl_decision": {"approved": True, "user_notes": "looks good"}}
    assert "workflow_status" not in out
    assert fake_interrupt.call_count == 1
    payload: dict[str, Any] = fake_interrupt.call_args.args[0]
    assert payload["type"] == "hitl_plan_review"
    assert payload["need"] == "approve_or_reject"
    assert "plan" in payload
    assert "error" not in payload


def test_hitl_approved_no_notes(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    mocker.patch(
        "harness_stata.nodes.hitl.interrupt",
        return_value={"approved": True, "user_notes": None},
    )
    state = _make_state(make_empirical_spec(), make_model_plan(), make_probe_report())

    out = hitl(state)

    assert out == {"hitl_decision": {"approved": True, "user_notes": None}}
    assert "workflow_status" not in out


def test_hitl_rejected_valid(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    mocker.patch(
        "harness_stata.nodes.hitl.interrupt",
        return_value={"approved": False, "user_notes": "样本过小, 请扩大到省级样本"},
    )
    state = _make_state(make_empirical_spec(), make_model_plan(), make_probe_report())

    out = hitl(state)

    assert out["hitl_decision"]["approved"] is False
    assert out["hitl_decision"]["user_notes"] == "样本过小, 请扩大到省级样本"
    assert out["workflow_status"] == "rejected"


def test_hitl_rejected_empty_notes_retries(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    fake_interrupt = mocker.patch(
        "harness_stata.nodes.hitl.interrupt",
        side_effect=[
            {"approved": False, "user_notes": ""},
            {"approved": False, "user_notes": "补充原因"},
        ],
    )
    state = _make_state(make_empirical_spec(), make_model_plan(), make_probe_report())

    out = hitl(state)

    assert fake_interrupt.call_count == 2
    second_payload: dict[str, Any] = fake_interrupt.call_args_list[1].args[0]
    assert "error" in second_payload
    assert "user_notes" in second_payload["error"]
    assert out["hitl_decision"]["approved"] is False
    assert out["hitl_decision"]["user_notes"] == "补充原因"
    assert out["workflow_status"] == "rejected"


def test_hitl_rejected_persistent_invalid_raises(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    fake_interrupt = mocker.patch(
        "harness_stata.nodes.hitl.interrupt",
        side_effect=[
            {"approved": False, "user_notes": ""},
            {"approved": False, "user_notes": "   "},
            {"approved": False, "user_notes": ""},
        ],
    )
    state = _make_state(make_empirical_spec(), make_model_plan(), make_probe_report())

    with pytest.raises(ValueError, match="HITL decision validation failed"):
        hitl(state)

    assert fake_interrupt.call_count == 3


def test_hitl_malformed_resume_raises(
    mocker: MockerFixture,
    make_empirical_spec: Callable[..., EmpiricalSpec],
    make_model_plan: Callable[..., ModelPlan],
    make_probe_report: Callable[..., ProbeReport],
) -> None:
    fake_interrupt = mocker.patch(
        "harness_stata.nodes.hitl.interrupt",
        side_effect=[
            "not_a_dict",
            {"user_notes": "missing approved key"},
            42,
        ],
    )
    state = _make_state(make_empirical_spec(), make_model_plan(), make_probe_report())

    with pytest.raises(ValueError, match="HITL decision validation failed"):
        hitl(state)

    assert fake_interrupt.call_count == 3
