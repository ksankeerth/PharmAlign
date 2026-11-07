"""Who may call what: tool lists, tokens, case and tenant scope, the call log, and the agent login's lack of write rights."""
import base64
import json
from datetime import timedelta

import pytest
from pharmalign_contracts.agents.knowledge_assistant import KNOWLEDGE_TOOLS, TOOLS as ASSISTANT_TOOLS
from pharmalign_contracts.agents.run_agents import RUN_AGENTS
from sqlalchemy import text

from app.config import Settings
from app.db import make_sessions
from app.main import UnsafeConfiguration, create_app
from app.mcp.server import CONTRACT_TOOLS, agent_database_url, not_built
from app.mcp.token import BadToken, read, run_token as make_run_token
from app.mcp.tools import TOOLS
from app.pipeline.records import RunWork
from app.stores import STORES

from ..conftest import START, TENANT, TEST_URL
from .conftest import (CASE, OTHER_CASE, SA_CASE, SA_SNAPSHOT, SA_TENANT, SNAPSHOT, add_run, call, connect, http, run_token, session_token,
                       system, token)

pytestmark = pytest.mark.seeded
CONTRACT = {a.name: a.tools for a in RUN_AGENTS}
INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


async def tool_names(app, tok) -> list[str]:
    async with connect(app, tok) as s:
        return [t.name for t in (await s.list_tools()).tools]


# ---------------------------------------------------------------- tool lists

@pytest.mark.parametrize("agent", ["dossier-reader", "checklist-assessor", "sameness", "challenger", "coordinator"])
async def test_a_case_agent_sees_exactly_the_tools_its_contract_names(app, engine, agent):
    assert await tool_names(app, await run_token(app, engine, agent)) == list(CONTRACT[agent])


@pytest.mark.parametrize("agent", ["reliance-watch", "gap-analyst"])
async def test_a_workspace_agent_with_no_case_sees_exactly_the_tools_its_contract_names(app, engine, agent):
    assert await tool_names(app, await run_token(app, engine, agent, case=None)) == list(CONTRACT[agent])


def test_every_agent_in_the_contracts_is_covered():
    assert set(CONTRACT) == {"dossier-reader", "checklist-assessor", "sameness", "challenger", "coordinator", "reliance-watch", "gap-analyst"}


async def test_a_case_chat_sees_exactly_the_tools_its_contract_names(app, engine):
    assert await tool_names(app, await session_token(app, engine)) == list(ASSISTANT_TOOLS)


async def test_a_knowledge_only_chat_sees_only_the_knowledge_tools(app, engine):
    names = await tool_names(app, await session_token(app, engine, case=None))
    assert names == list(KNOWLEDGE_TOOLS) and {"search_kb", "get_doc_status"} <= set(names)
    assert not {"get_facts", "get_check", "read_page", "draft_comment", "get_run_trace"} & set(names)


def test_every_tool_the_contracts_name_is_built():
    assert not_built() == {}
    assert set(TOOLS) == {t for tools in CONTRACT_TOOLS.values() for t in tools}, "every built tool is named by some contract"


def test_no_tool_takes_a_tenant_or_a_case():
    for t in TOOLS.values():
        fields = set(t.args.model_fields)
        assert not fields & {"tenant", "tenant_id", "workspace_id", "case", "case_id", "snapshot_id", "run_id"}, t.name


async def test_a_tool_outside_the_agents_list_is_refused(app, engine):
    async with connect(app, await run_token(app, engine, "dossier-reader")) as s:
        r = await call(s, "search_kb", query="stability testing long-term conditions")
        assert r.error and r["code"] == "not_allowed"
        r = await call(s, "drop_everything")
        assert r.error and r["code"] == "not_allowed"


# ---------------------------------------------------------------- case and tenant scope

