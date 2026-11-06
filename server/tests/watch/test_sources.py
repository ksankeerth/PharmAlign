"""A source that errors or goes quiet is unhealthy, visibly, and a grant that relies on it needs a reason."""
from datetime import timedelta

from sqlalchemy import update

from app.db import make_sessions
from app.jobs import run_tick
from app.tables import ROW
from app.watch.sources import SourceError, SourceFetcher

from ..conftest import START
from .conftest import AGENCY_A, UNKNOWN, deliver, headers, rows
from .test_effects import package, sign

A_SOURCES = ("src-raa-notices", "src-raa-recalls")


async def relies_on_agency_a(http):
    r = await http.post("/api/v1/cases/CASE-1044/reliance-basis", json={"regulator": AGENCY_A, "approval_ref": "REF-1", "approved_on": "2026-01-05"}, headers=headers("p-alex"))
    assert r.status_code == 201, r.text


async def health_of(engine, source_id):
    [row] = await rows(engine, "watch_source", ROW["watch_source"].id == source_id)
    return row.health, row.doc["last_error"]


async def sign_with(http, reason=None):
    pkg = await package(http, "CASE-1044")
    body = {"type": "grant", "fingerprint": pkg["fingerprint"], "recommendation_version": pkg["recommendation"]["version"], "accepts_recommendation": True,
            "reason": None, "conditions": [], "foreign_status_reason": reason}
    return await http.post("/api/v1/cases/CASE-1044/decisions", json=body, headers=headers("p-dana"))


async def test_a_source_that_fails_is_unhealthy_and_a_grant_then_needs_a_reason(make_app, engine):
    async def broken(rt, tenant_id, source):
        raise SourceError("The agency's server answered 503.")

    from ..conftest import client_for

    app = make_app(fetcher=SourceFetcher(readers={"staged": broken, "feed": broken}))
    async with client_for(app) as http:
        await relies_on_agency_a(http)
        refused = await sign_with(http)
        assert refused.status_code == 409 and {g["guard"] for g in refused.json()["failed"]} == {"foreign_sources_fresh"}
        assert await health_of(engine, "src-raa-notices") == ("failing", "The agency's server answered 503.")
        shown = {s["id"]: s["health"] for s in (await http.get("/api/v1/watch/sources", headers=headers("p-alex"))).json()["items"]}
        assert shown["src-raa-notices"] == "failing"
        assert "src-raa-notices" in (await http.get("/api/v1/watch/coverage", headers=headers("p-alex"))).json()["unhealthy_sources"]
        told = await rows(engine, "notification_delivery", ROW["notification_delivery"].event == "source_unhealthy")
        assert told and "src-raa-notices" not in told[0].id
        allowed = await sign_with(http, "The agency's feed is down; its site was read by hand today.")
        assert allowed.status_code == 201, allowed.text


async def test_a_source_that_yields_nothing_for_too_long_is_unhealthy_and_a_grant_then_needs_a_reason(make_app, engine, clock):
    async def nothing(rt, tenant_id, source):
        return []

    from ..conftest import client_for

    async with make_sessions(engine)() as db, db.begin():
        for source_id in A_SOURCES:
            await db.execute(update(ROW["watch_source"]).where(ROW["watch_source"].id == source_id).values(last_item_at=START - timedelta(days=15)))
    app = make_app(fetcher=SourceFetcher(readers={"staged": nothing, "feed": nothing}))
    async with client_for(app) as http:
        await relies_on_agency_a(http)
        refused = await sign_with(http)
        assert refused.status_code == 409 and {g["guard"] for g in refused.json()["failed"]} == {"foreign_sources_fresh"}
        health, error = await health_of(engine, "src-raa-notices")
        assert health == "zero_yield" and "15 days" in error
        assert (await sign_with(http, "Nothing was published; checked the agency's page by hand.")).status_code == 201


async def test_a_quiet_source_that_is_not_silent_yet_stays_healthy(make_app, engine):
    from ..conftest import client_for

    app = make_app()
    async with client_for(app) as http:
        r = await http.post("/api/v1/watch/sources/src-raa-notices/fetch", headers=headers("p-jordan"))
    assert r.status_code == 200 and r.json()["health"] == "healthy" and r.json()["new_notices"] == 0


async def test_the_tick_reads_sources_that_are_due_and_marks_the_silent_ones(make_app, engine, clock):
    app = make_app()
    rt = app.state.rt
    async with make_sessions(engine)() as db, db.begin():
        await db.execute(update(ROW["watch_source"]).where(ROW["watch_source"].id == "src-raa-recalls").values(last_item_at=START - timedelta(days=20)))
    clock.advance(hours=3)  # past every source's check interval
    done = await run_tick(rt, ["watch"])
    assert done["watch"] >= 2  # the two readable sources; the changed layout and the manual one wait for a person
    assert (await health_of(engine, "src-raa-recalls"))[0] == "zero_yield"
    assert (await health_of(engine, "src-raa-notices"))[0] == "healthy"
    assert (await health_of(engine, "src-rab-safety"))[0] == "layout_changed"


async def test_a_staged_item_is_read_once_through_the_same_entry_point(make_app, engine):
    import uuid

    from app.pipeline.runner import system_tx
    from ..conftest import TENANT

    app = make_app()
    rt = app.state.rt
    async with system_tx(rt, TENANT) as ctx:
        ctx.db.add(ROW["watch_staged_item"](id="stg-1", tenant_id=TENANT, source_id="src-raa-notices", notice=UNKNOWN, staged_at=ctx.now()))
    first = await rt.fetcher.fetch(rt, TENANT, ["src-raa-notices"])
    second = await rt.fetcher.fetch(rt, TENANT, ["src-raa-notices"])
    assert first == {"src-raa-notices": (1, 0)} and second == {"src-raa-notices": (0, 0)}
    [notice] = [n for n in await rows(engine, "foreign_notice") if n.doc["title"] == UNKNOWN["title"]]
    assert notice.source_id == "src-raa-notices"
    assert (await health_of(engine, "src-raa-notices"))[0] == "healthy"
