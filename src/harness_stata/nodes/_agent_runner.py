"""Shared helper to run one ``create_agent`` ReAct loop with structured output.

Used by ``data_cleaning`` / ``descriptive_stats`` / ``regression`` — they share
the same装配 + ainvoke + payload 校验 pattern. Differences live in the caller
(tools source, prompt, schema, iteration cap, post-processing).

Returns ``(payload, messages)``. ``messages`` is the full message log so that
post-hoc inspection (e.g. ``regression`` reading the last ``run_do``
ToolMessage) does not require a second invocation.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.structured_output import ToolStrategy
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel

from harness_stata.clients.llm import get_chat_model


async def run_structured_agent[T: BaseModel](
    *,
    tools: Sequence[Any],
    system_prompt: str,
    output_schema: type[T],
    human_message: str,
    max_iterations: int,
    node_name: str,
) -> tuple[T, list[BaseMessage]]:
    """Drive one ReAct loop and validate the structured-output payload.

    ``node_name`` is interpolated into error messages so existing log lines
    (``"<node>: ReAct reached max_iterations ..."``) are preserved verbatim.
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
    except ModelCallLimitExceededError as exc:
        raise RuntimeError(
            f"{node_name}: ReAct reached max_iterations ({max_iterations})"
            f" without a terminal response"
        ) from exc

    payload = result.get("structured_response")
    if not isinstance(payload, output_schema):
        raise RuntimeError(
            f"{node_name}: agent did not produce a structured response"
            f" (got {type(payload).__name__})"
        )

    messages_raw = result.get("messages")
    messages: list[BaseMessage] = messages_raw if isinstance(messages_raw, list) else []
    return payload, messages
