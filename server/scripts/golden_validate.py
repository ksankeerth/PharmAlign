"""Checks the golden set against the source files, so no gold answer can be invented. Run it from the repo root after any edit:

    server/.venv/bin/python -I server/scripts/golden_validate.py

It needs PyMuPDF and the files in knowledge/documents/, which are not in git. The documents are read as data only. An answerable item
needs its quote and key phrases on the page it names. A no-answer item needs its absent_terms missing from every document, matched as a
word prefix, so a plural or a longer form also counts as present.
"""
import collections
import csv
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "knowledge" / "golden" / "retrieval-golden.jsonl"
MARKS = re.compile("[ؐ-ًؚ-ٰٟـ]")
FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ک": "ك", "ی": "ي"})
TYPES = {"fact", "numeric", "definition", "procedure", "clause", "paraphrase", "cross_language", "no_answer", "superseded"}
FIELDS = {"id", "question", "language", "type", "answerable", "expected_documents", "key_phrases", "evidence", "source_language", "forbidden_documents", "absent_terms", "notes"}


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(re.findall(r"[^\W_]+", MARKS.sub("", text).translate(FOLD).casefold()))


def pages_of(path: Path) -> list[str]:
    if path.suffix == ".pdf":
        with pymupdf.open(path) as document:
            return [page.get_text() for page in document]
    text = path.read_bytes().decode("utf8", "ignore")
    if path.suffix == ".html":
        text = re.sub(r"(?is)<(script|style|nav|footer|header)\b.*?</\1>", " ", text)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return [text]


def load_corpus() -> dict[str, dict[int, str]]:
    corpus: dict[str, dict[int, str]] = {}
    for path in sorted((ROOT / "knowledge" / "documents").glob("*/*")):
        if path.suffix in (".pdf", ".html", ".txt", ".csv"):
            pages = corpus.setdefault(path.name.split("__")[0], {})
            for number, text in enumerate(pages_of(path), 1):
                pages[number] = f" {norm(text)} " if path.suffix == ".pdf" or number not in pages else pages[number]
    return corpus


def problems(item: dict, corpus: dict[str, dict[int, str]], known: set[str]) -> list[str]:
    found: list[str] = []
    if set(item) != FIELDS:
        found.append(f"fields differ: {sorted(FIELDS ^ set(item))}")
        return found
    if item["type"] not in TYPES:
        found.append("unknown type")
    if (item["language"] == "ar") != bool(re.search("[ء-ي]", item["question"])):
        found.append("the question's script does not match its language")
    for document in item["expected_documents"] + item["forbidden_documents"]:
        if document not in known or document not in corpus:
            found.append(f"unknown or missing document {document}")
    if item["answerable"]:
        evidence = item["evidence"] or {}
        page = corpus.get(evidence.get("document_id"), {}).get(evidence.get("page"))
        if evidence.get("document_id") not in item["expected_documents"]:
            found.append("the evidence document is not expected")
        if not 1 <= len(item["key_phrases"]) <= 3:
            found.append("needs 1 to 3 key phrases")
        if page is None:
            found.append("the evidence page does not exist")
        else:
            for phrase in [evidence.get("quote", ""), *item["key_phrases"]]:
                if f" {norm(phrase)} " not in page:
                    found.append(f"not on the page: {phrase[:60]}")
    else:
        if item["type"] != "no_answer" or item["expected_documents"] or item["key_phrases"] or item["evidence"] or not item["absent_terms"]:
            found.append("a no-answer item has absent_terms and nothing expected")
        for term in item["absent_terms"]:
            hits = [d for d, pages in corpus.items() if any(f" {norm(term)}" in text for text in pages.values())]
            if hits:
                found.append(f'absent term is present: "{term}" in {hits[:4]}')
    return found


def main() -> int:
    items = [json.loads(line) for line in GOLDEN.read_text(encoding="utf8").splitlines() if line.strip()]
    with (ROOT / "knowledge" / "manifests" / "Inputs.csv").open(encoding="utf8") as f:
        known = {row["id"] for row in csv.DictReader(f)}
    corpus = load_corpus()
    errors = [f"{i['id']}: {p}" for i in items for p in problems(i, corpus, known)]
    for name, count in collections.Counter(i["id"] for i in items).items():
        if count > 1:
            errors.append(f"{name}: duplicate id")
    print(f"{len(items)} items, {len(errors)} errors")
    print(*errors[:60], sep="\n") if errors else None
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
