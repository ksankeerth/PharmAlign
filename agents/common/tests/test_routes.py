import asyncio

import httpx
import pytest
from pharmalign_agent_common import BudgetExceeded, RunOutcome, create_app
from pharmalign_contracts.agents.run_agents import DossierReadResult
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION

from conftest import good_result, make_body


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent")


async def finished(client, headers, run_id, path="/dossier-reads"):
    for _ in range(100):
        r = await client.get(f"{path}/{run_id}", headers=headers)
        if r.json()["status"] not in ("queued", "running"):
            return r
        await asyncio.sleep(0.01)
    raise AssertionError("run didn't finish")


def instant(result: dict):
    async def run(request):
        return RunOutcome(result=DossierReadResult.model_validate(result))

    return run


async def test_start_poll_result(spec, settings, headers):
    gate = asyncio.Event()

    async def run(request):
        await gate.wait()
        return await instant(good_result())(request)

    async with client_for(create_app(spec, settings, run)) as client:
        started = await client.post("/dossier-reads", json=make_body(), headers=headers)
        assert started.status_code == 202
        assert started.headers[CONTRACT_HEADER] == CONTRACT_VERSION
        assert started.json()["status"] in ("queued", "running")
        # same run, same body, still running: 202
        assert (await client.post("/dossier-reads", json=make_body(), headers=headers)).status_code == 202
        gate.set()
        done = await finished(client, headers, "case-1042-s2-reader-g1-a1")
        body = done.json()
        assert body["status"] == "completed" and body["finish_reason"] == "stop"
        assert body["result"]["facts"][0]["key"] == "shelf_life_months"
        assert body["agent"]["name"] == "dossier-reader" and body["agent"]["prompt_hash"].startswith("sha256:")
        # finished, same body: 200. Different body: 409.
        assert (await client.post("/dossier-reads", json=make_body(), headers=headers)).status_code == 200
        other = make_body(max_tool_calls=3)
        reused = await client.post("/dossier-reads", json=other, headers=headers)
        assert reused.status_code == 409 and reused.json()["code"] == "run_id_reused"


async def test_delete_cancels_and_a_late_finish_is_ignored(spec, settings, headers):
    gate = asyncio.Event()

    async def stubborn(request):
        # ignores cancellation, then finishes late
        await asyncio.shield(gate.wait())
        return RunOutcome(result=DossierReadResult.model_validate(good_result()))

    async with client_for(create_app(spec, settings, stubborn)) as client:
        await client.post("/dossier-reads", json=make_body(), headers=headers)
        await asyncio.sleep(0.02)
        deleted = await client.delete("/dossier-reads/case-1042-s2-reader-g1-a1", headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "cancelled" and deleted.json()["finish_reason"] == "cancelled"
        gate.set()
        await asyncio.sleep(0.05)
        polled = (await client.get("/dossier-reads/case-1042-s2-reader-g1-a1", headers=headers)).json()
        assert polled["status"] == "cancelled" and polled["result"] is None


async def test_unknown_run_is_404(spec, settings, headers):
    async with client_for(create_app(spec, settings, instant(good_result()))) as client:
        for method in (client.get, client.delete):
            r = await method("/dossier-reads/no-such-run-1", headers=headers)
            assert r.status_code == 404 and r.json()["code"] == "run_not_found"
        assert (await client.get("/dossier-reads/x", headers=headers)).json()["code"] == "invalid_run_id"


async def test_contract_header_is_checked(spec, settings, headers):
    async with client_for(create_app(spec, settings, instant(good_result()))) as client:
        missing = {k: v for k, v in headers.items() if k != CONTRACT_HEADER}
        r = await client.post("/dossier-reads", json=make_body(), headers=missing)
        assert r.status_code == 422 and r.json()["code"] == "contract_header_missing"
        wrong = {**headers, CONTRACT_HEADER: "9.0.0"}
        r = await client.post("/dossier-reads", json=make_body(), headers=wrong)
        assert r.status_code == 422 and r.json()["code"] == "contract_version_unsupported"
        assert r.headers["content-type"].startswith("application/problem+json")


async def test_a_run_starts_with_no_authorization_header(spec, settings, headers):
    assert "Authorization" not in headers
    async with client_for(create_app(spec, settings, instant(good_result()))) as client:
        r = await client.post("/dossier-reads", json=make_body(), headers=headers)
        assert r.status_code == 202
        assert (await client.get("/dossier-reads/case-1042-s2-reader-g1-a1", headers=headers)).status_code == 200


async def test_breaking_the_contract_is_422(spec, settings, headers):
    async with client_for(create_app(spec, settings, instant(good_result()))) as client:
        r = await client.post("/dossier-reads", json={"run_id": "case-1042-s2-reader-g1-a1"}, headers=headers)
        assert r.status_code == 422 and r.json()["code"] == "contract_violation"


async def test_health_needs_no_header(spec, settings):
    async with client_for(create_app(spec, settings, instant(good_result()))) as client:
        r = await client.get("/health")
        assert r.status_code == 200 and r.json() == {"status": "ok"}
        assert (await client.get("/health/details")).status_code == 404


def test_the_router_can_leave_health_to_the_host_app(spec, settings):
    from pharmalign_agent_common import build_router

    paths = {route.path for route in build_router(spec, settings, health=False).routes}
    assert "/health" not in paths and "/dossier-reads" in paths


async def test_budget_stops_early_without_a_result(spec, settings, headers):
    async def over_budget(request):
        raise BudgetExceeded("too many")

    async with client_for(create_app(spec, settings, over_budget)) as client:
        await client.post("/dossier-reads", json=make_body(), headers=headers)
        body = (await finished(client, headers, "case-1042-s2-reader-g1-a1")).json()
        assert body["status"] == "stopped_early" and body["finish_reason"] == "budget" and body["result"] is None


async def test_deadline_stops_early_without_a_result(spec, settings, headers, monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda seconds: real_timeout(0.05))  # the contract's floor is 5 s

    async def slow(request):
        await asyncio.sleep(30)

    async with client_for(create_app(spec, settings, slow)) as client:
        await client.post("/dossier-reads", json=make_body(), headers=headers)
        body = (await finished(client, headers, "case-1042-s2-reader-g1-a1")).json()
        assert body["status"] == "stopped_early" and body["finish_reason"] == "deadline" and body["result"] is None


async def test_crash_is_an_honest_failure(spec, settings, headers):
    async def boom(request):
        raise RuntimeError("tools unreachable")

    async with client_for(create_app(spec, settings, boom)) as client:
        await client.post("/dossier-reads", json=make_body(), headers=headers)
        body = (await finished(client, headers, "case-1042-s2-reader-g1-a1")).json()
        assert body["status"] == "failed" and body["finish_reason"] == "error"
        assert body["error"]["code"] == "internal_error" and body["result"] is None
