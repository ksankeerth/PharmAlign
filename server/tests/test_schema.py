"""The full schema: seed, fixed records, and what the agent role can and can't reach. The module loads the seed once."""
import json
import shutil
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from app import seed
from app.db import make_sessions
from app.seed import SEED_DIR, SeedError, load_files, truncate_all
from app.tables import ROW
from tests.conftest import TEST_URL

pytestmark = pytest.mark.seeded
NOW = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
TENANT = "ws-ae"
CASE = "CASE-1042"

# Seed files that land in a table with another name.
TABLE_OF = {"timeline_event": "domain_event", "notification_event": "domain_event"}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def seeded(engine):
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, NOW)


@pytest_asyncio.fixture
async def agent(database):
    url = make_url(TEST_URL).set(username="pharmalign_agent", password="pharmalign_agent")
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


async def scoped(conn, tenant=TENANT, case=CASE):
    """What the tool server does at the start of a run."""
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.case_id', :c, true)"), {"t": tenant, "c": case})


async def values(conn, sql: str, **params) -> list:
    return [r[0] for r in (await conn.execute(text(sql), params)).all()]


# ---------------------------------------------------------------- seed


async def test_row_counts_match_the_exported_files(engine):
    expected: dict[str, int] = {}
    for path in SEED_DIR.glob("*.json"):
        if path.stem != "manifest":
            table = TABLE_OF.get(path.stem, path.stem)
            expected[table] = expected.get(table, 0) + len(json.loads(path.read_text()))
    assert json.loads((SEED_DIR / "manifest.json").read_text())["counts"].keys() >= {"case", "chunk", "audit_event"}
    async with engine.connect() as conn:
        for table, n in sorted(expected.items()):
            assert await conn.scalar(text(f'SELECT count(*) FROM "{table}"')) == n, table
    assert expected["case"] == 20 and expected["chunk"] > 1000


def test_every_table_is_in_the_seed_or_empty_on_purpose():
    empty_on_purpose = {"chunk_embedding", "assistant_exchange", "agent_tool_log", "model_registry", "agent_run_work", "watch_staged_item",
                       "document_file", "quality_report", "ingest_output",
                       "reference_page"}  # made from reliance_basis when the seed loads
    missing = [t for t in ROW if t not in empty_on_purpose and not (SEED_DIR / f"{t}.json").exists()]
    assert missing == []


async def test_audit_chain_from_the_seed_verifies(engine):
    from app import audit

    async with make_sessions(engine)() as db:
        result = await audit.verify(db, TENANT)
    assert result.ok and result.rows_checked == 84


async def test_times_are_relative_to_the_load(engine):
    async with engine.connect() as conn:
        deadline, created = (await conn.execute(text("SELECT doc ->> 'next_deadline', doc ->> 'created_at' FROM \"case\" WHERE id = :c"), {"c": CASE})).one()
    assert datetime.fromisoformat(deadline) > NOW > datetime.fromisoformat(created)
    assert datetime.fromisoformat(deadline) - NOW == timedelta(seconds=777600)


async def test_a_row_that_does_not_fit_stops_the_load(tmp_path, monkeypatch):
    shutil.copytree(SEED_DIR, tmp_path / "seed")
    bad = tmp_path / "seed" / "workspace.json"
    rows = json.loads(bad.read_text())
    rows[0]["doc"]["mode"] = "not-a-mode"
    bad.write_text(json.dumps(rows))
    monkeypatch.setattr(seed, "SEED_DIR", tmp_path / "seed")
    with pytest.raises(SeedError, match=r"workspace\.json row 0"):
        async with seed.make_sessions(seed.make_engine(TEST_URL))() as db:
            await load_files(db, NOW)


# ---------------------------------------------------------------- records that stay fixed


async def refused(engine, sql: str, **params):
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError, match="fixed once written|cannot"):
            await conn.execute(text(sql), params)
        await conn.rollback()