async def test_a_token_for_one_case_cannot_read_another_cases_documents_by_any_tool(app, engine):
    async with connect(app, await run_token(app, engine, "checklist-assessor", case=OTHER_CASE)) as s:
        assert (await call(s, "read_page", doc_id="d-1042-17", page=2))["code"] == "not_found"
        assert (await call(s, "read_table", doc_id="d-1042-17", page=2))["code"] == "not_found"
        hits = (await call(s, "search_dossier", query="shelf life 36 months proposed"))["hits"]
        assert not [h for h in hits if h["doc_id"].startswith("d-1042")]
        r = await call(s, "verify_quote", anchor="d-1042-17:v1:p2", quote="a shelf life of 36 months is proposed when stored below 30 C")
        assert r["verified"] is False and r["rule"] == "anchor_not_found"
        facts = (await call(s, "get_facts"))["facts"]
        assert not [e for f in facts for e in f["evidence"] if e["anchor"].startswith("d-1042")]
    async with connect(app, await session_token(app, engine, case=OTHER_CASE)) as s:
        assert (await call(s, "read_page", doc_id="d-1042-17", page=2))["code"] == "not_found"
    async with connect(app, await run_token(app, engine, "dossier-reader", case=OTHER_CASE)) as s:
        docs = (await call(s, "list_documents"))["documents"]
        assert docs and not [d for d in docs if d["doc_id"].startswith("d-1042")]


async def test_another_tenants_token_cannot_read_this_tenants_documents(app, engine):
    async with connect(app, await run_token(app, engine, "checklist-assessor", case=SA_CASE, tenant=SA_TENANT, snapshot=SA_SNAPSHOT)) as s:
        assert (await call(s, "read_page", doc_id="d-1042-17", page=2))["code"] == "not_found"
        assert (await call(s, "verify_quote", anchor="d-1042-17:v1:p2", quote="a shelf life of 36 months is proposed when stored below 30 C"))["verified"] is False
        assert not [h for h in (await call(s, "search_dossier", query="shelf life 36 months"))["hits"] if h["doc_id"].startswith("d-1042")]
    # A token can't name another tenant's case either: the server checks the case belongs to the token's tenant.
    run_id = await add_run(app.state.rt.engine, "checklist-assessor", case=CASE)
    async with connect(app, token(app, run_id, "checklist-assessor", tenant=SA_TENANT, snapshot=SA_SNAPSHOT)) as s:
        r = await call(s, "read_page", doc_id="d-1042-17", page=2)
        assert r.error and r["code"] == "unknown_case"


# ---------------------------------------------------------------- tokens

def test_a_run_token_carries_its_scope_and_rejects_any_change():
    settings = Settings(env="simulation")
    tok = make_run_token(settings, run_id="r-1", agent="dossier-reader", tenant=TENANT, case_id=CASE, snapshot_id=SNAPSHOT, max_calls=20,
                         expires_at=START + timedelta(minutes=5))
    scope = read(settings, tok)
    assert (scope.kind, scope.tenant, scope.case, scope.agent, scope.id, scope.snapshot, scope.calls) == ("run", TENANT, CASE, "dossier-reader", "r-1", SNAPSHOT, 20)
    assert scope.expires_at == START + timedelta(minutes=5)
    with pytest.raises(BadToken):
        read(Settings(env="simulation", run_token_secret="another"), tok)


def _tampered(tok: str) -> str:
    prefix, claims, signature = tok.split(".")
    body = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
    body["case"] = OTHER_CASE
    return ".".join([prefix, base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode(), signature])


async def test_a_tampered_or_missing_token_is_refused_before_mcp(app, engine):
    good = await run_token(app, engine, "dossier-reader")
    for tok in (_tampered(good), "", "not-a-token", good[:-4] + "AAAA"):
        async with http(app, tok) as client:
            r = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
            assert r.status_code == 401, tok
            assert r.json()["error"] == "invalid_token"


