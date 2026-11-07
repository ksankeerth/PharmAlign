"""These tests run on the full demo seed, loaded once per module. A module's tests keep to their own packs, cases and gaps."""
import hashlib
from datetime import timedelta

from sqlalchemy import select, text

from app.db import make_sessions
from app.handlers.packs import source_check_of
from app.jobs import Worker
from app.seed import _ctx as seed_ctx
from app.stores import STORES
from app.tables import PersonRow

from ..conftest import START
from ..handlers.conftest import audit_rows, count_rows, failed, headers, http, seeded  # noqa: F401  (fixtures and helpers shared with the handler tests)


async def run_jobs(app, clock, seconds: int = 5) -> None:
    """Lets the background jobs that are due run, as the worker does."""
    clock.advance(seconds=seconds)
    await Worker(app.state.rt).run_due()


async def add_person(engine, person_id: str, tenant_id: str, view: str) -> str:
    async with make_sessions(engine)() as db, db.begin():
        if await db.get(PersonRow, person_id) is None:
            db.add(PersonRow(id=person_id, tenant_id=tenant_id, display_name=person_id, view=view, is_demo=True, created_at=START - timedelta(days=1)))
    return person_id


async def add_clause_passages(db, tenant_id: str, document: dict) -> list[tuple[str, str]]:
    """TEST-ONLY. A passage for each source clause of a pack document that matches none in the knowledge snapshot, as a heading of that document's version in it,
    so a pack that cites those clauses validates. Returns the (doc_id, clause) pairs it added."""
    check = await source_check_of(db, tenant_id, document)
    added = sorted(check.unresolved)
    for n, (doc_id, clause) in enumerate(added):
        version = (await db.execute(text(
            "SELECT i.document_version_id FROM kb_snapshot_item i JOIN document_version dv ON dv.id = i.document_version_id "
            "WHERE i.snapshot_id = :s AND i.tenant_id = :t AND dv.document_id = :d"), {"s": check.snapshot_id, "t": tenant_id, "d": doc_id})).scalar_one()
        anchor = f"{doc_id}:v1:t{n:04d}"
        body = f"[Placeholder] {clause}. A test passage; no source text."
        STORES["chunk"].add(seed_ctx(db, None), {
            "anchor": anchor, "position": 9000 + n, "chunk_type": "heading", "page": 1, "page_end": 1, "article": None, "section_path": clause, "text": body,
            "document_version_id": version, "language": "en", "text_hash": "sha256:" + hashlib.sha256(body.encode()).hexdigest()}, id=anchor)
    await db.flush()
    return added
