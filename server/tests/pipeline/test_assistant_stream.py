"""The server reads the agent's turn stream line by line. A long turn is kept alive by comment lines, and a turn that goes silent says so plainly."""
import httpx
import pytest
from pharmalign_contracts.agents.knowledge_assistant import TurnRequest

from app.pipeline.assistant import ASSISTANT_TOO_SLOW, AssistantTimedOut, AssistantUnavailable, HttpAssistantClient

pytestmark = pytest.mark.asyncio

BASE = "https://agent.example"
STREAM = (
    ": keepalive\n\n"
    ": keepalive\n\n"
    'data: {"type": "text_delta", "text": "Hello."}\n\n'
    ": keepalive\n\n"
    'data: {"type": "usage", "usage": {"model_calls": 1, "tool_calls": 0, "input_tokens": 3, "output_tokens": 2}}\n\n'
)


async def events_of(client: HttpAssistantClient) -> list[dict]:
    return [e async for e in client.turn("asa-1", TurnRequest(message="Hi"))]


async def test_comment_lines_between_events_are_skipped():
    client = HttpAssistantClient(BASE, transport=httpx.MockTransport(lambda r: httpx.Response(200, content=STREAM.encode())))
    try:
        got = await events_of(client)
    finally:
        await client.aclose()
    assert [e["type"] for e in got] == ["text_delta", "usage"]


async def test_a_silent_agent_is_a_timeout_not_a_generic_failure():
    def silent(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no bytes for 60 s", request=request)

    client = HttpAssistantClient(BASE, transport=httpx.MockTransport(silent))
    try:
        with pytest.raises(AssistantTimedOut):
            await events_of(client)
    finally:
        await client.aclose()
    assert issubclass(AssistantTimedOut, AssistantUnavailable)  # callers that catch the base class still catch it
    assert "too long" in ASSISTANT_TOO_SLOW and "Saved traces" not in ASSISTANT_TOO_SLOW
