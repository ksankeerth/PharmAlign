"""PharmAlign ingestion (basic).

Reads InputDocs/<Category>/<file> -> parses (PDF per page, HTML, text) -> chunks per page
(256 words, 30 overlap, 2000 char cap) -> embeds with Ollama nomic-embed-text -> stores in
ChromaDB, using the same collection names, cosine space and metadata keys as ITU's reference
code, plus PharmAlign fields read from Inputs.csv (title, issuer, legal_status, page, ...).

Differences from ITU's ingest.py: chunks never cross a page (so every chunk has a page number),
chunk ids are deterministic and written with upsert (re-running does not duplicate), HTML is
stripped without keeping <script>/<style>, and metadata comes from the manifest instead of
being guessed from the file name.

Usage (from the repo root, after `pip install -r simulation/requirements-ingest.txt`,
`ollama serve` and `ollama pull nomic-embed-text`):

    python simulation/server/knowledge/pharmalign_ingest.py --dry-run      # parse + chunk only
    python simulation/server/knowledge/pharmalign_ingest.py --reset        # full rebuild
    python simulation/server/knowledge/pharmalign_ingest.py --only A-01 B-06
    python simulation/server/knowledge/pharmalign_ingest.py --resume       # skip finished docs
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[1]  # simulation/

# Same names and settings as ITU's server/config.py
KB_MAIN = "kb_main"
KB_USECASE = "kb_usecase"
EMBED_MODEL = "nomic-embed-text"
OLLAMA_URL = "http://localhost:11434"
CHUNK_WORDS = 256
CHUNK_OVERLAP = 30
MAX_CHARS = 2000
MIN_CHUNK_WORDS = 8
EMBED_BATCH = 16

# manifest column -> chunk metadata key (PharmAlign fields next to ITU's)
MANIFEST_FIELDS = {
    "id": "doc_id", "title": "title", "issuer": "issuer", "jurisdiction": "jurisdiction",
    "language": "language", "doc_type": "doc_type", "domain": "domain", "priority": "priority",
    "authority_tier": "authority_tier", "legal_status": "legal_status", "version": "version",
    "date": "date", "source_url": "source_url", "pair_id": "pair_id",
}
SUPPORTED = {".pdf", ".txt", ".md", ".csv", ".html"}


# --------------------------------------------------------------------------- parsing

class _Text(HTMLParser):
    """HTML -> text; keeps paragraph breaks, drops <script>/<style>."""
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table"}
    SKIP = {"script", "style", "noscript", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def parse_file(path: Path) -> list[tuple[int | None, str]]:
    """Return [(page_number or None, text)]."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pymupdf  # PyMuPDF
        with pymupdf.open(str(path)) as doc:
            return [(i + 1, page.get_text()) for i, page in enumerate(doc)]
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext == ".html":
        p = _Text()
        p.feed(raw)
        raw = "".join(p.parts)
    return [(None, raw)]


# --------------------------------------------------------------------------- chunking

def _cap(chunk: str) -> list[str]:
    """Split a chunk longer than MAX_CHARS on whitespace."""
    if len(chunk) <= MAX_CHARS:
        return [chunk]
    out, cur = [], ""
    for w in chunk.split():
        if cur and len(cur) + 1 + len(w) > MAX_CHARS:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return out + ([cur] if cur else [])


def chunk_page(text: str) -> list[str]:
    """Chunk one page: words in order, CHUNK_WORDS per chunk, CHUNK_OVERLAP carried over."""
    words = text.split()
    chunks, i = [], 0
    step = CHUNK_WORDS - CHUNK_OVERLAP
    while i < len(words):
        piece = words[i:i + CHUNK_WORDS]
        if len(piece) >= MIN_CHUNK_WORDS or not chunks:
            chunks.extend(_cap(" ".join(piece)))
        else:  # short tail: fold into the previous chunk instead of dropping it
            chunks[-1] = _cap(chunks[-1] + " " + " ".join(piece))[0]
        if i + CHUNK_WORDS >= len(words):
            break
        i += step
    return chunks


def chunk_document(pages: list[tuple[int | None, str]]) -> list[tuple[int | None, str]]:
    out = []
    for page, text in pages:
        for c in chunk_page(text):
            if len(c.split()) >= MIN_CHUNK_WORDS:
                out.append((page, c))
    return out


# --------------------------------------------------------------------------- embedding

