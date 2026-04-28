"""CLI tests — pure typer / helper coverage only.

按项目测试约定:不 mock LLM/MCP。原本通过 stub 全部业务节点跑 CLI 全链路的
测试已删除(transitive 等同 mock LLM)。本文件仅覆盖 typer 参数解析和
两个纯 helper 函数。
"""

from __future__ import annotations

from typer.testing import CliRunner

from harness_stata.cli import app


_REQUIRED_ARGS = [
    "run",
    "--topic", "数字化转型对盈利能力的影响",
    "--x-variable", "DIGITAL",
    "--y-variable", "ROA",
    "--sample-scope", "A股上市公司",
    "--time-range-start", "2015",
    "--time-range-end", "2022",
    "--data-frequency", "yearly",
    "--thread-id", "test-thread",
]


def test_missing_required_arg() -> None:
    args = [a for a in _REQUIRED_ARGS if a != "--x-variable" and a != "DIGITAL"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "Missing option" in result.output or "--x-variable" in result.output


def test_invalid_data_frequency() -> None:
    args = list(_REQUIRED_ARGS)
    idx = args.index("--data-frequency")
    args[idx + 1] = "weekly"
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 2
    assert "weekly" in result.output or "data-frequency" in result.output.lower()


def test_interrupt_payload_extraction_handles_missing_key() -> None:
    from harness_stata.cli import _interrupt_payload

    assert _interrupt_payload({}) is None
    assert _interrupt_payload({"foo": 1}) is None

    class _Obj:
        value = {"type": "hitl_plan_review", "plan": "hi"}

    assert _interrupt_payload({"__interrupt__": [_Obj()]}) == {"type": "hitl_plan_review", "plan": "hi"}


def test_final_state_not_dumped_without_merged_dataset() -> None:
    from harness_stata.cli import _dump_final_state

    state = {"workflow_status": "failed_hard_contract"}
    assert _dump_final_state(state) is None
