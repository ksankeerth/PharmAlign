"""The demo status, and a reset to the seed. A reset empties the database and loads it again, so each test ends on the seed."""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from app import seed
from app.config import Settings
from app.db import make_sessions
from app.handlers import demo
from app.main import create_app
from app.pipeline import runner
from app.seed import SEED_DIR
from app.tables import ROW, DemoStateRow

from ..conftest import SERVICE_TOKEN, TEST_URL, FixedIdentity, client_for
from .conftest import count_rows, headers

pytestmark = pytest.mark.seeded
ADMIN, OWNER = headers("p-jordan"), headers("p-sam")


def seed_file(name: str) -> list[dict]:
    return json.loads((SEED_DIR / f"{name}.json").read_text())


SEED_WORKSPACES = [r["doc"]["id"] for r in seed_file("workspace")]
SEED_PEOPLE = sum(1 for r in seed_file("person") if r["doc"]["is_demo"])
SEED_CASES = len(seed_file("case"))
NEW_COUNTRY = {
    "name": "Qatar (synthetic)", "country_code": "QAT", "authority_name": "Synthetic Medicines Authority", "languages": ["en"],
    "time_zone": "Asia/Qatar", "day_counting": "sun_thu", "data_location": "Synthetic region", "template": "platform",
}
RESET = {"confirm": "RESET", "with_library": False}


async def state(engine) -> DemoStateRow | None:
    async with make_sessions(engine)() as db:
        return await db.get(DemoStateRow, 1)


async def wait_for_reset(http, seconds: float = 60) -> dict:
    for _ in range(int(seconds * 20)):
        status = (await http.get("/api/v1/demo", headers=ADMIN)).json()
        if not status["resetting"]:
            return status
        await asyncio.sleep(0.05)
    raise AssertionError("the reset did not finish")


@pytest.fixture
def held(monkeypatch):
    """Makes the load wait until the test lets it go, so a reset can be seen while it runs."""
    gate = asyncio.Event()

    async def slow(*args, **kwargs):
        await gate.wait()
        return {}

    monkeypatch.setattr(demo, "load_seed", slow)
    return gate


async def test_the_status_counts_what_is_loaded(http):
    status = (await http.get("/api/v1/demo", headers=ADMIN)).json()
    assert [w["id"] for w in status["workspaces"]] == SEED_WORKSPACES
    assert status["people"] == SEED_PEOPLE and status["cases"] == SEED_CASES
    assert set(status["workspaces"][0]) == {"id", "name", "country_code", "authority_name", "mode"}


async def test_a_reset_loads_the_seed_again(http, engine):
    made = await http.post("/api/v1/workspaces", json=NEW_COUNTRY, headers=ADMIN)
    assert made.status_code == 201, made.text
    thread = await http.post("/api/v1/cases/CASE-1046/threads", json={"target": {"type": "check", "ref": "cr-1046-lbl-02"}, "text": "Check the leaflet."}, headers=headers("p-alex"))
    assert thread.status_code == 201, thread.text
    assert (await http.get("/api/v1/demo", headers=ADMIN)).json()["workspaces"][-1]["id"] == made.json()["id"]

    reply = await http.post("/api/v1/demo/reset", json=RESET, headers=ADMIN)
    assert reply.status_code == 202, reply.text
    assert reply.json()["resetting"] is True
    status = await wait_for_reset(http)

    assert [w["id"] for w in status["workspaces"]] == SEED_WORKSPACES
    assert status["people"] == SEED_PEOPLE and status["cases"] == SEED_CASES
    assert status["loaded_at"] is not None and status["with_library"] is False
    assert await count_rows(engine, "review_thread", id=thread.json()["id"]) == 0


async def test_a_reset_expires_the_session_cookies(http):
    http.cookies.set("pharmalign_session", "p-jordan")
    http.cookies.set("pharmalign_workspace", "ws-om")
    reply = await http.post("/api/v1/demo/reset", json=RESET)
    assert reply.status_code == 202, reply.text
    expired = {c.split("=")[0]: c for c in reply.headers.get_list("set-cookie")}
    assert set(expired) == {"pharmalign_session", "pharmalign_workspace"}
    assert all("max-age=0" in c.lower() for c in expired.values())
    await wait_for_reset(http)


async def test_a_second_reset_is_refused_while_one_runs(http, held, engine):
    first = await http.post("/api/v1/demo/reset", json=RESET, headers=ADMIN)
    assert first.status_code == 202 and first.json()["resetting"] is True
    second = await http.post("/api/v1/demo/reset", json=RESET, headers=ADMIN)
    assert second.status_code == 409 and second.json()["code"] == "reset_running"
    assert (await http.get("/api/v1/demo", headers=ADMIN)).json()["resetting"] is True
    held.set()
    status = await wait_for_reset(http)
    assert status["resetting"] is False
    assert (await state(engine)).resetting is False
    held.clear()


