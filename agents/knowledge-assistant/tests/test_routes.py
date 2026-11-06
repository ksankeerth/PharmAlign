"""The session routes: contract header, bodies, unknown sessions, one /health."""
import httpx
from fastapi import FastAPI
from pharmalign_agent_common import Settings, build_session_router
from pharmalign_contracts.agents.knowledge_assistant import SessionCreated
from pharmalign_contracts.common import CONTRACT_HEADER, Problem

from app.agent import SPEC
from tests.conftest import HEADERS, chat, session_body


async def test_open_a_session_gives_201_and_an_id():
    async with chat([]) as c:
        r = await c.client.post("/sessions", json=session_body(), headers=HEADERS)
    assert r.status_code == 201 and r.headers[CONTRACT_HEADER]
    assert SessionCreated.model_validate(r.json()).agent_session_id.startswith("asa-")


async def test_the_contract_header_is_required_on_both_routes():
    async with chat([]) as c:
        sid = await c.open()
        for method, url, body in (("POST", "/sessions", session_body()), ("POST", f"/sessions/{sid}/turns", {"message": "Hi"})):
            missing = await c.client.request(method, url, json=body)
            assert missing.status_code == 422 and Problem.model_validate(missing.json()).code == "contract_header_missing"
            wrong = await c.client.request(method, url, json=body, headers={CONTRACT_HEADER: "9.0.0"})
            assert wrong.status_code == 422 and Problem.model_validate(wrong.json()).code == "contract_version_unsupported"


async def test_a_body_that_breaks_the_contract_is_refused():
    async with chat([]) as c:
        sid = await c.open()
        for url, body in (("/sessions", {**session_body(), "extra": 1}), ("/sessions", {"run_token": "short"}),
                          (f"/sessions/{sid}/turns", {"message": ""}), (f"/sessions/{sid}/turns", {"message": "Hi", "mode": "x"})):
            r = await c.client.post(url, json=body, headers=HEADERS)
            assert r.status_code == 422 and Problem.model_validate(r.json()).code == "contract_violation"


async def test_an_unknown_session_gives_404_so_the_caller_starts_a_new_one():
    async with chat([]) as c:
        r = await c.client.post("/sessions/asa-unknown/turns", json={"message": "Hi"}, headers=HEADERS)
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")
    assert Problem.model_validate(r.json()).code == "session_not_found"


async def test_a_restart_forgets_sessions():
    async with chat([]) as first:
        sid = await first.open()
    async with chat([]) as second:  # a new router has new, empty memory
        r = await second.client.post(f"/sessions/{sid}/turns", json={"message": "Hi"}, headers=HEADERS)
    assert r.status_code == 404


async def test_the_router_has_the_contract_routes_and_exactly_one_health():
    router = build_session_router(SPEC, Settings(mcp_url="http://127.0.0.1:1/mcp"))
    assert sorted((r.path, sorted(r.methods)) for r in router.routes) == [
        ("/health", ["GET"]), ("/sessions", ["POST"]), ("/sessions/{session_id}/turns", ["POST"])]
    app = FastAPI()
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
        assert (await client.get("/health")).json() == {"status": "ok"}  # no header needed
    host = FastAPI()  # a host that already serves /health gets none from the router
    host.include_router(build_session_router(SPEC, Settings(mcp_url="http://127.0.0.1:1/mcp"), health=False))
    assert "/health" not in host.openapi()["paths"]
