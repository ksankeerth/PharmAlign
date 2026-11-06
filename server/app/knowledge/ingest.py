"""The ingestion worker, on the jobs table.

A run is a chain of short steps, each one a job under the usual lease and each one a single transaction:

    parse -> assess -> (full run only) commit -> embed, a batch at a time

A step that dies leaves nothing behind. Its lease runs out and the job is taken up again from the same step. What a step wrote
is safe to write twice: chunks and vectors that exist are left alone. A trial stops after `assess` and has no effect on search.
The parse step is the slow one, so its result is stored, and a later step reads it instead of parsing again.

A person accepts the version in between: a full run is refused until the version is approved and has passed the gate.
Nothing in a document is ever read as an instruction. Its text is parsed, stored and searched as data.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from pharmalign_contracts.server import models as m
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ..jobs import task
from ..stores import STORES
from ..tables import ROW
from . import indexing, parsers, quality
from .chunking import ChunkRules, apply_table_mode, chunk_blocks, chunk_language, split_long
from .embedding import EmbedderUnavailable, get_embedder
from .layout import ArabicNotSupported, Hidden, Options, Raw, stitch

log = logging.getLogger(__name__)

STEP = "library.ingest"
PROFILE_PROGRESS = {"parse": 5, "assess": 45, "commit": 60, "embed": 60}
HOLD_NOTE = "Held for a person"


# ------------------------------------------------------------------ what a step reads and writes

def raw_to_json(raw: Raw) -> dict:
    return {
        "blocks": raw.blocks, "warnings": raw.warnings, "page_count": raw.page_count, "image_only": raw.image_only,
        "hidden": [{"page": h.page, "reason": h.reason, "text": h.text} for h in raw.hidden], "meta_text": raw.meta_text,
    }


def raw_from_json(d: dict) -> Raw:
    return Raw(d["blocks"], d["warnings"], d["page_count"], d["image_only"], [Hidden(**h) for h in d["hidden"]], d.get("meta_text", ""))


def options_for(profile: m.IngestProfile) -> Options:
    return Options(
        headers_footers=profile.remove_headers_footers, page_numbers=profile.remove_page_numbers, contents=profile.remove_contents_pages,
        ocr=profile.images in ("ocr_image_only", "describe_with_person_check"), ocr_languages=list(profile.ocr_languages),
    )


async def _fail(ctx, job: m.IngestJob, message: str) -> None:
    done = job.model_copy(update={"status": "failed", "error": message[:500], "finished_at": ctx.now()})
    await STORES["ingest_job"].save(ctx, done)
    ctx.audit(action="ingest_finished", object_type="ingest_job", object_id=job.id, after={"status": "failed", "error": done.error},
              actor_type="system", actor_id="ingest-worker")


async def _save(ctx, job: m.IngestJob, **changes) -> m.IngestJob:
    job = job.model_copy(update=changes)
    await STORES["ingest_job"].save(ctx, job)
    return job


def _next(ctx, job_id: str, stage: str) -> None:
    ctx.schedule(STEP, {"job_id": job_id, "stage": stage})


async def start(ctx, job: m.IngestJob) -> None:
    """Queues the first step. A version that already has chunks (a full run being carried on, or one loaded from the demo data) skips to the vectors."""
    first = "parse"
    if job.mode == "full":
        chunks, _ = await indexing.counts(ctx.db, job.document_version_id, get_embedder(ctx.rt.settings).name)
        if chunks:
            first = "embed"
    _next(ctx, job.id, first)


@task(STEP)
async def step(payload: dict, ctx) -> None:
    job = await STORES["ingest_job"].get(ctx, payload["job_id"], for_update=True)
    if job is None or job.status in ("cancelled", "completed", "failed"):
        return
    if job.status == "queued":
        job = await _save(ctx, job, status="running", started_at=ctx.now())
    stage = payload["stage"]
    try:
        await {"parse": _parse, "assess": _assess, "commit": _commit, "embed": _embed}[stage](ctx, job)
    except (parsers.ParserUnavailable, ArabicNotSupported, EmbedderUnavailable) as e:
        await _fail(ctx, job, str(e))
    except Exception as e:  # a problem with this file or this run, not a reason to try the same step again
        log.exception("ingest %s stopped at %s", job.id, stage)
        await _fail(ctx, job, f"Ingestion stopped at {stage}: {type(e).__name__}: {e}")


# ------------------------------------------------------------------ parse

async def _source(ctx, version: m.DocumentVersion, document: m.Document) -> tuple[parsers.Source, Optional[list[dict]]]:
    file = ROW["document_file"]
    data = await ctx.db.scalar(select(file.data).where(file.document_version_id == version.id))
    stored = None
    if data is None:
        rows = (await ctx.db.execute(text("SELECT text, page, page_end, article FROM chunk WHERE document_version_id = :v ORDER BY position"), {"v": version.id})).mappings().all()
        stored = [dict(r) for r in rows]
    return parsers.Source(bytes(data) if data is not None else b"", version.filename, version.content_type, document.language), stored


async def _parse(ctx, job: m.IngestJob) -> None:
    version = await STORES["document_version"].require(ctx, job.document_version_id, "Document version")
    document = await STORES["document"].require(ctx, version.document_id, "Document")
    source, stored = await _source(ctx, version, document)
    parser = parsers.choose_parser(job.profile.parser, source, stored)
    opts = options_for(job.profile)
    raw = await asyncio.to_thread(parser.parse, source, opts)  # off the loop, so the lease keeps being renewed
    raw = parsers.apply_profile_pages(raw, job.profile.page_range)
    out = ROW["ingest_output"]
    await ctx.db.execute(pg_insert(out).values(
        id=f"{job.id}:out", ingest_job_id=job.id, document_version_id=version.id, stage="parse", raw=raw_to_json(raw),
    ).on_conflict_do_nothing())
    await _save(ctx, job, progress_pct=PROFILE_PROGRESS["parse"], pages_done=raw.page_count)
    _next(ctx, job.id, "assess")


# ------------------------------------------------------------------ assess: chunk, then the quality gate

def build_chunks(raw: Raw, profile: m.IngestProfile, language: str, max_chars: int) -> tuple[list[dict], int]:
    """Blocks to chunks under the profile. Returns the chunks, in order, and how many long ones were split."""
    blocks = parsers.drop_annexes(raw.blocks) if not profile.include_annexes else raw.blocks
    blocks = apply_table_mode(stitch(blocks), profile.tables)
    rules = ChunkRules(
        split_by=profile.split_by, words_per_chunk=profile.words_per_chunk, overlap_words=profile.overlap_words,
        min_chunk_words=profile.min_chunk_words, max_chars=max_chars, tables=profile.tables,
    )
    chunks = chunk_blocks(blocks, rules)
    chunks, split = split_long(chunks, max_chars)
    for i, c in enumerate(chunks):
        c["position"] = i
        c["language"] = chunk_language(c["text"], language)
    return chunks, split


async def _assess(ctx, job: m.IngestJob) -> None:
    version = await STORES["document_version"].require(ctx, job.document_version_id, "Document version", for_update=True)
    document = await STORES["document"].require(ctx, version.document_id, "Document")
    embedder = get_embedder(ctx.rt.settings)
    out_row = await ctx.db.scalar(select(ROW["ingest_output"]).where(ROW["ingest_output"].ingest_job_id == job.id))
    raw = raw_from_json(out_row.raw)
    max_chars = embedder.max_chars - indexing.PREFIX_CHARS
    chunks, split = build_chunks(raw, job.profile, document.language, max_chars)
    report_row = ROW["quality_report"]
    others = [(r.document_version_id, r.sketch) for r in (await ctx.db.scalars(select(report_row).where(report_row.document_version_id != version.id))).all()]
    result = quality.assess(
        version.id, raw, chunks, language=document.language, doc_type=document.doc_type, images=job.profile.images, split_by=job.profile.split_by,
        split_count=split, max_chars=max_chars, others=others,
    )
    report = result.report
    values = dict(document_version_id=version.id, ingest_job_id=job.id, status=report.status, held=result.held, doc=report.model_dump(mode="json"),
                  sketch=result.sketch)
    await ctx.db.execute(pg_insert(report_row).values(id=f"{version.id}:quality", **values).on_conflict_do_update(index_elements=["document_version_id"], set_=values))
    out_row.stage, out_row.chunks = "assess", chunks
    await STORES["document_version"].save(ctx, version.model_copy(update={
        "quality_status": report.status, "held": result.held, "duplicate_of": version.duplicate_of or result.duplicate_of,
    }))
    warnings = [c.detail for c in report.checks if c.status != "pass" and c.detail]
    job = await _save(ctx, job, progress_pct=PROFILE_PROGRESS["assess"], chunks=len(chunks), warnings=[w[:500] for w in warnings])
    hidden_note = next((c.detail for c in report.checks if c.key == "hidden_text" and c.detail), None)
    if hidden_note:
        ctx.audit(action="hidden_text_flagged", object_type="document_version", object_id=version.id, after={"held": result.held, "spans": len(raw.hidden)},
                  reason=(" | ".join(f"p{h.page or '-'}: {h.text[:120]}" for h in raw.hidden[:5]) or hidden_note)[:500], actor_type="system", actor_id="ingest-worker")

    if report.status == "fail":
        detail = next((c.detail for c in report.checks if c.status == "fail" and c.detail), "A check failed.")
        return await _fail(ctx, job, f"Quality gate failed: {detail}")
    if job.mode == "trial":
        await _save(ctx, job, status="completed", progress_pct=100, finished_at=ctx.now())
        ctx.audit(action="ingest_finished", object_type="ingest_job", object_id=job.id, after={"status": "completed", "chunks": len(chunks)},
                  actor_type="system", actor_id="ingest-worker")
        return
    if result.held and version.review_status != "approved_with_notes":
        return await _fail(ctx, job, f"{HOLD_NOTE}: hidden text with an instruction. Nothing was stored.")
    _next(ctx, job.id, "commit")


# ------------------------------------------------------------------ commit and embed (full runs)

async def _commit(ctx, job: m.IngestJob) -> None:
    version = await STORES["document_version"].require(ctx, job.document_version_id, "Document version", for_update=True)
    document = await STORES["document"].require(ctx, version.document_id, "Document")
    out_row = await ctx.db.scalar(select(ROW["ingest_output"]).where(ROW["ingest_output"].ingest_job_id == job.id))
    await indexing.insert_chunks(ctx.db, version.id, document.language, out_row.chunks or [])
    await _save(ctx, job, progress_pct=PROFILE_PROGRESS["commit"])
    _next(ctx, job.id, "embed")


async def _embed(ctx, job: m.IngestJob) -> None:
    embedder = get_embedder(ctx.rt.settings)
    await indexing.embed_missing(ctx.db, embedder, job.document_version_id, limit=ctx.rt.settings.embed_batch)
    total, done = await indexing.counts(ctx.db, job.document_version_id, embedder.name)
    if done < total:
        await _save(ctx, job, progress_pct=min(99, 60 + round(40 * done / max(total, 1))), chunks=total)
        return _next(ctx, job.id, "embed")
    await _save(ctx, job, status="completed", progress_pct=100, chunks=total, finished_at=ctx.now())
    ctx.audit(action="ingest_finished", object_type="ingest_job", object_id=job.id, after={"status": "completed", "chunks": total},
              actor_type="system", actor_id="ingest-worker")
