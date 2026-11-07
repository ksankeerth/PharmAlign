"""End to end through the server on 8310, scored in code (no LLM judge).

    .venv/bin/python eval/run_dialogues.py --label baseline            # all 30 dialogues, one full run
    .venv/bin/python eval/run_dialogues.py --label A --only KA-08,KA-14 # single dialogues while fixing

Dialogues run one after another, never in parallel, and never in a loop of runs. Every run is written to eval/runs/<label>-<time>.json
and counted in eval/runs/ledger.jsonl. A full run is refused once 15 have been made, to cap spending.

Scoring (PLAN section 5):
- answer turn: one message, >= 1 citation, unverified == 0, every docs group cited, every facts regex in the text, no forbid_docs cited,
  no forbid_text in the text.
- no_source turn: a no_source event and no message.
- fixed turn: the right event with the exact text from app/replies.py, and 0 searches (turn log when there is one, else usage).
  KA-24 also needs 0 model calls.
"""
import argparse
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
AGENT = HERE.parent
sys.path.insert(0, str(AGENT))
RUNS = HERE / "runs"
LEDGER = RUNS / "ledger.jsonl"
MAX_FULL_RUNS = 15
PEOPLE = ("p-sam", "p-jordan", "p-priya", "p-alex", "p-chris")

# Vertex AI list prices per 1M tokens (input, output). Thinking tokens are billed as output.
# Checked against Google's pricing page before phase 5 is reported; see NOTES.md.
PRICES = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
}


def load_dialogues(path: Path, only: set[str] | None) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [d for d in rows if not only or d["id"] in only]


def fixed_texts(reply: str) -> set[str]:
    """Every text app/replies.py may send for this fixed reply. Empty before phase 3 (the baseline has no fixed replies)."""
    try:
        from app import replies
    except ImportError:
        return set()
    return set(replies.texts_for(reply))


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line.removeprefix("data:").strip()) for line in text.splitlines() if line.startswith("data:")]


class TurnLog:
    """Reads the lines the agent appended to its turn log during one turn."""

    def __init__(self, path: Path | None):
        self.path = path
        self.offset = path.stat().st_size if path and path.exists() else 0

    def mark(self) -> None:
        self.offset = self.path.stat().st_size if self.path and self.path.exists() else 0

    def new_lines(self) -> list[dict]:
        if not self.path or not self.path.exists():
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            data = f.read()
        self.offset += len(data)
        return [json.loads(line) for line in data.decode().splitlines() if line.strip()]


def cost_of(models: list[dict]) -> float:
    total = 0.0
    for m in models:
        price_in, price_out = PRICES.get(m.get("model", ""), (0.0, 0.0))
        total += m.get("in", 0) * price_in / 1e6 + (m.get("out", 0) + m.get("thought", 0)) * price_out / 1e6
    return total


def cited_docs(message: dict) -> set[str]:
    docs = set()
    for c in message.get("citations", []):
        docs.add(c["anchor"].split(":")[0])
        if c.get("document_id"):
            docs.add(c["document_id"])
    return docs


