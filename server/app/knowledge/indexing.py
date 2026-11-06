"""Writing chunks and their vectors. Both are safe to repeat: a chunk or vector that is already there is left as it is."""
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..tables import ROW
from .chunking import anchor_for, text_hash
from .embedding import Embedder, vector_text
from .keywords import tokens

PREFIX_CHARS = 300  # room kept for the title and section that go in front of the text when it is embedded


def embed_input(title: str, section_path: Optional[str], body: str, limit: int) -> str:
    """The section path helps the embedding but is stored apart from the text. It always fits: chunks are capped below the limit."""
    head = f"{title[:150]} | {(section_path or '')[:150]}\n"
    return (head + body)[:limit]


async def insert_chunks(db: AsyncSession, version_id: str, language: str, chunks: Sequence[dict]) -> int:
    """All of a version's chunks at once, with stable anchors. Existing positions are left alone, so the old chunks of a version never change."""
    table = ROW["chunk"]
    rows = []
    for c in chunks:
        anchor = anchor_for(version_id, c["position"])
        body = c["text"]
        doc = {
            "anchor": anchor, "document_version_id": version_id, "position": c["position"], "chunk_type": c["chunk_type"], "page": c.get("page"),
            "page_end": c.get("page_end"), "article": c.get("article") or None, "section_path": (c.get("section_path") or None),
            "language": c.get("language") or language, "text": body, "text_hash": text_hash(body),
        }
        if doc["section_path"] and len(doc["section_path"]) > 1000:
            doc["section_path"] = doc["section_path"][:1000]
        rows.append(dict(
            id=anchor, doc=doc, document_version_id=version_id, position=c["position"], chunk_type=doc["chunk_type"], page=doc["page"], page_end=doc["page_end"],
            article=doc["article"], section_path=doc["section_path"], language=doc["language"], text=body, text_hash=doc["text_hash"],
        ))
    done = 0
    for i in range(0, len(rows), 500):
        result = await db.execute(pg_insert(table).values(rows[i:i + 500]).on_conflict_do_nothing())
        done += result.rowcount or 0
    return done


async def index_terms(db: AsyncSession, version_id: Optional[str] = None) -> int:
    """Stores the stemmed words of chunks that have none yet."""
    rows = (await db.execute(text(
        "SELECT ch.id, ch.text FROM chunk ch WHERE (CAST(:v AS text) IS NULL OR ch.document_version_id = :v) "
        "AND NOT EXISTS (SELECT 1 FROM chunk_terms t WHERE t.chunk_id = ch.id)"
    ), {"v": version_id})).mappings().all()
    if rows:
        await db.execute(
            text("INSERT INTO chunk_terms (id, chunk_id, tokens) VALUES (:id, :id, :tokens) ON CONFLICT DO NOTHING"),
            [{"id": r["id"], "tokens": " ".join(tokens(r["text"]))} for r in rows],
        )
    return len(rows)


async def embed_missing(db: AsyncSession, embedder: Embedder, version_id: Optional[str] = None, limit: Optional[int] = None) -> int:
    """Embeds chunks that have no vector for this model yet, and stores their keyword terms. Returns how many vectors it wrote."""
    await index_terms(db, version_id)
    sql = """
        SELECT ch.id, ch.text, ch.section_path, d.doc ->> 'title' AS title
        FROM chunk ch JOIN document_version dv ON dv.id = ch.document_version_id JOIN document d ON d.id = dv.document_id
        WHERE (CAST(:v AS text) IS NULL OR ch.document_version_id = :v)
          AND NOT EXISTS (SELECT 1 FROM chunk_embedding e WHERE e.chunk_id = ch.id AND e.model = :model)
        ORDER BY ch.document_version_id, ch.position
    """
    params: dict = {"v": version_id, "model": embedder.name}
    if limit:
        sql += " LIMIT :n"
        params["n"] = limit
    rows = (await db.execute(text(sql), params)).mappings().all()
    written = 0
    step = 64
    for i in range(0, len(rows), step):
        part = rows[i:i + step]
        vectors = embedder.embed([embed_input(r["title"] or "", r["section_path"], r["text"], embedder.max_chars) for r in part])
        await db.execute(
            text("INSERT INTO chunk_embedding (id, chunk_id, model, dims, embedding) "
                 "VALUES (:id, :chunk, :model, :dims, CAST(CAST(:emb AS text) AS vector)) ON CONFLICT DO NOTHING"),
            [{"id": f"{r['id']}|{embedder.name}", "chunk": r["id"], "model": embedder.name, "dims": embedder.dims, "emb": vector_text(v)} for r, v in zip(part, vectors)],
        )
        written += len(part)
    return written


async def counts(db: AsyncSession, version_id: str, model: str) -> tuple[int, int]:
    """(chunks, chunks with a vector for this model)"""
    total = (await db.execute(text("SELECT count(*) FROM chunk WHERE document_version_id = :v"), {"v": version_id})).scalar_one()
    done = (await db.execute(
        text("SELECT count(*) FROM chunk_embedding e JOIN chunk c ON c.id = e.chunk_id WHERE c.document_version_id = :v AND e.model = :m"),
        {"v": version_id, "m": model})).scalar_one()
    return int(total), int(done)
