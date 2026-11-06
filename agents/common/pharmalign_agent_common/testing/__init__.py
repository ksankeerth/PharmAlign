"""Fakes for agent tests: a scripted model and an MCP server with canned tools."""
from .fake_mcp import FakeMcpServer, ToolCall
from .fake_model import FakeModel, call_tool, cut_off, say, say_json
from .fake_tools import Clause, FakeCase, Notice, Page

__all__ = ["Clause", "FakeCase", "FakeMcpServer", "FakeModel", "Notice", "Page", "ToolCall", "call_tool", "cut_off", "say", "say_json"]
