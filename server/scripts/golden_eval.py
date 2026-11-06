"""Scores retrieval against the golden set through POST /api/v1/kb-search, the search the Knowledge search page and the agents use.

    python3 server/scripts/golden_eval.py                          the active snapshot on http://127.0.0.1:8000
    python3 server/scripts/golden_eval.py --snapshot snap-ae-14    a pinned snapshot
    python3 server/scripts/golden_eval.py --out run.json --compare old.json

Standard library only. It only searches, so it changes nothing on the server. An item's passage counts as found when one hit from an
expected document holds every key phrase, so the set doesn't depend on how documents are chunked. The golden file: knowledge/golden/.
"""
import argparse
import collections
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "knowledge" / "golden" / "retrieval-golden.jsonl"
MARKS = re.compile("[ؐ-ًؚ-ٰٟـ]")
FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ئ": "ي", "ؤ": "و", "ة": "ه", "ک": "ك", "ی": "ي"})
PLACEHOLDER = ("[Placeholder]", "[Demo paraphrase")


def norm(text: str) -> str:
    """One spelling for text variants: Arabic letters and marks, case, punctuation and spacing."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    return " ".join(re.findall(r"[^\W_]+", MARKS.sub("", text).translate(FOLD).casefold()))


def has_phrase(phrase: str, text: str) -> bool:
    return f" {norm(phrase)} " in f" {norm(text)} "


def search(server: str, person: str, question: str, snapshot: str | None, top_k: int) -> dict:
    body = {"query": question, "top_k": top_k, **({"snapshot_id": snapshot} if snapshot else {})}
    request = urllib.request.Request(f"{server}/api/v1/kb-search", json.dumps(body).encode(), {"Authorization": f"Bearer {person}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def score(item: dict, found: dict, top_k: int) -> dict:
    hits = found["hits"]
    docs = [h["document_id"] for h in hits]
    row = {
        "id": item["id"], "question": item["question"], "language": item["language"], "type": item["type"], "no_source": found["no_source_found"],
        "returned": docs[:5], "top_score": hits[0]["score"] if hits else None, "placeholder_in_top5": sum(h["text"].startswith(PLACEHOLDER) for h in hits[:5]),
        "forbidden_hit": sorted(set(docs) & set(item["forbidden_documents"])),
    }
    if not item["answerable"]:
        return {**row, "passed": found["no_source_found"]}
    expected = set(item["expected_documents"])
    document_rank = next((i for i, d in enumerate(docs, 1) if d in expected), None)
    passage_rank = next((i for i, h in enumerate(hits, 1) if h["document_id"] in expected and all(has_phrase(k, h["text"]) for k in item["key_phrases"])), None)
    return {**row, "document_rank": document_rank, "passage_rank": passage_rank, "passed": bool(passage_rank and passage_rank <= 5) and not row["forbidden_hit"]}


def summary(rows: list[dict]) -> dict:
    asked = [r for r in rows if "document_rank" in r]
    refused = [r for r in rows if "document_rank" not in r]

    def share(pick) -> float | None:
        return round(sum(1 for r in asked if pick(r)) / len(asked), 3) if asked else None

    return {
        "items": len(rows),
        "document_hit_1": share(lambda r: r["document_rank"] == 1),
        "document_hit_5": share(lambda r: r["document_rank"] and r["document_rank"] <= 5),
        "passage_hit_5": share(lambda r: r["passage_rank"] and r["passage_rank"] <= 5),
        "passage_hit_10": share(lambda r: r["passage_rank"]),
        "mrr": round(sum(1 / r["passage_rank"] for r in asked if r["passage_rank"]) / len(asked), 3) if asked else None,
        "no_answer_refused": round(sum(1 for r in refused if r["passed"]) / len(refused), 3) if refused else None,
    }


def groups(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"ALL": rows}
    for r in rows:
        out.setdefault(f"language {r['language']}", []).append(r)
        out.setdefault(f"type {r['type']}", []).append(r)
    return out


def print_table(table: dict[str, dict], old: dict[str, dict]) -> None:
    keys = ("items", "document_hit_1", "document_hit_5", "passage_hit_5", "passage_hit_10", "mrr", "no_answer_refused")
    print(f"{'':28}{'n':>4}{'doc@1':>8}{'doc@5':>8}{'pass@5':>8}{'pass@10':>9}{'mrr':>7}{'refused':>9}")
    for name, s in table.items():
        cells = "".join(f"{'-' if s[k] is None else s[k]:>{w}}" for k, w in zip(keys, (4, 8, 8, 8, 9, 7, 9)))
        print(f"{name:28}{cells}")
        if name in old and old[name].get("passage_hit_5") is not None and s["passage_hit_5"] is not None:
            print(f"{'':28}    change in pass@5: {s['passage_hit_5'] - old[name]['passage_hit_5']:+.3f}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--person", default="p-jordan", help="the demo person to search as")
    parser.add_argument("--snapshot", help="snapshot id (default: the active one)")
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--language", choices=("en", "ar"))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--out", type=Path, help="write every result as JSON")
    parser.add_argument("--compare", type=Path, help="an earlier --out file to compare with")
    parser.add_argument("--failures", type=int, default=15, help="how many failed items to print")
    args = parser.parse_args()

    items = [json.loads(line) for line in args.golden.read_text(encoding="utf8").splitlines() if line.strip()]
    items = [i for i in items if not args.language or i["language"] == args.language]
    rows, started = [], time.time()
    for item in items:
        try:
            rows.append(score(item, search(args.server, args.person, item["question"], args.snapshot, args.top_k), args.top_k))
        except urllib.error.HTTPError as e:
            print(f"{item['id']}: the server answered {e.code}: {e.read()[:200].decode('utf8', 'ignore')}", file=sys.stderr)
            return 2
    table = {name: summary(part) for name, part in groups(rows).items()}
    old = json.loads(args.compare.read_text(encoding="utf8"))["summary"] if args.compare else {}
    print_table(table, old)
    placeholders = sum(r["placeholder_in_top5"] for r in rows)
    shown = sum(min(5, len(r["returned"])) for r in rows)
    print(f"\nPlaceholder text in the top 5: {placeholders} of {shown} hits. Repealed law returned on: {sum(bool(r['forbidden_hit']) for r in rows)} item(s).")
    failed = [r for r in rows if not r["passed"]]
    print(f"Passed {len(rows) - len(failed)} of {len(rows)} in {time.time() - started:.0f}s.")
    for r in failed[: args.failures]:
        why = "should have found nothing" if "document_rank" not in r else f"doc rank {r['document_rank']}, passage rank {r['passage_rank']}"
        print(f"  {r['id']:8} {why:34} got {r['returned'][:3]}  {r['question'][:64]}")
    if args.out:
        args.out.write_text(json.dumps({"summary": table, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