def embed(client, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = [t[:MAX_CHARS] for t in texts[i:i + EMBED_BATCH]]
        try:
            out.extend(client.embed(model=EMBED_MODEL, input=batch)["embeddings"])
        except Exception:  # fall back to one at a time, shorter text on a second failure
            for t in batch:
                try:
                    out.extend(client.embed(model=EMBED_MODEL, input=[t])["embeddings"])
                except Exception:
                    out.extend(client.embed(model=EMBED_MODEL, input=[t[:800]])["embeddings"])
    return out


# --------------------------------------------------------------------------- manifest

def load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {Path(r["file"]).as_posix(): r for r in csv.DictReader(f)}


def doc_metadata(rel: str, category: str, row: dict | None) -> dict[str, str]:
    meta = {
        "source_file": Path(rel).name,
        "source_type": (row or {}).get("itu_source_type") or "standards_document",
        "country": "United Arab Emirates" if category.startswith("UAE_") else "global",
        "document_category": category,
        "confidentiality": "public",
    }
    if row:
        for col, key in MANIFEST_FIELDS.items():
            if row.get(col):
                meta[key] = row[col]
    return meta


# --------------------------------------------------------------------------- main

def collect(input_dir: Path, manifest: dict[str, dict]):
    """Yield (path, rel, category, manifest_row) for every file to ingest, plus missing manifest rows."""
    seen = set()
    for cat in sorted(p for p in input_dir.iterdir() if p.is_dir()):
        for f in sorted(p for p in cat.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED):
            rel = f"{cat.name}/{f.name}"
            seen.add(rel)
            yield f, rel, cat.name, manifest.get(rel), None
    for rel in sorted(set(manifest) - seen):
        yield None, rel, rel.split("/")[0], manifest[rel], "file not found (documents are not stored in git)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=HERE / "InputDocs")
    ap.add_argument("--manifest", type=Path, default=HERE / "Inputs.csv")
    ap.add_argument("--chroma", type=Path, default=BASE / "data" / "chromadb")
    ap.add_argument("--dry-run", action="store_true", help="parse and chunk only; no Ollama, no ChromaDB")
    ap.add_argument("--reset", action="store_true", help="delete kb_main and kb_usecase first")
    ap.add_argument("--resume", action="store_true", help="skip documents already stored")
    ap.add_argument("--only", nargs="+", metavar="ID", help="ingest only these document ids")
    ap.add_argument("--limit", type=int, help="stop after N documents")
    args = ap.parse_args()

    manifest = load_manifest(args.manifest)
    now = datetime.now(timezone.utc).isoformat()
    client = chroma = None
    if not args.dry_run:
        import chromadb
        import ollama
        client = ollama.Client(host=OLLAMA_URL)
        try:
            client.embed(model=EMBED_MODEL, input=["ping"])
        except Exception as e:  # noqa: BLE001
            sys.exit(f"Cannot embed with Ollama ({e}).\nRun: ollama serve   and   ollama pull {EMBED_MODEL}")
        args.chroma.mkdir(parents=True, exist_ok=True)
        chroma = chromadb.PersistentClient(path=str(args.chroma))
        if args.reset:
            for name in (KB_MAIN, KB_USECASE):
                try:
                    chroma.delete_collection(name)
                except Exception:  # noqa: BLE001
                    pass
        cols = {n: chroma.get_or_create_collection(n, metadata={"hnsw:space": "cosine"})
                for n in (KB_MAIN, KB_USECASE)}

    totals = {"docs": 0, "chunks": 0}
    problems: list[str] = []
    done = 0
    for path, rel, category, row, missing in collect(args.dir, manifest):
        doc_id = (row or {}).get("id") or rel
        if args.only and doc_id not in args.only:
            continue
        if args.limit and done >= args.limit:
            break
        done += 1
        if missing:
            problems.append(f"{doc_id}: {missing}")
            continue
        col_name = KB_USECASE if category == "AI_Use_Cases" else KB_MAIN
        if chroma and args.resume and cols[col_name].get(where={"doc_id": doc_id}, limit=1)["ids"]:
            print(f"  {doc_id:10} already stored, skipped")
            continue
        try:
            chunks = chunk_document(parse_file(path))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{doc_id}: parse failed ({e})")
            continue
        if not chunks:
            problems.append(f"{doc_id}: no text extracted (scanned or empty), skipped")
            continue
        meta = {**doc_metadata(rel, category, row), "ingested_at": now}
        metas = []
        for i, (page, _) in enumerate(chunks):
            m = {**meta, "chunk_index": str(i), "total_chunks": str(len(chunks))}
            if page is not None:
                m["page"] = str(page)
            metas.append(m)
        if chroma:
            col = cols[col_name]
            col.delete(where={"doc_id": doc_id})  # replace any earlier version of this document
            vectors = embed(client, [c for _, c in chunks])
            ids = [f"{doc_id}:{i:05d}" for i in range(len(chunks))]
            for s in range(0, len(ids), 5000):
                col.upsert(ids=ids[s:s + 5000], documents=[c for _, c in chunks][s:s + 5000],
                           embeddings=vectors[s:s + 5000], metadatas=metas[s:s + 5000])
        totals["docs"] += 1
        totals["chunks"] += len(chunks)
        print(f"  {doc_id:10} {len(chunks):5} chunks  {rel}")

    print(f"\n{'Dry run: ' if args.dry_run else ''}{totals['docs']} documents, {totals['chunks']} chunks")
    if chroma:
        print({n: c.count() for n, c in cols.items()})
    if problems:
        print(f"\n{len(problems)} problems:")
        print("\n".join("  " + p for p in problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
