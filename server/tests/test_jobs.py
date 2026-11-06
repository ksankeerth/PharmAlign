"""Leases: two workers never take the same job, and an expired lease is retried."""
import asyncio

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db import make_sessions
from app.jobs import Worker
from app.main import create_app
from app.tables import AuditEventRow, JobRow

from .conftest import SERVICE, TEST_URL, as_person, client_for, system_ctx


async def noop(payload, ctx):
    pass


async def audited(payload, ctx):
    ctx.audit(action="job_ran", object_type="job", object_id=str(payload["n"]))
    ctx.timeline("c-1", "job_ran", "The job ran")


async def enqueue(rt, kind="noop", n=1, **fields):
    ids = []
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        for i in range(n):
            ids.append(ctx.schedule(kind, {"n": i}, **fields))
    return ids


async def job(engine, job_id) -> JobRow:
    async with make_sessions(engine)() as db:
        return await db.get(JobRow, job_id)


@pytest.fixture
def rt(make_app):
    return make_app(tasks={"noop": noop, "audited": audited}).state.rt


async def test_two_workers_never_take_the_same_job(rt):
    await enqueue(rt, n=20)
    one, two = Worker(rt, owner="one"), Worker(rt, owner="two")

    async def drain(worker):
        leases = []
        while lease := await worker.claim():
            leases.append(lease)
            await asyncio.sleep(0)
        return leases

    first, second = await asyncio.gather(drain(one), drain(two))
    ids = [l.job_id for l in first + second]
    assert len(ids) == 20 and len(set(ids)) == 20


async def test_a_locked_job_is_skipped_not_waited_for(rt):
    first, second = await enqueue(rt, n=2)
    locked = sorted([first, second])[0]
    async with rt.sessions() as other, other.begin():
        await other.scalar(select(JobRow).where(JobRow.id == locked).with_for_update())
        lease = await asyncio.wait_for(Worker(rt).claim(), 5)
    assert lease.job_id != locked


async def test_an_expired_lease_is_retried(rt, clock, engine):
    [job_id] = await enqueue(rt)
    one, two = Worker(rt, owner="one"), Worker(rt, owner="two")
    lease = await one.claim()
    assert lease.attempts == 1
    assert await two.claim() is None

    clock.advance(seconds=rt.settings.job_lease_seconds + 1)
    retry = await two.claim()
    assert retry.job_id == job_id
    assert retry.attempts == 2
    assert retry.token != lease.token
    assert (await job(engine, job_id)).lease_owner == "two"


async def test_a_worker_that_lost_its_lease_changes_nothing(rt, clock, engine, count):
    [job_id] = await enqueue(rt, "audited")
    one, two = Worker(rt, owner="one"), Worker(rt, owner="two")
    stale = await one.claim()
    clock.advance(seconds=rt.settings.job_lease_seconds + 1)
    current = await two.claim()

    assert await one.run(stale) is False
    assert await count(AuditEventRow) == 0
    assert (await job(engine, job_id)).status == "running"

    assert await two.run(current) is True
    assert await count(AuditEventRow) == 1
    assert (await job(engine, job_id)).status == "done"


async def test_a_job_commits_with_its_work(rt, engine, count):
    [job_id] = await enqueue(rt, "audited")
    assert await Worker(rt).run_due() == 1
    assert (await job(engine, job_id)).status == "done"
    assert await count(AuditEventRow) == 1
    assert await Worker(rt).claim() is None


async def test_a_job_due_later_waits(rt, clock):
    await enqueue(rt, run_at=clock.now().replace(year=2030))
    assert await Worker(rt).run_due() == 0
    clock.at = clock.now().replace(year=2031)
    assert await Worker(rt).run_due() == 1


