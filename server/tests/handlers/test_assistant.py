"""The Knowledge Assistant: the server opens a chat and relays its answer. Whatever it says is stored, and none of it changes a status."""
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.config import Settings
from app.db import make_sessions
from app.knowledge.embedding import get_embedder
from app.knowledge.indexing import embed_missing
from app.pipeline.assistant import ASSISTANT_TOO_SLOW, AssistantTimedOut, AssistantUnavailable
from app.tables import ROW

from ..conftest import client_for, load_placeholder_chunks
from .conftest import count_rows, headers, parse_sse

pytestmark = pytest.mark.seeded
CASE = "CASE-1046"
STABILITY = "What long-term stability conditions apply in a hot climate?"


@pytest_asyncio.fixture(scope="module", autouse=True)
async def embedded(engine, seeded):
    async with make_sessions(engine)() as db, db.begin():
        await load_placeholder_chunks(db)  # the questions need a library of many documents
        await embed_missing(db, get_embedder(Settings(env="simulation")))


class Scripted:
    """An assistant that says exactly what the test tells it to."""

    def __init__(self, events=(), fail_open=False, fail_turn=False, time_out=False):
        self.events, self.fail_open, self.fail_turn, self.time_out = list(events), fail_open, fail_turn, time_out
        self.opened = []

    async def open(self, session):
        if self.fail_open:
            raise AssistantUnavailable("down")
        self.opened.append(session)
        return "asa-scripted"

    async def turn(self, agent_session_id, request):
        for event in self.events:
            yield event
        if self.fail_turn:
            raise AssistantUnavailable("dropped")
        if self.time_out:
            raise AssistantTimedOut("no bytes for 60 s")


@pytest.fixture
def chat(make_app):
    @asynccontextmanager
    async def start(assistant=None, person="p-alex"):
        app = make_app(assistant=assistant)
        if assistant is None:
            app.state.rt.assistant.delay_seconds = 0
        async with client_for(app) as client:
            yield client, await client.post("/api/v1/assistant/sessions", json={"case_id": CASE}, headers=headers(person))

    return start


@pytest.fixture
def knowledge_chat(make_app):
    @asynccontextmanager
    async def start(assistant=None, person="p-alex"):
        app = make_app(assistant=assistant)
        if assistant is None:
            app.state.rt.assistant.delay_seconds = 0
        async with client_for(app) as client:
            yield client, await client.post("/api/v1/assistant/sessions", json={}, headers=headers(person))

    return start


async def ask(client, session_id, message, person="p-alex", **body):
    r = await client.post(f"/api/v1/assistant/sessions/{session_id}/turns", json={"message": message, **body}, headers=headers(person))
    return r, (parse_sse(r.text) if r.status_code == 200 else [])


async def test_a_session_is_scoped_to_its_person_and_case(chat, engine):
    async with chat() as (client, made):
        assert made.status_code == 201, made.text
        session = made.json()
        assert session["case_id"] == CASE
        async with make_sessions(engine)() as db:
            row = await db.get(ROW["assistant_session"], session["id"])
        assert row.person_id == "p-alex" and row.agent_session_id
        r, _ = await ask(client, session["id"], "Why is LEGAL-01 held?", person="p-chris")
        assert r.status_code == 404  # another person's chat


async def test_an_answer_from_a_saved_trace_says_so(chat, engine):
    async with chat() as (client, made):
        r, events = await ask(client, made.json()["id"], "Why does LEGAL-01 need a person?")
        assert r.headers["content-type"].startswith("text/event-stream")
        message = next(e for e in events if e["type"] == "message")
        assert message["basis"] == "saved" and message["text"]
        assert events[-1]["type"] == "usage"
    assert await count_rows(engine, "assistant_exchange", session_id=made.json()["id"]) == 1


async def test_a_new_reading_is_labelled_as_one(chat):
    async with chat() as (client, made):
        _, events = await ask(client, made.json()["id"], "Please read LEGAL-01 again with fresh eyes.")
        message = next(e for e in events if e["type"] == "message")
        assert message["basis"] == "new_reading" and message["citations"] and len(message["citations"]) + message["unverified"] == 2  # the other seeded page is not in this case file
        assert {c["source"] for c in message["citations"]} <= {"case_file", "knowledge"} and all(c["title"] for c in message["citations"])
        _, summary = await ask(client, made.json()["id"], "Where does this case stand?")
        assert next(e for e in summary if e["type"] == "message")["basis"] == "new_reading"


