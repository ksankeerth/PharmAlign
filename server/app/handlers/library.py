"""The document library: documents and immutable versions, ingestion, chunk preview, quality gate, document review,
per-country adoption, slots, snapshots and knowledge search. Search lives in app/knowledge/."""
import hashlib
from typing import Optional

from pharmalign_contracts.agents.run_agents import GapSignal
from pharmalign_contracts.server import models as m
from sqlalchemy import or_, select, text

from ..context import Ctx
from ..domain.fingerprints import fingerprint_of
from ..domain.guards import document_review_guards
from ..jobs import task
from ..knowledge import ingest, quality, source
from ..knowledge.embedding import EmbedderUnavailable, get_embedder, terms
from ..knowledge.parsers import StoredTextParser
from ..knowledge.presets import PRESETS
from ..knowledge.retrieval import QUESTION_SETS, for_country, run_questions
from ..knowledge.search import Scope, search
from ..ops import op
from ..paging import paginate
from ..problems import enforce_guards, guard, invalid, not_found, problem
from ..stores import STORES
from ..tables import ROW
from .gaps import SIGNALS, add_candidate, candidate_from
from .support import reach, with_limit

FINISH_RETRIEVAL = "library.finish_retrieval_test"


async def country_of(ctx: Ctx) -> Optional[str]:
    return (await STORES["workspace"].require(ctx, ctx.tenant.id, "Workspace")).country_code


async def visible_document(ctx: Ctx, document_id: str) -> m.Document:
    """A shared document, or this country's own. A Platform Admin sees every shelf."""
    d = await STORES["document"].require(ctx, document_id, "Document")
    if ctx.view != "platform_admin" and d.shelf == "country":
        owner = await ctx.db.scalar(select(ROW["document"].country).where(ROW["document"].id == d.id))
        if owner != await country_of(ctx):
            not_found("Document", document_id)
    return d


# ------------------------------------------------------------------ documents and versions

@op("GET", "/api/v1/documents")
async def list_documents(req, ctx):
    """Shared documents, and this country's own. A Platform Admin sees every shelf."""
    q = req.query
    row = ROW["document"]
    where = [c for c in (
        None if ctx.view == "platform_admin" else or_(row.shelf == "shared", row.country == await country_of(ctx)),
        row.shelf == q.shelf if q.shelf else None,
        row.doc["slot_id"].astext == q.slot_id if q.slot_id else None,
        row.latest_version_id.in_(select(ROW["document_version"].id).where(ROW["document_version"].review_status == q.review_status)) if q.review_status else None,
        row.doc["next_check_on"].astext <= ctx.now().date().isoformat() if q.due_for_check else None,
    ) if c is not None]
    return await STORES["document"].page(ctx, with_limit(q, 200), *where)


@op("POST", "/api/v1/documents")
async def register_document(req, ctx):
    b = req.body
    doc = m.Document(**b.model_dump(), id=ctx.id("DOC"), latest_version_id=None, created_at=ctx.now(), next_check_on=None)
    STORES["document"].add(ctx, doc, country=await country_of(ctx) if doc.shelf == "country" else None)
    ctx.audit(action="document_registered", object_type="document", object_id=doc.id, after=doc)
    return doc


@op("GET", "/api/v1/documents/{document_id}")
async def get_document(req, ctx):
    d = await visible_document(ctx, req.path.document_id)
    row = ROW["document_version"]
    versions = await STORES["document_version"].list(ctx, row.document_id == d.id, order_by=row.added_at.desc())
    return m.DocumentDetail(**d.model_dump(), versions=versions)


def _sniff(data: bytes) -> str:
    head = data[:512].lstrip().lower()
    if data[:5] == b"%PDF-":
        return "application/pdf"
    if head.startswith((b"<!doctype html", b"<html")) or b"<body" in head:
        return "text/html"
    return "text/plain"