async def test_a_failed_reset_clears_the_flag_and_keeps_the_time(http, engine, monkeypatch, caplog):
    async with make_sessions(engine)() as db, db.begin():
        await seed.write_demo_state(db, datetime(2026, 1, 2, tzinfo=timezone.utc), True)

    async def broken(*args, **kwargs):
        raise RuntimeError("the load broke")

    monkeypatch.setattr(demo, "load_seed", broken)
    assert (await http.post("/api/v1/demo/reset", json=RESET, headers=ADMIN)).status_code == 202
    status = await wait_for_reset(http)
    assert status["loaded_at"].startswith("2026-01-02") and status["with_library"] is True
    assert "demo reset failed" in caplog.text
    assert (await http.get("/api/v1/demo", headers=ADMIN)).json()["cases"] == SEED_CASES


async def test_a_deadlock_while_emptying_the_tables_is_tried_again(http, monkeypatch):
    class Deadlock(Exception):
        sqlstate = "40P01"

    calls = []

    async def once_then_works(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise DBAPIError("TRUNCATE", {}, Deadlock())
        return {}

    monkeypatch.setattr(demo, "load_seed", once_then_works)
    assert (await http.post("/api/v1/demo/reset", json=RESET, headers=ADMIN)).status_code == 202
    await wait_for_reset(http)
    assert len(calls) == 2 and calls[0]["reset"] is True and calls[0]["with_library"] is False


async def test_unfinished_runs_are_picked_up_after_the_load(engine, clock, monkeypatch):
    seen = []

    async def spy(rt):
        # The load is over and the flag is still up.
        row = await state(engine)
        seen.append((await count_rows(engine, "case"), row.resetting))

    monkeypatch.setattr(runner, "pick_up_unfinished", spy)
    app = create_app(settings=Settings(env="simulation", database_url=TEST_URL, service_token=SERVICE_TOKEN, worker_enabled=True), clock=clock)
    try:
        async with client_for(app) as http:
            assert (await http.post("/api/v1/demo/reset", json=RESET, headers=ADMIN)).status_code == 202
            await wait_for_reset(http)
    finally:
        await app.state.rt.engine.dispose()
    assert seen == [(SEED_CASES, True)]


async def test_a_reset_forgets_the_runs_the_replay_has_started(engine, clock):
    app = create_app(settings=Settings(env="simulation", database_url=TEST_URL, service_token=SERVICE_TOKEN, worker_enabled=False), clock=clock)
    try:
        app.state.rt.agents.runs["case-1043-s1-brief-g1-a1"] = object()
        async with client_for(app) as http:
            assert (await http.post("/api/v1/demo/reset", json=RESET, headers=ADMIN)).status_code == 202
            await wait_for_reset(http)
        assert app.state.rt.agents.runs == {}
    finally:
        await app.state.rt.engine.dispose()


async def test_a_reset_outside_simulation_is_refused(make_app, engine):
    before = await state(engine)
    before = before and (before.loaded_at, before.with_library, before.resetting)
    for env in ("shadow", "live"):
        async with client_for(make_app(env=env, identity=FixedIdentity("platform_admin"))) as http:
            refused = await http.post("/api/v1/demo/reset", json=RESET)
            assert refused.status_code == 403 and refused.json()["code"] == "simulation_only"
            assert (await http.get("/api/v1/demo")).status_code == 403
    after = await state(engine)
    assert (after and (after.loaded_at, after.with_library, after.resetting)) == before


async def test_only_a_platform_admin_may_look_or_reset(http, engine):
    for person in ("p-sam", "p-alex", "p-robin"):
        assert (await http.get("/api/v1/demo", headers=headers(person))).status_code == 403
        assert (await http.post("/api/v1/demo/reset", json=RESET, headers=headers(person))).status_code == 403
    row = await state(engine)
    assert row is None or row.resetting is False


async def test_the_reset_needs_the_word_reset(http):
    assert (await http.post("/api/v1/demo/reset", json={"confirm": "yes", "with_library": False}, headers=ADMIN)).status_code == 422


async def test_the_command_line_load_still_works(engine, monkeypatch):
    monkeypatch.setenv("PHARMALIGN_ENV", "simulation")
    monkeypatch.setenv("PHARMALIGN_DATABASE_URL", TEST_URL)
    assert await seed.main(["--reset"]) == 0
    row = await state(engine)
    assert row.loaded_at is not None and row.with_library is False and row.resetting is False
    assert await count_rows(engine, "case") == SEED_CASES
    async with make_sessions(engine)() as db:
        assert await db.scalar(select(func.count()).select_from(ROW["workspace"])) == len(SEED_WORKSPACES)


async def test_the_command_line_refuses_what_it_always_did(engine, monkeypatch):
    monkeypatch.setenv("PHARMALIGN_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("PHARMALIGN_ENV", "live")
    assert await seed.main(["--reset"]) == 2
    monkeypatch.setenv("PHARMALIGN_ENV", "shadow")
    assert await seed.main(["--with-library"]) == 2
