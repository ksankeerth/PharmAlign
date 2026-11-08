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


# ---- opt-in controls for an agent whose model works in a loop of tool calls (all off in SPEC above)
import asyncio  # noqa: E402

from google.genai import types  # noqa: E402
from pharmalign_agent_common import sessions as sessions_module  # noqa: E402
from pharmalign_agent_common.sessions import ANSWER_NOW, KEEPALIVE_LINE, keepalive  # noqa: E402

READ = call_tool("read_clause", anchor="E-S25:v3:c1")


def tuned(**changes) -> SessionSpec:
    return SessionSpec(**{**SPEC.__dict__, "max_tool_calls": 6, **changes})


async def talk_with(spec, script, *messages):
    model = FakeModel(script=script)
    with FakeMcpServer(TOOLS) as mcp:
        app = FastAPI()
        app.include_router(build_session_router(spec, Settings(mcp_url=mcp.url), model=model))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
            sid = (await client.post("/sessions", json={**KNOWLEDGE_SESSION_EXAMPLE, "run_token": "token-12345"}, headers=HEADERS)).json()["agent_session_id"]
            texts = [(await client.post(f"/sessions/{sid}/turns", json={"message": m}, headers=HEADERS)).text for m in messages]
    return texts, model, mcp


def asked_to_answer_now(request) -> bool:
    return any(ANSWER_NOW in (p.text or "") for c in request.contents for p in (c.parts or []))


async def test_keepalive_sends_comment_lines_while_the_task_works_and_none_when_off():
    async def work():
        await asyncio.sleep(0.35)
        return "done"

    task = asyncio.ensure_future(work())
    beats = [b async for b in keepalive(task, 0.1)]
    assert beats and set(beats) == {KEEPALIVE_LINE} and task.result() == "done"
    quiet = asyncio.ensure_future(work())
    assert [b async for b in keepalive(quiet, None)] == []


async def test_a_caller_that_goes_away_cancels_the_work():
    task = asyncio.ensure_future(asyncio.sleep(30))
    gen = keepalive(task, 0.05)
    assert await gen.__anext__() == KEEPALIVE_LINE
    await gen.aclose()
    await asyncio.sleep(0)
    assert task.cancelled()


async def test_a_slow_turn_streams_comment_lines_between_its_events(monkeypatch):
    real = sessions_module.run_turn

    async def slow(spec, held, request):
        await asyncio.sleep(0.3)
        return await real(spec, held, request)

    monkeypatch.setattr(sessions_module, "run_turn", slow)
    [text], _, _ = await talk_with(tuned(keepalive_seconds=0.1), [READ, say_json(GOOD)], "Hi")
    assert text.count(": keepalive") >= 2 and text.startswith(": keepalive")
    assert [e["type"] for e in sse(text)] == ["text_delta", "message", "usage"]  # the comment lines are not events
    [plain], _, _ = await talk_with(tuned(), [READ, say_json(GOOD)], "Hi")
    assert ": keepalive" not in plain  # off by default


async def test_the_model_is_told_to_answer_now_after_the_soft_number_of_rounds():
    [text], model, _ = await talk_with(tuned(soft_rounds=1), [READ, call_tool("read_clause", anchor="E-S25:v3:c1", extra=1), say_json(GOOD)], "Hi")
    assert not asked_to_answer_now(model.requests[0]) and asked_to_answer_now(model.requests[1])
    assert sse(text)[-2]["type"] == "message"


async def test_the_model_is_told_to_answer_now_once_the_soft_time_has_passed():
    _, model, _ = await talk_with(tuned(soft_seconds=0.0), [READ, say_json(GOOD)], "Hi")
    assert asked_to_answer_now(model.requests[0])
    _, model, _ = await talk_with(tuned(), [READ, say_json(GOOD)], "Hi")
    assert not asked_to_answer_now(model.requests[0])


