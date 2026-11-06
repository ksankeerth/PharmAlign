"""Government records, end to end on real Postgres: the server, its worker, and the mock regulator reached over HTTP."""
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.connector import make_connector
from app.db import make_sessions
from app.jobs import Worker
from app.mock_regulator.tables import MockLicenceRow, MockProposalRow
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files, truncate_all
from app.tables import JobRow, ROW

from ..conftest import START, TENANT, as_person, client_for

pytestmark = pytest.mark.seeded
TOKEN = "test-regulator-token"
CASE = "CASE-1044"


@pytest_asyncio.fixture(autouse=True)
async def seeded(engine):  # signing changes what the next test would read
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)


@pytest_asyncio.fixture
async def world(make_app, clock):
    """An app whose connector calls the mock over HTTP, with calls that give up after a third of a second."""
    app = make_app(settings={
        "connector_base_url": "http://regulator/mock", "connector_token": TOKEN, "connector_timeout_seconds": 0.3,
        "mock_regulator_hang_seconds": 5, "mock_regulator_slow_seconds": 0.05,
    })
    rt = app.state.rt
    rt.connector = make_connector(rt.settings, transport=httpx.ASGITransport(app=app))
    async with client_for(app) as http:
        yield World(app, rt, http, clock)
    await rt.connector.aclose()


class World:
    def __init__(self, app, rt, http, clock):
        self.app, self.rt, self.http, self.clock = app, rt, http, clock

    def headers(self, person: str, **extra) -> dict:
        return {**as_person(person), **extra}

    async def regulator_mode(self, mode: str) -> None:
        r = await self.http.post("/mock/test-controls/failure-mode", json={"mode": mode}, headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, r.text

    async def sign(self, case: str = CASE, person: str = "p-dana") -> dict:
        pkg = (await self.http.get(f"/api/v1/cases/{case}/signing-package", headers=self.headers(person))).json()
        body = {"type": "grant", "fingerprint": pkg["fingerprint"], "recommendation_version": pkg["recommendation"]["version"],
                "accepts_recommendation": True, "reason": None, "conditions": []}
        r = await self.http.post(f"/api/v1/cases/{case}/decisions", json=body, headers=self.headers(person))
        assert r.status_code == 201, r.text
        return r.json()

    async def run_jobs(self, owner: str = "worker-1") -> int:
        return await Worker(self.rt, owner=owner).run_due()

    async def proposal(self, id: str) -> dict:
        r = await self.http.get(f"/api/v1/proposals/{id}", headers=self.headers("p-jordan"))
        assert r.status_code == 200, r.text
        return r.json()

    async def decision(self, id: str) -> dict:
        return (await self.http.get(f"/api/v1/decisions/{id}", headers=self.headers("p-dana"))).json()

    async def case(self, id: str = CASE) -> dict:
        return (await self.http.get(f"/api/v1/cases/{id}", headers=self.headers("p-dana"))).json()

    async def timeline(self, case: str = CASE) -> list[str]:
        r = await self.http.get(f"/api/v1/cases/{case}/timeline", headers=self.headers("p-dana"))
        assert r.status_code == 200, r.text
        return [e["kind"] for e in r.json()["items"]]

    async def regulator_records(self, decision_id: str) -> int:
        async with self.rt.sessions() as db:
            return await db.scalar(select(func.count()).select_from(MockProposalRow).where(MockProposalRow.body["decision_ref"].astext == decision_id))

    async def regulator_licences(self) -> int:
        async with self.rt.sessions() as db:
            return await db.scalar(select(func.count()).select_from(MockLicenceRow))

    async def mirror_licences(self) -> int:
        async with self.rt.sessions() as db:
            return await db.scalar(select(func.count()).select_from(ROW["licence"]))

    async def jobs(self, status: str) -> int:
        async with self.rt.sessions() as db:
            return await db.scalar(select(func.count()).select_from(JobRow).where(JobRow.kind == "decisions.send_proposal", JobRow.status == status))

    async def until_quiet(self, rounds: int = 12) -> None:
        """Runs the worker, then lets the retry waits pass, until no job is left."""
        for _ in range(rounds):
            await self.run_jobs()
            if not await self.jobs("queued"):
                return
            self.clock.advance(minutes=10)
