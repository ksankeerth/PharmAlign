"""These tests run on the full demo seed, loaded once per module. A module's tests keep to their own packs, cases and gaps."""
from datetime import timedelta

from sqlalchemy import select

from app.db import make_sessions
from app.jobs import Worker
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