async def test_expired_wrong_agent_superseded_cancelled_and_finished_tokens_are_refused(app, engine):
    db = app.state.rt.engine
    expired = await run_token(app, engine, "dossier-reader", expires_in=timedelta(seconds=-1))
    wrong_agent = token(app, await add_run(db, "checklist-assessor"), "coordinator")
    superseded = token(app, await add_run(db, "dossier-reader", is_current=False), "dossier-reader")
    cancelled = token(app, await add_run(db, "dossier-reader", status="cancelled"), "dossier-reader")
    finished = token(app, "case-1042-dossier-reader-g1-a1-13", "dossier-reader")  # completed a day before the clock
    for tok, code in ((expired, "expired"), (wrong_agent, "wrong_agent"), (superseded, "superseded"), (cancelled, "cancelled"), (finished, "finished")):
        async with connect(app, tok) as s:
            r = await call(s, "list_documents")
            assert r.error and r["code"] == code
            with pytest.raises(Exception):
                await s.list_tools()


async def test_only_workspace_agents_may_run_with_no_case(app, engine):
    async with connect(app, await run_token(app, engine, "checklist-assessor", case=None)) as s:
        r = await call(s, "search_kb", query="stability testing long-term conditions")
        assert r.error and r["code"] == "needs_case"


async def test_a_workspace_run_reads_no_case_file_and_a_case_run_sees_only_its_own_reliance(app, engine):
    async with connect(app, await run_token(app, engine, "reliance-watch", case=None)) as s:
        relied = (await call(s, "get_relied_on"))["relied_on"]
        assert {r["target_type"] for r in relied} >= {"case", "licence"}
        assert (await call(s, "read_notice"))["code"] == "invalid_arguments"
    async with connect(app, await run_token(app, engine, "reliance-watch", case=OTHER_CASE)) as s:
        relied = (await call(s, "get_relied_on"))["relied_on"]
        assert all(r["target_type"] == "case" and r["target_id"] == OTHER_CASE for r in relied)
        assert (await call(s, "get_licence", licence_id="lic-0007"))["code"] == "not_found"  # another medicine's licence


# ---------------------------------------------------------------- knowledge-only sessions

async def test_a_knowledge_only_session_reads_knowledge_but_no_case_file(app, engine):
    tok = await session_token(app, engine, case=None)
    async with connect(app, tok) as s:
        clause = await call(s, "read_clause", document_id="E-S25", article="3.2.6.1")
        assert not clause.error and clause["clauses"] and all(c["legal_status"] for c in clause["clauses"])
        term = await call(s, "lookup_term", term="long-term")
        assert not term.error and term["passages"]
        for tool, args in (("read_page", {"doc_id": "d-1042-17", "page": 2}), ("get_facts", {}), ("get_findings", {}), ("get_check", {"check_id": "QUAL-07"})):
            r = await call(s, tool, **args)
            assert r.error and r["code"] == "not_allowed", tool  # a knowledge-only chat is not even offered the case tools
        search = await call(s, "search_kb", query="stability testing long-term conditions")
        assert not search.error and search["hits"] and all(h["legal_status"] == "in_force" for h in search["hits"])
        status = await call(s, "get_doc_status", document_id="E-S25")
        assert not status.error and status["versions"]
        assert (await call(s, "draft_comment", text="x"))["code"] == "not_allowed"  # a case tool, not a knowledge one


async def test_a_knowledge_only_scope_searches_the_active_snapshot_through_the_views(app):
    tools = app.state.tools
    async with tools.agent_engine.connect() as conn, conn.begin():
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.case_id', '', true), set_config('app.snapshot_id', :s, true)"),
                           {"t": TENANT, "s": SNAPSHOT})
        from app.mcp.tools import Limits, SearchKb
        found = await TOOLS["search_kb"].run(conn, SearchKb(query="stability testing long-term conditions"), Limits(50, tools.embedder, START))
        assert found["no_source_found"] is False and {h["legal_status"] for h in found["hits"]} == {"in_force"}
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.dossier_page")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.case_fact")) == 0


