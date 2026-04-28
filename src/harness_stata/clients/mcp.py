"""Shared invocation helpers for langchain-mcp-adapters tools.

``langchain-mcp-adapters`` 把每个 MCP 工具注册成 ``response_format="content_and_artifact"``,
裸的 ``tool.ainvoke(args)`` 只会拿到 content(``list[ToolMessageContentBlock]``)那一半,
真正的结构化 dict 在 artifact 里被丢掉。这层 helper 用 ToolCall 协议调用工具,
优先取 ``artifact.structured_content``;若上游 MCP 工具未返回 ``structuredContent``,
退化为 JSON 解码第一块文本内容。csmar 与 stata 两侧的 MCP 调用都走它。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool


async def call_structured_mcp_tool(
    tool: BaseTool, args: dict[str, object], context: str
) -> dict[str, Any]:
    """Invoke an MCP-backed BaseTool and return its structured JSON payload.

    ``context`` 是给上游用于拼错误信息的人类可读上下文(例如
    "coverage check for variable 'ROA' on table 'FI_T5'")。

    Raises:
        ValueError: 上游既没回 structuredContent,文本内容也无法 JSON 解码,
            或工具内部抛出 ToolException。
    """
    tool_call = {
        "name": tool.name,
        "args": args,
        "id": uuid.uuid4().hex,
        "type": "tool_call",
    }
    result = await tool.ainvoke(tool_call)
    if not isinstance(result, ToolMessage):
        raise ValueError(
            f"{context}: expected ToolMessage from {tool.name}, got {type(result).__name__}"
        )

    artifact = result.artifact
    if isinstance(artifact, dict):
        structured = artifact.get("structured_content")
        if isinstance(structured, dict):
            return structured

    return _decode_text_fallback(result.content, tool.name, context)


def _decode_text_fallback(content: object, tool_name: str, context: str) -> dict[str, Any]:
    if isinstance(content, str):
        text = content
    elif isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, str):
            text = first
        elif isinstance(first, dict) and first.get("type") == "text":
            text = str(first.get("text", ""))
        else:
            raise ValueError(
                f"{context}: {tool_name} returned no structured_content and "
                f"first content block is not text: {first!r}"
            )
    else:
        raise ValueError(f"{context}: {tool_name} returned no structured_content and empty content")

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{context}: {tool_name} text content is not JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            f"{context}: {tool_name} JSON payload is {type(decoded).__name__}, expected dict"
        )
    return decoded
