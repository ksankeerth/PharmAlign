"""Knowledge tests run on real Postgres, with documents made here: small text files, so no parser extra is needed."""
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.context import Ctx, Tenant
from app.db import make_sessions
from app.identity import Actor
from app.jobs import Worker
from app.stores import STORES
from app.tables import PersonRow, ROW
from pharmalign_contracts.server import models as m

from ..conftest import START, TENANT, as_person, client_for

ADMIN, OWNER, OTHER_OWNER = "p-jordan", "p-sam", "p-owner-sa"
PROFILE = {"preset": "law", "parser": "fast_text", "split_by": "article"}


def law(topic: str, articles: int = 4, suffix: str = "") -> bytes:
    """A small law as plain text: numbered articles of a few sentences each, all about one topic."""
    parts = []
    for i in range(1, articles + 1):
        parts.append(
            f"Article {i} {topic.title()} rules\n\n"
            f"The competent authority sets the conditions for {topic} under article {i}, and every holder must keep records of {topic} "
            f"for at least five years and show them on request. {suffix}"
        )
    return "\n\n".join(parts).encode()


class Library:
    """A client for the library routes, and a worker that runs the jobs they queue."""

    def __init__(self, http, app, clock):
        self.http, self.app, self.clock = http, app, clock
        self.n = 0

    def as_(self, person: str) -> dict:
        return as_person(person)

    async def run_jobs(self) -> None:
        await Worker(self.app.state.rt).run_due()

    async def register(self, title: str, **extra) -> str:
        body = {"title": title, "issuer": "Test authority", "jurisdiction": "Test", "shelf": "shared", "doc_type": "law", "language": "en", "update_pattern": "fixed", **extra}
        r = await self.http.post("/api/v1/documents", json=body, headers=self.as_(ADMIN))
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def upload(self, doc_id: str, data: bytes, label: str = "v") -> dict:
        r = await self.http.post(f"/api/v1/documents/{doc_id}/versions", files={"file": ("law.txt", data)}, data={"version_label": label}, headers=self.as_(ADMIN))
        assert r.status_code == 201, r.text
        return r.json()

    async def ingest(self, version_id: str, mode: str = "full", profile: dict | None = None, expect: int = 202):
        r = await self.http.post(f"/api/v1/document-versions/{version_id}/ingest-jobs", json={"mode": mode, "profile": profile or PROFILE}, headers=self.as_(ADMIN))
        assert r.status_code == expect, r.text
        if expect != 202:
            return r
        await self.run_jobs()
        job = (await self.http.get(f"/api/v1/ingest-jobs/{r.json()['id']}", headers=self.as_(ADMIN))).json()
        return job

    async def review(self, version_id: str, decision: str = "approved", notes: str | None = None):
        return await self.http.post(f"/api/v1/document-versions/{version_id}/review", json={"decision": decision, "notes": notes}, headers=self.as_(OWNER))

    async def adopt(self, version_id: str, status: str = "in_force", workspace: str = "ws-ae", person: str = OWNER, level: int = 1):
        r = await self.http.put(f"/api/v1/workspaces/{workspace}/adoptions/{version_id}", json={"legal_status": status, "authority_level": level}, headers=self.as_(person))
        assert r.status_code == 200, r.text
        return r.json()

    async def search(self, query: str, person: str = OWNER, **body):
        r = await self.http.post("/api/v1/kb-search", json={"query": query, **body}, headers=self.as_(person))
        assert r.status_code == 200, r.text
        return r.json()

    async def snapshot(self, label: str = "Pinned", workspace: str = "ws-ae", person: str = OWNER) -> str:
        r = await self.http.post(f"/api/v1/workspaces/{workspace}/kb-snapshots", json={"label": label}, headers=self.as_(person))
        assert r.status_code == 201, r.text
        return r.json()["id"]

    async def chunks(self, version_id: str) -> list[dict]:
        r = await self.http.get(f"/api/v1/document-versions/{version_id}/chunks?limit=200", headers=self.as_(ADMIN))
        assert r.status_code == 200, r.text
        return r.json()["items"]

    async def quality(self, version_id: str) -> dict:
        return (await self.http.get(f"/api/v1/document-versions/{version_id}/quality", headers=self.as_(ADMIN))).json()

    async def process(self, version_id: str, notes: str | None = None) -> dict:
        """The path a version takes: a trial, a second person's approval, then the full run."""
        trial = await self.ingest(version_id, "trial")
        assert trial["status"] == "completed", trial
        assert (await self.review(version_id, "approved" if not notes else "approved_with_notes", notes)).status_code == 201
        full = await self.ingest(version_id, "full")
        assert full["status"] == "completed", full
        return full

    async def publish(self, title: str, data: bytes, status: str = "in_force", **extra) -> tuple[str, str]:
        """Register, add, ingest, approve and adopt. Returns the document and version ids."""
        doc = await self.register(title, **extra)
        v = (await self.upload(doc, data))["id"]
        await self.process(v)
        await self.adopt(v, status)
        return doc, v


@pytest_asyncio.fixture
async def world(engine):
    """Two countries with their own owner, and the demo people of the first."""
    async with make_sessions(engine)() as db, db.begin():
        for tenant, code in (("ws-ae", "AE"), ("ws-sa", "SA")):
            ctx = Ctx(None, db, Actor(type="system", id="test", tenant_id=tenant), Tenant(tenant, "simulation"))
            STORES["workspace"].add(ctx, m.Workspace(
                id=tenant, name=code, country_code=code, authority_name="Authority", languages=["en"], time_zone="UTC", day_counting="mon_fri",
                data_location="local", mode="simulation", created_at=START))
        if await db.get(PersonRow, OTHER_OWNER) is None:
            db.add(PersonRow(id=OTHER_OWNER, tenant_id="ws-sa", display_name="Owner SA", view="process_owner", is_demo=True, created_at=START - timedelta(days=1)))


@pytest_asyncio.fixture
async def lib(make_app, clock, world):
    app = make_app()
    async with client_for(app) as http:
        yield Library(http, app, clock)


@pytest_asyncio.fixture
async def lib_small_batches(make_app, clock, world):
    """Two chunks are embedded per step, so a run takes several steps."""
    app = make_app(settings={"embed_batch": 2})
    async with client_for(app) as http:
        yield Library(http, app, clock)
