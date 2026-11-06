"""The ingestion worker: staged, leased, and safe to restart."""
from sqlalchemy import text

from app.db import make_sessions
from app.jobs import Worker
from app.tables import JobRow

from .conftest import ADMIN, PROFILE, law


async def counts(engine, version_id):
    async with make_sessions(engine)() as db:
        chunks = await db.scalar(text("SELECT count(*) FROM chunk WHERE document_version_id = :v"), {"v": version_id})
        vectors = await db.scalar(text("SELECT count(*) FROM chunk_embedding e JOIN chunk c ON c.id = e.chunk_id WHERE c.document_version_id = :v"), {"v": version_id})
        distinct = await db.scalar(text("SELECT count(DISTINCT position) FROM chunk WHERE document_version_id = :v"), {"v": version_id})
    return chunks, vectors, distinct


async def test_a_restart_in_the_middle_of_ingestion_resumes_with_no_duplicate_chunks(lib_small_batches, engine, clock):
    lib = lib_small_batches
    doc = await lib.register("Long law")
    v = (await lib.upload(doc, law("record keeping", articles=9)))["id"]
    await lib.ingest(v, "trial")
    await lib.review(v)
    started = await lib.http.post(f"/api/v1/document-versions/{v}/ingest-jobs", json={"mode": "full", "profile": PROFILE}, headers=lib.as_(ADMIN))
    job_id = started.json()["id"]

    worker = Worker(lib.app.state.rt, owner="first", lease_seconds=30)
    while True:  # one step at a time, until some of the chunks have vectors and some do not
        lease = await worker.claim()
        assert lease is not None
        await worker.run(lease)
        chunks, vectors, _ = await counts(engine, v)
        if chunks and 0 < vectors < chunks:
            break
    midway = (await counts(engine, v))

    crashed = await worker.claim()  # the next step is taken, and the process dies before it finishes
    assert crashed is not None
    assert await Worker(lib.app.state.rt, owner="second").claim() is None  # its lease still holds
    clock.advance(seconds=31)
    await Worker(lib.app.state.rt, owner="second", lease_seconds=30).run_due()  # a new process takes it up where it stopped

    chunks, vectors, distinct = await counts(engine, v)
    assert midway[0] == chunks  # every chunk was already written at the midpoint; none were added again
    assert chunks == distinct == vectors > midway[1]
    job = (await lib.http.get(f"/api/v1/ingest-jobs/{job_id}", headers=lib.as_(ADMIN))).json()
    assert job["status"] == "completed" and job["chunks"] == chunks
    async with make_sessions(engine)() as db:
        assert await db.scalar(text("SELECT count(*) FROM job WHERE kind = 'library.ingest' AND status <> 'done'")) == 0


async def test_a_trial_has_no_effect_on_search(lib, engine):
    doc = await lib.register("Trial only")
    v = (await lib.upload(doc, law("trial subject")))["id"]
    assert (await lib.ingest(v, "trial"))["status"] == "completed"
    assert await lib.chunks(v)  # a preview
    assert await counts(engine, v) == (0, 0, 0)  # nothing stored
    await lib.adopt(v, "in_force")
    assert (await lib.search("trial subject records"))["no_source_found"] is True


async def test_a_full_run_is_refused_until_the_version_is_approved(lib):
    doc = await lib.register("Waiting for a person")
    v = (await lib.upload(doc, law("approval subject")))["id"]
    r = await lib.ingest(v, "full", expect=409)
    assert "A full ingest needs an approved version. Run a trial first." in r.text
    await lib.ingest(v, "trial")
    r = await lib.ingest(v, "full", expect=409)
    assert {g["guard"] for g in r.json()["failed"]} == {"full_needs_approved_version"}
    await lib.review(v)
    assert (await lib.ingest(v, "full"))["status"] == "completed"
    again = await lib.ingest(v, "full", expect=409)
    assert "not_already_ingested" in {g["guard"] for g in again.json()["failed"]}


async def test_options_from_the_screen_apply(lib):
    doc = await lib.register("Options")
    body = "Article 1 One\n\n" + "\n\n".join(f"Article {i} Rule {i}\n\nThe holder must keep records number {i} for five years and show them." for i in range(2, 6))
    v = (await lib.upload(doc, body.encode()))["id"]
    await lib.ingest(v, "trial", {**PROFILE, "split_by": "article"})
    by_article = await lib.chunks(v)
    await lib.ingest(v, "trial", {**PROFILE, "split_by": "fixed", "words_per_chunk": 400})
    fixed = await lib.chunks(v)
    assert len(by_article) > len(fixed)  # splitting by article gives more, smaller chunks than a fixed size
    overlap = await lib.http.post(f"/api/v1/document-versions/{v}/ingest-jobs", json={"mode": "trial", "profile": {**PROFILE, "words_per_chunk": 50, "overlap_words": 60}}, headers=lib.as_(ADMIN))
    assert overlap.status_code == 422
