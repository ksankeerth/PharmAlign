"""Structure-aware chunking: articles, sections and tables, with a hard cap on a chunk's length in characters.

Chunks follow the document's structure and never cross a page unless stitching joined the pieces. A chunk
longer than the embedder can read is split here, so no text is silently cut off at embedding time.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .layout import has_header, legal_level, script_stats

SOFT_BREAK_WORDS = 60  # a non-article heading starts a new chunk only if the current one is this long


@dataclass(frozen=True)
class ChunkRules:
    """From the ingestion profile, plus the embedder's limit."""

    split_by: str = "heading"  # article, heading or fixed
    words_per_chunk: int = 256
    overlap_words: int = 30  # used when one paragraph is longer than words_per_chunk
    min_chunk_words: int = 8
    max_chars: int = 4000  # the embedder's limit
    tables: str = "keep"  # keep, flatten or ignore


def words(t: str) -> int:
    return len(t.split())


def anchor_for(version_id: str, position: int) -> str:
    """A chunk's address for good: `doc:vN:cNNNN` for a version id like `doc-vN`. It never changes, so a citation keeps working."""
    head, sep, tail = version_id.rpartition("-v")
    base = f"{head}:v{tail}" if sep and tail.isdigit() else version_id
    return f"{base}:c{position:04d}"


def text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_table(rows, caption, header, body_rows):
    keep = [i for i in range(max((len(r) for r in rows), default=0)) if any(i < len(r) and r[i] for r in rows)]  # drop empty columns
    lines = [caption] if caption else []
    for r in ([header] if header else []) + body_rows:
        lines.append("| " + " | ".join(r[i] if i < len(r) else "" for i in keep) + " |")
    return "\n".join(lines)


def chunk_blocks(blocks: list[dict], rules: ChunkRules = ChunkRules()) -> list[dict]:
    """Each chunk: text, page, page_end, section_path, section, article, chunk_type, block_types, stitched."""
    chunks: list[dict] = []
    path: list[tuple[int, str, str]] = []  # (level, heading text, article number)
    cur: list[dict] = []
    start_path: list[tuple[int, str, str]] = []

    def path_info(p):
        return (" > ".join(t[:80] for _, t, _ in p), p[-1][1][:120] if p else "",
                next((a for _, _, a in reversed(p) if a), ""))

    def emit(items, p, text=None, ctype=None):
        body = text if text is not None else "\n".join(i["text"] for i in items)
        pages = [i["page"] for i in items if i.get("page") is not None]
        pages_end = [i.get("page_end") or i["page"] for i in items if i.get("page") is not None]
        sp, sec, art = path_info(p)
        kinds = list(dict.fromkeys(i["type"] for i in items))
        chunks.append({
            "text": body, "page": min(pages) if pages else None, "page_end": max(pages_end) if pages else None,
            "section_path": sp, "section": sec, "article": art,
            "chunk_type": ctype or ("list" if kinds == ["list_item"] else "text"),
            "block_types": ",".join(kinds), "stitched": any(i.get("stitched") for i in items),
        })

    def flush():
        nonlocal cur
        if cur and words("\n".join(i["text"] for i in cur)) >= rules.min_chunk_words:
            emit(cur, start_path)
        cur = []

    def add(item):
        nonlocal start_path
        if not cur:
            start_path = list(path)
        cur.append(item)

    for b in blocks:
        t = b["type"]
        if t == "heading":
            lvl = b["level"] or 4
            art = legal_level(b["text"])[1]
            while path and path[-1][0] >= lvl:
                path.pop()
            path.append((lvl, b["text"], art))
            hard = (lvl <= 3 and legal_level(b["text"])[0]) if rules.split_by == "article" else lvl <= 3 if rules.split_by == "heading" else False
            if hard or sum(words(i["text"]) for i in cur) >= SOFT_BREAK_WORDS and rules.split_by != "fixed":
                flush()
            add(b)
        elif t in ("paragraph", "list_item", "caption"):
            w = words(b["text"])
            if cur and sum(words(i["text"]) for i in cur) + w > rules.words_per_chunk:
                flush()
            if w > rules.words_per_chunk:  # one very long paragraph: windows with overlap
                flush()
                ws, step = b["text"].split(), max(1, rules.words_per_chunk - rules.overlap_words)
                for s in range(0, len(ws), step):
                    piece = {**b, "text": " ".join(ws[s:s + rules.words_per_chunk])}
                    emit([piece], list(path))
                    if s + rules.words_per_chunk >= len(ws):
                        break
            else:
                add(b)
        elif t == "table":
            flush()
            rows = b["rows"]
            header = rows[0] if has_header(rows) else None
            body = rows[1:] if header else rows
            group, size = [], 0
            for r in body + [None]:
                rw = words(" ".join(r)) if r else 0
                if r is None or (group and size + rw > rules.words_per_chunk):
                    if group:
                        text = render_table(rows, b["caption"], header, group)
                        emit([b], list(path), text=text, ctype="table")
                    group, size = [], 0
                if r is not None:
                    group.append(r)
                    size += rw
    flush()
    return chunks


def apply_table_mode(blocks: list[dict], mode: str) -> list[dict]:
    """keep: as parsed. flatten: a table becomes lines of text. ignore: tables are dropped."""
    if mode == "keep":
        return blocks
    out = []
    for b in blocks:
        if b["type"] != "table":
            out.append(b)
        elif mode == "flatten":
            for r in b["rows"]:
                text = "; ".join(c for c in r if c)
                if text:
                    out.append({**b, "type": "paragraph", "text": text, "rows": None})
    return out


_SENTENCE = re.compile(r"(?<=[.!?؟۔;:])\s+|\n+")


def split_long(chunks: list[dict], max_chars: int) -> tuple[list[dict], int]:
    """Splits any chunk over max_chars at line, sentence or word boundaries, never in the middle of a word. Returns the chunks and how many were split."""
    out: list[dict] = []
    split = 0
    for c in chunks:
        if len(c["text"]) <= max_chars:
            out.append(c)
            continue
        split += 1
        piece = ""
        parts: list[str] = []
        for unit in _SENTENCE.split(c["text"]):
            while len(unit) > max_chars:  # a run with no break: cut at a space near the limit
                cut = unit.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                head, unit = unit[:cut], unit[cut:].lstrip()
                if piece:
                    parts.append(piece)
                    piece = ""
                parts.append(head)
            if piece and len(piece) + 1 + len(unit) > max_chars:
                parts.append(piece)
                piece = ""
            piece = f"{piece} {unit}".strip() if piece else unit
        if piece:
            parts.append(piece)
        out.extend({**c, "text": p, "stitched": c["stitched"]} for p in parts if p.strip())
    return out, split


def chunk_language(text: str, fallback: str) -> str:
    ar, lat = script_stats(text)
    if ar + lat < 5:
        return fallback
    ratio = ar / (ar + lat)
    return "ar" if ratio >= 0.85 else "en" if ratio <= 0.15 else fallback