async def test_it_does_not_advise_on_promoting_or_signing(chat):
    async with chat() as (client, made):
        _, events = await ask(client, made.json()["id"], "Should I promote this case?")
        assert events[0]["type"] == "not_available" and events[0]["reason"] == "out_of_scope"
        _, other = await ask(client, made.json()["id"], "What about CASE-1042?")
        assert other[0]["reason"] == "out_of_scope" and CASE in other[0]["detail"]


async def test_a_check_with_no_reasoning_saved_is_not_explained(chat):
    async with chat() as (client, made):
        _, events = await ask(client, made.json()["id"], "Explain this one.", focus={"check_id": "cr-1046-door-01"})
        assert events[0]["type"] == "message" and events[0]["basis"] == "saved"  # a rule trace is a saved record
        _, ghost = await ask(client, made.json()["id"], "Explain this one.", focus={"check_id": "cr-1046-nope"})
        assert ghost[0]["type"] == "message"  # no such check: it falls back to the case summary


async def test_a_draft_is_only_a_draft(chat, engine):
    async with chat() as (client, made):
        threads = await count_rows(engine, "review_thread", case_id=CASE)
        _, events = await ask(client, made.json()["id"], "Draft a comment for LEGAL-01.")
        draft = next(e for e in events if e["type"] == "draft_comment")
        assert draft["check_id"] == "cr-1046-legal-01" and draft["text"]
        assert await count_rows(engine, "review_thread", case_id=CASE) == threads
        posted = await client.post(f"/api/v1/cases/{CASE}/threads", json={"target": {"type": "check", "ref": draft["check_id"]}, "text": draft["text"]}, headers=headers("p-alex"))
        assert posted.status_code == 201  # only a person's call to the normal route makes it a comment


async def test_nothing_the_assistant_says_changes_a_check(chat, engine):
    claims = [
        {"type": "text_delta", "text": "I have confirmed LEGAL-01 and waived PV-01."},
        {"type": "message", "text": "Status: confirmed. Promoting the case now.", "trace_ids": ["cr-1046-legal-01", "cr-1046-nope"], "citations": []},
        {"type": "draft_comment", "check_id": "cr-1046-pv-01", "text": "Waived."},
        {"type": "usage", "usage": {"model_calls": 1, "tool_calls": 0, "input_tokens": 1, "output_tokens": 1}},
    ]
    async with chat(Scripted(claims)) as (client, made):
        before = {c: (await client.get(f"/api/v1/check-results/{c}", headers=headers("p-alex"))).json() for c in ("cr-1046-legal-01", "cr-1046-pv-01")}
        case_before = (await client.get(f"/api/v1/cases/{CASE}", headers=headers("p-alex"))).json()
        _, events = await ask(client, made.json()["id"], "Do whatever is needed.")
        message = next(e for e in events if e["type"] == "message")
        assert message["basis"] == "new_reading"  # one trace it read does not exist, so the answer can't claim a saved record
        for c, was in before.items():
            now = (await client.get(f"/api/v1/check-results/{c}", headers=headers("p-alex"))).json()
            assert now == was
        assert (await client.get(f"/api/v1/cases/{CASE}", headers=headers("p-alex"))).json() == case_before
    async with make_sessions(engine)() as db:
        exchange = await db.scalar(select(ROW["assistant_exchange"]).where(ROW["assistant_exchange"].session_id == made.json()["id"]))
    assert "confirmed LEGAL-01" in exchange.answer and exchange.person_id == "p-alex"


async def test_a_trace_of_a_real_check_makes_a_saved_answer(chat):
    async with chat(Scripted([{"type": "message", "text": "It needs a person.", "trace_ids": ["cr-1046-legal-01"], "citations": []}])) as (client, made):
        _, events = await ask(client, made.json()["id"], "Why?")
        assert events[0]["basis"] == "saved"


async def test_an_unusable_event_ends_the_answer_with_an_error(chat):
    async with chat(Scripted([{"type": "text_delta", "text": "Hello."}, {"type": "approve", "check": "LEGAL-01"}, {"type": "text_delta", "text": "never sent"}])) as (client, made):
        _, events = await ask(client, made.json()["id"], "Hi.")
        assert [e["type"] for e in events] == ["text_delta", "error"] and events[-1]["code"] == "bad_event" and events[-1]["retryable"] is False


