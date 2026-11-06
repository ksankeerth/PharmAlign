"""Exactly once: a decision reaches the government record one time, whatever goes wrong on the way."""
import asyncio

import pytest

from app.jobs import Worker

from .conftest import CASE, pytestmark  # noqa: F401


class Crash(BaseException):
    """The process dies. Nothing catches it, so no cleanup of the job runs."""


class Crashing:
    """A connector whose process dies after the proposal has been sent."""

    def __init__(self, inner):
        self.inner = inner

    async def propose(self, key, proposal):
        await self.inner.propose(key, proposal)
        raise Crash()

    async def read_back(self, key):
        return await self.inner.read_back(key)


async def test_applied_but_timed_out_is_found_by_read_back_and_recorded_once(world):
    await world.regulator_mode("applied_but_timed_out")
    mirror = await world.mirror_licences()
    dec = await world.sign()
    assert dec["record_status"] == "queued"
    assert await world.run_jobs() == 1
    # The call timed out, so nothing is assumed. The mirror hasn't moved and the proposal says so.
    p = await world.proposal(dec["proposal_id"])
    assert (p["state"], p["attempts"], p["gov_ref"]) == ("unknown", 1, None)
    assert await world.mirror_licences() == mirror and (await world.case())["status"] == "recording"
    assert await world.regulator_records(dec["id"]) == 1  # it did arrive

    world.clock.advance(seconds=30)
    await world.until_quiet()
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "acknowledged" and p["gov_ref"].startswith("GOV-MA-2026-")
    assert p["attempts"] == 1  # read back, never sent again
    assert (await world.decision(dec["id"]))["record_status"] == "acknowledged"
    assert (await world.case())["status"] == "closed"
    assert await world.regulator_records(dec["id"]) == 1
    assert await world.regulator_licences() == 1
    assert (await world.timeline()).count("record_acknowledged") == 1


async def test_timed_out_before_applying_is_sent_again_with_the_same_key(world):
    await world.regulator_mode("timeout_before_applying")
    dec = await world.sign()
    await world.run_jobs()
    assert (await world.proposal(dec["proposal_id"]))["state"] == "unknown"
    assert await world.regulator_records(dec["id"]) == 0  # read-back will find nothing

    world.clock.advance(seconds=30)
    await world.until_quiet()
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "acknowledged" and p["attempts"] == 2
    assert await world.regulator_records(dec["id"]) == 1
    assert await world.regulator_licences() == 1


async def test_a_worker_that_dies_after_sending_reads_back_on_restart(world):
    good = world.rt.connector
    world.rt.connector = Crashing(good)
    dec = await world.sign()
    worker = Worker(world.rt, owner="server-1")
    lease = await worker.claim()
    with pytest.raises(Crash):
        await worker.run(lease)
    assert await world.regulator_records(dec["id"]) == 1  # sent, and the outcome was never saved
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "sent" and (await world.decision(dec["id"]))["record_status"] == "sent"

    world.rt.connector = good  # restarted
    assert await Worker(world.rt, owner="server-1").reattach() == 1
    await world.until_quiet()
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "acknowledged" and p["attempts"] == 1
    assert await world.regulator_records(dec["id"]) == 1
    assert await world.regulator_licences() == 1


async def test_two_workers_on_one_proposal_let_one_through(world):
    dec = await world.sign()
    a, b = Worker(world.rt, owner="a"), Worker(world.rt, owner="b")
    first, second = await asyncio.gather(a.claim(), b.claim())
    assert sorted(x is None for x in (first, second)) == [False, True]
    lease, worker = (first, a) if first else (second, b)
    other = b if worker is a else a
    assert await worker.run(lease) is True
    assert await other.claim() is None
    assert await world.regulator_records(dec["id"]) == 1


async def test_a_worker_that_lost_its_lease_cannot_record_twice(world):
    dec = await world.sign()
    slow, quick = Worker(world.rt, owner="slow"), Worker(world.rt, owner="quick")
    held = await slow.claim()
    assert await quick.claim() is None  # leased
    world.clock.advance(minutes=5)  # the lease runs out
    taken = await quick.claim()
    assert taken is not None and await quick.run(taken) is True
    assert await slow.run(held) is False  # it finds the work done, and its lease gone
    assert await world.regulator_records(dec["id"]) == 1
    assert (await world.timeline()).count("record_acknowledged") == 1


async def test_a_failing_regulator_ends_unknown_after_bounded_attempts_and_a_person_settles_it(world):
    await world.regulator_mode("unavailable")
    mirror = await world.mirror_licences()
    dec = await world.sign()
    await world.until_quiet()
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "unknown" and p["attempts"] == 5 and p["last_error"]
    assert (await world.decision(dec["id"]))["record_status"] == "unknown"
    assert await world.jobs("queued") == 0  # it stopped trying
    assert await world.regulator_records(dec["id"]) == 0
    # The mirror still follows the government, which has said nothing.
    assert (await world.case())["status"] == "recording" and await world.mirror_licences() == mirror
    assert "record_unknown" in await world.timeline()
    listed = (await world.http.get("/api/v1/proposals", params={"state": "unknown"}, headers=world.headers("p-jordan"))).json()
    assert dec["proposal_id"] in [x["id"] for x in listed["items"]] and all(x["state"] == "unknown" for x in listed["items"])
    note = await world.http.get("/api/v1/notifications", headers=world.headers("p-alex"))
    assert note.status_code == 200 and "unconfirmed" in note.text

    settled = await world.http.post(f"/api/v1/proposals/{dec['proposal_id']}/manual-ack", json={"outcome": "applied", "gov_ref": "GOV-MA-2026-9500", "note": "Checked on the portal."},
                                    headers=world.headers("p-alex"))
    assert settled.status_code == 200, settled.text
    assert settled.json()["state"] == "acknowledged" and settled.json()["gov_ref"] == "GOV-MA-2026-9500"
    assert await world.mirror_licences() == mirror + 1 and (await world.case())["status"] == "closed"
