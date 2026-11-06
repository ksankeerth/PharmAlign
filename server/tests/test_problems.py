"""Everything that goes wrong is problem JSON with a stable code."""
from pharmalign_contracts.common import Problem
from pharmalign_contracts.server.models import GuardProblem, Person

from app.db import make_engine, make_sessions
from app.problems import enforce_guards, guard

from .conftest import as_person, client_for


def assert_problem(response, status: int, code: str, model=Problem) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    model.model_validate(body)
    assert body["code"] == code
    return body


async def test_unknown_path_is_a_404_problem(make_app):
    async with client_for(make_app()) as client:
        assert_problem(await client.get("/api/v1/nope"), 404, "not_found")


async def test_wrong_method_is_a_405_problem(make_app):
    async with client_for(make_app()) as client:
        assert_problem(await client.delete("/api/v1/me"), 405, "method_not_allowed")


async def test_view_not_allowed_gets_403(make_app):
    async with client_for(make_app()) as client:
        # The default person is a Case Officer. Listing people is for Platform Admins.
        assert_problem(await client.get("/api/v1/people"), 403, "forbidden")
        assert (await client.get("/api/v1/people", headers=as_person("p-jordan"))).status_code == 200


async def test_validation_errors_are_the_422_problem(make_app):
    async with client_for(make_app()) as client:
        cases = [
            await client.post("/api/v1/session/view-as", json={}),
            await client.post("/api/v1/session/view-as", json={"person_id": "p-sam", "extra": 1}),
            await client.post("/api/v1/session/view-as", content=b"not json"),
            await client.post("/api/v1/session/view-as", content=b"a=1", headers={"content-type": "text/plain"}),
            await client.get("/api/v1/demo-people?limit=many"),
            await client.get("/api/v1/demo-people?nope=1"),
            await client.get("/api/v1/demo-people?cursor=abc"),
        ]
    for response in cases:
        assert_problem(response, 422, "invalid_request")
    assert "person_id" in cases[0].json()["detail"]


async def test_query_values_are_checked_against_the_contract(make_app):
    seen = {}

    async def handler(req, ctx):
        seen.update(req.query)
        return {"items": [], "next_cursor": None}

    app = make_app(handlers={("GET", "/api/v1/decisions"): handler})
    async with client_for(app) as client:
        headers = as_person("p-dana")
        assert (await client.get("/api/v1/decisions?type=grant&limit=5", headers=headers)).status_code == 200
        assert_problem(await client.get("/api/v1/decisions?type=wish", headers=headers), 422, "invalid_request")
    assert seen == {"case_id": None, "type": "grant", "state": None, "record_status": None, "cursor": None, "limit": 5}


async def test_required_headers_and_query_are_enforced(make_app):
    async def handler(req, ctx):
        raise AssertionError("should not run")

    app = make_app(handlers={("PUT", "/api/v1/pack-versions/{version_id}"): handler, ("GET", "/api/v1/pack-versions/{version_id}/diff"): handler})
    async with client_for(app) as client:
        headers = as_person("p-sam")
        assert_problem(await client.put("/api/v1/pack-versions/v1", json={}, headers=headers), 422, "invalid_request")
        assert_problem(await client.get("/api/v1/pack-versions/v1/diff", headers=headers), 422, "invalid_request")
        assert_problem(await client.get("/api/v1/pack-versions/" + "x" * 129 + "/diff?against=a", headers=headers), 422, "invalid_request")


async def test_guards_list_every_failure(make_app):
    async def handler(req, ctx):
        enforce_guards(
            [guard("one", False, "First"), guard("two", True), guard("three", False, "Third", "R3")],
            "This can't be signed yet", "sha256:77ab",
        )

    app = make_app(handlers={("POST", "/api/v1/pack-versions/{version_id}/activate"): handler})
    async with client_for(app) as client:
        response = await client.post("/api/v1/pack-versions/v-1/activate", headers=as_person("p-sam"))
    body = assert_problem(response, 409, "guard_failed", GuardProblem)
    assert [g["guard"] for g in body["failed"]] == ["one", "three"]
    assert body["failed"][1]["check_id"] == "R3"
    assert body["current_fingerprint"] == "sha256:77ab"


async def test_no_passing_guards_means_no_problem():
    enforce_guards([guard("one", True)])


async def test_a_crash_is_a_500_problem_without_details(make_app):
    async def handler(req, ctx):
        raise RuntimeError("secret detail")

    app = make_app(handlers={("GET", "/api/v1/people"): handler})
    async with client_for(app) as client:
        body = assert_problem(await client.get("/api/v1/people", headers=as_person("p-jordan")), 500, "internal_error")
    assert "secret" not in str(body)


async def test_database_down_is_a_503_problem_but_health_still_answers(make_app):
    app = make_app()
    bad = make_engine("postgresql+asyncpg://nobody:nobody@localhost:1/nothing")
    app.state.rt.sessions = make_sessions(bad)
    async with client_for(app) as client:
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert_problem(await client.get("/api/v1/me"), 503, "database_unavailable")
    await bad.dispose()


async def test_response_is_checked_against_the_contract(make_app):
    async def handler(req, ctx):
        return {"id": "p-1", "unexpected": True}

    app = make_app(handlers={("GET", "/api/v1/people/{person_id}"): handler})
    async with client_for(app) as client:
        assert_problem(await client.get("/api/v1/people/p-1", headers=as_person("p-jordan")), 500, "internal_error")


async def test_no_response_model_means_no_body(make_app):
    async def handler(req, ctx):
        return None

    app = make_app(handlers={("DELETE", "/api/v1/role-grants/{grant_id}"): handler})
    async with client_for(app) as client:
        response = await client.delete("/api/v1/role-grants/g-1", headers=as_person("p-jordan"))
    assert response.status_code == 204
    assert response.content == b""


async def test_a_model_or_a_dict_can_be_returned(make_app):
    async def handler(req, ctx):
        return Person(id=req.path.person_id, display_name="A", is_demo=True)

    app = make_app(handlers={("GET", "/api/v1/people/{person_id}"): handler})
    async with client_for(app) as client:
        response = await client.get("/api/v1/people/p-1", headers=as_person("p-jordan"))
    assert response.json() == {"id": "p-1", "display_name": "A", "is_demo": True, "grants": []}


async def test_a_stream_validates_its_events(make_app):
    async def handler(req, ctx):
        async def events():
            yield {"type": "text_delta", "text": "Hello"}

        return events()

    app = make_app(handlers={("POST", "/api/v1/assistant/sessions/{session_id}/turns"): handler})
    async with client_for(app) as client:
        response = await client.post("/api/v1/assistant/sessions/s-1/turns", json={"message": "Hi"}, headers=as_person("p-alex"))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith('data: {"type":"text_delta"')