@op("POST", "/api/v1/documents/{document_id}/versions")
async def add_version(req, ctx):
    """A new file is a new version. An old version is never edited or removed, and neither are its chunks."""
    b = req.body
    d = await STORES["document"].require(ctx, req.path.document_id, "Document", for_update=True)
    note = getattr(b, "provenance_note", None)
    if isinstance(b, m.VersionUpload):
        data, name, via = b.file, None, "upload"
    elif b.source_url.startswith("upload://"):
        data, name, via = None, b.source_url.removeprefix("upload://"), "upload"  # the file name only: the file itself comes later
    else:
        data, ctype = await source.fetch(b.source_url, ctx.rt.settings.fetch_max_bytes)
        name, via = b.source_url.split("?")[0].rstrip("/").split("/")[-1] or None, "url"
    row = ROW["document_version"]
    existing = await STORES["document_version"].list(ctx, row.document_id == d.id)
    if data is not None:
        file_hash = "sha256:" + hashlib.sha256(data).hexdigest()
    else:
        file_hash = fingerprint_of({"url": getattr(b, "source_url", None), "label": b.version_label})
    same = next((v for v in existing if v.file_hash == file_hash), None)
    if same:
        problem(409, "duplicate_version", "This file is already stored", f"Same file hash as version {same.id}. Change is detected by hash, not by name.")
    twin = None
    if data is not None:  # the same file under another document: flagged, not merged
        twin = await ctx.db.scalar(select(row.id).where(row.file_hash == file_hash).order_by(row.added_at).limit(1))
    ctype = _sniff(data) if data is not None else ("text/html" if (name or "").endswith(".html") else "application/pdf")
    ext = {"application/pdf": "pdf", "text/html": "html"}.get(ctype, "txt")
    v = m.DocumentVersion(
        id=f"{d.id}-v{len(existing) + 1}", document_id=d.id, version_label=b.version_label, file_hash=file_hash, filename=(name or f"{d.id}.{ext}")[:500],
        content_type=ctype, size_bytes=len(data) if data is not None else 0, published_on=b.published_on, effective_on=b.effective_on, source=via,
        added_by=ctx.person_id, added_at=ctx.now(), review_status="unreviewed", duplicate_of=twin, quality_status=None,
    )
    STORES["document_version"].add(ctx, v)
    await ctx.db.flush()
    if data is not None:
        ctx.db.add(ROW["document_file"](id=f"{v.id}:file", document_version_id=v.id, data=data))
    await STORES["document"].save(ctx, d.model_copy(update={"latest_version_id": v.id}))
    ctx.audit(action="document_version_added", object_type="document_version", object_id=v.id, before={"latest_version_id": d.latest_version_id}, after=v, reason=note)
    return v


@op("GET", "/api/v1/document-versions/{version_id}")
async def get_document_version(req, ctx):
    return await STORES["document_version"].require(ctx, req.path.version_id, "Document version")


# ------------------------------------------------------------------ ingestion

@op("GET", "/api/v1/ingest-presets")
async def list_presets(req, ctx):
    return {"items": PRESETS, "next_cursor": None}


async def _quality_row(ctx: Ctx, version_id: str):
    return await ctx.db.scalar(select(ROW["quality_report"]).where(ROW["quality_report"].document_version_id == version_id))


@op("POST", "/api/v1/document-versions/{version_id}/ingest-jobs")
async def start_ingest(req, ctx):
    v = await STORES["document_version"].require(ctx, req.path.version_id, "Document version", for_update=True)
    b = req.body
    full = b.mode == "full"
    approved = v.review_status in ("approved", "approved_with_notes")
    report = await _quality_row(ctx, v.id)
    held = bool(report and report.held)
    jobs = ROW["ingest_job"]
    running = await ctx.db.scalar(select(jobs.id).where(jobs.document_version_id == v.id, jobs.status.in_(("queued", "running"))).limit(1))
    done = await ctx.db.scalar(select(jobs.id).where(jobs.document_version_id == v.id, jobs.status == "completed", jobs.doc["mode"].astext == "full").limit(1))
    file = ROW["document_file"]
    has_file = await ctx.db.scalar(select(file.id).where(file.document_version_id == v.id).limit(1)) is not None
    has_chunks = await ctx.db.scalar(select(ROW["chunk"].id).where(ROW["chunk"].document_version_id == v.id).limit(1)) is not None
    enforce_guards([
        guard("has_source", has_file or has_chunks, None if has_file or has_chunks else "No file is stored for this version. Add the file first."),
        guard("full_needs_approved_version", not full or approved,
              "A full ingest needs an approved version. Run a trial first." if full and not approved else None),
        guard("quality_gate_passed", not full or v.quality_status in ("pass", "attention"),
              None if not full or v.quality_status in ("pass", "attention") else
              "No quality result yet. Run a trial first." if v.quality_status is None else
              "The quality gate failed on the last trial. Change the options and run a trial again."),
        guard("hold_cleared", not full or not held or v.review_status == "approved_with_notes",
              "Held for a person: hidden text with an instruction was found. Approve with notes saying what you checked." if full and held and v.review_status != "approved_with_notes" else None),
        guard("not_already_ingested", not full or done is None, "This version is already ingested. Add a new version to ingest it again." if full and done else None),
        guard("no_running_job", running is None, "Another ingestion of this version is still running." if running else None),
    ])
    try:
        get_embedder(ctx.rt.settings)
    except EmbedderUnavailable as e:
        problem(503, "embedder_unavailable", "No embedding model is available", str(e))
    job = m.IngestJob(id=ctx.id("ij"), document_version_id=v.id, mode=b.mode, status="queued", profile=b.profile, progress_pct=0, started_at=None, finished_at=None)
    STORES["ingest_job"].add(ctx, job)
    await ctx.db.flush()
    await ingest.start(ctx, job)
    ctx.audit(action="ingest_started", object_type="ingest_job", object_id=job.id, after={"mode": job.mode, "preset": job.profile.preset})
    return job


