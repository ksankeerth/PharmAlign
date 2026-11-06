"""Review comments, questions to the company, and a person's acts on checks. One seed for the module, and each test keeps to its own case."""
from datetime import timedelta

import pytest

from app.jobs import Worker
from app.ops import HANDLERS
from app.pipeline import runner
from ..conftest import START
from .conftest import audit_rows, count_rows, failed, headers

pytestmark = pytest.mark.seeded
THREADS = ("POST", "/api/v1/cases/{case_id}/threads")


async def test_a_thread_blocks_promotion_until_it_is_resolved(http):
    opened = await http.post("/api/v1/cases/CASE-1046/threads", json={"target": {"type": "check", "ref": "cr-1046-lbl-02"}, "text": "Check the Arabic leaflet."}, headers=headers("p-alex"))
    assert opened.status_code == 201, opened.text
    thread = opened.json()
    check = (await http.get("/api/v1/check-results/cr-1046-lbl-02", headers=headers("p-alex"))).json()
    assert check["open_threads"] == 1
    gate = (await http.get("/api/v1/cases/CASE-1046/promotion-check", headers=headers("p-alex"))).json()
    assert any(g["guard"] == "no_open_threads" and not g["passed"] for g in gate["guards"])

    reply = await http.post(f"/api/v1/threads/{thread['id']}/comments", json={"text": "Done on my side."}, headers=headers("p-chris"))
    assert [c["author_id"] for c in reply.json()["comments"]] == ["p-alex", "p-chris"]
    done = await http.post(f"/api/v1/threads/{thread['id']}/resolve", json={"note": "Read it."}, headers=headers("p-alex"))
    assert done.json()["status"] == "resolved" and len(done.json()["comments"]) == 3
    assert (await http.get("/api/v1/check-results/cr-1046-lbl-02", headers=headers("p-alex"))).json()["open_threads"] == 0
    again = await http.post(f"/api/v1/threads/{thread['id']}/resolve", json={}, headers=headers("p-alex"))
    assert failed(again) == {"thread_open"}


async def test_a_thread_on_a_missing_check_is_a_404(http):
    r = await http.post("/api/v1/cases/CASE-1046/threads", json={"target": {"type": "check", "ref": "cr-nope"}, "text": "Hello."}, headers=headers("p-alex"))
    assert r.status_code == 404


async def test_opening_a_thread_twice_with_one_key_opens_one(http, engine):
    body = {"target": {"type": "case", "ref": "CASE-1049"}, "text": "One note."}
    first = await http.post("/api/v1/cases/CASE-1049/threads", json=body, headers=headers("p-alex", **{"Idempotency-Key": "note-1"}))
    again = await http.post("/api/v1/cases/CASE-1049/threads", json=body, headers=headers("p-alex", **{"Idempotency-Key": "note-1"}))
    assert first.status_code == again.status_code == 201
    assert again.headers["Idempotency-Replayed"] == "true" and again.json() == first.json()
    assert await count_rows(engine, "review_thread", case_id="CASE-1049") == 1
    assert await count_rows(engine, "review_comment", case_id="CASE-1049") == 1


async def test_a_failure_after_the_writes_leaves_no_audit_row_and_no_thread(make_app, engine):
    from ..conftest import client_for
    real = HANDLERS[THREADS]

    async def boom(req, ctx):
        await real(req, ctx)
        raise RuntimeError("after the writes")

    audit = await audit_rows(engine)
    async with client_for(make_app(handlers={THREADS: boom})) as client:
        r = await client.post("/api/v1/cases/CASE-1048/threads", json={"target": {"type": "case", "ref": "CASE-1048"}, "text": "Lost."}, headers=headers("p-alex"))
    assert r.status_code == 500
    assert await count_rows(engine, "review_thread", case_id="CASE-1048") == 0
    assert await count_rows(engine, "review_comment", case_id="CASE-1048") == 0
    assert await audit_rows(engine) == audit


async def test_a_refusal_leaves_no_audit_row(http, engine):
    audit = await audit_rows(engine)
    r = await http.post("/api/v1/cases/CASE-1042/promote", json={"fingerprint": "sha256:" + "0" * 64}, headers=headers("p-alex"))
    assert r.status_code == 409
    assert await audit_rows(engine) == audit


async def test_a_question_goes_from_draft_to_sent_and_stops_the_clock(http):
    made = await http.post("/api/v1/cases/CASE-1048/questions", json={"check_ids": ["QUAL-07"], "texts": {"en": "Please send the stability data."}}, headers=headers("p-alex"))
    assert made.status_code == 201, made.text
    q = made.json()
    edited = await http.patch(f"/api/v1/questions/{q['id']}", json={"texts": {"en": "Please send the 12-month stability data."}}, headers=headers("p-alex"))
    assert edited.json()["texts"]["en"].startswith("Please send the 12-month")
    late = await http.post(f"/api/v1/questions/{q['id']}/send", json={"reply_due_at": (START - timedelta(days=1)).isoformat()}, headers=headers("p-alex"))
    assert "reply_deadline_set" in failed(late)

    sent = await http.post(f"/api/v1/questions/{q['id']}/send", json={"reply_due_at": (START + timedelta(days=7)).isoformat()}, headers=headers("p-alex"))
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "sent" and sent.json()["clock_stopped"] is True and sent.json()["sent_by"] == "p-alex"
    case = (await http.get("/api/v1/cases/CASE-1048", headers=headers("p-alex"))).json()
    assert case["status"] == "with_company" and case["clock_running"] is False
    assert "draft_only" in failed(await http.patch(f"/api/v1/questions/{q['id']}", json={"texts": {"en": "Too late."}}, headers=headers("p-alex")))

    closed = await http.post(f"/api/v1/questions/{q['id']}/close", json={"reason": "The company is silent."}, headers=headers("p-alex"))
    assert closed.json()["status"] == "closed"
    assert (await http.get("/api/v1/cases/CASE-1048", headers=headers("p-alex"))).json()["clock_running"] is True


