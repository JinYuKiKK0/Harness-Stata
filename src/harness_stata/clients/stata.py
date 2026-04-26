"""Stata executor MCP client adapter.

Launches the ``stata-executor`` submodule as a stdio subprocess via
``langchain-mcp-adapters`` and exposes its tools as LangChain ``BaseTool``
instances for upstream ``nodes/`` and ``subgraphs/``.

Upstream modules must not import ``stata_executor`` directly
(enforced by import-linter) -- they must obtain tools via
:func:`get_stata_tools`.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

from harness_stata.clients._mcp_interceptors import append_structured_content
from harness_stata.config import get_settings


@asynccontextmanager
async def get_stata_tools() -> AsyncGenerator[list[BaseTool]]:
    """Yield LangChain tools backed by the stata-executor stdio server.

    Usage::

        async with get_stata_tools() as tools:
            ...  # bind tools to a ReAct agent or node

    On entry, spawns ``python -m stata_executor.adapters.mcp`` as a subprocess
    with Stata executable / edition injected through the child process env.
    On exit, the session and subprocess are closed automatically.
    """
    s = get_settings()
    client = MultiServerMCPClient(
        {
            "stata": {
                "command": sys.executable,
                "args": ["-m", "stata_executor.adapters.mcp"],
                "transport": "stdio",
                "env": {
                    "STATA_EXECUTOR_STATA_EXECUTABLE": s.stata_executable,
                    "STATA_EXECUTOR_EDITION": s.stata_edition,
                },
            }
        },
        tool_interceptors=[append_structured_content],
    )
    async with client.session("stata") as session:
        tools = await load_mcp_tools(session)
        yield tools
