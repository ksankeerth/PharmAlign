"""A signed case read back as it was when signed: from stored records, never from what the workspace uses now."""
import pytest
from sqlalchemy import select, update

from app.db import make_sessions
from app.handlers.audit import reconstruct
from app.tables import ROW

from ..conftest import TENANT, system_ctx
from ..handlers.conftest import headers
from ..handlers.test_signing import package, sign

pytestmark = pytest.mark.seeded
AUDITOR = headers("p-robin")


def copy_of(row, **changes):
    return type(row)(**{**{c.name: getattr(row, c.name) for c in row.__table__.columns}, **changes})


async def rebuilt(http, case):
    async with make_sessions(http.app.state.rt.engine)() as db:
        return await reconstruct(system_ctx(http.app.state.rt, db), case)


async def test_reconstruct_matches_the_signed_fingerprint(http):
    pkg = await package(http, "CASE-1044")
    dec = (await sign(http, "CASE-1044", pkg)).json()
    r = await http.get("/api/v1/cases/CASE-1044/reconstruct", headers=AUDITOR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"]["id"] == dec["id"] and body["decision"]["signed_payload_hash"] == pkg["fingerprint"]
    reconstruction, fingerprint = await rebuilt(http, "CASE-1044")
    assert fingerprint == pkg["fingerprint"] == dec["signed_payload_hash"]
    assert [c.id for c in reconstruction.checks] == [c["id"] for c in pkg["checks"]]
    assert [r.version for r in reconstruction.recommendations if r.is_current] == [pkg["recommendation"]["version"]]
    assert any(e.action == "decision_signed" and e.object_id == dec["id"] for e in reconstruction.audit_events)


async def test_reconstruct_matches_the_seeded_signed_cases(http):
    for case, decision in (("CASE-1039", "dec-1039"), ("CASE-1037", "dec-1037"), ("CASE-1036", "dec-1036")):
        reconstruction, fingerprint = await rebuilt(http, case)
        assert reconstruction.decision.id == decision and fingerprint == reconstruction.decision.signed_payload_hash, case
        assert (await http.get(f"/api/v1/cases/{case}/reconstruct", headers=AUDITOR)).status_code == 200


async def test_reconstruct_is_unaffected_by_a_later_pack_activation_and_a_later_adoption(http, engine):
    pkg = await package(http, "CASE-1044")
    await sign(http, "CASE-1044", pkg)
    before, fingerprint = await rebuilt(http, "CASE-1044")
    # The workspace moves on: a new pack becomes the active one, and a document's legal status changes.
    async with make_sessions(engine)() as db, db.begin():
        pack = ROW["pack_version"]
        old = await db.get(pack, before.case.pack_version_id)
        await db.execute(update(pack).where(pack.id == old.id).values(state="retired"))
        db.add(copy_of(old, id="pv-ae-9", version=9, state="active", doc={**old.doc, "id": "pv-ae-9", "version": 9, "state": "active"}))
        ws = ROW["workspace"]
        w = await db.get(ws, TENANT)
        await db.execute(update(ws).where(ws.id == TENANT).values(doc={**w.doc, "active_pack_version_id": "pv-ae-9"}, active_pack_version_id="pv-ae-9"))
        adoption = ROW["tenant_document_adoption"]
        row = (await db.scalars(select(adoption).where(adoption.tenant_id == TENANT).limit(1))).first()
        await db.execute(update(adoption).where(adoption.id == row.id).values(
            adopted=False, doc={**row.doc, "adopted": False}))
    after, again = await rebuilt(http, "CASE-1044")
    assert again == fingerprint
    assert after.model_dump() == before.model_dump()
    assert after.case.pack_version_id == before.case.pack_version_id != "pv-ae-9"


async def test_reconstruct_ignores_a_check_that_changed_after_signing(http, engine):
    pkg = await package(http, "CASE-1044")
    await sign(http, "CASE-1044", pkg)
    before, fingerprint = await rebuilt(http, "CASE-1044")
    check = before.checks[0]
    async with make_sessions(engine)() as db, db.begin():
        results = ROW["check_result"]
        current = await db.get(results, check.id)
        later = check.updated_at.replace(year=check.updated_at.year + 1)
        # A re-run after signing: the result as signed is kept as a replaced copy, and a new one takes its place.
        signed_copy = copy_of(current, id=f"{check.id}-signed", is_current=False, doc={**current.doc, "id": f"{check.id}-signed", "is_current": False})
        await db.execute(update(results).where(results.id == check.id).values(
            updated_at=later, generation=current.generation + 1,
            doc={**current.doc, "updated_at": later.isoformat(), "generation": current.generation + 1, "fingerprint": "sha256:" + "0" * 64}))
        db.add(signed_copy)
    after, again = await rebuilt(http, "CASE-1044")
    assert again == fingerprint
    assert [c.fingerprint for c in after.checks] == [c.fingerprint for c in before.checks]
    assert after.checks[0].id == f"{check.id}-signed"


async def test_an_unsigned_case_is_read_as_it_stands(http):
    reconstruction, fingerprint = await rebuilt(http, "CASE-1051")
    assert reconstruction.decision is None and fingerprint is None and reconstruction.checks
    assert (await http.get("/api/v1/cases/CASE-1051/reconstruct", headers=AUDITOR)).status_code == 200
    assert (await http.get("/api/v1/cases/CASE-9999/reconstruct", headers=AUDITOR)).status_code == 404
