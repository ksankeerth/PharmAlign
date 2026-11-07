"""The classifier alone, live, scored against eval/dialogues.jsonl. Calls the real models, so run it on purpose.

    .venv/bin/python -I eval/run_classifier.py                   # all dialogues, one turn at a time
    .venv/bin/python -I eval/run_classifier.py --only KA-01,KA-12

Turns the language gate stops (Arabic) are skipped: the classifier never sees them. Earlier turns of a dialogue are
fed as history with stubbed replies (the golden quote, "no source", or the fixed reply text). Each run is written to
eval/runs/classifier-<time>.json.

Pass mark: no real question labelled as a non-question; kind right on >= 44 of 48; follow_up >= 90%; rewrite >= 90%;
KA-12 has 2 parts; KA-13 has >= 4 parts; p95 <= 2000 ms.
"""
import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))
GOLDEN = AGENT.parent.parent / "knowledge" / "golden" / "retrieval-golden.jsonl"
RUNS = HERE / "runs"

# Vertex AI list prices per 1M tokens (input, output). Thought tokens are billed as output.
PRICES = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.8-flash": (0.75, 3.75),
}
DECISION_OR_QUESTION = {"KA-14"}  # the pass rule counts either label as right
NO_SOURCE_REPLY = "No source in the library answers this."


def golden_quotes() -> dict[str, str]:
    quotes = {}
    for line in GOLDEN.read_text().splitlines():
        if line.strip():
            item = json.loads(line)
            if item.get("evidence"):
                quotes[item["id"]] = item["evidence"]["quote"]
    return quotes


def stub_reply(turn: dict, quotes: dict[str, str]) -> str:
    """What the assistant is taken to have said, as history for the next turn."""
    if turn["route"] == "fixed":
        try:
            from app.replies import texts_for

            return texts_for(turn["reply"])[0]
        except Exception:
            return "(fixed reply)"
    if turn["route"] == "no_source":
        return NO_SOURCE_REPLY
    src = turn.get("src", "")
    ids = [s.strip() for s in src.split("+")]
    if ids and all(i in quotes for i in ids):
        return " ".join(quotes[i] for i in ids)
    return src


def expected_english(turn: dict) -> bool:
    return turn.get("reply") != "language"


def cost_of(calls: list[dict]) -> float:
    total = 0.0
    for call in calls:
        price_in, price_out = PRICES.get(call["model"], (0.0, 0.0))
        total += (call["in"] * price_in + (call["out"] + call["thought"]) * price_out) / 1_000_000
    return total


def percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1) + 0.4999))]


async def run(only: set[str] | None) -> dict:
    from dotenv import load_dotenv

    load_dotenv(AGENT / ".env")
    from app.chat import gate
    from app.chat.classify import classify
    from app.chat.models import chain_from_env

    chain = chain_from_env("classifier")
    quotes = golden_quotes()
    dialogues = [json.loads(line) for line in (HERE / "dialogues.jsonl").read_text().splitlines() if line.strip()]
    records: list[dict] = []
    for dialogue in dialogues:
        if only and dialogue["id"] not in only:
            continue
        history: list[tuple[str, str]] = []
        for number, turn in enumerate(dialogue["turns"], start=1):
            reply = stub_reply(turn, quotes)
            if gate.needs_english(turn["user"]):
                history.append((turn["user"], reply))
                continue
            started = time.monotonic()
            labels, calls = await classify(turn["user"], history, chain)
            ms = round((time.monotonic() - started) * 1000)
            history.append((turn["user"], reply))
            records.append({
                "dialogue": dialogue["id"], "turn": number, "user": turn["user"], "want": {k: turn.get(k) for k in (
                    "kind", "follow_up", "route", "parts", "history_wanted", "rewrite")},
                "labels": {"kind": labels.kind, "follow_up": labels.follow_up, "parts": list(labels.parts),
                           "history_wanted": labels.history_wanted, "english": labels.english,
                           "fail_open": labels.fail_open, "model": labels.model},
                "calls": [c.as_log() for c in calls], "ms": ms, "_turn": turn,
            })
    return score(records, full=not only)


