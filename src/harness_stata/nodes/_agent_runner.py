"""Shared helper to run one ``create_agent`` ReAct loop with structured output.

Returns ``(payload, messages, failure)``。 Callers decide how to react to
failure modes:
- ``data_cleaning``: 直接 ``raise RuntimeError``。
- stata 节点(``_stata_agent.run_stata_agent``): dump 现场后再 raise,把
  messages 和 history 一并落到 ``<workspace>/_failure/dump.txt``。

Helper 本身不为这两类失败抛异常,只为编程级错误(工具/模型构造异常)透传。
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel

from harness_stata.clients.llm import get_chat_model


class AgentRunFailure(StrEnum):
    """Non-exception failure modes captured by :func:`run_structured_agent`."""

    ITER_CAP_EXCEEDED = "iter_cap_exceeded"
    NO_STRUCTURED_RESPONSE = "no_structured_response"


async def run_structured_agent[T: BaseModel](
    *,
    tools: Sequence[Any],
    system_prompt: str,
    output_schema: type[T],
    human_message: str,
    max_iterations: int,
) -> tuple[T | None, list[BaseMessage], AgentRunFailure | None]:
    """Drive one ReAct loop and collect the structured-output payload.

    返回:
    - 成功: ``(payload, messages, None)``
    - 超轮: ``(None, [], ITER_CAP_EXCEEDED)`` —— ``ModelCallLimitExceededError``
      在 middleware 内抛出,messages 在异常路径上不可得;由调用方决定 dump
      策略(stata 节点用节点局部 history list 替代)。
    - 缺结构化输出: ``(None, messages, NO_STRUCTURED_RESPONSE)``
    """
    agent = create_agent(
        model=get_chat_model(),
        tools=list(tools),
        system_prompt=system_prompt,
        middleware=[
            ModelCallLimitMiddleware(run_limit=max_iterations, exit_behavior="error"),
        ],
        response_format=ToolStrategy(output_schema),
    )
    initial: Any = {"messages": [HumanMessage(content=human_message)]}
    try:
        result: dict[str, Any] = await agent.ainvoke(initial)
    except ModelCallLimitExceededError:
        return None, [], AgentRunFailure.ITER_CAP_EXCEEDED

    messages_raw = result.get("messages")
    messages: list[BaseMessage] = messages_raw if isinstance(messages_raw, list) else []
    payload_raw = result.get("structured_response")
    if not isinstance(payload_raw, output_schema):
        return None, messages, AgentRunFailure.NO_STRUCTURED_RESPONSE
    return payload_raw, messages, None