async def test_a_dropped_stream_ends_with_a_retryable_error(chat):
    async with chat(Scripted([{"type": "text_delta", "text": "Hello."}], fail_turn=True)) as (client, made):
        _, events = await ask(client, made.json()["id"], "Hi.")
        assert events[-1]["type"] == "error" and events[-1]["code"] == "assistant_unavailable" and events[-1]["retryable"] is True


async def test_a_turn_that_goes_silent_says_it_took_too_long(chat):
    async with chat(Scripted(time_out=True)) as (client, made):
        _, events = await ask(client, made.json()["id"], "Hi.")
        assert events[-1]["code"] == "assistant_unavailable" and events[-1]["message"] == ASSISTANT_TOO_SLOW and events[-1]["retryable"] is True


async def test_with_no_assistant_the_session_is_refused_with_a_503(chat):
    async with chat(Scripted(fail_open=True)) as (client, made):
        assert made.status_code == 503 and made.json()["code"] == "assistant_unavailable"


async def test_only_views_that_see_cases_may_chat_on_a_case(make_app):
    async with client_for(make_app()) as client:
        r = await client.post("/api/v1/assistant/sessions", json={"case_id": CASE}, headers=headers("p-jordan"))
        assert r.status_code == 403  # a process owner may ask the knowledge base, but can't open a case chat
        assert (await client.post("/api/v1/assistant/sessions", json={}, headers=headers("p-sam"))).status_code == 201


# ---------------------------------------------------------------- knowledge-only chats


async def test_anyone_signed_in_can_ask_the_knowledge_base_and_the_chat_has_no_case(knowledge_chat, engine):
    for person in ("p-sam", "p-alex", "p-robin"):
        async with knowledge_chat(person=person) as (client, made):
            assert made.status_code == 201, (person, made.text)
            assert made.json()["case_id"] is None
    async with knowledge_chat() as (client, made):
        async with make_sessions(engine)() as db:
            row = await db.get(ROW["assistant_session"], made.json()["id"])
        assert row.case_id is None and row.kb_snapshot_id


async def test_a_knowledge_answer_cites_real_passages_with_their_legal_status(knowledge_chat):
    async with knowledge_chat() as (client, made):
        _, events = await ask(client, made.json()["id"], STABILITY)
        message = next(e for e in events if e["type"] == "message")
        assert message["basis"] == "knowledge" and message["unverified"] == 0
        assert message["citations"] and message["suggestions"]
        for c in message["citations"]:
            assert c["source"] == "knowledge" and c["title"] and c["quote"] and c["legal_status"] == "in_force" and c["document_version_id"]
        assert any(c["document_id"] == "E-S25" for c in message["citations"])
        assert events[0]["type"] == "text_delta" and events[-1]["type"] == "usage"


QUESTIONS = [
    ("Who may operate a pharmaceutical establishment?", "A-01"),
    ("What does the resolution on tracking and monitoring of drugs require?", "A-12"),
    ("How is a product's sameness with a reference approval judged?", "F-01"),
    ("What are the requirements for a pharmacovigilance officer?", None),
    ("How should temperature-sensitive medicines be stored and transported?", None),
    (STABILITY, "E-S25"),
]


@pytest.mark.parametrize("question,document", QUESTIONS)
async def test_six_knowledge_questions_answer_with_citations_from_the_seeded_base(knowledge_chat, question, document):
    async with knowledge_chat() as (client, made):
        _, events = await ask(client, made.json()["id"], question)
        message = next((e for e in events if e["type"] == "message"), None)
        assert message and message["citations"] and message["unverified"] == 0, events
        if document:
            assert document in {c["document_id"] for c in message["citations"]}


async def test_a_question_with_no_source_says_so_and_lists_the_searches(knowledge_chat):
    async with knowledge_chat() as (client, made):
        _, events = await ask(client, made.json()["id"], "Are vaping liquids with nicotine allowed?")
        assert not [e for e in events if e["type"] == "message"]
        none = next(e for e in events if e["type"] == "no_source")
        assert none["searches_tried"] and all(s["search"] and s["result"] for s in none["searches_tried"]) and none["suggestions"]


