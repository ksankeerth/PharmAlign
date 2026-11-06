"""One transaction per request: state, audit, events and jobs commit together or not at all."""
import pytest
from pharmalign_contracts.server.models import Person
from sqlalchemy import select

from app import audit
from app.db import make_sessions
from app.problems import invalid
from app.tables import AuditEventRow, DomainEventRow, JobRow, PersonRow

from .conftest import TENANT, as_person, client_for

SEEDED = 8
ROUTE = ("POST", "/api/v1/people")


async def _noop(payload, ctx):
    pass


def adder(after_work):
    """Writes state, audit, a timeline event, a notification and a job, then does `after_work`."""

    async def handler(req, ctx):
        person_id = ctx.id("p")
        ctx.db.add(PersonRow(id=person_id, tenant_id=ctx.tenant.id, display_name=req.body.display_name, view="case_officer", is_demo=True, created_at=ctx.now()))
        ctx.audit(action="person_created", object_type="person", object_id=person_id, after={"name": req.body.display_name})
        ctx.timeline("c-1", "person_added", "A person was added")
        ctx.notify("p-sam", "person_added", "info", "A person was added")
        ctx.schedule("noop", {"person_id": person_id}, ctx.now())
        return after_work(person_id, req)

    return handler


def succeed(person_id, req):
    return Person(id=person_id, display_name=req.body.display_name, is_demo=True)


def refuse(person_id, req):
    invalid("Refused after writing.")


def crash(person_id, req):
    raise RuntimeError("boom")


def break_the_contract(person_id, req):
    return {"id": person_id}


@pytest.mark.parametrize("after_work,status", [(refuse, 422), (crash, 500), (break_the_contract, 500)])
async def test_a_failing_handler_rolls_everything_back(make_app, count, after_work, status):
    app = make_app(handlers={ROUTE: adder(after_work)}, tasks={"noop": _noop})
    async with client_for(app) as client:
        response = await client.post("/api/v1/people", json={"display_name": "New"}, headers=as_person("p-jordan"))
    assert response.status_code == status, response.text
    assert await count(PersonRow) == SEEDED
    assert await count(AuditEventRow) == 0
    assert await count(DomainEventRow) == 0
    assert await count(JobRow) == 0


async def test_a_good_handler_commits_everything(make_app, count, engine):
    app = make_app(handlers={ROUTE: adder(succeed)}, tasks={"noop": _noop})
    async with client_for(app) as client:
        response = await client.post("/api/v1/people", json={"display_name": "New"}, headers=as_person("p-jordan"))
    assert response.status_code == 201, response.text
    assert await count(PersonRow) == SEEDED + 1
    assert await count(AuditEventRow) == 1
    assert await count(DomainEventRow) == 2
    assert await count(JobRow) == 1
    async with make_sessions(engine)() as db:
        assert await db.scalar(select(AuditEventRow.role_used)) == "platform_admin"
        assert (await audit.verify(db, TENANT)).ok


async def test_scheduling_an_unbound_job_fails_the_request(make_app, count):
    app = make_app(handlers={ROUTE: adder(succeed)}, tasks={})
    async with client_for(app) as client:
        response = await client.post("/api/v1/people", json={"display_name": "New"}, headers=as_person("p-jordan"))
    assert response.status_code == 500
    assert await count(PersonRow) == SEEDED
