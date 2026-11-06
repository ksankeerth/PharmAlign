"""The demo library, as loaded for the screens: synthetic chunks with vectors from the deterministic embedder."""
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import make_sessions
from app.jobs import Worker
from app.knowledge.embedding import get_embedder
from app.knowledge.indexing import embed_missing
from app.knowledge.retrieval import QUESTION_SETS, for_country
from app.knowledge.search import agent_search
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files, truncate_all
from pharmalign_contracts.server import models as m

from ..conftest import START, TENANT, TEST_URL, as_person, client_for

pytestmark = pytest.mark.seeded
EMBEDDER = get_embedder(Settings(env="simulation"))


@pytest_asyncio.fixture(scope="module", autouse=True)
async def seeded(engine):
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)
        await embed_missing(db, EMBEDDER)


@pytest_asyncio.fixture
async def http(make_app):
    app = make_app()
    async with client_for(app) as client:
        client.app = app
        yield client


JORDAN, SAM = as_person("p-jordan"), as_person("p-sam")


async def ask(http, query, **body):
    r = await http.post("/api/v1/kb-search", json={"query": query, **body}, headers=JORDAN)
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_question_with_no_in_force_source_shows_no_source(http):
    result = await ask(http, "child-resistant closure for oral liquids")
    assert result["no_source_found"] is True and result["hits"] == []
    assert result["searches_tried"]


async def test_a_requirement_question_with_a_real_source_lists_hits_with_their_legal_status(http):
    result = await ask(http, "stability testing long-term conditions")
    assert result["no_source_found"] is False
    assert {h["legal_status"] for h in result["hits"]} == {"in_force"}
    assert "E-S25" in {h["document_id"] for h in result["hits"]}


async def test_superseded_law_is_only_in_the_context_search(http):
    query = "pharmaceutical establishment licence competent authority"
    assert "A-11" not in {h["document_id"] for h in (await ask(http, query, top_k=50))["hits"]}
    history = await ask(http, query, purpose="context", top_k=50)
    assert {h["legal_status"] for h in history["hits"] if h["document_id"] == "A-11"} == {"superseded"}


async def test_the_readiness_run_computes_the_retrieval_result_from_a_real_search(http):
    started = await http.post("/api/v1/workspaces/ws-ae/readiness-runs", headers=JORDAN)
    assert started.status_code == 202
    http.app.state.rt.clock.advance(seconds=5)
    await Worker(http.app.state.rt).run_due()
    run = (await http.get("/api/v1/workspaces/ws-ae/readiness", headers=JORDAN)).json()
    check = next(c for c in run["checks"] if c["key"] == "retrieval")
    # The demo A-03 chunks are placeholders, so the fee question finds nothing: 3 of 4 is under the pass line. Real text is seeded with --with-library.
    assert check["status"] == "fail" and check["detail"].startswith("Document hit at 5: 0.75 on 5 questions (embedder: hash-256).") and "needs 0.80" in check["detail"]


async def test_a_retrieval_test_run_reports_ranks(http):
    snapshot = (await http.get("/api/v1/workspaces/ws-ae/kb-snapshots", headers=JORDAN)).json()["items"][0]["id"]
    run = (await http.post("/api/v1/retrieval-tests", json={"snapshot_id": snapshot, "question_set": "core-questions"}, headers=JORDAN)).json()
    assert run["status"] == "running"
    http.app.state.rt.clock.advance(seconds=5)
    await Worker(http.app.state.rt).run_due()
    done = (await http.get(f"/api/v1/retrieval-tests/{run['id']}", headers=JORDAN)).json()
    assert done["status"] == "completed" and len(done["results"]) == 5
    assert [r for r in done["results"] if r["expected_document_id"] is None][0]["no_source_found"] is True