# ---------------------------------------------------------------- the call log

async def log_rows(engine, run_id: str) -> list:
    async with make_sessions(engine)() as db:
        return (await db.execute(text("SELECT tool, outcome, agent, args_hash, result_hash, result_chars, ms, hits, case_id, tenant_id FROM agent_tool_log "
                                      "WHERE run_id = :r ORDER BY at, outcome"), {"r": run_id})).mappings().all()


async def test_every_call_is_logged_including_refused_ones(app, engine):
    run_id = await add_run(engine, "dossier-reader")
    async with connect(app, token(app, run_id, "dossier-reader")) as s:
        await call(s, "list_documents")
        await call(s, "search_kb", query="secret words")
        await call(s, "read_page", doc_id="d-1042-17")
        await call(s, "read_page", doc_id="d-404", page=1)
    async with connect(app, token(app, run_id, "dossier-reader", expires_in=timedelta(seconds=-1))) as s:
        await call(s, "list_documents")
    rows = await log_rows(engine, run_id)
    assert sorted(r["outcome"] for r in rows) == sorted(["ok", "refused:not_allowed", "error:invalid_arguments", "error:not_found", "refused:expired"])
    assert {r["agent"] for r in rows} == {"dossier-reader"} and {r["case_id"] for r in rows} == {CASE} and {r["tenant_id"] for r in rows} == {TENANT}
    assert all(r["args_hash"].startswith("sha256:") and r["result_chars"] > 0 and r["ms"] is not None for r in rows)
    assert "secret" not in json.dumps([dict(r) for r in rows])  # hashes, never content
    assert next(r for r in rows if r["outcome"] == "ok")["hits"] > 0


async def test_a_knowledge_only_session_logs_without_a_case(app, engine):
    tok = await session_token(app, engine, case=None)
    async with connect(app, tok) as s:
        await call(s, "lookup_term", term="shelf life")
    rows = await log_rows(engine, read(app.state.rt.settings, tok).id)
    assert [(r["outcome"], r["case_id"]) for r in rows] == [("ok", None)]


# ---------------------------------------------------------------- the agent login can't write

WRITES = [
    "INSERT INTO party (id, tenant_id, kind, doc) VALUES ('x', 'ws-ae', 'applicant', '{}')",
    "UPDATE \"case\" SET status = 'closed'",
    "UPDATE check_result SET status = 'pass'",
    "UPDATE fact SET status = 'found'",
    "INSERT INTO dossier_chunk (id, tenant_id, case_id, document_id, position, suspect, text, text_hash) VALUES ('x', 'ws-ae', 'CASE-1042', 'd-1042-17', 1, false, 'x', 'x')",
    "UPDATE agent_run SET is_current = true",
    "INSERT INTO audit_event (id) VALUES ('x')",
    "UPDATE chunk SET text = 'x'",
    "UPDATE agent_tool_log SET outcome = 'ok'",
    "DELETE FROM agent_tool_log",
    "UPDATE agent_read.case_fact SET status = 'found'",
    "INSERT INTO agent_read.dossier_page (id) VALUES ('x')",
]


@pytest.mark.parametrize("sql", WRITES)
async def test_the_agent_login_cannot_write_any_other_table(app, sql):
    async with app.state.tools.agent_engine.connect() as conn:
        await conn.execute(text("SELECT set_config('app.tenant_id', 'ws-ae', false), set_config('app.case_id', 'CASE-1042', false)"))
        with pytest.raises(Exception, match="permission denied|cannot (insert|update|delete)|not updatable|read-only"):
            await conn.execute(text(sql))
        await conn.rollback()


async def test_a_tool_reads_in_a_read_only_transaction(app):
    async with app.state.tools.agent_engine.connect() as conn, conn.begin():
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        with pytest.raises(Exception, match="read-only"):
            await conn.execute(text("INSERT INTO agent_tool_log (id, tenant_id, run_id, tool, args_hash, outcome) VALUES ('x', 'ws-ae', 'r', 't', 'h', 'ok')"))