@op("GET", "/api/v1/ingest-jobs/{job_id}")
async def get_ingest_job(req, ctx):
    return await STORES["ingest_job"].require(ctx, req.path.job_id, "Ingest job")


@op("DELETE", "/api/v1/ingest-jobs/{job_id}")
async def cancel_ingest(req, ctx):
    job = await STORES["ingest_job"].require(ctx, req.path.job_id, "Ingest job", for_update=True)
    if job.status in ("queued", "running"):
        job = job.model_copy(update={"status": "cancelled", "finished_at": ctx.now()})
        await STORES["ingest_job"].save(ctx, job)
        ctx.audit(action="ingest_cancelled", object_type="ingest_job", object_id=job.id)
    return job


@op("GET", "/api/v1/document-versions/{version_id}/chunks")
async def list_chunks(req, ctx):
    """The stored chunks, or, before a full run, the chunks the latest trial made."""
    v = await STORES["document_version"].require(ctx, req.path.version_id, "Document version")
    row = ROW["chunk"]
    stored = await STORES["chunk"].list(ctx, row.document_version_id == v.id, order_by=row.position)
    if stored:
        return paginate(stored, req.query)
    out = ROW["ingest_output"]
    draft = await ctx.db.scalar(select(out).where(out.document_version_id == v.id, out.chunks.is_not(None)).order_by(out.created_at.desc()).limit(1))
    if draft is None:
        return paginate([], req.query)
    document = await STORES["document"].require(ctx, v.document_id, "Document")
    from ..knowledge.chunking import anchor_for, text_hash

    items = [m.ChunkView(
        anchor=anchor_for(v.id, c["position"]), document_version_id=v.id, position=c["position"], chunk_type=c["chunk_type"], page=c.get("page"), page_end=c.get("page_end"),
        article=c.get("article") or None, section_path=(c.get("section_path") or None), language=c.get("language") or document.language, text=c["text"], text_hash=text_hash(c["text"]),
    ) for c in draft.chunks]
    return paginate(items, req.query)


@op("GET", "/api/v1/document-versions/{version_id}/quality")
async def get_quality(req, ctx):
    v = await STORES["document_version"].require(ctx, req.path.version_id, "Document version")
    stored = await _quality_row(ctx, v.id)
    if stored is not None:
        return m.QualityReport.model_validate(stored.doc)
    # No trial has run here, such as a version loaded with its chunks already. Check the text it has.
    document = await STORES["document"].require(ctx, v.document_id, "Document")
    rows = (await ctx.db.execute(text("SELECT text, page, page_end, article, section_path FROM chunk WHERE document_version_id = :v ORDER BY position"), {"v": v.id})).mappings().all()
    chunks = [dict(r) for r in rows]
    raw = StoredTextParser(chunks).parse(None, None)
    a = quality.assess(v.id, raw, chunks, language=document.language, doc_type=document.doc_type, images="ignore_all", split_by="heading", split_count=0, max_chars=3700)
    report = a.report
    if v.quality_status and v.quality_status != report.status:
        # The status recorded when the version was first checked stands. The checks above are of the text as it is stored now.
        report = report.model_copy(update={
            "status": v.quality_status,
            "checks": [*report.checks, m.QualityCheck(key="earlier_result", status=v.quality_status, detail="Recorded when this version was first checked.")],
        })
    return report


