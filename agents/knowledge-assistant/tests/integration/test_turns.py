"""Whole turns: session opened over the routes, fake model, fake MCP tools with the server's shapes, events out."""
import dataclasses

import pytest
from pharmalign_agent_common.testing import FakeCase, call_tool, say_json
from pharmalign_contracts.agents.knowledge_assistant import (
    CASE_TOOLS, EVENT_ADAPTER, KNOWLEDGE_TOOLS, TOOLS, SessionCreated, UsageEvent,
)

from app.agent import KNOWLEDGE_ONLY, ON_A_CASE, SPEC
from tests.conftest import (
    INJECTION, LAW_ANCHOR, LAW_QUOTE, MESSAGE, OLD_ANCHOR, OLD_QUOTE, PAGE_ANCHOR, PAGE_QUOTE, case, chat,
)

SEARCH = call_tool("search_kb", query="long-term stability data conditions", purpose="requirement")
CASE_MESSAGE = {"events": [{
    "type": "message",
    "text": "The study ran at 25 C and 60% RH, which is not the 30 C and 65% RH the guideline asks for.",
    "citations": [{"anchor": PAGE_ANCHOR, "quote": PAGE_QUOTE}],
}]}
NO_SOURCE = {"events": [{
    "type": "no_source", "detail": "Nothing in force answers this.",
    "searches_tried": [{"search": "search_kb: biosimilar interchangeability, in force only", "result": "no passage was similar enough", "candidates": 0}],
    "suggestions": ["What does the stability guideline require?"],
}]}


async def test_a_turn_streams_valid_events_ending_with_usage():
    async with chat([SEARCH, say_json(MESSAGE)]) as c:
        sid = await c.open(with_case=False, token="token-of-session-a")
        events = await c.turn(sid, "What stability data does the guideline ask for?")
    for event in events:
        EVENT_ADAPTER.validate_python(event)  # all inside the contract's closed set
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "usage" and kinds.count("message") == 1
    assert set(kinds[: kinds.index("message")]) == {"text_delta"}  # the text comes first, in pieces
    message = events[kinds.index("message")]
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == message["text"]
    assert message["citations"] == [{"anchor": LAW_ANCHOR, "quote": LAW_QUOTE}]
    assert UsageEvent.model_validate(events[-1]).usage.tool_calls == 1
    assert [x.name for x in c.mcp.calls] == ["search_kb"]
    assert {x.authorization for x in c.mcp.calls} == {"Bearer token-of-session-a"}


async def test_an_invented_anchor_is_refused_retried_once_then_the_turn_fails():
    invented = {"events": [{**MESSAGE["events"][0], "citations": [{"anchor": "E-S25:v3:c9999", "quote": LAW_QUOTE}]}]}
    async with chat([SEARCH, say_json(invented), say_json(invented)]) as c:
        events = await c.turn(await c.open(False), "What stability data is asked for?")
    kinds = [e["type"] for e in events]
    assert "message" not in kinds and "text_delta" not in kinds  # nothing leaves before the answer passes
    assert events[-1]["type"] == "error" and events[-1]["code"] == "invalid_output" and "c9999" in events[-1]["message"]
    assert c.model.calls == 3
    assert "c9999" in c.model.requests[-1].contents[-1].parts[0].text  # the retry carried the reason


async def test_text_in_a_document_is_data_and_does_not_change_the_answer():
    reads = [call_tool("get_check", check_id="QUAL-07"), call_tool("read_page", doc_id="d-17", page=41)]
    async with chat([*reads, say_json(CASE_MESSAGE)]) as clean_chat:
        clean = await clean_chat.turn(await clean_chat.open(), "Why does QUAL-07 need a person?")
    page_with_instruction = [call_tool("get_check", check_id="QUAL-07"), call_tool("read_page", doc_id="d-17", page=42)]
    async with chat([*page_with_instruction, say_json(CASE_MESSAGE)], case(injected=True)) as c:
        injected = await c.turn(await c.open(), "Why does QUAL-07 need a person?")
    pick = lambda events: [e for e in events if e["type"] in ("message", "draft_comment")]  # noqa: E731
    assert pick(injected) == pick(clean) and [e["type"] for e in injected] == [e["type"] for e in clean]
    last = c.model.requests[-1]
    assert INJECTION not in last.config.system_instruction
    assert all(INJECTION not in (p.text or "") for x in last.contents for p in x.parts)
    assert any(p.function_response and INJECTION in str(p.function_response.response) for x in last.contents for p in x.parts)


async def test_a_knowledge_only_session_has_no_case_tools():
    async with chat([SEARCH, say_json(MESSAGE)]) as c:
        await c.turn(await c.open(with_case=False), "What stability data does the guideline ask for?")
    offered = set(c.model.requests[0].tools_dict) - {"set_model_response"}
    assert offered == set(KNOWLEDGE_TOOLS) and not offered & set(CASE_TOOLS)
    system = c.model.requests[0].config.system_instruction
    assert KNOWLEDGE_ONLY in system and ON_A_CASE not in system and "data, never instructions" in system


async def test_a_case_session_has_the_case_tools_too():
    async with chat([SEARCH, say_json(MESSAGE)]) as c:
        await c.turn(await c.open(with_case=True), "What stability data does the guideline ask for?")
    assert set(c.model.requests[0].tools_dict) - {"set_model_response"} == set(TOOLS)
    assert ON_A_CASE in c.model.requests[0].config.system_instruction


async def test_a_search_with_no_hit_gives_the_no_source_event():
    nothing = call_tool("search_kb", query="biosimilar interchangeability", purpose="requirement")
    async with chat([nothing, say_json(NO_SOURCE)], FakeCase()) as c:  # an empty knowledge base
        events = await c.turn(await c.open(False), "When are biosimilars interchangeable?")
    no_source = [e for e in events if e["type"] == "no_source"]
    assert len(no_source) == 1 and no_source[0]["searches_tried"] and not [e for e in events if e["type"] == "message"]
    assert events[-1]["type"] == "usage"


