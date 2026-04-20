"""Shared MCP tool-call interceptors.

Both csmar-mcp and stata-executor-mcp respond with a dual channel:
``content`` (short summary) + ``structuredContent`` (full payload).
langchain-mcp-adapters by default stashes ``structuredContent`` into
``ToolMessage.artifact``, which the LLM never sees.
``append_structured_content`` appends the structured payload as an extra
``TextContent`` block so it becomes visible to the model.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult, TextContent


async def append_structured_content(
    request: MCPToolCallRequest,
    handler: Callable[[MCPToolCallRequest], Awaitable[CallToolResult]],
) -> CallToolResult:
    result = await handler(request)
    if result.structuredContent:
        result.content = [
            *result.content,
            TextContent(
                type="text",
                text=json.dumps(result.structuredContent, ensure_ascii=False),
            ),
        ]
    return result
