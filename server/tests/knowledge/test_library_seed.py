"""The library seed: real files go in through the server's own ingestion, as new versions, with a new active snapshot."""
import pytest
from sqlalchemy import text

from app.config import Settings
from app.db import make_sessions
from app.knowledge.embedding import get_embedder
from app.knowledge.indexing import embed_missing
from app.knowledge.retrieval import QUESTION_SETS
from app.knowledge.search import Scope, search
from app.pipeline.assistant_replay import Knowledge, knowledge_answer
from app.seed import load_files, truncate_all
from app.seed_library import LIBRARY_DIR, find_files, load_library

from ..conftest import START, TENANT, TEST_URL, load_placeholder_chunks

pytestmark = pytest.mark.seeded
SETTINGS = Settings(env="simulation", database_url=TEST_URL, service_token="t", worker_enabled=False)
EMBEDDER = get_embedder(SETTINGS)


def fixture_law(path):
    path.write_text("\n\n".join(
        f"Article {i} Pharmaceutical establishments\n\nA pharmaceutical establishment may operate only under a licence from the competent authority, "
        f"and the licence names the person responsible for article {i}. The holder must keep records for five years." for i in range(1, 6)))
    return path


async def fresh(engine):
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_placeholder_chunks(db, with_licence_paraphrase=False)  # the official text is what should answer, not a paraphrase of the excerpt
        await embed_missing(db, EMBEDDER)
    async with engine.connect() as conn:
        cases = (await conn.execute(text("SELECT id, doc ->> 'kb_snapshot_id' FROM \"case\" ORDER BY id"))).all()
        old = (await conn.execute(text("SELECT count(*) FROM kb_snapshot_item WHERE snapshot_id = 'snap-ae-14'"))).scalar_one()
    return cases, old


async def test_a_file_becomes_a_new_adopted_version_in_a_new_active_snapshot_the_demo_cases_move_to_it_and_the_cited_excerpt_stays_in_force(engine, tmp_path):
    cases, old_items = await fresh(engine)
    lines: list[str] = []
    result = await load_library(SETTINGS, {"A-01": fixture_law(tmp_path / "a01.txt"), "A-03": None}, say=lines.append)

    assert result["ingested"]["A-01"] > 0
    async with engine.connect() as conn:
        async def one(sql, **p):
            return (await conn.execute(text(sql), p)).all()

        assert (await one("SELECT latest_version_id FROM document WHERE id = 'A-01'")) == [("A-01-v2",)]
        assert (await one("SELECT doc ->> 'review_status' FROM document_version WHERE id = 'A-01-v2'"))[0][0] == "approved"
        reviewer = (await one("SELECT doc ->> 'reviewed_by' FROM document_review WHERE document_version_id = 'A-01-v2'"))[0][0]
        assert reviewer != (await one("SELECT doc ->> 'added_by' FROM document_version WHERE id = 'A-01-v2'"))[0][0]
        status = dict(await one("SELECT document_version_id, legal_status FROM tenant_document_adoption WHERE tenant_id = 'ws-ae' AND document_version_id IN ('A-01-v1', 'A-01-v2')"))
        # Seeded cases cite the short excerpt of A-01 (see CITED_EXCERPTS), so the first version stays in force beside the full text.
        assert status == {"A-01-v2": "in_force", "A-01-v1": "in_force"}
        embedded = (await one("SELECT count(*) FROM chunk c JOIN chunk_embedding e ON e.chunk_id = c.id WHERE c.document_version_id = 'A-01-v2'"))[0][0]
        assert embedded == result["ingested"]["A-01"]
        active = (await one("SELECT active_kb_snapshot_id FROM workspace WHERE id = 'ws-ae'"))[0][0]
        assert active == result["snapshot_id"] and active not in {"snap-ae-13", "snap-ae-14"}
        assert (await one("SELECT count(*) FROM kb_snapshot_item WHERE snapshot_id = :s AND document_version_id = 'A-01-v2'", s=active))[0][0] == 1
        assert dict(await one("SELECT document_version_id, legal_status FROM kb_snapshot_item WHERE snapshot_id = :s AND document_version_id IN ('A-01-v1', 'A-01-v2')", s=active)) == {
            "A-01-v1": "in_force", "A-01-v2": "in_force"}
        # The demo cases move to the new snapshot, so they read the real text. The snapshot they were pinned to is as it was.
        tenants = dict(await one("SELECT id, tenant_id FROM \"case\""))
        assert (await one("SELECT id, doc ->> 'kb_snapshot_id' FROM \"case\" ORDER BY id")) == [
            (case_id, active if tenants[case_id] == TENANT else pinned) for case_id, pinned in cases]
        assert (await one("SELECT count(*) FROM kb_snapshot_item WHERE snapshot_id = 'snap-ae-14'"))[0][0] == old_items
    assert any("A-03: file not found" in line for line in lines) and "A-03" in result["skipped"]

    async with make_sessions(engine)() as db:
        scope = lambda snap: Scope(TENANT, "AE", snap, "requirement", document_ids=["A-01"])  # noqa: E731
        question = "Who may operate a pharmaceutical establishment?"
        assert "v2" in {h.anchor.split(":")[1] for h in (await search(db, scope(active), question, EMBEDDER)).hits if h.document_id == "A-01"}
        assert {h.anchor.split(":")[1] for h in (await search(db, scope("snap-ae-14"), question, EMBEDDER)).hits if h.document_id == "A-01"} == {"v1"}
        # The assistant, asked in a chat on the active snapshot, cites the new text.
        events = await knowledge_answer(db, TENANT, question, Knowledge(active, "AE", EMBEDDER))
        message = next(e for e in events if e["type"] == "message")
        assert any(c["anchor"].startswith("A-01:v2:") for c in message["citations"])