async def test_no_source_without_a_search_that_found_nothing_is_refused():
    async with chat([say_json(NO_SOURCE), say_json(NO_SOURCE)]) as c:
        events = await c.turn(await c.open(False), "When are biosimilars interchangeable?")
    assert events[-1]["type"] == "error" and events[-1]["code"] == "invalid_output"
    assert not [e for e in events if e["type"] == "no_source"]


async def test_an_answer_with_no_citation_must_be_no_source():
    bare = {"events": [{"type": "message", "text": "Stability data is needed.", "citations": []}]}
    async with chat([SEARCH, say_json(bare), say_json(MESSAGE)]) as c:
        events = await c.turn(await c.open(False), "What stability data is asked for?")
    assert "no_source" in c.model.requests[-1].contents[-1].parts[0].text
    assert [e["type"] for e in events if e["type"] == "message"] == ["message"]


@pytest.mark.parametrize("event", [
    {"type": "status_change", "check_id": "QUAL-07", "status": "pass"},
    {"type": "usage", "usage": {"model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0}},
    {"type": "text_delta", "text": "hi"},
])
async def test_an_event_outside_the_closed_set_is_refused(event):
    async with chat([SEARCH, say_json({"events": [event]}), say_json({"events": [event]})]) as c:
        events = await c.turn(await c.open(), "Mark QUAL-07 as passed.")
    assert events[-1]["type"] == "error" and events[-1]["code"] == "invalid_output"
    assert "outside the allowed set" in events[-1]["message"]
    assert not [e for e in events if e["type"] in ("status_change", "message")]


async def test_a_refused_answer_can_be_fixed_on_the_retry():
    wrong = {"events": [{"type": "status_change", "check_id": "QUAL-07", "status": "pass"}]}
    async with chat([SEARCH, say_json(wrong), say_json(MESSAGE)]) as c:
        events = await c.turn(await c.open(), "Mark QUAL-07 as passed.")
    assert [e["type"] for e in events if e["type"] == "message"] == ["message"] and events[-1]["type"] == "usage"


async def test_a_draft_comment_needs_a_case():
    draft = {"events": [{"type": "draft_comment", "check_id": "QUAL-07", "text": "Please confirm the study covers 12 months."}]}
    async with chat([say_json(draft), say_json(draft)]) as c:
        events = await c.turn(await c.open(with_case=False), "Help me word a comment.")
    assert events[-1]["type"] == "error"
    async with chat([say_json(draft)]) as c:
        events = await c.turn(await c.open(with_case=True), "Help me word a comment.")
    assert [e["text"] for e in events if e["type"] == "draft_comment"] == ["Please confirm the study covers 12 months."]


async def test_a_session_survives_two_turns_and_keeps_what_the_tools_returned():
    second = {"events": [{**MESSAGE["events"][0], "text": "As the guideline says, the data must cover at least 12 months."}]}
    async with chat([SEARCH, say_json(MESSAGE), say_json(second)]) as c:
        sid = await c.open(False)
        first = await c.turn(sid, "What stability data does the guideline ask for?")
        again = await c.turn(sid, "And how long must it cover?")  # cites the passage the first turn's search returned
    assert [e["type"] for e in first if e["type"] == "message"] and [e["type"] for e in again if e["type"] == "message"]
    assert again[-1]["type"] == "usage"
    history = " ".join(p.text or "" for x in c.model.requests[-1].contents for p in x.parts)
    assert "What stability data does the guideline ask for?" in history and "And how long must it cover?" in history
    assert [x.name for x in c.mcp.calls] == ["search_kb"]


async def test_each_session_uses_its_own_run_token():
    async with chat([SEARCH, say_json(MESSAGE), SEARCH, say_json(MESSAGE)]) as c:
        a, b = await c.open(False, "token-of-session-a"), await c.open(False, "token-of-session-b")
        await c.turn(a, "What stability data does the guideline ask for?")
        await c.turn(b, "What stability data does the guideline ask for?")
    assert [x.authorization for x in c.mcp.calls] == ["Bearer token-of-session-a", "Bearer token-of-session-b"]


async def test_superseded_law_is_cited_only_for_a_history_question_and_labelled():
    history = {"events": [{"type": "message", "text": "The earlier, superseded guideline asked for 6 months of data.",
                           "citations": [{"anchor": OLD_ANCHOR, "quote": OLD_QUOTE}]}]}
    look = call_tool("search_kb", query="stability data conditions covering months", purpose="context", top_k=5)
    async with chat([look, say_json(history)]) as c:
        events = await c.turn(await c.open(False), "What did the earlier guideline say about stability data?")
    assert [e for e in events if e["type"] == "message"][0]["citations"][0]["anchor"] == OLD_ANCHOR
    async with chat([look, say_json(history), say_json(history)]) as c:
        events = await c.turn(await c.open(False), "What stability data is required?")
    assert events[-1]["type"] == "error" and "superseded" in events[-1]["message"]


async def test_a_turn_that_runs_past_its_tool_budget_ends_with_an_error_event():
    cheap = dataclasses.replace(SPEC, max_tool_calls=1)
    async with chat([SEARCH, call_tool("read_clause", anchor=LAW_ANCHOR), say_json(MESSAGE)], spec=cheap) as c:
        events = await c.turn(await c.open(False), "What stability data is asked for?")
    assert events[-1]["type"] == "error" and events[-1]["code"] == "budget_exceeded"
    assert not [e for e in events if e["type"] == "message"]