def score(records: list[dict], full: bool = True) -> dict:
    misses: list[dict] = []
    stats = {"kind": 0, "follow_up": 0, "english": 0, "rewrite": 0, "rewrite_total": 0, "history": 0, "history_total": 0,
             "real_as_non_question": 0, "fail_open": 0, "fallback": 0}
    ka12 = ka13 = None
    cost = 0.0
    for record in records:
        turn = record.pop("_turn")
        label, did = record["labels"], record["dialogue"]

        def miss(field: str, want, got):
            misses.append({"dialogue": did, "turn": record["turn"], "field": field, "want": want, "got": got,
                           "parts": label["parts"]})

        allowed = {turn["kind"]} | ({"decision_request", "question"} if did in DECISION_OR_QUESTION else set())
        if label["kind"] in allowed:
            stats["kind"] += 1
        else:
            miss("kind", turn["kind"], label["kind"])
        if turn["route"] in ("answer", "no_source") and label["kind"] not in allowed | {"question"}:
            stats["real_as_non_question"] += 1
        if label["follow_up"] == turn["follow_up"]:
            stats["follow_up"] += 1
        else:
            miss("follow_up", turn["follow_up"], label["follow_up"])
        if label["english"] == expected_english(turn):
            stats["english"] += 1
        else:
            miss("english", expected_english(turn), label["english"])
        if "history_wanted" in turn:
            stats["history_total"] += 1
            if label["history_wanted"] == turn["history_wanted"]:
                stats["history"] += 1
            else:
                miss("history_wanted", turn["history_wanted"], label["history_wanted"])
        if turn.get("rewrite"):
            stats["rewrite_total"] += 1
            joined = " ".join(label["parts"])
            failed = [r for r in turn["rewrite"] if not re.search(r, joined, re.IGNORECASE)]
            if failed:
                miss("rewrite", turn["rewrite"], f"unmatched {failed}")
            else:
                stats["rewrite"] += 1
        if label["fail_open"]:
            stats["fail_open"] += 1
        if any(c["fallback"] or c["error"] for c in record["calls"]):
            stats["fallback"] += 1
        if did == "KA-12":
            ka12 = len(label["parts"])
        if did == "KA-13":
            ka13 = len(label["parts"])
        cost += cost_of(record["calls"])

    total = len(records)
    times = [r["ms"] for r in records]
    p50, p95 = (statistics.median(times) if times else 0), percentile(times, 0.95)
    follow_rate = stats["follow_up"] / total if total else 0.0
    rewrite_rate = stats["rewrite"] / stats["rewrite_total"] if stats["rewrite_total"] else 1.0
    checks = [
        ("0 real questions labelled non-question", stats["real_as_non_question"] == 0, stats["real_as_non_question"]),
        (f"kind right on >= 44 of 48 (turns run: {total})", stats["kind"] >= (44 if full else total), f"{stats['kind']}/{total}"),
        ("follow_up >= 90%", follow_rate >= 0.9, f"{follow_rate:.0%}"),
        ("rewrite >= 90%", rewrite_rate >= 0.9, f"{stats['rewrite']}/{stats['rewrite_total']}"),
        ("KA-12 has 2 parts", ka12 == 2 or (ka12 is None and not full), "not run" if ka12 is None else ka12),
        ("KA-13 has >= 4 parts", (ka13 or 0) >= 4 or (ka13 is None and not full), "not run" if ka13 is None else ka13),
        ("p95 <= 2000 ms", p95 <= 2000, f"{p95:.0f} ms"),
    ]
    return {
        "turns": records,
        "misses": misses,
        "summary": {
            "turns_run": total, **stats,
            "english_accuracy": stats["english"] / total if total else 0.0,
            "p50_ms": p50, "p95_ms": p95, "cost_usd": round(cost, 5),
            "checks": [{"name": n, "pass": ok, "value": v} for n, ok, v in checks],
            "pass": all(ok for _, ok, _ in checks),
        },
    }


def report(result: dict) -> None:
    for m in result["misses"]:
        print(f"MISS {m['dialogue']} turn {m['turn']} {m['field']}: want {m['want']!r} got {m['got']!r} parts={m['parts']}")
    s = result["summary"]
    print()
    for c in s["checks"]:
        print(f"{'PASS' if c['pass'] else 'FAIL'}  {c['name']}: {c['value']}")
    print(f"\nenglish {s['english_accuracy']:.0%}  history_wanted {s['history']}/{s['history_total']}  fail_open {s['fail_open']}  "
          f"fallbacks {s['fallback']}  p50 {s['p50_ms']:.0f} ms  p95 {s['p95_ms']:.0f} ms  cost ${s['cost_usd']:.4f}")
    print("OVERALL", "PASS" if s["pass"] else "FAIL")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the classifier alone (live) against eval/dialogues.jsonl.")
    parser.add_argument("--only", help="comma list of dialogue ids, e.g. KA-01,KA-12")
    args = parser.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None
    result = asyncio.run(run(only))
    report(result)
    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"classifier-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print("written", path)
    return 0 if result["summary"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
