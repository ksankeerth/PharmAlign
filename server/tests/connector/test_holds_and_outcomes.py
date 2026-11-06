"""What the government system says decides what PharmAlign shows, and a held decision is never sent."""
from sqlalchemy import select

from app.handlers.records import hold_for_notice
from app.tables import ROW

from ..conftest import system_ctx
from .conftest import CASE, pytestmark  # noqa: F401


async def hold(world, decision_id: str) -> None:
    """What a matching foreign notice does after signing."""
    async with world.rt.sessions() as db, db.begin():
        ctx = system_ctx(world.rt, db)
        await hold_for_notice(ctx, decision_id)
        await ctx.flush()


async def resolve(world, decision, action: str):
    return await world.http.post(f"/api/v1/decisions/{decision['id']}/{action}", headers=world.headers("p-dana"),
                                 json={"fingerprint": decision["signed_payload_hash"], "reason": "The suspension concerns another approval."})


async def test_a_rejection_shows_and_changes_nothing_in_the_mirror(world):
    await world.regulator_mode("reject_next")
    mirror = await world.mirror_licences()
    dec = await world.sign()
    await world.until_quiet()
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "rejected" and p["last_error"] and p["gov_ref"] is None
    assert (await world.decision(dec["id"]))["record_status"] == "rejected"
    assert await world.mirror_licences() == mirror
    assert (await world.case())["status"] == "recording"
    kinds = await world.timeline()
    assert "record_rejected" in kinds and "applicant_told" not in kinds and "record_acknowledged" not in kinds
    assert await world.jobs("queued") == 0 and await world.regulator_records(dec["id"]) == 1


async def test_the_applicant_is_not_told_before_the_government_acknowledges(world):
    await world.regulator_mode("applied_but_timed_out")
    dec = await world.sign()
    await world.run_jobs()
    assert (await world.proposal(dec["proposal_id"]))["state"] == "unknown"
    assert "applicant_told" not in await world.timeline() and (await world.case())["status"] == "recording"
    world.clock.advance(seconds=30)
    await world.until_quiet()
    kinds = await world.timeline()
    assert kinds.count("applicant_told") == 1 and kinds.index("record_acknowledged") < kinds.index("applicant_told")


async def test_a_notice_after_signing_holds_the_proposal_and_it_is_never_sent(world):
    dec = await world.sign()
    await hold(world, dec["id"])
    assert (await world.decision(dec["id"]))["state"] == "on_hold"
    await world.until_quiet()
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "queued" and p["attempts"] == 0
    assert await world.regulator_records(dec["id"]) == 0
    assert "decision_on_hold" in await world.timeline()


async def test_resolving_a_hold_to_continue_sends_the_proposal(world):
    dec = await world.sign()
    await hold(world, dec["id"])
    await world.until_quiet()
    assert await world.regulator_records(dec["id"]) == 0
    assert (await resolve(world, dec, "reconfirm")).status_code == 200
    await world.until_quiet()
    assert (await world.proposal(dec["proposal_id"]))["state"] == "acknowledged"
    assert await world.regulator_records(dec["id"]) == 1
    assert (await world.case())["status"] == "closed"


async def test_resolving_a_hold_to_withdraw_sends_nothing(world):
    mirror = await world.mirror_licences()
    dec = await world.sign()
    await hold(world, dec["id"])
    assert (await resolve(world, dec, "revoke")).status_code == 200
    await world.until_quiet()
    p = await world.proposal(dec["proposal_id"])
    assert p["state"] == "rejected" and p["last_error"].startswith("Withdrawn")
    assert await world.regulator_records(dec["id"]) == 0 and await world.mirror_licences() == mirror
    assert "applicant_told" not in await world.timeline()


async def test_only_a_held_decision_can_be_resolved(world):
    dec = await world.sign()
    refused = await resolve(world, dec, "reconfirm")
    assert refused.status_code == 409 and "decision_on_hold" in {g["guard"] for g in refused.json()["failed"]}


async def test_a_manual_outcome_needs_a_reference_and_cannot_settle_a_final_proposal(world):
    await world.regulator_mode("unavailable")
    dec = await world.sign()
    await world.until_quiet()
    url = f"/api/v1/proposals/{dec['proposal_id']}/manual-ack"
    no_ref = await world.http.post(url, json={"outcome": "applied"}, headers=world.headers("p-alex"))
    assert no_ref.status_code == 422
    assert (await world.proposal(dec["proposal_id"]))["state"] == "unknown"
    assert (await world.http.post(url, json={"outcome": "applied", "gov_ref": "GOV-MA-2026-9501"}, headers=world.headers("p-alex"))).status_code == 200
    again = await world.http.post(url, json={"outcome": "rejected"}, headers=world.headers("p-alex"))
    assert again.status_code == 409
    assert (await world.proposal(dec["proposal_id"]))["state"] == "acknowledged"


async def test_a_manual_rejection_leaves_the_mirror_alone(world):
    await world.regulator_mode("unavailable")
    mirror = await world.mirror_licences()
    dec = await world.sign()
    await world.until_quiet()
    r = await world.http.post(f"/api/v1/proposals/{dec['proposal_id']}/manual-ack", json={"outcome": "rejected", "note": "The portal refused it."}, headers=world.headers("p-alex"))
    assert r.status_code == 200 and r.json()["state"] == "rejected"
    assert await world.mirror_licences() == mirror and (await world.case())["status"] == "recording"


async def test_the_proposal_and_its_job_are_written_in_the_signing_transaction(world):
    dec = await world.sign()
    assert await world.jobs("queued") == 1
    assert (await world.proposal(dec["proposal_id"]))["idempotency_key"] == f"idem-{dec['id']}"