async def test_chunk_text_cannot_change_or_be_deleted(engine):
    anchor = "A-01:v1:c0117"
    await refused(engine, "UPDATE chunk SET text = 'changed' WHERE id = :a", a=anchor)
    await refused(engine, "UPDATE chunk SET doc = jsonb_set(doc, '{text}', '\"changed\"') WHERE id = :a", a=anchor)
    await refused(engine, "DELETE FROM chunk WHERE id = :a", a=anchor)
    await refused(engine, "TRUNCATE chunk CASCADE")
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT text FROM chunk WHERE id = :a"), {"a": anchor}) != "changed"


async def test_chunk_hash_is_the_hash_of_its_text(engine):
    async with engine.connect() as conn:
        bad = await conn.scalar(text("SELECT count(*) FROM chunk WHERE text_hash <> 'sha256:' || encode(sha256(convert_to(text, 'UTF8')), 'hex')"))
    assert bad == 0


async def test_snapshot_items_cannot_change(engine):
    await refused(engine, "UPDATE kb_snapshot_item SET legal_status = 'in_force' WHERE legal_status = 'superseded'")
    await refused(engine, "DELETE FROM kb_snapshot_item")


async def test_an_approved_pack_cannot_change(engine):
    pack = "pv-ae-3"
    await refused(engine, "UPDATE pack_version SET content_hash = 'sha256:abcd' WHERE id = :p", p=pack)
    await refused(engine, "UPDATE pack_version SET doc = jsonb_set(doc, '{document,spot_check_rate}', '0.9') WHERE id = :p", p=pack)
    await refused(engine, "UPDATE pack_version SET approved_by = 'p-jordan' WHERE id = :p", p=pack)
    await refused(engine, "DELETE FROM pack_version WHERE id = :p", p=pack)


async def test_an_approved_pack_may_still_change_state(engine):
    async with engine.connect() as conn:
        await conn.execute(text("UPDATE pack_version SET state = 'retired', doc = jsonb_set(doc, '{state}', '\"retired\"') WHERE id = 'pv-ae-3'"))
        assert await conn.scalar(text("SELECT state FROM pack_version WHERE id = 'pv-ae-3'")) == "retired"
        await conn.rollback()


async def test_a_draft_pack_may_change(engine):
    async with engine.connect() as conn:
        await conn.execute(text("UPDATE pack_version SET doc = jsonb_set(doc, '{content_hash}', '\"sha256:abcd\"'), content_hash = 'sha256:abcd' WHERE id = 'pv-ae-4'"))
        await conn.rollback()


async def test_a_pack_is_approved_only_when_it_has_an_approval(engine):
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE pack_version SET state = 'active' WHERE id = 'pv-ae-4'"))
        await conn.rollback()


async def test_one_current_result_per_check(engine):
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE check_result SET is_current = true WHERE id = 'cr-1042-qual-07~g1a1'"))
        await conn.rollback()


async def test_a_proposal_key_is_used_once(engine):
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE proposal SET idempotency_key = (SELECT idempotency_key FROM proposal LIMIT 1) WHERE id = (SELECT id FROM proposal OFFSET 1 LIMIT 1)"))
        await conn.rollback()


async def test_reliance_basis_must_point_at_a_case_or_licence(engine):
    async with engine.connect() as conn:
        for target in (("case", "nope"), ("licence", "nope"), ("site", "x")):
            with pytest.raises(DBAPIError):
                await conn.execute(text(
                    "INSERT INTO reliance_basis (id, tenant_id, target_type, target_id, regulator, verified, doc) VALUES ('rb-x', :t, :ty, :id, 'r', false, '{}')"),
                    {"t": TENANT, "ty": target[0], "id": target[1]})
            await conn.rollback()
        await conn.execute(text(
            "INSERT INTO reliance_basis (id, tenant_id, target_type, target_id, regulator, verified, doc) VALUES ('rb-x', :t, 'licence', 'lic-0007', 'r', false, '{}')"), {"t": TENANT})
        await conn.rollback()


async def test_a_watch_match_with_no_target_has_no_target_id(engine):
    async with engine.connect() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE watch_match SET target_type = 'none' WHERE target_id IS NOT NULL"))
        await conn.rollback()