async def test_a_call_repeated_in_one_turn_is_not_sent_again():
    script = [READ, READ, say_json(GOOD)]
    _, model, mcp = await talk_with(tuned(repeat_answer="You already have this result."), script, "Hi")
    assert len(mcp.calls) == 1
    _, _, every = await talk_with(tuned(), script, "Hi")
    assert len(every.calls) == 2  # off by default
    # a new turn may read it again: the guard is per turn
    _, _, mcp = await talk_with(tuned(repeat_answer="x"), [READ, say_json(GOOD), READ, say_json(GOOD)], "One?", "Two?")
    assert len(mcp.calls) == 2


async def test_the_thinking_level_is_set_on_every_model_request():
    _, model, _ = await talk_with(tuned(thinking_level="low"), [READ, say_json(GOOD)], "Hi")
    assert model.requests[0].config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    _, model, _ = await talk_with(tuned(), [READ, say_json(GOOD)], "Hi")
    assert getattr(model.requests[0].config.thinking_config, "thinking_level", None) is None


from google.adk.models.llm_response import LlmResponse  # noqa: E402
from pharmalign_agent_common.runner import RunTracker  # noqa: E402
from pharmalign_agent_common.sessions import TOO_SLOW, TimedModel  # noqa: E402


class Hangs:
    """A model whose first calls hang, then answers like the one it wraps."""

    def __init__(self, inner, hang_first: int):
        self.inner, self.hang_first, self.calls = inner, hang_first, 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def generate_content_async(self, llm_request, stream=False):
        self.calls += 1
        if self.calls <= self.hang_first:
            await asyncio.sleep(30)
        async for response in self.inner.generate_content_async(llm_request, stream=stream):
            yield response


class _Plain(__import__("google.adk.models.base_llm", fromlist=["BaseLlm"]).BaseLlm):
    """Gives a duck-typed model the BaseLlm shape the wrapper needs."""

    model: str = "m"
    source: object = None

    def __init__(self, source):
        super().__init__(model="m", source=source)

    async def generate_content_async(self, llm_request, stream=False):
        async for r in self.source.generate_content_async(llm_request, stream=stream):
            yield r


async def test_a_hung_model_call_is_cancelled_and_tried_once_more():
    inner = FakeModel(script=[say_json(GOOD)])
    hung = Hangs(inner, 1)
    request = type("R", (), {"contents": [], "config": None})()
    responses = [r async for r in TimedModel(model="m", inner=_Plain(hung), seconds=0.2).generate_content_async(request)]
    assert hung.calls == 2 and len(responses) == 1  # the first call hung and was cancelled, the second answered
    with pytest.raises(TimeoutError):  # two hung calls end the turn: the common error mapping turns it into the plain message
        _ = [r async for r in TimedModel(model="m", inner=_Plain(Hangs(inner, 5)), seconds=0.1).generate_content_async(request)]
    assert sessions_module._failure(TimeoutError("x"), RunTracker(1, 1)).message == TOO_SLOW
    wrapped = RuntimeError("model error TimeoutError: the model did not answer within 25 seconds, 2 times")  # how ADK re-raises it
    assert sessions_module._failure(wrapped, RunTracker(1, 1)).message == TOO_SLOW
    assert sessions_module._failure(ValueError("x"), RunTracker(1, 1)).code == "internal_error"


async def test_a_turn_that_runs_out_of_time_says_so_in_plain_words(monkeypatch):
    async def slow(spec, held, request):
        await asyncio.sleep(5)

    monkeypatch.setattr(sessions_module, "run_turn", slow)
    [text], _, _ = await talk_with(tuned(turn_seconds=1), [say_json(GOOD)], "Hi")
    error = sse(text)[-1]
    assert error["type"] == "error" and error["code"] == "deadline_exceeded" and error["message"] == TOO_SLOW
    assert "Saved traces" not in TOO_SLOW


async def test_the_output_cap_is_set_on_every_model_request_when_asked():
    _, model, _ = await talk_with(tuned(max_output_tokens=1234), [READ, say_json(GOOD)], "Hi")
    assert model.requests[0].config.max_output_tokens == 1234
    _, model, _ = await talk_with(tuned(), [READ, say_json(GOOD)], "Hi")
    assert model.requests[0].config.max_output_tokens is None
