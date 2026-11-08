"""Multi-turn understanding, live, scored against eval/conversation.jsonl. Calls the real classifier, so run it on purpose.

    .venv/bin/python -I eval/run_conversation.py                  # all cases
    .venv/bin/python -I eval/run_conversation.py --only CV-01,CV-09

Each case gives the earlier turns (user, reply, kind, suggestions) and one new message. understand() reads it as the
pipeline does (gate, suggestion pick, classifier, English guard, clarify follow-up, route) and the result is scored
against the case's `expect`. Nothing is searched or answered. Each run is written to eval/runs/conversation-<time>.json.

expect keys: kinds (labels.kind in the list), follow_up (when not null), parts (each regex matches the joined parts),
not_parts (no regex matches), route ("answer" or "fixed:<reply>"), style, sources_named (each name among the labels'),
history_wanted.
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
CASES = HERE / "conversation.jsonl"
RUNS = HERE / "runs"

# Vertex AI list prices per 1M tokens (input, output). Thought tokens are billed as output.
PRICES = {
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}


def load_cases(path: Path = CASES) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


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


def route_name(route) -> str:
    return "answer" if route.kind == "answer" else f"fixed:{route.reply}"


def misses_of(expect: dict, labels, route) -> list[str]:
    """What the case expected and the result did not give. Empty when the case passes."""
    missed: list[str] = []
    if "kinds" in expect and labels.kind not in expect["kinds"]:
        missed.append(f"kind {labels.kind} not in {expect['kinds']}")
    if expect.get("follow_up") is not None and labels.follow_up != expect["follow_up"]:
        missed.append(f"follow_up {labels.follow_up}, want {expect['follow_up']}")
    joined = " ".join(labels.parts)
    unmatched = [r for r in expect.get("parts") or [] if not re.search(r, joined, re.IGNORECASE)]
    if unmatched:
        missed.append(f"parts missing {unmatched}")
    found = [r for r in expect.get("not_parts") or [] if re.search(r, joined, re.IGNORECASE)]
    if found:
        missed.append(f"parts hold {found}")
    if "route" in expect and route_name(route) != expect["route"]:
        missed.append(f"route {route_name(route)}, want {expect['route']}")
    if "style" in expect and labels.style != expect["style"]:
        missed.append(f"style {labels.style}, want {expect['style']}")
    named = {n.casefold() for n in labels.sources_named}
    absent = [n for n in expect.get("sources_named") or [] if n.casefold() not in named]
    if absent:
        missed.append(f"sources_named missing {absent}, got {list(labels.sources_named)}")
    if "history_wanted" in expect and labels.history_wanted != expect["history_wanted"]:
        missed.append(f"history_wanted {labels.history_wanted}, want {expect['history_wanted']}")
    return missed


async def run(cases: list[dict], chain) -> dict:
    """Runs the cases one after another through understand() with `chain` as the classifier."""
    from app.chat.labels import HistoryEntry
    from app.chat.understand import understand

    records: list[dict] = []
    for case in cases:
        history = [HistoryEntry.from_dict(h) for h in case.get("history") or []]
        started = time.monotonic()
        result = await understand(case["message"], history, chain)
        ms = round((time.monotonic() - started) * 1000)
        missed = misses_of(case["expect"], result.labels, result.route)
        records.append({
            "id": case["id"], "category": case.get("category", ""), "message": case["message"], "pass": not missed,
            "missed": missed, "how": result.how, "parts": list(result.labels.parts), "kind": result.labels.kind,
            "follow_up": result.labels.follow_up, "route": route_name(result.route), "style": result.labels.style,
            "sources_named": list(result.labels.sources_named), "history_wanted": result.labels.history_wanted,
            "search_all": result.route.search_all, "fail_open": result.labels.fail_open,
            "calls": [c.as_log() for c in result.calls], "ms": ms,
        })
    return summarise(records)


def summarise(records: list[dict]) -> dict:
    categories: dict[str, list[int]] = {}
    for r in records:
        passed, total = categories.setdefault(r["category"], [0, 0])
        categories[r["category"]] = [passed + r["pass"], total + 1]
    times = [r["ms"] for r in records if r["calls"]]  # the cases the classifier answered
    return {
        "cases": records,
        "summary": {
            "total": len(records), "passed": sum(r["pass"] for r in records),
            "categories": {k: {"passed": v[0], "total": v[1]} for k, v in categories.items()},
            "cost_usd": round(sum(cost_of(r["calls"]) for r in records), 5),
            "p50_ms": statistics.median(times) if times else 0, "p95_ms": percentile(times, 0.95),
        },
    }


def report(result: dict) -> None:
    for r in result["cases"]:
        line = f"{'PASS' if r['pass'] else 'FAIL'} {r['id']} [{r['how']}]"
        if not r["pass"]:
            line += f" {'; '.join(r['missed'])}"
        print(f"{line} parts={r['parts']}")
    s = result["summary"]
    print(f"\n{s['passed']}/{s['total']} passed")
    for name, c in s["categories"].items():
        print(f"  {name}: {c['passed']}/{c['total']}")
    print(f"cost ${s['cost_usd']:.4f}  p50 {s['p50_ms']:.0f} ms  p95 {s['p95_ms']:.0f} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run understand() (live classifier) against eval/conversation.jsonl.")
    parser.add_argument("--only", help="comma list of case ids, e.g. CV-01,CV-09")
    args = parser.parse_args()
    only = {x.strip() for x in args.only.split(",") if x.strip()} if args.only else None
    cases = [c for c in load_cases() if not only or c["id"] in only]

    from dotenv import load_dotenv

    load_dotenv(AGENT / ".env")
    from app.chat.models import chain_from_env

    result = asyncio.run(run(cases, chain_from_env("classifier")))
    report(result)
    RUNS.mkdir(exist_ok=True)
    path = RUNS / f"conversation-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print("written", path)
    return 0 if result["summary"]["passed"] == result["summary"]["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