async def test_agent_run_status_includes_lost(engine):
    async with engine.connect() as conn:
        await conn.execute(text("UPDATE agent_run SET status = 'lost' WHERE id = (SELECT id FROM agent_run LIMIT 1)"))
        with pytest.raises(IntegrityError):
            await conn.execute(text("UPDATE agent_run SET status = 'vanished'"))
        await conn.rollback()


# ---------------------------------------------------------------- the agent role


async def test_agent_cannot_touch_core_tables(agent):
    async with agent.connect() as conn:
        for sql in (
            'SELECT 1 FROM "case"', "SELECT 1 FROM chunk", "SELECT 1 FROM dossier_page", "SELECT 1 FROM person", "SELECT 1 FROM job",
            "SELECT 1 FROM agent_tool_log", "INSERT INTO party (id, tenant_id, kind, doc) VALUES ('x', 'ws-ae', 'applicant', '{}')",
            "UPDATE \"case\" SET status = 'closed'", "DELETE FROM chunk", "TRUNCATE chunk", "CREATE TABLE public.mine (x int)",
            "UPDATE agent_read.dossier_page SET suspect = false", "DELETE FROM agent_read.dossier_page",
        ):
            with pytest.raises(ProgrammingError, match="permission denied"):
                await conn.execute(text(sql))
            await conn.rollback()


async def test_agent_cannot_read_audit(agent):
    async with agent.connect() as conn:
        for sql in ("SELECT * FROM audit_event", "SELECT count(*) FROM audit_event", "INSERT INTO audit_event (id) VALUES ('x')", "SELECT * FROM public.audit_event"):
            with pytest.raises(ProgrammingError, match="permission denied"):
                await conn.execute(text(sql))
            await conn.rollback()


async def test_agent_sees_nothing_until_the_server_scopes_it(agent):
    async with agent.connect() as conn:
        for view in ("case_context", "kb_chunk", "kb_requirement_chunk", "dossier_document", "dossier_page", "dossier_chunk", "case_fact", "reliance"):
            assert await conn.scalar(text(f"SELECT count(*) FROM agent_read.{view}")) == 0, view
    async with agent.begin() as conn:
        await scoped(conn, tenant=TENANT, case=None)
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.dossier_page")) == 0


async def test_agent_sees_only_its_own_case(agent):
    async with agent.begin() as conn:
        await scoped(conn)
        assert await values(conn, "SELECT DISTINCT case_id FROM agent_read.dossier_chunk") == [CASE]
        assert set(await values(conn, "SELECT DISTINCT case_id FROM agent_read.case_fact")) == {CASE}
        assert await values(conn, "SELECT case_id FROM agent_read.case_context") == [CASE]
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.dossier_page")) > 0
    async with agent.begin() as conn:
        await scoped(conn, case="CASE-1047")
        assert await values(conn, "SELECT DISTINCT case_id FROM agent_read.dossier_chunk") == ["CASE-1047"]
        assert await values(conn, "SELECT anchor FROM agent_read.dossier_chunk WHERE anchor LIKE 'd-1042-%'") == []
    async with agent.begin() as conn:
        await scoped(conn, case="CASE-1043")
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.dossier_chunk")) == 0
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.dossier_document")) > 0


async def test_agent_cannot_see_another_tenant(agent):
    async with agent.begin() as conn:
        await scoped(conn, tenant="ws-sa", case=CASE)
        for view in ("case_context", "kb_chunk", "dossier_page", "dossier_chunk", "case_fact"):
            assert await conn.scalar(text(f"SELECT count(*) FROM agent_read.{view}")) == 0, view


async def test_scope_ends_with_the_transaction(agent):
    async with agent.begin() as conn:
        await scoped(conn)
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.case_context")) == 1
    async with agent.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM agent_read.case_context")) == 0


async def test_dossier_pages_carry_no_hidden_text(agent):
    async with agent.begin() as conn:
        await scoped(conn)
        columns = await values(conn, "SELECT column_name FROM information_schema.columns WHERE table_schema = 'agent_read' AND table_name = 'dossier_page'")
    assert "hidden_text" not in columns and "doc" not in columns