# ---------------------------------------------------------------- mounting

def test_the_tool_server_is_off_unless_enabled_and_on_in_simulation():
    assert Settings(env="simulation").mcp_enabled is True
    assert Settings(env="live").mcp_enabled is False
    with pytest.raises(UnsafeConfiguration):
        create_app(settings=Settings(env="live", database_url=TEST_URL, mcp_enabled=True, worker_enabled=False))


async def test_mcp_is_not_mounted_when_disabled(make_app):
    app = make_app(settings={"mcp_enabled": False})
    assert app.state.tools is None
    async with http(app, "x") as client:
        assert (await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)).status_code in (404, 405)


def test_the_agent_login_defaults_to_the_development_role_in_simulation():
    url = agent_database_url(Settings(env="simulation", database_url=TEST_URL))
    assert url.startswith("postgresql+asyncpg://pharmalign_agent:pharmalign_agent@")


# ---------------------------------------------------------------- a check's documents

async def add_assessor_run(engine, source: dict, supporting: list | None = None, moved: dict | None = None) -> str:
    """An assessor run with the request the runner stores for it."""
    run_id = await add_run(engine, "checklist-assessor")
    request = {"check": {"id": "LEGAL-01", "source": source, "supporting_sources": supporting or []}, "moved_source": moved}
    async with make_sessions(engine)() as db, db.begin():
        STORES["agent_run_work"].add(system(db), RunWork(run_id=run_id, agent="checklist-assessor", kind="check", case_id=CASE, pipeline_run_id="pr-test",
                                                         generation=1, attempt=1, check_id="LEGAL-01", request=request, next_poll_at=START,
                                                         deadline_at=START + timedelta(hours=1)))
    return run_id


async def test_an_assessor_run_reads_only_its_checks_documents(app, engine):
    run_id = await add_assessor_run(engine, {"doc_id": "A-01", "clause": "Article (6)"})
    async with connect(app, token(app, run_id, "checklist-assessor")) as s:
        hits = (await call(s, "search_kb", query="The Applicant shall be a Pharmaceutical Establishment licensed as a Marketing Office", top_k=10))["hits"]
        assert hits and {h["document_id"] for h in hits} == {"A-01"}
        refused = await call(s, "search_kb", query="required documents", document_ids=["B-01"])
        assert refused.error and refused["code"] == "outside_check_sources"
        assert (await call(s, "read_clause", document_id="B-01", article="Required documents"))["code"] == "outside_check_sources"
        assert (await call(s, "read_clause", document_id="A-01", article="Article (6)"))["clauses"]
        assert all(p["document_id"] == "A-01" for p in (await call(s, "lookup_term", term="applicant"))["passages"])


async def test_supporting_sources_widen_the_scope_and_other_agents_are_not_scoped(app, engine):
    run_id = await add_assessor_run(engine, {"doc_id": "A-01", "clause": "Article (6)"}, [{"doc_id": "B-01", "clause": "Required documents"}])
    async with connect(app, token(app, run_id, "checklist-assessor")) as s:
        assert (await call(s, "read_clause", document_id="B-01", article="Required documents"))["clauses"]
    async with connect(app, await run_token(app, engine, "challenger")) as s:
        assert (await call(s, "read_clause", document_id="B-01", article="Required documents"))["clauses"]


async def test_a_check_moved_to_other_law_reads_that_law_and_not_the_old_one(app, engine):
    run_id = await add_assessor_run(engine, {"doc_id": "A-01", "clause": "Article (6)"}, moved={"doc_id": "B-01", "clause": "Whole document"})
    async with connect(app, token(app, run_id, "checklist-assessor")) as s:
        assert (await call(s, "read_clause", document_id="A-01", article="Article (6)"))["code"] == "outside_check_sources"
        assert (await call(s, "read_clause", document_id="B-01", article="Required documents"))["clauses"]
