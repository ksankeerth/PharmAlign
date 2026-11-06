"""The session router with a tiny agent: sessions, streamed turns, the closed event set, rules, tokens."""
import json
from typing import Annotated

import httpx
import pytest
from fastapi import FastAPI
from pharmalign_agent_common import SessionSpec, Settings, ToolLog, build_session_router, check_evidence
from pharmalign_agent_common.testing import FakeMcpServer, FakeModel, call_tool, say_json
from pharmalign_contracts.agents.knowledge_assistant import (
    KNOWLEDGE_SESSION_EXAMPLE, SESSION_EXAMPLE, Message, SessionCreate,
)
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION, Strict
from pydantic import Field

HEADERS = {CONTRACT_HEADER: CONTRACT_VERSION}
QUOTE = "a shelf life of 36 months"


class Answer(Strict):
    events: Annotated[list[Message], Field(min_length=1)]


def no_long_answers(turn, answer, log: ToolLog) -> None:
    check_evidence(answer, log)
    if any(len(e.text) > 200 for e in answer.events):
        raise ValueError("answer in 200 characters or fewer")


SPEC = SessionSpec(
    name="tiny-chat", adk_name="tiny_chat", instruction="Answer in JSON.", default_model="fake-default-model",
    answer_model=Answer, model_events=frozenset({"message"}), knowledge_tools=("read_clause",), case_tools=("read_page",),
    build_message=lambda session, request: request.message, check_turn=no_long_answers,
    variants={"knowledge": "No case.", "case": "On a case."}, max_tool_calls=2,
)
TOOLS = {"read_clause": {"anchor": "E-S25:v3:c1", "text": f"The product has {QUOTE}."}, "read_page": {"anchor": "d-17:v1:p2", "text": "x"}}
GOOD = {"events": [{"type": "message", "text": "Shelf life is 36 months.", "citations": [{"anchor": "E-S25:v3:c1", "quote": QUOTE}]}]}


def sse(text: str) -> list[dict]:
    return [json.loads(x.removeprefix("data:")) for x in text.splitlines() if x.startswith("data:")]


async def talk(script, *messages, body=KNOWLEDGE_SESSION_EXAMPLE):
    model = FakeModel(script=script)
    with FakeMcpServer(TOOLS) as mcp:
        app = FastAPI()
        app.include_router(build_session_router(SPEC, Settings(mcp_url=mcp.url), model=model))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            sid = (await client.post("/sessions", json={**body, "run_token": "token-12345"}, headers=HEADERS)).json()["agent_session_id"]
            out = [sse((await client.post(f"/sessions/{sid}/turns", json={"message": m}, headers=HEADERS)).text) for m in messages]
    return out, model, mcp


async def test_a_turn_streams_pieces_then_the_message_then_usage():
    [events], model, mcp = await talk([call_tool("read_clause", anchor="E-S25:v3:c1"), say_json(GOOD)], "How long is the shelf life?")
    assert [e["type"] for e in events] == ["text_delta", "message", "usage"]
    assert {c.authorization for c in mcp.calls} == {"Bearer token-12345"}
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == {"read_clause"}  # no case tools without a case
    assert "No case." in model.requests[0].config.system_instruction


async def test_a_case_session_adds_the_case_tools():
    _, model, _ = await talk([say_json(GOOD)], "Hi", body=SESSION_EXAMPLE)
    assert set(model.requests[0].tools_dict) - {"set_model_response"} == {"read_clause", "read_page"}
    assert "On a case." in model.requests[0].config.system_instruction


async def test_the_rules_run_before_anything_is_streamed_and_the_retry_carries_the_reason():
    long = {"events": [{**GOOD["events"][0], "text": "x" * 300}]}
    [events], model, _ = await talk([call_tool("read_clause", anchor="E-S25:v3:c1"), say_json(long), say_json(GOOD)], "Hi")
    assert "200 characters" in model.requests[-1].contents[-1].parts[0].text
    assert [e["type"] for e in events] == ["text_delta", "message", "usage"]
    [failed], _, _ = await talk([call_tool("read_clause", anchor="E-S25:v3:c1"), say_json(long), say_json(long)], "Hi")
    assert [e["type"] for e in failed] == ["usage", "error"] and failed[-1]["code"] == "invalid_output"


async def test_the_budget_is_per_turn_and_a_session_keeps_history():
    read = call_tool("read_clause", anchor="E-S25:v3:c1")
    [one, two], model, _ = await talk([read, read, say_json(GOOD), read, read, say_json(GOOD)], "First?", "Second?")
    assert one[-2]["type"] == "message" and two[-2]["type"] == "message"  # 2 tool calls each turn: within max_tool_calls
    assert "First?" in str(model.requests[-1].contents)
    [over], _, _ = await talk([read, read, read, say_json(GOOD)], "Hi")
    assert over[-1]["code"] == "budget_exceeded"


async def test_routes_header_unknown_session_and_health():
    router = build_session_router(SPEC, Settings(mcp_url="http://127.0.0.1:1/mcp"))
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.post("/sessions", json={})).status_code == 422
        r = await client.post("/sessions/asa-nope/turns", json={"message": "Hi"}, headers=HEADERS)
        assert r.status_code == 404 and r.json()["code"] == "session_not_found"
    assert [r.path for r in build_session_router(SPEC, Settings(), health=False).routes].count("/health") == 0
    no_tools = FastAPI()
    no_tools.include_router(build_session_router(SPEC, Settings()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=no_tools), base_url="http://agent") as client:
        r = await client.post("/sessions", json={**KNOWLEDGE_SESSION_EXAMPLE, "run_token": "token-12345"}, headers=HEADERS)
        assert r.status_code == 503


def test_the_spec_hash_covers_the_variants():
    other = SessionSpec(**{**SPEC.__dict__, "variants": {"knowledge": "Changed."}})
    assert SPEC.prompt_hash != other.prompt_hash and SPEC.prompt_hash.startswith("sha256:")
    knowledge = SessionCreate.model_validate({**KNOWLEDGE_SESSION_EXAMPLE, "run_token": "token-12345"})
    on_case = SessionCreate.model_validate({**SESSION_EXAMPLE, "run_token": "token-12345"})
    assert SPEC.tools_for(knowledge) == ("read_clause",) and SPEC.tools_for(on_case) == ("read_clause", "read_page")
