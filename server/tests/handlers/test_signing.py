"""Signing: what is written together, what is refused, and what stays still afterwards. Each test starts from a fresh seed."""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db import make_sessions
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files, truncate_all
from app.tables import ROW

from ..conftest import START, TENANT
from .conftest import audit_rows, count_rows, failed, headers

pytestmark = pytest.mark.seeded
REGULATOR_B = "Reference Agency B (synthetic)"
REGULATOR_A = "Reference Agency A (synthetic)"


@pytest_asyncio.fixture(autouse=True)
async def seeded(engine):  # replaces the module-wide seed: signing changes what the next test would read
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)


async def package(http, case, person="p-dana") -> dict:
    r = await http.get(f"/api/v1/cases/{case}/signing-package", headers=headers(person))
    assert r.status_code == 200, r.text
    return r.json()


def sign_body(pkg: dict, **over) -> dict:
    return {"type": "grant", "fingerprint": pkg["fingerprint"], "recommendation_version": pkg["recommendation"]["version"], "accepts_recommendation": True, "reason": None, "conditions": [], **over}


async def sign(http, case, pkg, person="p-dana", key=None, **over):
    extra = {"Idempotency-Key": key} if key else {}
    return await http.post(f"/api/v1/cases/{case}/decisions", json=sign_body(pkg, **over), headers=headers(person, **extra))


async def row_of(engine, table, id):
    async with make_sessions(engine)() as db:
        return await db.get(ROW[table], id)


async def test_a_grant_writes_the_decision_its_proposal_and_the_case_together(http, engine):
    pkg = await package(http, "CASE-1044")
    r = await sign(http, "CASE-1044", pkg, conditions=["Annual report"])
    assert r.status_code == 201, r.text
    dec = r.json()
    assert dec["state"] == "signed" and dec["record_status"] == "queued" and dec["legal_effect"] == "demonstration_only"
    assert dec["signers"][0]["person_id"] == "p-dana" and dec["signed_payload_hash"] == pkg["fingerprint"]
    proposal = await row_of(engine, "proposal", dec["proposal_id"])
    assert proposal.state == "queued" and proposal.doc["action"] == "grant_licence" and proposal.doc["idempotency_key"] == f"idem-{dec['id']}"
    case = (await http.get("/api/v1/cases/CASE-1044", headers=headers("p-dana"))).json()
    assert case["status"] == "recording" and case["decision_id"] == dec["id"] and case["clock_running"] is False
    assert (await row_of(engine, "decision", dec["id"])).foreign_status_reason is None
    assert await count_rows(engine, "decision_signer", decision_id=dec["id"]) == 1
    assert await audit_rows(engine, action="decision_signed", object_id=dec["id"]) == 1


async def test_a_close_needs_no_government_record(http, engine):
    pkg = await package(http, "CASE-1052")
    r = await sign(http, "CASE-1052", pkg, type="close", accepts_recommendation=False, reason="The company withdrew.")
    assert r.status_code == 201, r.text
    assert r.json()["record_status"] == "not_needed" and r.json()["proposal_id"] is None
    assert (await http.get("/api/v1/cases/CASE-1052", headers=headers("p-dana"))).json()["status"] == "closed"


async def test_signing_twice_with_one_key_signs_once(http, engine):
    pkg = await package(http, "CASE-1052")
    first = await sign(http, "CASE-1052", pkg, key="sign-1052")
    again = await sign(http, "CASE-1052", pkg, key="sign-1052")
    assert first.status_code == again.status_code == 201
    assert again.headers["Idempotency-Replayed"] == "true" and again.json() == first.json()
    assert await count_rows(engine, "decision", case_id="CASE-1052") == 1
    assert await count_rows(engine, "proposal") == 4  # three seeded, one new


async def test_a_stale_fingerprint_is_refused_with_the_current_one(http, engine):
    pkg = await package(http, "CASE-1053", person="p-noor")
    again = await http.post("/api/v1/check-results/cr-1053-lbl-02/recheck", json={"comment": "The evidence changed."}, headers=headers("p-alex"))
    assert again.status_code == 202, again.text
    r = await sign(http, "CASE-1053", pkg, person="p-noor")
    assert "fingerprint_matches" in failed(r)
    now = await package(http, "CASE-1053", person="p-noor")
    assert r.json()["current_fingerprint"] == now["fingerprint"] != pkg["fingerprint"]
    assert await count_rows(engine, "decision", case_id="CASE-1053") == 0


async def test_a_refusal_stops_a_stale_read_too(http):
    pkg = await package(http, "CASE-1044")
    r = await sign(http, "CASE-1044", {**pkg, "fingerprint": "sha256:" + "1" * 64}, type="refusal", accepts_recommendation=False, reason="Trying.")
    assert "fingerprint_matches" in failed(r)


async def add_reliance(http, case, regulator):
    r = await http.post(f"/api/v1/cases/{case}/reliance-basis", json={"regulator": regulator, "approval_ref": "REF-1", "approved_on": "2026-01-05"}, headers=headers("p-alex"))
    assert r.status_code == 201, r.text


