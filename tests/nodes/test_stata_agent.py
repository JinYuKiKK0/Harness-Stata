"""Pure-logic tests for `_stata_agent` helpers — no LLM/MCP wiring.

覆盖:
- ``_unwrap_mcp_payload`` 五态形态归一(dict / str / list[ContentBlock] 等)
- ``_extract_artifacts`` 从 run.log 推 input.do 的成功/失败路径

不模拟 ``run_stata_agent`` 全链路,严格遵守"测试只允许纯代码"原则。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness_stata.nodes._stata_agent import _extract_artifacts, _unwrap_mcp_payload

# ---------------------------------------------------------------------------
# _unwrap_mcp_payload
# ---------------------------------------------------------------------------


def test_unwrap_dict_passthrough() -> None:
    """已结构化的 dict 原样返回。"""
    payload = {"ready": True, "summary": "ok"}
    assert _unwrap_mcp_payload(payload) is payload


def test_unwrap_str_json_decoded() -> None:
    """合法 JSON 字符串解码为 dict。"""
    raw = '{"ready": true, "value": 42}'
    assert _unwrap_mcp_payload(raw) == {"ready": True, "value": 42}


def test_unwrap_str_non_json_returned_raw() -> None:
    """非 JSON 字符串原样返回(不抛)。"""
    assert _unwrap_mcp_payload("not json") == "not json"


def test_unwrap_list_text_blocks_decoded_to_dict() -> None:
    """list[ContentBlock] 形态(adapter 0.2.x 默认返回值)抽 text 块拼接后解码。"""
    raw = [
        {"type": "text", "text": '{"status": "succeeded", "exit_code": 0}', "id": "lc_1"},
    ]
    assert _unwrap_mcp_payload(raw) == {"status": "succeeded", "exit_code": 0}


def test_unwrap_list_multiple_text_blocks_joined() -> None:
    """多 text 块按顺序拼接后 JSON 解码(MCP 协议合法形态)。"""
    raw = [
        {"type": "text", "text": '{"status":', "id": "lc_1"},
        {"type": "text", "text": ' "succeeded"}', "id": "lc_2"},
    ]
    assert _unwrap_mcp_payload(raw) == {"status": "succeeded"}


def test_unwrap_list_text_block_non_json_returns_joined_str() -> None:
    """text 块内容不是 JSON 时返回拼接后的字符串。"""
    raw = [{"type": "text", "text": "ready", "id": "lc_1"}]
    assert _unwrap_mcp_payload(raw) == "ready"


def test_unwrap_list_empty_returned_raw() -> None:
    """空 list 没有 text 块,原样返回。"""
    raw: list[object] = []
    assert _unwrap_mcp_payload(raw) is raw


def test_unwrap_list_without_text_blocks_returned_raw() -> None:
    """list 但无 type=text 的 block,原样返回(adapter 边界形态)。"""
    raw = [{"type": "image", "url": "...", "id": "lc_1"}]
    assert _unwrap_mcp_payload(raw) is raw


# ---------------------------------------------------------------------------
# _extract_artifacts
# ---------------------------------------------------------------------------


def _history_with_run_log(
    log_path: str, commands: str = "summarize ROA"
) -> list[dict[str, object]]:
    return [
        {
            "commands": commands,
            "execution_result": {
                "status": "succeeded",
                "artifacts": [log_path],
            },
        }
    ]


def test_extract_artifacts_derives_input_do_from_run_log_parent(tmp_path: Path) -> None:
    """run.log 与 input.do 同 job 目录共存时,从 run.log 父目录拼出 input.do。"""
    job_dir = tmp_path / "job_xxx"
    job_dir.mkdir()
    (job_dir / "input.do").write_text("summarize ROA", encoding="utf-8")
    log = job_dir / "run.log"
    log.write_text("...", encoding="utf-8")

    do_path, log_path = _extract_artifacts(_history_with_run_log(str(log)))
    assert Path(do_path) == job_dir / "input.do"
    assert Path(log_path) == log


def test_extract_artifacts_raises_when_run_log_missing_from_artifacts() -> None:
    """succeeded ExecutionResult.artifacts 不含 run.log 必须 raise。"""
    history: list[dict[str, object]] = [
        {
            "commands": "summarize ROA",
            "execution_result": {"status": "succeeded", "artifacts": ["other.txt"]},
        }
    ]
    with pytest.raises(RuntimeError, match="missing run.log artifact"):
        _extract_artifacts(history)


def test_extract_artifacts_raises_when_input_do_missing_beside_run_log(tmp_path: Path) -> None:
    """run.log 存在但 input.do 不在同目录(stata-executor 异常情况)必须 raise。"""
    job_dir = tmp_path / "job_yyy"
    job_dir.mkdir()
    log = job_dir / "run.log"
    log.write_text("...", encoding="utf-8")
    # 故意不写 input.do

    with pytest.raises(RuntimeError, match="input.do not found beside run.log"):
        _extract_artifacts(_history_with_run_log(str(log)))


def test_extract_artifacts_raises_when_no_succeeded_entry() -> None:
    """history 中无 succeeded 必须 raise(防止把 failed 结果当作终态)。"""
    history: list[dict[str, object]] = [
        {
            "commands": "summarize ROA",
            "execution_result": {"status": "failed", "artifacts": []},
        }
    ]
    with pytest.raises(RuntimeError, match="no succeeded ExecutionResult"):
        _extract_artifacts(history)


def test_extract_artifacts_picks_last_succeeded_when_multiple(tmp_path: Path) -> None:
    """多条 succeeded 时取最后一条(self-heal 后的最终成功)。"""
    job1 = tmp_path / "job_1"
    job2 = tmp_path / "job_2"
    for d in (job1, job2):
        d.mkdir()
        (d / "input.do").write_text("...", encoding="utf-8")
        (d / "run.log").write_text("...", encoding="utf-8")
    history: list[dict[str, object]] = [
        {
            "commands": "first",
            "execution_result": {"status": "failed", "artifacts": []},
        },
        {
            "commands": "second",
            "execution_result": {
                "status": "succeeded",
                "artifacts": [str(job1 / "run.log")],
            },
        },
        {
            "commands": "third",
            "execution_result": {
                "status": "succeeded",
                "artifacts": [str(job2 / "run.log")],
            },
        },
    ]
    do_path, log_path = _extract_artifacts(history)
    assert Path(do_path) == job2 / "input.do"
    assert Path(log_path) == job2 / "run.log"
