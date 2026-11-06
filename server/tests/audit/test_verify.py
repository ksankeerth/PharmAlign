"""The chain check: it passes on the seeded log, and names the first row that was changed or removed."""
import pytest
from sqlalchemy import delete, select, text, update

from app.db import make_sessions
from app.tables import AuditEventRow

from ..conftest import TENANT
from ..handlers.conftest import headers

pytestmark = pytest.mark.seeded
AUDITOR = headers("p-robin")


async def chain_ids(engine) -> list[str]:
    async with make_sessions(engine)() as db:
        return list((await db.scalars(select(AuditEventRow.id).where(AuditEventRow.tenant_id == TENANT).order_by(AuditEventRow.seq))).all())


async def as_owner(engine, statement):
    """What someone with the database's own login could do. The product has no way to."""
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_event DISABLE TRIGGER USER"))
        await conn.execute(statement)
        await conn.execute(text("ALTER TABLE audit_event ENABLE TRIGGER USER"))


async def test_verify_passes_on_the_seeded_log(http, engine):
    ids = await chain_ids(engine)
    r = await http.get("/api/v1/audit/verify", headers=AUDITOR)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "rows_checked": len(ids), "first_bad_row": None}
    assert len(ids) > 10


async def test_verify_finds_a_changed_row_and_names_it(http, engine):
    ids = await chain_ids(engine)
    await as_owner(engine, update(AuditEventRow).where(AuditEventRow.id == ids[3]).values(reason="edited later"))
    body = (await http.get("/api/v1/audit/verify", headers=AUDITOR)).json()
    assert body == {"ok": False, "rows_checked": 3, "first_bad_row": ids[3]}


async def test_verify_finds_a_deleted_row(http, engine):
    ids = await chain_ids(engine)
    await as_owner(engine, delete(AuditEventRow).where(AuditEventRow.id == ids[5]))
    body = (await http.get("/api/v1/audit/verify", headers=AUDITOR)).json()
    # The row after the gap no longer follows its predecessor, so that is the first one that fails.
    assert body == {"ok": False, "rows_checked": 5, "first_bad_row": ids[6]}


async def test_the_log_lists_newest_first_and_filters_by_case(http):
    everything = (await http.get("/api/v1/audit-events?limit=200", headers=AUDITOR)).json()
    case = next(e["case_id"] for e in everything["items"] if e["case_id"])
    one = (await http.get(f"/api/v1/audit-events?case_id={case}", headers=AUDITOR)).json()["items"]
    assert one and all(e["case_id"] == case for e in one) and len(one) < len(everything["items"])
    action = one[0]["action"]
    by_action = (await http.get(f"/api/v1/audit-events?action={action}&actor_id={one[0]['actor_id']}", headers=AUDITOR)).json()["items"]
    assert by_action and all(e["action"] == action for e in by_action)
    first = (await http.get("/api/v1/audit-events?limit=2", headers=AUDITOR)).json()
    assert len(first["items"]) == 2 and first["next_cursor"] == "2"
    assert (await http.get("/api/v1/audit-events", headers=headers("p-alex"))).status_code == 403


async def test_verify_finds_rows_removed_from_the_end(http, engine):
    ids = await chain_ids(engine)
    await as_owner(engine, delete(AuditEventRow).where(AuditEventRow.id.in_(ids[-2:])))
    body = (await http.get("/api/v1/audit/verify", headers=AUDITOR)).json()
    # Every remaining row is intact. The stored head still names the last row that was written.
    assert body == {"ok": False, "rows_checked": len(ids) - 2, "first_bad_row": ids[-1]}
