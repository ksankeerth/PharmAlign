"""Search that filters first, then ranks, and says so when nothing answers.

The filter (tenant, snapshot, legal status, review and quality state, shelf, any document or topic limit) picks the
candidate chunks. Only then does pgvector rank them, with an exact scan of that set. Ranking first and filtering the top k
would let strong matches from outside the filter push the qualifying ones out.

"No source found" is an answer. It comes back when nothing qualifies, or when nothing is similar enough to count, and it lists
the searches tried. A source that exists only outside the filter (superseded, say) is named in that list, never returned as a hit.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from pharmalign_contracts.server import models as m
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .embedding import Embedder, terms, vector_text

APPROVED = ("approved", "approved_with_notes")


@dataclass
class Scope:
    """Everything the filter needs. A snapshot pins the versions and their status as frozen; without one, the latest adopted versions."""

    tenant_id: str
    country: Optional[str]
    snapshot_id: Optional[str] = None
    purpose: str = "requirement"  # requirement: in force only. context: any adopted status
    document_ids: Optional[Sequence[str]] = None
    doc_types: Optional[Sequence[str]] = None


@dataclass
class Found:
    hits: list[m.KbHit]
    no_source_found: bool
    filters_applied: dict[str, Any]
    searched: int  # candidate chunks inside the filter
    searches_tried: list[dict[str, Any]] = field(default_factory=list)  # when nothing was found: what was searched


def _filter_sql(scope: Scope, any_status: bool) -> tuple[str, dict]:
    """The candidate chunks. With a snapshot, each version's status is the one frozen in the snapshot. Without one, it is the country's adoption of each version,
    so a document can have an old version in force and a new one still a draft."""
    params: dict[str, Any] = {"tenant": scope.tenant_id, "country": scope.country, "docs": list(scope.document_ids) if scope.document_ids else None,
                              "types": list(scope.doc_types) if scope.doc_types else None, "approved": list(APPROVED)}
    if scope.snapshot_id:
        source = "kb_snapshot_item i JOIN document_version dv ON dv.id = i.document_version_id AND i.snapshot_id = :snap AND i.tenant_id = :tenant"
        status, level = "i.legal_status", "i.authority_level"
        params["snap"] = scope.snapshot_id
    else:
        source = "tenant_document_adoption i JOIN document_version dv ON dv.id = i.document_version_id AND i.tenant_id = :tenant AND i.adopted"
        status, level = "i.legal_status", "i.authority_level"
    where = "" if any_status else f"AND {status} = 'in_force'"
    sql = f"""
        SELECT ch.id AS chunk_id, ch.document_version_id, dv.document_id, d.doc ->> 'title' AS title, d.issuer, d.doc ->> 'jurisdiction' AS jurisdiction,
               {status} AS legal_status, {level} AS authority_level, ch.article, ch.page, ch.text, ch.language, ch.section_path
        FROM {source}
        JOIN document d ON d.id = dv.document_id
        JOIN chunk ch ON ch.document_version_id = dv.id
        WHERE dv.review_status = ANY(:approved) AND coalesce(dv.quality_status, 'attention') <> 'fail'
          AND (d.shelf = 'shared' OR d.country IS NOT DISTINCT FROM :country)
          AND (CAST(:docs AS text[]) IS NULL OR d.id = ANY(CAST(:docs AS text[])))
          AND (CAST(:types AS text[]) IS NULL OR d.doc_type = ANY(CAST(:types AS text[])))
          {where}
    """
    return sql, params


async def _ranked(db: AsyncSession, scope: Scope, any_status: bool, embedder: Embedder, qvec: Sequence[float], limit: int) -> tuple[list[Any], int]:
    candidates, params = _filter_sql(scope, any_status)
    count_sql = f"SELECT count(*) FROM ({candidates}) c"
    searched = (await db.execute(text(count_sql), params)).scalar_one()
    # MATERIALIZED: the filter is settled before any ranking starts.
    sql = f"""
        WITH cand AS MATERIALIZED ({candidates})
        SELECT c.*, 1 - (e.embedding <=> CAST(CAST(:q AS text) AS vector)) AS score
        FROM cand c JOIN chunk_embedding e ON e.chunk_id = c.chunk_id AND e.model = :model
        ORDER BY e.embedding <=> CAST(CAST(:q AS text) AS vector), c.chunk_id
        LIMIT :limit
    """
    rows = (await db.execute(text(sql), {**params, "q": vector_text(qvec), "model": embedder.name, "limit": limit})).mappings().all()
    return list(rows), searched


def _supported(query_terms: set[str], row: Any) -> bool:
    """A chunk in the query's own language must share at least one meaningful word with it. Similarity alone can't tell a near miss from an answer."""
    body = set(terms(f"{row['title']} {row['section_path'] or ''} {row['text']}"))
    return bool(query_terms & body)


def _hit(row: Any, score: float) -> m.KbHit:
    return m.KbHit(
        anchor=row["chunk_id"], text=row["text"], score=round(float(score), 3), document_id=row["document_id"], title=row["title"], issuer=row["issuer"],
        jurisdiction=row["jurisdiction"], legal_status=row["legal_status"], authority_level=row["authority_level"], article=row["article"], page=row["page"],
    )


async def search(db: AsyncSession, scope: Scope, query: str, embedder: Embedder, top_k: int = 10) -> Found:
    qvec = embedder.embed([query])[0]
    query_terms = set(terms(query))
    requirement = scope.purpose == "requirement"
    filters = {
        "workspace_id": scope.tenant_id, "snapshot_id": scope.snapshot_id, "adopted": True,
        "legal_status": ["in_force"] if requirement else "any", "review_status": list(APPROVED), "quality_gate": "not failed",
        "document_ids": list(scope.document_ids) if scope.document_ids else None, "doc_types": list(scope.doc_types) if scope.doc_types else None,
        "applied_before_search": True, "embedder": embedder.name, "similarity_floor": embedder.min_score,
    }
    rows, searched = await _ranked(db, scope, not requirement, embedder, qvec, top_k + 25)
    hits: list[m.KbHit] = []
    best = float(rows[0]["score"]) if rows else None
    for r in rows:
        if float(r["score"]) < embedder.min_score:
            break
        if not _supported(query_terms, r):
            continue
        hits.append(_hit(r, r["score"] + (0.0 if requirement or r["legal_status"] == "in_force" else -0.02)))
        if len(hits) == top_k:
            break
    hits.sort(key=lambda h: (-h.score, h.authority_level, h.anchor))
    if hits:
        return Found(hits, False, filters, searched)

    tried = [{
        "search": "meaning and words, inside the filter", "candidates": searched, "best_score": None if best is None else round(best, 3),
        "floor": embedder.min_score,
        "result": "nothing is in the filter" if not searched else "no chunk in the filter was similar enough" if best is not None else "no chunk in the filter has been indexed",
    }]
    if requirement:
        # Look once outside the legal-status filter, so a person can see why. Never returned as a hit.
        outside, n = await _ranked(db, scope, True, embedder, qvec, 5)
        near = next((r for r in outside if float(r["score"]) >= embedder.min_score and _supported(query_terms, r) and r["legal_status"] != "in_force"), None)
        tried.append({
            "search": "same question, any legal status", "candidates": n,
            "result": f"closest is {near['title']} ({near['legal_status'].replace('_', ' ')}). Not a source for a requirement." if near else "nothing similar either",
        })
    return Found([], True, filters, searched, tried)


# ------------------------------------------------------------------ agents

async def agent_search(conn, query: str, embedder: Embedder, top_k: int = 10, requirement: bool = True) -> Found:
    """The same ranking for an agent, through the agent_read views only. The run's tenant and case are set on the connection by the tool server,
    and the views show nothing else: the pinned snapshot, approved versions that passed the gate."""
    qvec = vector_text(embedder.embed([query])[0])
    view = "agent_read.kb_requirement_chunk" if requirement else "agent_read.kb_chunk"
    sql = f"""
        WITH cand AS MATERIALIZED (SELECT * FROM {view})
        SELECT c.anchor AS chunk_id, c.document_id, c.title, c.issuer, c.country AS jurisdiction, c.legal_status, c.authority_level, c.article, c.page, c.text,
               c.section_path, 1 - (v.embedding <=> CAST(CAST(:q AS text) AS vector)) AS score
        FROM cand c JOIN agent_read.kb_chunk_vector v ON v.anchor = c.anchor AND v.model = :model
        ORDER BY v.embedding <=> CAST(CAST(:q AS text) AS vector), c.anchor
        LIMIT :limit
    """
    rows = (await conn.execute(text(sql), {"q": qvec, "model": embedder.name, "limit": top_k + 25})).mappings().all()
    candidates = (await conn.execute(text(f"SELECT count(*) FROM {view}"))).scalar_one()
    query_terms = set(terms(query))
    hits = [_hit({**r, "jurisdiction": r["jurisdiction"] or "-"}, r["score"]) for r in rows if float(r["score"]) >= embedder.min_score and _supported(query_terms, r)][:top_k]
    filters: dict[str, Any] = {"source": view, "similarity_floor": embedder.min_score, "applied_before_search": True}
    if hits:
        return Found(hits, False, filters, candidates)
    tried = [{"search": "meaning and words, inside the run's pinned snapshot", "candidates": candidates, "floor": embedder.min_score,
              "best_score": round(float(rows[0]["score"]), 3) if rows else None}]
    return Found([], True, filters, candidates, tried)