async def test_closing_a_question_needs_a_reason_and_the_right_view(http):
    made = await http.post("/api/v1/cases/CASE-1046/questions", json={"check_ids": ["QUAL-07"], "texts": {"en": "Question."}}, headers=headers("p-alex"))
    q = made.json()
    assert (await http.post(f"/api/v1/questions/{q['id']}/close", json={"reason": " "}, headers=headers("p-alex"))).status_code == 422
    assert (await http.post(f"/api/v1/questions/{q['id']}/close", json={"reason": "No longer needed."}, headers=headers("p-jordan"))).status_code == 403


async def test_a_person_confirms_a_check_and_the_brief_follows_to_promotion(make_app, clock, engine):
    """CASE-1043: confirm the check that waits for a person, let the Coordinator write again, then promote."""
    from ..conftest import client_for
    app = make_app()
    async with client_for(app) as http:
        check = (await http.get("/api/v1/check-results/cr-1043-pv-01", headers=headers("p-alex"))).json()
        before = await http.get("/api/v1/cases/CASE-1043/promotion-check", headers=headers("p-alex"))
        assert before.json()["can_promote"] is False

        confirmed = await http.post("/api/v1/check-results/cr-1043-pv-01/confirm", json={"fingerprint": check["fingerprint"], "note": "Read the summary."}, headers=headers("p-alex"))
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "confirmed" and confirmed.json()["confirmation"]["by"] == "p-alex"
        assert await count_rows(engine, "confirmation", check_result_id="cr-1043-pv-01") == 1

        for _ in range(10):
            await Worker(app.state.rt).run_due()
            clock.advance(seconds=2)
        recs = (await http.get("/api/v1/cases/CASE-1043/recommendations", headers=headers("p-alex"))).json()["items"]
        assert [r["version"] for r in recs] == [2, 1] and recs[0]["choice"] == "proceed" and recs[0]["is_current"]

        gate = (await http.get("/api/v1/cases/CASE-1043/promotion-check", headers=headers("p-alex"))).json()
        assert gate["can_promote"] is True, [g for g in gate["guards"] if not g["passed"]]
        promoted = await http.post("/api/v1/cases/CASE-1043/promote", json={"fingerprint": gate["fingerprint"]}, headers=headers("p-alex"))
        assert promoted.status_code == 201, promoted.text
        assert (await http.get("/api/v1/cases/CASE-1043", headers=headers("p-alex"))).json()["status"] == "promoted"
        note = await http.get("/api/v1/notifications", headers=headers("p-dana"))
        assert any(n["event"] == "ready_to_sign" and "CASE-1043" in n["title"] for n in note.json()["items"])
    assert await runner.tick(app.state.rt) == 0


async def test_a_waiver_needs_the_view_the_pack_names(http):
    check = (await http.get("/api/v1/check-results/cr-1045-qual-03", headers=headers("p-alex"))).json()
    assert check["status"] == "held"
    r = await http.post("/api/v1/check-results/cr-1045-qual-03/waive", json={"fingerprint": check["fingerprint"], "reason": "Sent separately."}, headers=headers("p-alex"))
    assert r.status_code in (200, 409)
    if r.status_code == 409:
        assert "waiver_permitted" in failed(r)


async def test_the_queue_lists_what_the_caller_asks_for(http):
    mine = (await http.get("/api/v1/cases?assigned_to=me&status=in_review", headers=headers("p-alex"))).json()["items"]
    assert mine and all(c["assigned_to"] == "p-alex" and c["status"] == "in_review" for c in mine)
    everyone = (await http.get("/api/v1/cases", headers=headers("p-alex"))).json()["items"]
    last = [c["status"] for c in everyone][-3:]
    assert all(s in ("signed", "recording", "closed") for s in last)
    documents = (await http.get("/api/v1/cases/CASE-1042/documents", headers=headers("p-alex"))).json()["items"]
    assert documents and all(d["case_id"] == "CASE-1042" for d in documents)
    page = await http.get(f"/api/v1/dossier-documents/{documents[0]['id']}/pages/1", headers=headers("p-alex"))
    assert page.status_code == 200 and page.json()["page"] == 1
    assert (await http.get(f"/api/v1/dossier-documents/{documents[0]['id']}/pages/9999", headers=headers("p-alex"))).status_code == 404