async def test_a_version_that_failed_the_gate_is_refused_for_a_full_run(http):
    r = await http.post("/api/v1/document-versions/B-S17-v1/ingest-jobs", json={"mode": "full", "profile": {"preset": "custom", "parser": "fast_text", "split_by": "heading"}}, headers=JORDAN)
    assert r.status_code == 409
    assert "The quality gate failed on the last trial. Change the options and run a trial again." in r.text


async def test_a_trial_on_a_demo_version_reads_the_chunks_it_has(http):
    profile = {"preset": "scanned", "parser": "best_layout", "split_by": "heading"}
    r = await http.post("/api/v1/document-versions/B-S01-v1/ingest-jobs", json={"mode": "trial", "profile": profile}, headers=JORDAN)
    assert r.status_code == 202, r.text
    http.app.state.rt.clock.advance(seconds=1)
    await Worker(http.app.state.rt).run_due()
    job = (await http.get(f"/api/v1/ingest-jobs/{r.json()['id']}", headers=JORDAN)).json()
    assert job["status"] == "completed"
    assert (await http.get("/api/v1/document-versions/B-S01-v1/quality", headers=JORDAN)).json()["status"] in ("pass", "attention")


async def test_a_missing_required_slot_creates_a_gap_candidate(http):
    slots = (await http.get("/api/v1/workspaces/ws-ae/slots", headers=JORDAN)).json()["items"]
    slot = next(s for s in slots if s["status"] != "missing")
    r = await http.patch(f"/api/v1/slots/{slot['id']}", json={"status": "missing", "notes": "Not found."}, headers=JORDAN)
    assert r.status_code == 200 and r.json()["gap_id"]


async def test_an_agent_searches_only_through_the_views_of_its_pinned_snapshot():
    url = make_url(TEST_URL).set(username="pharmalign_agent", password="pharmalign_agent")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                await conn.execute(text("SELECT count(*) FROM chunk_embedding"))
            await conn.rollback()
            await conn.execute(text("SELECT set_config('app.tenant_id', 'ws-ae', true), set_config('app.case_id', 'CASE-1042', true)"))
            found = await agent_search(conn, "stability testing long-term conditions", EMBEDDER)
            assert found.hits and {h.legal_status for h in found.hits} == {"in_force"}
            old = await agent_search(conn, "pharmaceutical establishment licence competent authority", EMBEDDER, top_k=50)
            assert "A-11" not in {h.document_id for h in old.hits}
            await conn.execute(text("SELECT set_config('app.case_id', '', true)"))  # no case, nothing visible
            nothing = await agent_search(conn, "stability testing long-term conditions", EMBEDDER)
            assert nothing.no_source_found is True
    finally:
        await engine.dispose()


def slot(key: str, document_id: str | None) -> m.Slot:
    return m.Slot(id=f"sl-{key}", key=key, label=key, importance="must", status="filled" if document_id else "missing", document_id=document_id)


def test_a_linked_slot_swaps_the_document_a_question_asks_for_and_clears_the_passage():
    core = QUESTION_SETS["core-questions"]
    asked = {q.slot: q for q in for_country(core, [slot("medicines_law", "DOC-own-law")])}["medicines_law"]
    assert asked.document_id == "DOC-own-law" and asked.passage == ()
    assert [q for q in for_country(core, [slot("medicines_law", "DOC-own-law")]) if q.slot != "medicines_law"] == [q for q in core if q.slot != "medicines_law"]


def test_an_unlinked_slot_keeps_the_default_document():
    core = QUESTION_SETS["core-questions"]
    assert for_country(core, [slot("medicines_law", None), slot("fees_schedule", None)]) == core
    assert for_country(core, []) == core


async def test_the_uaes_own_slots_leave_the_questions_unchanged(http):
    slots = (await http.get("/api/v1/workspaces/ws-ae/slots?limit=100", headers=JORDAN)).json()["items"]
    assert any(s["document_id"] for s in slots)
    assert for_country(QUESTION_SETS["core-questions"], [m.Slot.model_validate(s) for s in slots]) == QUESTION_SETS["core-questions"]