def score(turn: dict, events: list[dict], log: dict | None) -> tuple[bool, list[str], str]:
    """(passed, reasons it failed, what came back: answer, no_source, fixed, not_available, error or other)."""
    kinds = [e["type"] for e in events]
    messages = [e for e in events if e["type"] == "message"]
    errors = [e for e in events if e["type"] == "error"]
    usage = next((e["usage"] for e in events if e["type"] == "usage"), None)
    searches = len(log["searches"]) if log else (usage or {}).get("tool_calls", 0)
    model_calls = len(log["models"]) if log else (usage or {}).get("model_calls", 0)
    all_fixed = set().union(*(fixed_texts(r) for r in ("greeting", "clarify", "split", "language", "distress", "decision", "off_topic",
                                                         "unsafe", "own_data")))
    if errors:
        got = "error"
    elif log and log.get("route") not in (None, "answer", "no_source"):
        got = "fixed"
    elif messages and messages[0]["text"] in all_fixed:
        got = "fixed"
    elif "not_available" in kinds:
        got = "not_available"
    elif messages:
        got = "answer"
    elif "no_source" in kinds:
        got = "no_source"
    else:
        got = "other"

    why: list[str] = []
    if errors:
        why.append(f"error event: {errors[0].get('code')}: {errors[0].get('message', '')[:120]}")
    route = turn["route"]
    if route == "answer":
        if len(messages) != 1:
            why.append(f"{len(messages)} messages, want 1 (got {got})")
        else:
            m = messages[0]
            if not m.get("citations"):
                why.append("no citation")
            if m.get("unverified", 0):
                why.append(f"{m['unverified']} unverified citations")
            docs = cited_docs(m)
            for group in turn.get("docs", []):
                if not docs & set(group):
                    why.append(f"none of {group} cited (cited {sorted(docs)})")
            for fact in turn.get("facts", []):
                if not re.search(fact, m["text"], re.IGNORECASE):
                    why.append(f"fact /{fact}/ missing")
            for bad in turn.get("forbid_docs", []):
                if bad in docs:
                    why.append(f"forbidden doc {bad} cited")
            for bad in turn.get("forbid_text", []):
                if re.search(bad, m["text"], re.IGNORECASE):
                    why.append(f"forbidden text /{bad}/ present")
    elif route == "no_source":
        if "no_source" not in kinds or messages:
            why.append(f"want no_source and no message (got {got})")
    else:  # fixed
        want_event, texts = turn["event"], fixed_texts(turn["reply"])
        if want_event == "message":
            text = messages[0]["text"] if len(messages) == 1 else None
            if text is None or text not in texts:
                why.append(f"want the fixed '{turn['reply']}' message (got {got})")
        else:
            na = [e for e in events if e["type"] == "not_available"]
            if not na or na[0].get("reason") != "out_of_scope" or na[0].get("detail") not in texts:
                why.append(f"want not_available out_of_scope with the '{turn['reply']}' text (got {got})")
        if searches:
            why.append(f"{searches} searches, want 0")
        if "model_calls" in turn and model_calls != turn["model_calls"]:
            why.append(f"{model_calls} model calls, want {turn['model_calls']}")
    return not why, why, got


def open_session(client: httpx.Client, person: str) -> str:
    r = client.post("/api/v1/assistant/sessions", json={"language": "en"}, headers={"Authorization": f"Bearer {person}"})
    if r.status_code != 201:
        raise RuntimeError(f"opening a session as {person}: {r.status_code} {r.text[:300]}")
    return r.json()["id"]


def pick_person(client: httpx.Client) -> str:
    """The first person who may open a knowledge session in ws-ae."""
    for person in PEOPLE:
        me = client.get("/api/v1/me", headers={"Authorization": f"Bearer {person}"})
        if me.status_code == 200 and me.json().get("person_id") == person and me.json().get("workspace_id") == "ws-ae":
            return person  # any signed-in person may open a knowledge-only session; open_session proves it
    raise RuntimeError("no person in ws-ae")


def full_runs_so_far() -> int:
    if not LEDGER.exists():
        return 0
    return sum(1 for line in LEDGER.read_text().splitlines() if line.strip() and json.loads(line).get("full"))


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * len(ordered) + 0.5) - 1))]


