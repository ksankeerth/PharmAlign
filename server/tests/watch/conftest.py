"""The watch tests run on the full demo seed, loaded again for each test, because a notice changes what the next test would read."""
import pytest
import pytest_asyncio
from pharmalign_contracts.server import models as m
from sqlalchemy import func, select

from app.db import make_sessions
from app.pipeline.replay import load_recorded_dossier
from app.pipeline.runner import system_tx
from app.seed import load_files, truncate_all
from app.tables import ROW, DomainEventRow, JobRow
from app.watch.notices import ingest

from ..conftest import START, TENANT, as_person, client_for

pytestmark = pytest.mark.seeded
TABLES = {"job": JobRow, "domain_event": DomainEventRow}
AGENCY_A = "Reference Agency A (synthetic)"
SUSPENSION = {
    "regulator": AGENCY_A, "title": "Suspension of marketing authorisation: clopidogrel 75 mg tablets (synthetic)",
    "text": "Reference Agency A (synthetic) suspends the authorisation for clopidogrel 75 mg film-coated tablets while it reviews a quality defect.",
}
LABEL = {"regulator": AGENCY_A, "title": "Label change: rosuvastatin 10 mg (synthetic)", "text": "A new interaction warning is added to the label of rosuvastatin 10 mg tablets."}
UNKNOWN = {"regulator": AGENCY_A, "title": "Notice about an unrelated product (synthetic)", "text": "Guidance on storing the vaccine named Examplevax. No other product."}
BURST = {
    "regulator": AGENCY_A, "title": "GMP non-compliance: Example Pharma, Plant 9 (synthetic)",
    "text": "Plant 9 made paracetamol, sitagliptin and amoxicillin products. Inspectors found it did not follow good manufacturing practice.",
}
DEAD_AGENTS = {"agent_mode": "http", **{f"agent_url_{a}": "http://127.0.0.1:9" for a in (
    "dossier_reader", "checklist_assessor", "sameness", "reliance_watch", "challenger", "coordinator", "gap_analyst", "knowledge_assistant")}}


@pytest_asyncio.fixture(autouse=True)
async def seeded(engine):
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)


@pytest_asyncio.fixture
async def http(make_app):
    app = make_app()
    async with client_for(app) as client:
        client.app = app
        yield client


def headers(person: str, **extra) -> dict:
    return {**as_person(person), **extra}


async def deliver(rt, notice: dict, source_id=None):
    """A notice arrives by the entry point every source uses."""
    async with system_tx(rt, TENANT) as ctx:
        return await ingest(ctx, m.StagedNotice(**notice), source_id)


async def rows(engine, table: str, *where) -> list:
    async with make_sessions(engine)() as db:
        return list((await db.scalars(select(ROW[table]).where(*where))).all())


async def total(engine, table: str, *where) -> int:
    async with make_sessions(engine)() as db:
        return await db.scalar(select(func.count()).select_from(TABLES.get(table) or ROW[table]).where(*where))


async def alerts_for(engine, person: str, notice_id: str) -> list:
    row = ROW["notification_delivery"]
    return await rows(engine, "notification_delivery", row.recipient == person, row.id.like(f"nt-{notice_id}-%"))
