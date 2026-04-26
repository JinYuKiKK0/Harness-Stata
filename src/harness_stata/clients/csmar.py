"""CSMAR MCP client adapter.

Launches the ``csmar-mcp`` submodule as a stdio subprocess via
``langchain-mcp-adapters`` and exposes its tools as LangChain ``BaseTool``
instances for upstream ``nodes/`` and ``subgraphs/``.

Upstream modules must not import ``csmar_mcp`` / ``csmarapi`` directly
(enforced by import-linter) -- they must obtain tools via
:func:`get_csmar_tools`.
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
async def get_csmar_tools() -> AsyncGenerator[list[BaseTool]]:
    """Yield LangChain tools backed by the csmar-mcp stdio server.

    Usage::

        async with get_csmar_tools() as tools:
            ...  # bind tools to a ReAct agent or node

    On entry, spawns ``python -m csmar_mcp`` as a subprocess and opens an MCP
    session. On exit, the session and subprocess are closed automatically.
    """
    s = get_settings()
    client = MultiServerMCPClient(
        {
            "csmar": {
                "command": sys.executable,
                "args": [
                    "-m",
                    "csmar_mcp",
                    "--account",
                    s.csmar_account,
                    "--password",
                    s.csmar_password,
                ],
                "transport": "stdio",
            }
        },
        tool_interceptors=[append_structured_content],
    )
    async with client.session("csmar") as session:
        tools = await load_mcp_tools(session)
        yield tools