async def test_a_failed_job_rolls_back_and_backs_off_until_it_gives_up(make_app, clock, engine, count):
    async def fails(payload, ctx):
        ctx.audit(action="should_not_stay", object_type="job", object_id="x")
        raise RuntimeError("boom")

    rt = make_app(tasks={"fails": fails}).state.rt
    [job_id] = await enqueue(rt, "fails", max_attempts=2)
    worker = Worker(rt)

    assert await worker.run_due() == 0
    row = await job(engine, job_id)
    assert (row.status, row.attempts) == ("queued", 1)
    assert "boom" in row.last_error
    assert row.run_at > clock.now()
    assert await worker.claim() is None

    clock.advance(seconds=10)
    assert await worker.run_due() == 0
    assert (await job(engine, job_id)).status == "failed"
    assert await count(AuditEventRow) == 0


async def test_a_job_that_keeps_dying_gives_up(rt, clock, engine):
    [job_id] = await enqueue(rt, max_attempts=1)
    worker = Worker(rt)
    await worker.claim()
    clock.advance(seconds=rt.settings.job_lease_seconds + 1)
    assert await worker.claim() is None
    row = await job(engine, job_id)
    assert (row.status, row.last_error) == ("failed", "out of attempts")


async def test_an_unknown_job_kind_fails(rt, make_app, engine):
    [job_id] = await enqueue(rt, "noop")
    other = make_app(tasks={}).state.rt
    assert await Worker(other).run_due() == 0
    assert (await job(engine, job_id)).status == "failed"


async def test_a_restarted_worker_re_attaches_at_once(rt, engine):
    await enqueue(rt, n=2)
    mine = (await Worker(rt, owner="same").claim()).job_id
    theirs = (await Worker(rt, owner="other").claim()).job_id

    after_restart = Worker(rt, owner="same")
    assert await after_restart.reattach() == 1
    lease = await after_restart.claim()
    assert lease.job_id == mine
    assert lease.attempts == 2
    assert (await job(engine, theirs)).lease_owner == "other"


async def test_a_slow_job_keeps_its_lease(make_app, clock, engine):
    seen = []

    async def slow(payload, ctx):
        clock.advance(hours=1)
        await asyncio.sleep(0.2)
        seen.append(await ctx.db.scalar(select(JobRow.lease_expires_at)))

    rt = make_app(tasks={"slow": slow}).state.rt
    await enqueue(rt, "slow")
    worker = Worker(rt, lease_seconds=0.03)
    lease = await worker.claim()
    assert await worker.run(lease) is True
    assert seen[0] > clock.now()


async def test_ticks_run_due_work(make_app):
    app = make_app(tasks={"audited": audited})
    await enqueue(app.state.rt, "audited", n=3)
    async with client_for(app) as client:
        named = await client.post("/internal/ticks", json={"tasks": ["watch"]}, headers=SERVICE)
        assert named.json()["done"] == {"watch": 0}
        response = await client.post("/internal/ticks", json={"tasks": []}, headers=SERVICE)
        details = await client.get("/health/details", headers=as_person("p-jordan"))
    assert response.status_code == 200, response.text
    assert response.json()["done"] == {"reconcile_runs": 0, "watch": 0, "escalations": 0, "jobs": 3}
    assert details.json()["scheduler_last_tick_at"] is not None
    assert details.json()["escalation_loop_last_run_at"] == details.json()["scheduler_last_tick_at"]


async def test_health_details_have_no_escalation_run_before_the_first_tick(make_app):
    async with client_for(make_app()) as client:
        details = await client.get("/health/details", headers=as_person("p-jordan"))
    assert details.json()["escalation_loop_last_run_at"] is None


async def test_the_worker_loop_re_attaches_after_a_restart(make_app, clock, engine, count):
    before = make_app(tasks={"audited": audited}).state.rt
    [job_id] = await enqueue(before, "audited")
    await Worker(before, owner="server-1").claim()  # and the process dies here

    settings = Settings(env="simulation", database_url=TEST_URL, worker_id="server-1", worker_enabled=True, worker_poll_seconds=0.01)
    app = create_app(settings=settings, clock=clock, tasks={"audited": audited})
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(10):
            while (await job(engine, job_id)).status != "done":
                await asyncio.sleep(0.02)
    assert await count(AuditEventRow) == 1