def summarise(results: list[dict]) -> dict:
    q_turns = [r for r in results if r["route"] in ("answer", "no_source")]
    answers = [r for r in results if r["route"] == "answer"]
    no_src = [r for r in results if r["route"] == "no_source"]
    fixed = [r for r in results if r["route"] == "fixed"]
    ms = [r["ms"] for r in results]
    return {
        "turns": len(results),
        "E1_question_turns_fixed_or_not_available": sum(r["got"] in ("fixed", "not_available") for r in q_turns),
        "E1_of": len(q_turns),
        "E2_fixed_right": sum(r["passed"] for r in fixed), "E2_of": len(fixed),
        "E3_answer_pass": sum(r["passed"] for r in answers), "E3_of": len(answers),
        "E4_no_source_pass": sum(r["passed"] for r in no_src), "E4_of": len(no_src),
        "E5_answer_wrongly_refused": sum(r["got"] in ("no_source", "not_available", "fixed") for r in answers),
        "E6_error_events": sum(r["got"] == "error" for r in results),
        "E6_unverified_citations": sum(r["unverified"] for r in results),
        "got": {k: sum(r["got"] == k for r in results) for k in ("answer", "no_source", "fixed", "not_available", "error", "other")},
        "cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "input_tokens": sum(r["input_tokens"] for r in results),
        "output_tokens": sum(r["output_tokens"] for r in results),
        "p50_ms": int(statistics.median(ms)) if ms else 0,
        "p95_ms": int(pct(ms, 0.95)),
        "fallbacks": sum(r["fallbacks"] for r in results),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="baseline, A, B, or a fix label")
    ap.add_argument("--only", help="comma-separated dialogue ids, such as KA-08,KA-14")
    ap.add_argument("--base", default="http://127.0.0.1:8310")
    ap.add_argument("--dialogues", default=str(HERE / "dialogues.jsonl"))
    ap.add_argument("--turn-log", default=str(RUNS / "turns.jsonl"))
    ap.add_argument("--baseline-model", default="gemini-3.8-flash", help="the model the usage event is priced at when there is no turn log")
    ap.add_argument("--person", help="skip the search for a person")
    args = ap.parse_args()

    only = set(args.only.split(",")) if args.only else None
    dialogues = load_dialogues(Path(args.dialogues), only)
    full = only is None
    RUNS.mkdir(parents=True, exist_ok=True)
    if full and full_runs_so_far() >= MAX_FULL_RUNS:
        print(f"refused: {MAX_FULL_RUNS} full runs already made (see {LEDGER}). Use --only.")
        return 2

    turn_log = TurnLog(Path(args.turn_log))
    results: list[dict] = []
    started = datetime.now()
    with httpx.Client(base_url=args.base, timeout=httpx.Timeout(150.0, connect=5.0)) as client:
        person = args.person or pick_person(client)
        print(f"person {person}, {len(dialogues)} dialogues, label {args.label}")
        for d in dialogues:
            session = open_session(client, person)
            for i, turn in enumerate(d["turns"], 1):
                turn_log.mark()
                t0 = time.monotonic()
                r = client.post(f"/api/v1/assistant/sessions/{session}/turns", json={"message": turn["user"]},
                                headers={"Authorization": f"Bearer {person}"})
                ms = int((time.monotonic() - t0) * 1000)
                events = parse_sse(r.text) if r.status_code == 200 else [
                    {"type": "error", "code": f"http_{r.status_code}", "message": r.text[:300]}]
                lines = [x for x in turn_log.new_lines() if x.get("message") == turn["user"]]
                log = lines[-1] if lines else None
                passed, why, got = score(turn, events, log)
                usage = next((e["usage"] for e in events if e["type"] == "usage"), {}) or {}
                if log:
                    models = log.get("models", [])
                    tokens_in = sum(m.get("in", 0) for m in models)
                    tokens_out = sum(m.get("out", 0) + m.get("thought", 0) for m in models)
                    cost = cost_of(models)
                else:
                    tokens_in, tokens_out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
                    cost = cost_of([{"model": args.baseline_model, "in": tokens_in, "out": tokens_out}])
                message = next((e for e in events if e["type"] == "message"), None)
                results.append({
                    "dialogue": d["id"], "turn": i, "user": turn["user"], "route": turn["route"], "got": got, "passed": passed, "why": why,
                    "ms": ms, "cost_usd": cost, "input_tokens": tokens_in, "output_tokens": tokens_out,
                    "unverified": sum(e.get("unverified", 0) for e in events if e["type"] == "message"),
                    "fallbacks": sum(1 for m in (log or {}).get("models", []) if m.get("fallback")),
                    "text": message["text"] if message else None,
                    "cited": sorted(cited_docs(message)) if message else [],
                    "events": [e["type"] for e in events], "log": log,
                })
                mark = "PASS" if passed else "FAIL"
                print(f"{d['id']} t{i} {turn['route']:9} got {got:13} {mark} {ms:6d} ms  {'; '.join(why)[:160]}")

    summary = summarise(results)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    out = RUNS / f"{args.label}-{stamp}.json"
    out.write_text(json.dumps({"label": args.label, "started": started.isoformat(), "full": full, "only": sorted(only or []),
                               "person": person, "summary": summary, "turns": results}, indent=2, ensure_ascii=False))
    with LEDGER.open("a") as f:
        f.write(json.dumps({"label": args.label, "file": out.name, "full": full, "only": sorted(only or []), "at": started.isoformat(),
                            "cost_usd": summary["cost_usd"]}) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"written {out}. Full runs so far: {full_runs_so_far()} of {MAX_FULL_RUNS}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