async def test_a_missing_file_is_skipped_and_nothing_changes(engine):
    await fresh(engine)
    lines: list[str] = []
    result = await load_library(SETTINGS, {"A-01": None, "NOPE": None}, say=lines.append)
    assert result["ingested"] == {} and set(result["skipped"]) == {"A-01", "NOPE"}
    assert any("skipped A-01" in line for line in lines)
    async with engine.connect() as conn:
        assert (await conn.execute(text("SELECT active_kb_snapshot_id FROM workspace WHERE id = 'ws-ae'"))).scalar_one() == "snap-ae-14"
        assert (await conn.execute(text("SELECT count(*) FROM document_version WHERE id = 'A-01-v2'"))).scalar_one() == 0


async def test_a_file_that_is_not_a_seeded_document_is_skipped(engine, tmp_path):
    await fresh(engine)
    result = await load_library(SETTINGS, {"ZZ-99": fixture_law(tmp_path / "zz.txt")}, say=lambda _: None)
    assert result["skipped"] == {"ZZ-99": "not in the seeded library"}


REAL = find_files(LIBRARY_DIR, ["A-01"])["A-01"]


@pytest.mark.skipif(REAL is None, reason="the official file is not in knowledge/documents")
async def test_the_official_law_ingests_with_every_article(engine):
    await fresh(engine)
    result = await load_library(SETTINGS, {"A-01": REAL}, say=lambda _: None)
    assert "A-01" in result["ingested"], result["skipped"]
    # An article opens a line, or follows a part heading inside a chunk: "Article (N)" then a capitalised title.
    async with engine.connect() as conn:
        found = (await conn.execute(text(
            "SELECT g FROM generate_series(1, 181) g WHERE EXISTS (SELECT 1 FROM chunk WHERE document_version_id = 'A-01-v2' "
            "AND (text ~ ('(^|\n)Article \\(' || g || '\\)') OR text ~ ('Article \\(' || g || '\\)\\s+[A-Z]')))"))).scalars().all()
        beyond = (await conn.execute(text(
            "SELECT count(*) FROM chunk WHERE document_version_id = 'A-01-v2' AND text ~ '(^|\n)Article \\(182\\)'"))).scalar_one()
    assert len(found) == 181 and beyond == 0


def test_the_readiness_questions_ask_for_the_fee_schedule():
    asked = {q.document_id for q in QUESTION_SETS["core-questions"]}
    assert "A-03" in asked and "A-12" not in asked
    assert any("fee" in q.text.lower() for q in QUESTION_SETS["core-questions"])


async def test_a_repealed_law_stays_repealed_when_its_real_text_loads(engine, tmp_path):
    await fresh(engine)
    result = await load_library(SETTINGS, {"A-11": fixture_law(tmp_path / "old.txt")}, say=lambda _: None)
    assert "A-11" in result["ingested"], result["skipped"]
    async with engine.connect() as conn:
        status = await conn.scalar(text(f"SELECT legal_status FROM kb_snapshot_item WHERE snapshot_id = '{result['snapshot_id']}' AND document_version_id = 'A-11-v2'"))
    assert status == "superseded"


async def test_the_demo_cases_move_to_the_real_snapshot_and_keep_their_excerpts(engine, tmp_path):
    await fresh(engine)
    result = await load_library(SETTINGS, {"A-01": fixture_law(tmp_path / "law.txt")}, say=lambda _: None)
    async with engine.connect() as conn:
        pinned = await conn.scalar(text("SELECT doc ->> 'kb_snapshot_id' FROM \"case\" WHERE id = 'CASE-1042'"))
        excerpt = await conn.scalar(text(f"SELECT legal_status FROM kb_snapshot_item WHERE snapshot_id = '{result['snapshot_id']}' AND document_version_id = 'A-01-v1'"))
    assert pinned == result["snapshot_id"] and excerpt == "in_force"
