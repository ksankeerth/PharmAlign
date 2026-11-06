"""The same key returns the first response and changes nothing."""
import asyncio

from pharmalign_contracts.server.models import Person

from app.ops import OPERATIONS
from app.tables import AuditEventRow, IdempotencyKeyRow, PersonRow

from .conftest import as_person, client_for

SEEDED = 8
ROUTE = ("POST", "/api/v1/people")
ADMIN = as_person("p-jordan")


async def add_person(req, ctx):
    person_id = ctx.id("p")
    ctx.db.add(PersonRow(id=person_id, tenant_id=ctx.tenant.id, display_name=req.body.display_name, view="case_officer", is_demo=True, created_at=ctx.now()))
    ctx.audit(action="person_created", object_type="person", object_id=person_id)
    return Person(id=person_id, display_name=req.body.display_name, is_demo=True)


def empty_response(key, calls):
    """A handler that records the call and returns the operation's model, unfilled, because these tests don't read it."""

    async def handler(req, ctx):
        calls.append(req)
        return OPERATIONS[key].response.model_construct()

    return handler


def post(client, key, name="New", headers=ADMIN):
    return client.post("/api/v1/people", json={"display_name": name}, headers={**headers, "Idempotency-Key": key})


async def test_repeat_returns_the_same_response_with_one_change(make_app, count):
    async with client_for(make_app(handlers={ROUTE: add_person})) as client:
        first = await post(client, "k1")
        second = await post(client, "k1")
    assert first.status_code == second.status_code == 201
    assert first.content == second.content
    assert second.headers["idempotency-replayed"] == "true"
    assert await count(PersonRow) == SEEDED + 1
    assert await count(AuditEventRow) == 1


async def test_without_a_key_each_request_counts(make_app, count):
    async with client_for(make_app(handlers={ROUTE: add_person})) as client:
        await client.post("/api/v1/people", json={"display_name": "A"}, headers=ADMIN)
        await client.post("/api/v1/people", json={"display_name": "A"}, headers=ADMIN)
    assert await count(PersonRow) == SEEDED + 2
    assert await count(IdempotencyKeyRow) == 0


async def test_concurrent_repeats_run_once(make_app, count):
    async with client_for(make_app(handlers={ROUTE: add_person})) as client:
        responses = await asyncio.gather(*(post(client, "k1") for _ in range(5)))
    assert {r.status_code for r in responses} == {201}
    assert len({r.content for r in responses}) == 1
    assert await count(PersonRow) == SEEDED + 1


async def test_a_key_reused_for_another_request_is_refused(make_app, count):
    async with client_for(make_app(handlers={ROUTE: add_person})) as client:
        await post(client, "k1", "First")
        response = await post(client, "k1", "Second")
    assert response.status_code == 422
    assert response.json()["code"] == "idempotency_key_reused"
    assert await count(PersonRow) == SEEDED + 1


async def test_the_same_key_on_another_path_is_another_request(make_app):
    route = ("POST", "/api/v1/workspaces/{workspace_id}/readiness-runs")
    calls = []
    async with client_for(make_app(handlers={route: empty_response(route, calls)})) as client:
        headers = {**ADMIN, "Idempotency-Key": "k1"}
        first = await client.post("/api/v1/workspaces/ws-1/readiness-runs", headers=headers)
        again = await client.post("/api/v1/workspaces/ws-1/readiness-runs", headers=headers)
        other = await client.post("/api/v1/workspaces/ws-2/readiness-runs", headers=headers)
    assert first.status_code == again.status_code == 202
    assert again.headers["idempotency-replayed"] == "true"
    assert other.status_code == 422
    assert other.json()["code"] == "idempotency_key_reused"
    assert [c.path.workspace_id for c in calls] == ["ws-1"]


async def test_keys_belong_to_the_caller(make_app, count):
    route = ("POST", "/api/v1/conflicts")
    calls = []
    body = {"person_id": "p-alex", "party_id": "pt-1", "reason": "Family member works there."}

    async def declare(req, ctx):
        calls.append(req)
        return {**req.body.model_dump(), "id": ctx.id("coi"), "declared_at": ctx.now()}

    async with client_for(make_app(handlers={route: declare})) as client:
        for person in ("p-alex", "p-sam"):
            response = await client.post("/api/v1/conflicts", json=body, headers={**as_person(person), "Idempotency-Key": "k1"})
            assert response.status_code == 201, response.text
            assert "idempotency-replayed" not in response.headers
    assert len(calls) == 2
    assert await count(IdempotencyKeyRow) == 2


async def test_a_failed_attempt_leaves_the_key_free(make_app, count):
    attempts = []

    async def flaky(req, ctx):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("boom")
        return await add_person(req, ctx)

    async with client_for(make_app(handlers={ROUTE: flaky})) as client:
        assert (await post(client, "k1")).status_code == 500
        assert (await post(client, "k1")).status_code == 201
    assert await count(PersonRow) == SEEDED + 1


async def test_a_long_key_is_refused(make_app):
    async with client_for(make_app(handlers={ROUTE: add_person})) as client:
        response = await post(client, "k" * 129)
    assert response.status_code == 422


async def test_routes_not_marked_idempotent_ignore_the_header(make_app, count):
    async with client_for(make_app()) as client:
        await client.post("/api/v1/session/view-as", json={"person_id": "p-sam"}, headers={"Idempotency-Key": "k1"})
    assert await count(IdempotencyKeyRow) == 0


async def test_an_upload_with_a_key_replays(make_app):
    route = ("POST", "/api/v1/documents/{document_id}/versions")
    calls = []
    async with client_for(make_app(handlers={route: empty_response(route, calls)})) as client:
        headers = {**ADMIN, "Idempotency-Key": "k1"}
        files = {"file": ("a.pdf", b"content", "application/pdf")}
        boundary = {"content-type": "multipart/form-data; boundary=fixed"}
        first = await client.post("/api/v1/documents/d-1/versions", headers={**headers, **boundary}, files=files)
        again = await client.post("/api/v1/documents/d-1/versions", headers={**headers, **boundary}, files=files)
    assert first.status_code == again.status_code == 201, first.text
    assert again.headers["idempotency-replayed"] == "true"
    assert [c.body.file for c in calls] == [b"content"]