@op("POST", "/api/v1/document-versions/{version_id}/review")
async def review_version(req, ctx):
    v = await STORES["document_version"].require(ctx, req.path.version_id, "Document version", for_update=True)
    b = req.body
    report = await _quality_row(ctx, v.id)
    held = bool(report and report.held)
    enforce_guards([
        *document_review_guards(v, ctx.person_id, b.decision),
        guard("hold_needs_notes", not (held and b.decision == "approved"),
              "Hidden text with an instruction was found. Approve with notes that say what you checked." if held and b.decision == "approved" else "No hold on this version."),
    ], "This review is refused")
    review = m.DocumentReview(decision=b.decision, notes=b.notes, id=ctx.id("dr"), document_version_id=v.id, reviewed_by=ctx.person_id, reviewed_at=ctx.now())
    await STORES["document_version"].save(ctx, v.model_copy(update={"review_status": b.decision}))
    STORES["document_review"].add(ctx, review)
    ctx.audit(action="document_reviewed", object_type="document_version", object_id=v.id, before={"review_status": v.review_status},
              after={"review_status": b.decision}, reason=b.notes)
    return review


# ------------------------------------------------------------------ adoption and slots

@op("GET", "/api/v1/workspaces/{workspace_id}/adoptions")
async def list_adoptions(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    return await STORES["tenant_document_adoption"].page(wctx, with_limit(req.query, 200), order_by=ROW["tenant_document_adoption"].document_version_id)


@op("PUT", "/api/v1/workspaces/{workspace_id}/adoptions/{version_id}")
async def set_adoption(req, ctx):
    """Legal status is this country's call, made by a person. The document itself carries none."""
    wctx = await reach(ctx, req.path.workspace_id)
    await STORES["document_version"].require(ctx, req.path.version_id, "Document version")
    b = req.body
    a = m.Adoption(**b.model_dump(), workspace_id=wctx.tenant.id, document_version_id=req.path.version_id, decided_by=ctx.person_id, decided_at=ctx.now())
    store = STORES["tenant_document_adoption"]
    before = await store.get(wctx, f"{wctx.tenant.id}|{a.document_version_id}")
    if before:
        await store.save(wctx, a)
    else:
        store.add(wctx, a)
    wctx.audit(action="adoption_set", object_type="adoption", object_id=f"{a.workspace_id}|{a.document_version_id}", before=before, after=a, reason=a.notes)
    return a


@op("GET", "/api/v1/workspaces/{workspace_id}/slots")
async def list_slots(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    return await STORES["document_slot"].page(wctx, req.query, order_by=ROW["document_slot"].id)


@op("PATCH", "/api/v1/slots/{slot_id}")
async def update_slot(req, ctx):
    slot = await STORES["document_slot"].require(ctx, req.path.slot_id, "Slot", for_update=True)
    b = req.body
    if b.document_id is not None:
        await STORES["document"].require(ctx, b.document_id, "Document")
    changes = {k: v for k, v in b.model_dump(exclude_unset=True).items()}
    new = slot.model_copy(update=changes)
    if new.status == "missing" and not new.gap_id:
        # A required document that is missing is a gap candidate, for a person to look at.
        signal = GapSignal(id=ctx.id("s"), kind="empty_slot", detail=new.label, count=1)
        SIGNALS.add(ctx, signal)
        gap = await add_candidate(ctx, candidate_from(ctx, signal))
        new = new.model_copy(update={"gap_id": gap.id})
        ctx.audit(action="gap_candidate_created", object_type="gap", object_id=gap.id, after={"slot_id": slot.id})
    await STORES["document_slot"].save(ctx, new)
    ctx.audit(action="slot_updated", object_type="slot", object_id=slot.id, before=slot, after=new)
    return new


# ------------------------------------------------------------------ snapshots

@op("POST", "/api/v1/workspaces/{workspace_id}/kb-snapshots")
async def create_snapshot(req, ctx):
    """Freezes what this country has adopted among the approved versions. Each item keeps the status it has today."""
    wctx = await reach(ctx, req.path.workspace_id)
    ad, dv = ROW["tenant_document_adoption"], ROW["document_version"]
    rows = (await wctx.db.execute(
        select(ad.doc, dv.doc.label("version"), ROW["document"].doc["title"].astext.label("title"))
        .join(dv, dv.id == ad.document_version_id).join(ROW["document"], ROW["document"].id == dv.document_id)
        .where(ad.tenant_id == wctx.tenant.id, ad.adopted.is_(True), dv.review_status.in_(("approved", "approved_with_notes")), text("coalesce(document_version.quality_status, 'attention') <> 'fail'"))
        .order_by(ad.document_version_id)
    )).all()
    items = [m.SnapshotItem(document_version_id=r.doc["document_version_id"], title=r.title, legal_status=r.doc["legal_status"], authority_level=r.doc["authority_level"]) for r in rows]
    snap = m.Snapshot(
        id=ctx.id("snap"), workspace_id=wctx.tenant.id, label=req.body.label, content_hash=fingerprint_of([i.model_dump() for i in items]), item_count=len(items),
        created_by=ctx.person_id, created_at=ctx.now(),
    )
    STORES["kb_snapshot"].add(wctx, snap)
    await wctx.db.flush()
    for i in items:
        STORES["kb_snapshot_item"].add(wctx, i, id=f"{snap.id}|{i.document_version_id}", snapshot_id=snap.id)
    # A country in draft has no cases yet, so its newest freeze is the one to test against. After sign-off, only a sign-off changes it.
    workspace = await STORES["workspace"].require(wctx, wctx.tenant.id, "Workspace", for_update=True)
    made_active = workspace.mode == "draft"
    if made_active:
        await STORES["workspace"].save(wctx, workspace.model_copy(update={"active_kb_snapshot_id": snap.id}))
    wctx.audit(action="snapshot_created", object_type="kb_snapshot", object_id=snap.id, after={"item_count": snap.item_count, "content_hash": snap.content_hash, "made_active": made_active}, reason=req.body.note)
    return snap


@op("GET", "/api/v1/workspaces/{workspace_id}/kb-snapshots")
async def list_snapshots(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    return await STORES["kb_snapshot"].page(wctx, req.query, order_by=ROW["kb_snapshot"].created_at.desc())


@op("GET", "/api/v1/kb-snapshots/{snapshot_id}")
async def get_snapshot(req, ctx):
    snapshot = await STORES["kb_snapshot"].require(ctx, req.path.snapshot_id, "Snapshot")
    row = ROW["kb_snapshot_item"]
    items = await STORES["kb_snapshot_item"].list(ctx, row.snapshot_id == snapshot.id, order_by=row.document_version_id)
    return m.SnapshotDetail(**snapshot.model_dump(), items=items)


# ------------------------------------------------------------------ search

@op("POST", "/api/v1/kb-search")
async def kb_search(req, ctx):
    """The test search. It filters and ranks exactly as the agents' search does."""
    b = req.body
    if not terms(b.query):
        invalid("The query has no searchable words.")
    if b.snapshot_id:
        await STORES["kb_snapshot"].require(ctx, b.snapshot_id, "Snapshot")
    try:
        embedder = get_embedder(ctx.rt.settings)
    except EmbedderUnavailable as e:
        problem(503, "embedder_unavailable", "No embedding model is available", str(e))
    found = await search(ctx.db, Scope(ctx.tenant.id, await country_of(ctx), b.snapshot_id, b.purpose), b.query, embedder, b.top_k)
    return m.KbSearchResponse(no_source_found=found.no_source_found, filters_applied=found.filters_applied, searches_tried=found.searches_tried, hits=found.hits)


@op("POST", "/api/v1/retrieval-tests")
async def start_retrieval_test(req, ctx):
    await STORES["kb_snapshot"].require(ctx, req.body.snapshot_id, "Snapshot")
    if req.body.question_set not in QUESTION_SETS:
        invalid(f"Unknown question set. Known: {', '.join(sorted(QUESTION_SETS))}.")
    run = m.RetrievalTestRun(id=ctx.id("rt"), status="running")
    STORES["retrieval_test_run"].add(ctx, run)
    ctx.schedule(FINISH_RETRIEVAL, {"test_id": run.id, "snapshot_id": req.body.snapshot_id, "question_set": req.body.question_set})
    ctx.audit(action="retrieval_test_started", object_type="retrieval_test", object_id=run.id, after={"question_set": req.body.question_set})
    return run


@op("GET", "/api/v1/retrieval-tests/{test_id}")
async def get_retrieval_test(req, ctx):
    return await STORES["retrieval_test_run"].require(ctx, req.path.test_id, "Retrieval test")


@task(FINISH_RETRIEVAL)
async def finish_retrieval_test(payload, ctx):
    run = await STORES["retrieval_test_run"].get(ctx, payload["test_id"], for_update=True)
    if run is None or run.status != "running":
        return
    try:
        scope = Scope(ctx.tenant.id, await country_of(ctx), payload["snapshot_id"], "requirement")
        done = await run_questions(ctx.db, scope, get_embedder(ctx.rt.settings), for_country(QUESTION_SETS[payload["question_set"]], await STORES["document_slot"].list(ctx)))
        done = done.model_copy(update={"id": run.id})
    except EmbedderUnavailable:
        done = run.model_copy(update={"status": "failed"})
    await STORES["retrieval_test_run"].save(ctx, done)
    ctx.audit(action="retrieval_test_finished", object_type="retrieval_test", object_id=run.id, after={"status": done.status, "document_hit_at_5": done.document_hit_at_5},
              actor_type="system", actor_id="retrieval")