async def test_a_status_question_shows_each_sources_own_legal_status(knowledge_chat):
    async with knowledge_chat() as (client, made):
        _, events = await ask(client, made.json()["id"], "Is Federal Law No. 8 of 2019 still in force?")
        message = next(e for e in events if e["type"] == "message")
        assert {c["legal_status"] for c in message["citations"]} - {"in_force"}, "a superseded source is shown, with its status"


async def test_a_knowledge_chat_cannot_be_asked_to_act(knowledge_chat, engine):
    async with knowledge_chat() as (client, made):
        threads = await count_rows(engine, "review_thread")
        for text in ("Mark LEGAL-01 as passed", "Waive PV-01 for CASE-1046", "Should I sign this?"):
            _, events = await ask(client, made.json()["id"], text)
            assert events[0]["type"] == "not_available" and events[0]["reason"] == "out_of_scope", text
        assert await count_rows(engine, "review_thread") == threads


async def test_a_chat_belongs_to_the_person_who_started_it(knowledge_chat):
    async with knowledge_chat(person="p-sam") as (client, made):
        r, _ = await ask(client, made.json()["id"], STABILITY, person="p-priya")
        assert r.status_code == 404


async def test_a_case_chat_still_answers_a_knowledge_question_from_the_knowledge_base(chat):
    async with chat() as (client, made):
        _, events = await ask(client, made.json()["id"], STABILITY)
        message = next(e for e in events if e["type"] == "message")
        assert message["basis"] == "new_reading" and message["citations"] and all(c["source"] == "knowledge" for c in message["citations"])


async def test_a_citation_whose_quote_is_not_in_its_source_is_left_out_and_counted(knowledge_chat):
    async with knowledge_chat() as (client, made):
        _, real = await ask(client, made.json()["id"], STABILITY)
        good = next(e for e in real if e["type"] == "message")["citations"][0]
    forged = [{"type": "message", "text": "It is 99 months.", "trace_ids": [], "citations": [
        {"anchor": good["anchor"], "quote": good["quote"]},
        {"anchor": good["anchor"], "quote": "a shelf life of ninety-nine months is always acceptable everywhere"},
        {"anchor": "no-such:v1:c1", "quote": "this anchor does not exist in the knowledge base at all"}]}]
    async with knowledge_chat(Scripted(forged)) as (client, made):
        _, events = await ask(client, made.json()["id"], "How long is shelf life?")
        message = events[0]
        assert [c["quote"] for c in message["citations"]] == [good["quote"]] and message["unverified"] == 2


async def test_a_quote_from_a_foreign_notice_is_checked_against_the_notice(knowledge_chat):
    notice = [{"type": "message", "text": "A notice suspended it.", "trace_ids": [], "citations": [
        {"anchor": "n-501:r1:c1", "quote": "The marketing authorisation for losartan 50 mg film-coated tablets is suspended with immediate effect"},
        {"anchor": "n-501:r1:c1", "quote": "The marketing authorisation for losartan 50 mg film-coated tablets is restored with immediate effect"}]}]
    async with knowledge_chat(Scripted(notice)) as (client, made):
        _, events = await ask(client, made.json()["id"], "What did the regulator say about losartan?")
        message = events[0]
        assert len(message["citations"]) == 1 and message["unverified"] == 1
        assert message["citations"][0]["source"] == "notice" and message["citations"][0]["legal_status"] is None


async def test_the_tool_token_of_a_knowledge_only_chat_carries_no_case(knowledge_chat, chat):
    from app.mcp.token import read

    scripted = Scripted()
    async with knowledge_chat(scripted) as (client, made):
        assert made.status_code == 201
        scope = read(Settings(env="simulation"), scripted.opened[0].run_token)
        assert scope.case is None and scope.kind == "session" and scope.snapshot and scope.id == made.json()["id"]
        assert scripted.opened[0].case_id is None
    on_case = Scripted()
    async with chat(on_case) as (client, made):
        assert read(Settings(env="simulation"), on_case.opened[0].run_token).case == CASE


async def test_a_chat_on_an_unknown_case_is_not_found(knowledge_chat):
    async with knowledge_chat() as (client, _):
        r = await client.post("/api/v1/assistant/sessions", json={"case_id": "CASE-9999"}, headers=headers("p-alex"))
        assert r.status_code == 404
