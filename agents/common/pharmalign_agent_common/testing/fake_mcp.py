"""A fake MCP server with canned tools, on 127.0.0.1. No network, no keys.

    with FakeMcpServer({"read_page": {"text": "a shelf life of 36 months"}}) as mcp:
        settings = Settings(mcp_url=mcp.url, ...)
    mcp.calls  # every tool call: name, arguments and the Authorization header it came with

A tool value is returned as JSON. A function gets the arguments and returns the value, or
raises to make the tool fail.
"""
import asyncio
import contextlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route


@dataclass
class ToolCall:
    name: str
    arguments: dict
    authorization: str | None


class _Endpoint:
    def __init__(self, manager: StreamableHTTPSessionManager):
        self.manager = manager

    async def __call__(self, scope, receive, send):
        await self.manager.handle_request(scope, receive, send)


class FakeMcpServer:
    def __init__(self, tools: dict[str, Any]):
        self.tools = tools
        self.calls: list[ToolCall] = []
        self.url = ""
        self._server = Server("fake-pharmalign-tools")
        self._uvicorn: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._register()

    def _register(self) -> None:
        @self._server.list_tools()
        async def list_tools() -> list[types.Tool]:
            return [
                types.Tool(name=name, description=f"Canned tool {name}", inputSchema={"type": "object", "additionalProperties": True})
                for name in self.tools
            ]

        @self._server.call_tool(validate_input=False)
        async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
            request = self._server.request_context.request
            auth = request.headers.get("authorization") if request is not None else None
            self.calls.append(ToolCall(name, dict(arguments or {}), auth))
            value = self.tools[name]
            if callable(value):
                value = value(arguments or {})
            return [types.TextContent(type="text", text=json.dumps(value))]

    def calls_to(self, name: str) -> list[ToolCall]:
        return [c for c in self.calls if c.name == name]

    def __enter__(self) -> "FakeMcpServer":
        manager = StreamableHTTPSessionManager(app=self._server, json_response=True, stateless=True)

        @contextlib.asynccontextmanager
        async def lifespan(app):
            async with manager.run():
                yield

        app = Starlette(routes=[Route("/mcp", endpoint=_Endpoint(manager))], lifespan=lifespan)
        self._uvicorn = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", lifespan="on"))
        self._thread = threading.Thread(target=lambda: asyncio.run(self._uvicorn.serve()), daemon=True)
        self._thread.start()
        for _ in range(200):
            if self._uvicorn.started and self._uvicorn.servers:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("the fake MCP server didn't start")
        port = self._uvicorn.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self

    def __exit__(self, *exc) -> None:
        if self._uvicorn:
            self._uvicorn.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
