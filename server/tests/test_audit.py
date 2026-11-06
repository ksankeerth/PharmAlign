"""The audit log is append-only and chained."""
import asyncio

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app import audit
from app.audit import AuditEntry
from app.db import make_sessions
from app.tables import AuditEventRow, AuditHeadRow

from .conftest import START, TENANT


def entry(action="thing_done", **fields) -> AuditEntry:
    return AuditEntry(at=START, actor_type="person", actor_id="p-alex", action=action, object_type="thing", object_id="t-1", **fields)


async def write(engine, *entries, tenant=TENANT) -> list[str]:
    async with make_sessions(engine)() as db, db.begin():
        return await audit.write(db, tenant, entries)


async def test_update_is_refused_by_the_database(engine):
    await write(engine, entry())
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as conn:
            await conn.execute(update(AuditEventRow).values(action="changed"))


async def test_delete_is_refused_by_the_database(engine):
    await write(engine, entry())
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as conn:
            await conn.execute(delete(AuditEventRow))


async def test_truncate_is_refused_by_the_database(engine):
    await write(engine, entry())
    with pytest.raises(DBAPIError, match="append-only"):
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE audit_event"))


async def test_the_chain_verifies(engine):
    await write(engine, entry(), entry(before={"b": 1, "a": [1.5, 1e25, "é"]}, after={"at": "x"}, reason="Because"))
    await write(engine, entry(case_id="c-1"))
    async with make_sessions(engine)() as db:
        result = await audit.verify(db, TENANT)
        rows = (await db.scalars(select(AuditEventRow).order_by(AuditEventRow.seq))).all()
    assert result.ok and result.rows_checked == 3
    assert rows[0].prev_hash is None
    assert rows[1].prev_hash == rows[0].row_hash
    assert rows[2].prev_hash == rows[1].row_hash
    assert all(r.row_hash.startswith("sha256:") for r in rows)


async def test_tampering_is_found(engine):
    ids = await write(engine, entry(), entry(), entry())
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_event DISABLE TRIGGER USER"))
        await conn.execute(update(AuditEventRow).where(AuditEventRow.id == ids[1]).values(reason="Altered"))
        await conn.execute(text("ALTER TABLE audit_event ENABLE TRIGGER USER"))
    async with make_sessions(engine)() as db:
        result = await audit.verify(db, TENANT)
    assert not result.ok
    assert result.first_bad_row == ids[1]
    assert result.rows_checked == 1


async def test_concurrent_writers_keep_one_chain(engine):
    await asyncio.gather(*(write(engine, entry(), entry()) for _ in range(8)))
    async with make_sessions(engine)() as db:
        result = await audit.verify(db, TENANT)
    assert result.ok and result.rows_checked == 16


async def test_a_write_that_commits_during_the_check_is_not_a_cut(engine, monkeypatch):
    await write(engine, entry(), entry())
    read_head, wrote = AsyncSession.get, []

    async def get_after_a_write(self, entity, ident, *args, **kwargs):
        if entity is AuditHeadRow and not wrote:
            wrote.append(await write(engine, entry()))  # lands after the rows were read and before the head is
        return await read_head(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get_after_a_write)
    async with make_sessions(engine)() as db:
        result = await audit.verify(db, TENANT)
    assert wrote and result.ok and result.rows_checked == 2
    async with make_sessions(engine)() as db:
        assert (await audit.verify(db, TENANT)).rows_checked == 3


async def test_a_row_cut_from_the_end_is_still_found(engine):
    ids = await write(engine, entry(), entry())
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE audit_event DISABLE TRIGGER USER"))
        await conn.execute(delete(AuditEventRow).where(AuditEventRow.id == ids[1]))
        await conn.execute(text("ALTER TABLE audit_event ENABLE TRIGGER USER"))
    async with make_sessions(engine)() as db:
        result = await audit.verify(db, TENANT)
    assert not result.ok and result.first_bad_row == ids[1] and result.rows_checked == 1


async def test_each_tenant_has_its_own_chain(engine):
    await write(engine, entry())
    await write(engine, entry(), tenant="ws-other")
    await write(engine, entry())
    async with make_sessions(engine)() as db:
        assert (await audit.verify(db, TENANT)).rows_checked == 2
        assert (await audit.verify(db, "ws-other")).rows_checked == 1
        assert (await audit.verify(db, "ws-other")).ok


async def test_a_bad_entry_fails_the_contract(engine):
    with pytest.raises(ValueError):
        await write(engine, entry(action="Not A Slug"))