async def test_a_grant_with_an_unreadable_source_needs_a_reason(http, engine):
    await add_reliance(http, "CASE-1044", REGULATOR_B)  # its page layout changed, so it can't be read
    pkg = await package(http, "CASE-1044")
    refused = await sign(http, "CASE-1044", pkg)
    assert failed(refused) == {"foreign_sources_fresh"}
    assert await count_rows(engine, "decision", case_id="CASE-1044") == 0

    reason = "The agency's page is being rebuilt. Its last notice was checked by hand this morning."
    r = await sign(http, "CASE-1044", pkg, foreign_status_reason=reason)
    assert r.status_code == 201, r.text
    assert (await row_of(engine, "decision", r.json()["id"])).foreign_status_reason == reason
    async with make_sessions(engine)() as db:
        entry = await db.scalar(select(ROW["decision"].id).where(ROW["decision"].id == r.json()["id"]))
        from app.tables import AuditEventRow
        audit = await db.scalar(select(AuditEventRow).where(AuditEventRow.action == "decision_signed", AuditEventRow.object_id == entry))
    assert audit.after["foreign_status_reason"] == reason


async def test_a_readable_source_is_read_before_the_grant_is_signed(http, engine):
    await add_reliance(http, "CASE-1052", REGULATOR_A)
    async with make_sessions(engine)() as db:
        before = (await db.get(ROW["watch_source"], "src-raa-notices")).last_success_at
    pkg = await package(http, "CASE-1052")
    r = await sign(http, "CASE-1052", pkg)
    assert r.status_code == 201, r.text
    async with make_sessions(engine)() as db:
        assert (await db.get(ROW["watch_source"], "src-raa-notices")).last_success_at > before
    assert await audit_rows(engine, action="source_fetched", object_id="src-raa-notices") == 1


async def test_a_case_that_relies_on_no_foreign_authority_reads_no_source(http, engine):
    pkg = await package(http, "CASE-1044")
    assert (await sign(http, "CASE-1044", pkg)).status_code == 201
    assert await audit_rows(engine, action="source_fetched") == 0


async def test_a_held_case_can_be_refused_with_no_waiver(http, engine):
    pkg = await package(http, "CASE-1047")
    r = await sign(http, "CASE-1047", pkg, type="refusal", accepts_recommendation=False, reason="Hidden text was found in the file.")
    assert r.status_code == 201, r.text
    proposal = await row_of(engine, "proposal", r.json()["proposal_id"])
    assert proposal.doc["action"] == "record_refusal"


async def test_a_grant_on_a_held_case_is_still_refused(http):
    pkg = await package(http, "CASE-1047")
    assert "blocking_checks_clear" in failed(await sign(http, "CASE-1047", pkg))


async def test_a_clear_case_can_be_refused_by_overriding_the_recommendation(http, engine):
    pkg = await package(http, "CASE-1044")
    r = await sign(http, "CASE-1044", pkg, type="refusal", accepts_recommendation=False, reason="The leaflet wording is not acceptable.")
    assert r.status_code == 201, r.text
    assert r.json()["accepts_recommendation"] is False and r.json()["reason"].startswith("The leaflet")


async def test_a_refusal_with_an_open_comment_is_refused(http):
    pkg = await package(http, "CASE-1047")
    made = await http.post("/api/v1/cases/CASE-1047/threads", json={"target": {"type": "case", "ref": "CASE-1047"}, "text": "Wait for the company."}, headers=headers("p-alex"))
    assert made.status_code == 201
    r = await sign(http, "CASE-1047", pkg, type="refusal", accepts_recommendation=False, reason="Hidden text was found in the file.")
    assert "no_open_threads" in failed(r)


async def test_a_signed_case_is_frozen(http, engine):
    pkg = await package(http, "CASE-1044")
    dec = (await sign(http, "CASE-1044", pkg)).json()
    check = (await http.get("/api/v1/check-results/cr-1044-lbl-02", headers=headers("p-alex"))).json()
    refused = {
        "confirm": await http.post("/api/v1/check-results/cr-1044-qual-07/confirm", json={"fingerprint": check["fingerprint"]}, headers=headers("p-alex")),
        "waive": await http.post("/api/v1/check-results/cr-1044-lbl-02/waive", json={"fingerprint": check["fingerprint"], "reason": "Late."}, headers=headers("p-chris")),
        "assign": await http.post("/api/v1/cases/CASE-1044/assign", json={"person_id": "p-alex", "role": "assessor"}, headers=headers("p-alex")),
    }
    for name, r in refused.items():
        assert "case_not_frozen" in failed(r), name
    recheck = await http.post("/api/v1/check-results/cr-1044-lbl-02/recheck", json={"comment": "Late."}, headers=headers("p-alex"))
    assert "case_not_frozen" in failed(recheck)
    run = await http.post("/api/v1/cases/CASE-1044/pipeline-runs", json={}, headers=headers("p-alex"))
    assert "case_not_frozen" in failed(run)
    assert "case_promoted" in failed(await sign(http, "CASE-1044", pkg))
    assert (await http.get(f"/api/v1/decisions/{dec['id']}", headers=headers("p-alex"))).json()["state"] == "signed"


async def test_a_signer_who_waived_a_check_cannot_sign(http, engine):
    pkg = await package(http, "CASE-1053", person="p-dana")
    r = await sign(http, "CASE-1053", pkg, person="p-dana")
    assert failed(r) == {"signer_did_not_assess"}
    assert (await sign(http, "CASE-1053", pkg, person="p-noor")).status_code == 201
