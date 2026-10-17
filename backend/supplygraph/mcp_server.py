"""MCP server exposing the same graph tools over stdio.

Any MCP client (Claude Desktop, Claude Code, other agents) gets the exact tool contract the
built-in agent uses: typed inputs, fixed Cypher, explicit outcomes. One process serves one
tenant, set by ``SUPPLYGRAPH_DEFAULT_TENANT``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .config import Settings, get_settings
from .db import GraphStore
from .llm import LLMProvider, build_provider
from .logging_setup import configure_logging
from .registry import TOOLS, ToolCallError, call_tool
from .tools import toolbox_for

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Ctx:
    settings: Settings
    store: GraphStore
    provider: LLMProvider


def build_server(settings: Settings) -> Server:
    @asynccontextmanager
    async def lifespan(_: Server) -> AsyncIterator[_Ctx]:
        store = GraphStore(settings)
        try:
            yield _Ctx(settings, store, build_provider(settings))
        finally:
            await store.close()

    tool_list = [
        types.Tool(
            name=spec.name,
            description=spec.description,
            input_schema=spec.input_schema(),
            annotations=types.ToolAnnotations(
                read_only_hint=spec.read_only,
                destructive_hint=False,
                idempotent_hint=spec.read_only,
                open_world_hint=False,
            ),
        )
        for spec in TOOLS
    ]

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_list)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        app: _Ctx = ctx.lifespan_context
        toolbox = toolbox_for(
            app.settings,
            app.store,
            app.settings.default_tenant,
            provider=app.provider,
            session_id="mcp",
        )
        try:
            result = await call_tool(toolbox, params.name, params.arguments or {})
        except ToolCallError as exc:
            return types.CallToolResult(
                content=[types.TextContent(text=f"Error: {exc}")], is_error=True
            )
        payload = result.model_dump(mode="json")
        return types.CallToolResult(
            content=[types.TextContent(text=json.dumps(result.for_model(), default=str))],
            structured_content=payload,
        )

    return Server(
        "supplygraph",
        version=__version__,
        instructions="Supply-chain risk tools backed by a FalkorDB graph. Names are resolved "
        "by the tools; 'not_in_graph' means no relationship is recorded, not 'no impact'.",
        lifespan=lifespan,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def serve_stdio(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)  # logs go to stderr
    server = build_server(settings)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
