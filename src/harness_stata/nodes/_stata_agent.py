"""Stata ReAct 公共 helper —— 供 descriptive_stats / regression 两节点共享。

节点视角下,本 helper 把"拉起 stata MCP client → doctor 自检 → 包装 run_inline →
驱动 create_agent 单轮 ReAct → 取 do/log artifacts → 失败 dump 现场"封装成一个
异步函数。差异点(prompt/输出 schema/iter cap/post-check)由调用方注入。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, ToolException, tool
from pydantic import BaseModel

from harness_stata.clients.stata import get_stata_tools
from harness_stata.config import get_settings
from harness_stata.nodes._agent_runner import AgentRunFailure, run_structured_agent

_LOGGER = logging.getLogger(__name__)

NodeName = Literal["descriptive_stats", "regression"]

_TIMEOUT_SEC = 120
# stata-executor 落 jobs 在 `<wd>/.stata-executor/jobs/job_<ts>_<hash>/`,单层目录;
# 单星 glob 与 stata-executor::iter_artifact_matches 的 working_dir.glob 形态一一对应,
# 不要写 `**`。
_ARTIFACT_GLOBS = (
    ".stata-executor/jobs/*/run.log",
    ".stata-executor/jobs/*/input.do",
)

_BOOTSTRAP_ERROR_KINDS = frozenset({"bootstrap_error"})


def _resolve_workspace(node_name: NodeName) -> Path:
    """`<workspaces_root>/<node_name>/<run_id>/` —— 与 stata-executor job_id 同款公式。"""
    settings = get_settings()
    run_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    workspace = settings.workspaces_root / node_name / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _find_tool(tools: list[BaseTool], name: str) -> BaseTool:
    for t in tools:
        if t.name == name:
            return t
    msg = f"_stata_agent: required tool {name!r} not found in stata MCP tools"
    raise RuntimeError(msg)


def _unwrap_mcp_payload(raw: Any) -> Any:
    """langchain-mcp-adapters 0.2.x 把 CallToolResult.content 转成 LC 内容块列表
    ``[{"type":"text","text":"<json>","id":...}, ...]``;``structuredContent`` 走
    artifact 通道,不通过默认 ``ainvoke`` 返回。本 helper 把这些形态归一为原生 Python:
    dict 原样返回 / str 尝试 JSON 反序列化 / list 抽 text 块拼接后 JSON 反序列化。
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if isinstance(raw, list):
        texts = [
            item["text"]
            for item in raw
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if texts:
            joined = "".join(texts)
            try:
                return json.loads(joined)
            except json.JSONDecodeError:
                return joined
    return raw


async def _doctor_precondition(tools: list[BaseTool]) -> None:
    """前置自检：``ready=False`` 直接 raise,把诊断信息抛给上层。"""
    doctor_tool = _find_tool(tools, "doctor")
    raw = await doctor_tool.ainvoke({})
    payload = _unwrap_mcp_payload(raw)
    if not isinstance(payload, dict):
        msg = f"doctor: unexpected payload type {type(payload).__name__}"
        raise RuntimeError(msg)
    if not payload.get("ready", False):
        errors = payload.get("errors") or []
        msg = (
            f"doctor: stata-executor not ready (errors={errors!r})."
            f" 请在 .env 中确认 STATA_EXECUTOR_STATA_EXECUTABLE 与 STATA_EXECUTOR_EDITION."
        )
        raise RuntimeError(msg)


def _make_run_inline_wrapped(
    orig: BaseTool,
    working_dir: Path,
    history: list[dict[str, Any]],
) -> BaseTool:
    """把 run_inline 包成只暴露 ``commands`` 参数的 BaseTool,其余预填。

    工具体每次执行后将 ExecutionResult dict append 到 history 闭包,供节点终止后
    从中定位 do/log artifacts,绕开"从 ToolMessage 文本反 parse JSON"的脆弱链路。
    """
    working_dir_str = str(working_dir)
    artifact_globs = list(_ARTIFACT_GLOBS)

    @tool
    async def run_inline(commands: str) -> str:
        """在共享的 Stata 工作目录上执行一段内联 do 代码,返回 ExecutionResult JSON。

        返回字段含 ``status`` (succeeded/failed)、``exit_code``、``result_text``
        (精洗后的 Stata 输出摘要)、``diagnostic_excerpt`` (失败时的关键诊断片段)、
        ``error_kind``、``artifacts`` 等。

        策略:
        - ``status="succeeded"`` 且 ``result_text`` 已覆盖目标决策点 → 调用结构化
          输出工具上报终止。
        - ``status="failed"`` → 依据 ``error_kind`` 与 ``diagnostic_excerpt`` 修订
          commands 重试。
        - ``error_kind`` 为 ``bootstrap_error`` 或 ``env_error`` → 基础设施层故障,
          **不要尝试修复 do 代码**,立即调用结构化输出工具上报现场。
        """
        try:
            raw: Any = await orig.ainvoke(
                {
                    "commands": commands,
                    "working_dir": working_dir_str,
                    "artifact_globs": artifact_globs,
                    "timeout_sec": _TIMEOUT_SEC,
                }
            )
        except ToolException as exc:
            # langchain-mcp-adapters 在 CallToolResult.isError=True 时直接 raise
            # ToolException(error_msg);stata-executor 失败时 error_msg 即完整
            # ExecutionResult JSON 字符串。把它还原成 raw,让 LLM 看到失败结果并修 do。
            raw = str(exc)
        parsed = _unwrap_mcp_payload(raw)
        if isinstance(parsed, dict):
            history.append({"commands": commands, "execution_result": parsed})
            return json.dumps(parsed, ensure_ascii=False)
        if isinstance(parsed, str):
            return parsed
        return json.dumps(parsed, ensure_ascii=False)

    return run_inline


def _last_succeeded_entry(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    for entry in reversed(history):
        result = entry.get("execution_result")
        if isinstance(result, dict) and result.get("status") == "succeeded":
            return entry
    return None


def _extract_artifacts(history: list[dict[str, Any]]) -> tuple[str, str]:
    """从 history 末尾取最后一次 succeeded ExecutionResult 的 input.do 与 run.log 绝对路径。

    stata-executor 的 ``collect_artifacts`` 做差分采集且 ``snapshot_artifacts`` 在
    ``stage_inline_input`` (写 input.do) 之后才执行,导致 input.do 被判为未变更而不
    出现在 ExecutionResult.artifacts。这里以 run.log 为锚点,从其父 job 目录直接
    拼出 input.do(两文件由 stata-executor 保证同目录共存)。
    """
    entry = _last_succeeded_entry(history)
    if entry is None:
        msg = "_stata_agent: no succeeded ExecutionResult in history"
        raise RuntimeError(msg)
    result = cast(dict[str, Any], entry["execution_result"])
    artifacts_raw = result.get("artifacts") or []
    log_path: str | None = None
    for a in artifacts_raw:
        if isinstance(a, str) and Path(a).name == "run.log":
            log_path = a
            break
    if log_path is None:
        msg = (
            f"_stata_agent: succeeded ExecutionResult missing run.log artifact"
            f" (artifacts={artifacts_raw!r}); 检查 artifact_globs 配置"
        )
        raise RuntimeError(msg)
    do_candidate = Path(log_path).parent / "input.do"
    if not do_candidate.is_file():
        msg = (
            f"_stata_agent: input.do not found beside run.log"
            f" (expected at {do_candidate!s}); 检查 stata-executor job 目录布局"
        )
        raise RuntimeError(msg)
    return str(do_candidate), log_path


def _dump_failure(
    workspace: Path,
    history: list[dict[str, Any]],
    messages: list[BaseMessage],
    reason: str,
) -> Path:
    """统一 dump 失败现场到 ``<workspace>/_failure/dump.txt``。"""
    failure_dir = workspace / "_failure"
    failure_dir.mkdir(parents=True, exist_ok=True)
    dump_path = failure_dir / "dump.txt"
    last_entry = history[-1] if history else None
    last_commands = last_entry.get("commands") if isinstance(last_entry, dict) else None
    last_result = last_entry.get("execution_result") if isinstance(last_entry, dict) else None
    msgs_serialized = "\n\n".join(
        f"[{getattr(m, 'type', '?')}] {getattr(m, 'content', '')!r}" for m in messages
    )
    body = (
        "# Stata agent failure dump\n\n"
        f"## reason\n{reason}\n\n"
        f"## last commands\n{last_commands or '<no tool call recorded>'}\n\n"
        "## last execution_result\n"
        f"{json.dumps(last_result, ensure_ascii=False, indent=2) if last_result else '<none>'}\n\n"
        f"## agent messages\n{msgs_serialized or '<no messages captured>'}\n"
    )
    dump_path.write_text(body, encoding="utf-8")
    _LOGGER.error("_stata_agent: failure dumped to %s (reason: %s)", dump_path, reason)
    return dump_path


def _last_error_kind(history: list[dict[str, Any]]) -> str | None:
    if not history:
        return None
    last = history[-1].get("execution_result")
    if not isinstance(last, dict):
        return None
    raw = last.get("error_kind")
    return raw if isinstance(raw, str) else None


async def run_stata_agent[T: BaseModel](
    *,
    node_name: NodeName,
    system_prompt: str,
    human_message: str,
    output_schema: type[T],
    iter_cap: int,
    post_check_fn: Callable[[str], None],
) -> tuple[T, str, str]:
    """驱动一轮 Stata ReAct 节点的完整生命周期。

    成功:返回 ``(payload, do_file_path, log_file_path)``,后两者是绝对路径,从
    最后一次 succeeded ExecutionResult.artifacts 中筛 ``input.do`` 与 ``run.log`` 得到。

    失败 (超轮 / 缺结构化输出 / 缺成功执行 / bootstrap_error / post-check 不通过 /
    缺 artifacts) 一律 dump 现场到 ``<workspace>/_failure/dump.txt`` 后 raise。
    """
    workspace = _resolve_workspace(node_name)
    history: list[dict[str, Any]] = []
    async with get_stata_tools() as stata_tools:
        tools_list = list(stata_tools)
        await _doctor_precondition(tools_list)
        run_inline_orig = _find_tool(tools_list, "run_inline")
        run_inline_wrapped = _make_run_inline_wrapped(run_inline_orig, workspace, history)
        payload, messages, failure = await run_structured_agent(
            tools=[run_inline_wrapped],
            system_prompt=system_prompt,
            output_schema=output_schema,
            human_message=human_message,
            max_iterations=iter_cap,
        )

    if failure is AgentRunFailure.ITER_CAP_EXCEEDED:
        _dump_failure(workspace, history, messages, reason=f"reached max_iterations ({iter_cap})")
        msg = f"{node_name}: ReAct reached max_iterations ({iter_cap}) without a terminal response"
        raise RuntimeError(msg)
    if failure is AgentRunFailure.NO_STRUCTURED_RESPONSE or payload is None:
        _dump_failure(workspace, history, messages, reason="no structured_response")
        msg = f"{node_name}: agent did not produce a structured response"
        raise RuntimeError(msg)

    error_kind = _last_error_kind(history)
    if error_kind in _BOOTSTRAP_ERROR_KINDS:
        _dump_failure(
            workspace,
            history,
            messages,
            reason=f"infrastructure failure (error_kind={error_kind!r})",
        )
        msg = (
            f"{node_name}: stata-executor reported error_kind={error_kind!r};"
            f" Stata 进程或环境故障,见 dump.txt"
        )
        raise RuntimeError(msg)

    last_entry = _last_succeeded_entry(history)
    if last_entry is None:
        _dump_failure(workspace, history, messages, reason="no succeeded ExecutionResult")
        msg = f"{node_name}: agent terminated without any succeeded run_inline call"
        raise RuntimeError(msg)
    last_commands = cast(str, last_entry["commands"])

    try:
        post_check_fn(last_commands)
    except (ValueError, RuntimeError) as exc:
        _dump_failure(workspace, history, messages, reason=f"post-check failed: {exc}")
        raise

    try:
        do_path, log_path = _extract_artifacts(history)
    except RuntimeError as exc:
        _dump_failure(workspace, history, messages, reason=f"artifacts missing: {exc}")
        raise
    return payload, do_path, log_path