async def test_superseded_law_is_not_a_requirement(agent):
    async with agent.begin() as conn:
        await scoped(conn)
        anything = {r.anchor: r.usable_as_requirement for r in (await conn.execute(text("SELECT anchor, usable_as_requirement FROM agent_read.kb_chunk"))).all()}
        required = set(await values(conn, "SELECT anchor FROM agent_read.kb_requirement_chunk"))
    assert anything["A-11:v1:c0098"] is False and "A-11:v1:c0098" not in required
    assert anything["A-01:v1:c0117"] is True and "A-01:v1:c0117" in required
    assert required == {a for a, ok in anything.items() if ok}


async def test_only_snapshot_content_is_visible(agent, engine):
    async with engine.connect() as conn:
        outside = await values(conn, """
            SELECT ch.id FROM chunk ch WHERE NOT EXISTS (
                SELECT 1 FROM kb_snapshot_item i WHERE i.snapshot_id = 'snap-ae-14' AND i.document_version_id = ch.document_version_id)""")
    assert outside  # unreviewed documents are in the library but not in the snapshot
    async with agent.begin() as conn:
        await scoped(conn)
        visible = set(await values(conn, "SELECT anchor FROM agent_read.kb_chunk"))
    assert visible and not visible & set(outside)


@pytest.mark.parametrize("column, bad", [("review_status", "unreviewed"), ("review_status", "rejected"), ("quality_status", "fail")])
async def test_unapproved_content_disappears(agent, engine, column, bad):
    version = "E-S25-v1"
    async with engine.connect() as conn:
        old = await conn.scalar(text(f"SELECT {column} FROM document_version WHERE id = :v"), {"v": version})
        await conn.execute(text(f"UPDATE document_version SET {column} = :b WHERE id = :v"), {"b": bad, "v": version})
        await conn.commit()
    try:
        async with agent.begin() as conn:
            await scoped(conn)
            assert await values(conn, "SELECT anchor FROM agent_read.kb_chunk WHERE document_version_id = :v", v=version) == []
            assert await values(conn, "SELECT anchor FROM agent_read.kb_chunk WHERE document_version_id = 'A-01-v1'")
    finally:
        async with engine.connect() as conn:
            await conn.execute(text(f"UPDATE document_version SET {column} = :o WHERE id = :v"), {"o": old, "v": version})
            await conn.commit()
    async with agent.begin() as conn:
        await scoped(conn)
        assert await values(conn, "SELECT anchor FROM agent_read.kb_chunk WHERE document_version_id = :v", v=version)


async def test_agent_may_log_tool_calls_for_its_own_run_only(agent, engine):
    insert = text("INSERT INTO agent_tool_log (id, tenant_id, case_id, run_id, tool, args_hash, outcome) VALUES (:id, :t, :c, 'run-1', 'search_kb', 'sha256:ab', 'ok')")
    async with agent.begin() as conn:
        await scoped(conn)
        await conn.execute(insert, {"id": "tl-1", "t": TENANT, "c": CASE})
    for tenant, case in (("ws-sa", CASE), (TENANT, "CASE-1043")):
        async with agent.connect() as conn:
            await scoped(conn)
            with pytest.raises(ProgrammingError, match="row-level security"):
                await conn.execute(insert, {"id": "tl-2", "t": tenant, "c": case})
            await conn.rollback()
    async with agent.connect() as conn:
        for sql in ("UPDATE agent_tool_log SET tool = 'x'", "DELETE FROM agent_tool_log", "SELECT * FROM agent_tool_log"):
            with pytest.raises(ProgrammingError, match="permission denied"):
                await conn.execute(text(sql))
            await conn.rollback()
    async with engine.connect() as conn:
        assert await conn.scalar(select(func.count()).select_from(ROW["agent_tool_log"])) == 1
        await conn.execute(text("DELETE FROM agent_tool_log"))
        await conn.commit()
