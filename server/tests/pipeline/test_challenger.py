"""The Challenger tries to disprove a blocking finding. Only a verified quote that contradicts the claim changes the check."""
import pytest
from sqlalchemy import text

from app.pipeline.replay import ReplayScript
from app.stores import STORES
from app.tables import ROW

from ..conftest import system_ctx
from .world import CASE, all_runs, all_work, checks_by_id, drive, start

CHECK = "LBL-02"
RESULT = "cr-1042-lbl-02"
# A real line from the case's own file, which says nothing about the leaflet dose the check found.
CONTRADICTION = {"anchor": "d-1042-27:v1:p1", "quote": "Establishment licence number EST-11042, valid until 2028"}
INVENTED = {"anchor": "d-1042-27:v1:p1", "quote": "The leaflet dose is one tablet of 1000 mg once a month, as the label states"}


async def make_pass_alone(rt):
    """Lets the check pass on an agent's word, so a dispute has something to change."""
    async with rt.sessions() as db, db.begin():
        await db.execute(text("ALTER TABLE pack_version DISABLE TRIGGER USER"))  # an approved pack is locked, for good reason
        await db.execute(text("UPDATE pack_version SET doc = jsonb_set(doc, '{document,checks}', "
                              "(SELECT jsonb_agg(CASE WHEN c->>'id' = :check THEN jsonb_set(c, '{may_pass_alone}', 'true') ELSE c END) "
                              "FROM jsonb_array_elements(doc->'document'->'checks') c)) WHERE id = 'pv-ae-3'"), {"check": CHECK})
        await db.execute(text("ALTER TABLE pack_version ENABLE TRIGGER USER"))


def challenge(rt, agents, **result):
    agents.answer(CASE, f"challenge-{RESULT}", {"verdict": "upheld", "reason": "Same answer.", "evidence": [], **result})


async def run_case(rt, clock):
    await start(rt)
    await drive(rt, clock)


async def audited(rt, action) -> int:
    async with rt.sessions() as db:
        return (await db.execute(text("SELECT count(*) FROM audit_event WHERE action = :a AND object_id = :o"), {"a": action, "o": RESULT})).scalar()


async def test_a_dispute_with_a_verified_contradicting_quote_sends_the_check_to_a_person(rt, agents, clock):
    await make_pass_alone(rt)
    challenge(rt, agents, verdict="disputed", reason="The licence page says nothing that supports a leaflet dose.", evidence=[CONTRADICTION])
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "person_confirms" and check.agent_status == "met"
    assert check.challenge.verdict == "disputed" and check.challenge.counted is True and check.challenge.evidence[0].anchor == CONTRADICTION["anchor"]
    assert await audited(rt, "challenge_disputed") == 1


async def test_without_a_dispute_the_same_check_passes(rt, agents, clock):
    await make_pass_alone(rt)
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "pass" and check.challenge is None
    assert await audited(rt, "challenge_upheld") == 1


async def test_a_dispute_with_an_invented_quote_is_refused(rt, agents, clock):
    await make_pass_alone(rt)
    challenge(rt, agents, verdict="disputed", reason="The leaflet says something else.", evidence=[INVENTED])
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "pass" and check.challenge is None
    assert await audited(rt, "challenge_refused") == 1 and await audited(rt, "challenge_disputed") == 0


async def test_a_dispute_that_repeats_the_claims_own_quote_is_refused(rt, agents, clock):
    await make_pass_alone(rt)
    challenge(rt, agents, verdict="disputed", reason="Same words.", evidence=[{"anchor": "d-1042-22:v1:p3", "quote": "one tablet of 500 mg twice daily"}])
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "pass" and check.challenge is None
    assert await audited(rt, "challenge_refused") == 1


@pytest.mark.parametrize("verdict", ["cannot_verify", "upheld"])
async def test_a_verdict_that_does_not_dispute_changes_nothing(rt, agents, clock, verdict):
    await make_pass_alone(rt)
    challenge(rt, agents, verdict=verdict, reason="Could not settle it.")
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "pass" and check.challenge is None
    assert await audited(rt, f"challenge_{verdict}") == 1


async def test_a_challenge_that_never_answers_changes_nothing(rt, agents, clock):
    await make_pass_alone(rt)
    agents.script(CASE, f"challenge-{RESULT}", ReplayScript("failed"))
    await run_case(rt, clock)
    check = (await checks_by_id(rt))[CHECK]
    assert check.status == "pass" and check.challenge is None
    runs = [r for r in await all_runs(rt) if r.agent == "challenger" and r.check_id == CHECK]
    assert runs and all(r.status == "failed" for r in runs)


async def test_only_blocking_findings_that_are_met_are_challenged(rt, agents, clock):
    await run_case(rt, clock)
    challenged = {w.check_id for w in await all_work(rt) if w.kind == "challenge"}
    checks = await checks_by_id(rt)
    assert challenged and all(checks[c].blocking and checks[c].agent_status == "met" for c in challenged)
    assert CHECK in challenged and "QUAL-07" not in challenged and "SAME-01" not in challenged
    assert all(w.request["target"]["finding_id"] == checks[w.check_id].id for w in await all_work(rt) if w.kind == "challenge")


async def test_the_brief_waits_for_the_challenger(rt, agents, clock):
    await make_pass_alone(rt)
    challenge(rt, agents, verdict="disputed", reason="The licence page says nothing that supports a leaflet dose.", evidence=[CONTRADICTION])
    await run_case(rt, clock)
    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        row = ROW["recommendation_version"]
        current = next(r for r in await STORES["recommendation_version"].list(ctx, row.case_id == CASE) if r.is_current)
    briefs = [w for w in await all_work(rt) if w.kind == "brief" and w.applied_at]
    assert briefs and current.items_to_confirm.count(RESULT) == 1
