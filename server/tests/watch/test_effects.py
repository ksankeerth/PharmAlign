"""What a notice does to the cases it touches, and what a person does with a match."""
from sqlalchemy import select

from app.db import make_sessions
from app.jobs import Worker
from app.tables import ROW, AuditEventRow

from .conftest import AGENCY_A, SUSPENSION, deliver, headers, rows
from ..conftest import TENANT


async def package(http, case, person="p-dana") -> dict:
    return (await http.get(f"/api/v1/cases/{case}/signing-package", headers=headers(person))).json()


async def sign(http, case, person="p-dana"):
    pkg = await package(http, case, person)
    body = {"type": "grant", "fingerprint": pkg["fingerprint"], "recommendation_version": pkg["recommendation"]["version"], "accepts_recommendation": True, "reason": None, "conditions": []}
    return await http.post(f"/api/v1/cases/{case}/decisions", json=body, headers=headers(person))


def of_case(matches, case_id):
    [found] = [x for x in matches if x.target_type == "case" and x.target_id == case_id]
    return found


async def test_a_notice_on_a_case_blocks_signing_and_a_dismissal_with_a_reason_lifts_it(http, engine):
    clear = await package(http, "CASE-1052")
    assert all(g["passed"] for g in clear["guards"])
    done = await deliver(http.app.state.rt, SUSPENSION)
    match = of_case(done.new_matches, "CASE-1052")
    blocked = await sign(http, "CASE-1052")
    assert blocked.status_code == 409 and "foreign_status_current" in {g["guard"] for g in blocked.json()["failed"]}
    assert (await package(http, "CASE-1052"))["foreign_status"]["open_notices"] == 1

    refused = await http.post(f"/api/v1/watch/matches/{match.id}/dismiss", json={"reason": "  "}, headers=headers("p-alex"))
    assert refused.status_code == 422
    assert (await sign(http, "CASE-1052")).status_code == 409

    ok = await http.post(f"/api/v1/watch/matches/{match.id}/dismiss", json={"reason": "This is a different strength from ours."}, headers=headers("p-alex"))
    assert ok.status_code == 200 and ok.json()["status"] == "dismissed" and ok.json()["decided_by"] == "p-alex"
    lifted = await package(http, "CASE-1052")
    assert lifted["foreign_status"]["open_notices"] == 0
    assert {g["name"] if "name" in g else g["guard"]: g["passed"] for g in lifted["guards"]}["foreign_status_current"] is True
    again = await http.post(f"/api/v1/watch/matches/{match.id}/dismiss", json={"reason": "Twice."}, headers=headers("p-alex"))
    assert again.status_code == 409


async def test_a_notice_on_an_open_case_runs_its_foreign_status_check_again(http, engine):
    where = (ROW["check_result"].case_id == "CASE-1048", ROW["check_result"].check_id == "WATCH-01")
    before = len(await rows(engine, "check_result", *where))
    notice = {"regulator": AGENCY_A, "title": "Second suspension notice: losartan 50 mg (synthetic)", "text": "Suspension of losartan 50 mg tablets."}
    done = await deliver(http.app.state.rt, notice)
    assert of_case(done.new_matches, "CASE-1048").status == "open"
    assert len(await rows(engine, "check_result", *where)) == before + 1  # a new attempt, started in the notice's own transaction
    timeline = (await http.get("/api/v1/cases/CASE-1048/timeline", headers=headers("p-alex"))).json()["items"]
    assert done.notice.id in [e["object_ref"] for e in timeline if e["kind"] == "foreign_notice"]


async def test_a_notice_after_signing_and_before_recording_holds_the_proposal(http, engine):
    rt = http.app.state.rt
    pkg = await package(http, "CASE-1044")
    signed = await sign(http, "CASE-1044")
    assert signed.status_code == 201, signed.text
    decision_id = signed.json()["id"]
    await deliver(rt, {"regulator": "Reference Agency A (synthetic)", "title": "Recall: metformin hydrochloride 500 mg (synthetic)", "text": "Recall of metformin hydrochloride 500 mg tablets."})
    [decision] = await rows(engine, "decision", ROW["decision"].id == decision_id)
    assert decision.state == "on_hold"
    await Worker(rt).run_due()  # the send job finds a hold and does nothing
    [proposal] = await rows(engine, "proposal", ROW["proposal"].decision_id == decision_id)
    assert proposal.state == "queued" and proposal.doc["attempts"] == 0
    assert pkg["case_id"] == "CASE-1044"


async def test_opening_a_re_review_makes_a_linked_case(http, engine):
    done = await deliver(http.app.state.rt, SUSPENSION)
    match = next(x for x in done.new_matches if x.target_type == "licence")
    refused = await http.post(f"/api/v1/watch/matches/{match.id}/open-rereview", json={"reason": " "}, headers=headers("p-alex"))
    assert refused.status_code == 422
    r = await http.post(f"/api/v1/watch/matches/{match.id}/open-rereview", json={"reason": "The label change affects our leaflet."}, headers=headers("p-alex"))
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["case_type"] == "reliance_rereview" and case["medicine_id"] == "med-clopidogrel" and case["assigned_to"] == "p-alex"
    [saved] = await rows(engine, "watch_match", ROW["watch_match"].id == match.id)
    assert saved.status == "rereview_opened"
    timeline = (await http.get(f"/api/v1/cases/{case['id']}/timeline", headers=headers("p-alex"))).json()["items"]
    assert match.id in [e["object_ref"] for e in timeline if e["kind"] == "rereview_opened"]
    async with make_sessions(http.app.state.rt.sessions.kw["bind"])() as db:
        entry = await db.scalar(select(AuditEventRow).where(AuditEventRow.action == "rereview_opened", AuditEventRow.object_id == match.id))
    assert entry.case_id == case["id"] and entry.reason
    # A match that is no longer open can't open another.
    again = await http.post(f"/api/v1/watch/matches/{match.id}/open-rereview", json={"reason": "Once more."}, headers=headers("p-alex"))
    assert again.status_code == 409
