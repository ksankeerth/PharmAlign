"""The questions the assistant panel offers, run the way the panel runs them. Calls the real models, so run it on purpose.

    .venv/bin/python -I eval/run_golden.py --label adk-r1 --path adk --parts starters,case
    .venv/bin/python -I eval/run_golden.py --label cloud --path cloud --base https://<server> --parts smoke --no-log

What it sends (eval/golden_ui.json):
- starters: each of the 5 general starters in two chats. Chat "chips": the starter, then the first suggestion of its answer, then
  the second suggestion of that answer (the panel shows the chips of the latest answer only). Chat "typed": the starter, then two
  follow-ups a person would type.
- case: CASE-1042's starters in a case chat, as the panel builds them: "What blocks promotion?" and, for each of 5 checks, "Why is
  {check} {status}?", "Read the pages cited by {check} again" and "Draft a comment on {check}". --focus sends the check as the
  panel does when the person is on that check's page.
- smoke: the 5 starters and the first chip of each, read-only (for the live site).
- Each part keeps to --flows (chips, typed) so a repeat can be cheaper.

Every turn is written to eval/runs/golden-<label>-<time>.json: route, pass or fail and why, citations (verified and dropped), the
suggestions, tokens, latency and, where there is one, the turn log (pipeline) or the searches the agent made (ADK, read from
the tap in eval/mcp_tap.py). eval/runs/ is git-ignored: the records hold quotes of real law.
Input tokens of every turn are added to eval/runs/golden-budget.jsonl, and the run stops at --cap (default 2.9 million).
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_dialogues as rd  # noqa: E402  (its scoring and price table are the ones the 30 dialogues use)

RUNS = HERE / "runs"
BUDGET = RUNS / "golden-budget.jsonl"
SPEC = json.loads((HERE / "golden_ui.json").read_text())


def budget_used() -> int:
    if not BUDGET.exists():
        return 0
    return sum(json.loads(line)["input_tokens"] for line in BUDGET.read_text().splitlines() if line.strip())


def spend(label: str, tokens: int, note: str = "") -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    with BUDGET.open("a") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "label": label, "input_tokens": tokens, "note": note}) + "\n")


def why_question(check: str, status: str) -> str:
    """The panel's "Why ..." starter for a check (web/src/lib/assistantStarters.ts)."""
    return {
        "look": f"Why does {check} need a look?", "person_confirms": f"Why does {check} need a person to confirm it?",
        "pass": f"Why did {check} pass?", "waived": f"Why was {check} waived?", "skipped": f"Why was {check} skipped?",
        "error": f"Why did {check} end in an error?", "running": f"What is {check} doing now?",
    }.get(status, f"Why is {check} {status}?")


class Tap:
    """The searches the agent made in one turn, read from the tap's log by time."""

    def __init__(self, path: Path | None):
        self.path = path

    def between(self, t0: float, t1: float) -> list[dict]:
        if not self.path or not self.path.exists():
            return []
        rows = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]
        return [r for r in rows if t0 - 0.5 <= r["t"] <= t1 + 0.5]


class Runner:
    def __init__(self, args):
        self.args = args
        self.client = httpx.Client(base_url=args.base, timeout=httpx.Timeout(150.0, connect=10.0))
        self.turn_log = rd.TurnLog(Path(args.turn_log)) if not args.no_log else rd.TurnLog(None)
        self.tap = Tap(Path(args.tap) if not args.no_log else None)
        self.results: list[dict] = []
        self.stopped = False

    # ------------------------------------------------------------------ plumbing
    def headers(self, person: str) -> dict:
        return {"Authorization": f"Bearer {person}"}

    def open_session(self, person: str, case_id: str | None = None) -> str:
        body = {"language": "en"}
        if case_id:
            body["case_id"] = case_id
        r = self.client.post("/api/v1/assistant/sessions", json=body, headers=self.headers(person))
        if r.status_code != 201:
            raise RuntimeError(f"opening a session as {person}: {r.status_code} {r.text[:300]}")
        return r.json()["id"]

    def ask(self, session: str, person: str, message: str, focus: str | None, group: str, kind: str, expect: dict, chat: str) -> dict:
        used = budget_used()
        if used >= self.args.cap:
            self.stopped = True
            raise SystemExit(f"cost cap reached: {used} input tokens")
        self.turn_log.mark()
        body: dict = {"message": message}
        if focus:
            body["focus"] = {"check_id": focus, "finding_id": None}
        t0w = time.time()
        t0 = time.monotonic()
        r = self.client.post(f"/api/v1/assistant/sessions/{session}/turns", json=body, headers=self.headers(person))
        ms = int((time.monotonic() - t0) * 1000)
        t1w = time.time()
        events = rd.parse_sse(r.text) if r.status_code == 200 else [{"type": "error", "code": f"http_{r.status_code}", "message": r.text[:300]}]
        lines = [x for x in self.turn_log.new_lines() if x.get("message") == message]
        log = lines[-1] if lines else None
        usage = next((e["usage"] for e in events if e["type"] == "usage"), {}) or {}
        message_ev = next((e for e in events if e["type"] == "message"), None)
        none_ev = next((e for e in events if e["type"] == "no_source"), None)
        suggestions = (message_ev or none_ev or {}).get("suggestions") or []
        passed, why, got = self.judge(expect, events, log, message_ev)
        if log:
            models = log.get("models", [])
            cost = rd.cost_of(models)
        else:
            cost = rd.cost_of([{"model": "gemini-3.8-flash", "in": usage.get("input_tokens", 0), "out": usage.get("output_tokens", 0)}])
        tokens_in = usage.get("input_tokens", 0)
        spend(self.args.label, tokens_in, f"{group} {kind}")
        searches = [{"tool": s["tool"], "args": s["args"], "hits": s["hits"], "ms": s["ms"]} for s in self.tap.between(t0w, t1w)]
        row = {
            "group": group, "chat": chat, "kind": kind, "q": message, "focus": focus, "want": expect.get("route"), "got": got, "passed": passed, "why": why,
            "ms": ms, "model_calls": usage.get("model_calls"), "tool_calls": usage.get("tool_calls"), "input_tokens": tokens_in,
            "output_tokens": usage.get("output_tokens"), "cost_usd": round(cost, 5),
            "text": (message_ev or {}).get("text") or (none_ev or {}).get("detail") or next((e.get("detail") for e in events if e["type"] == "not_available"), None),
            "citations": [{"anchor": c["anchor"], "document_id": c.get("document_id"), "status": c.get("legal_status"), "source": c.get("source"),
                           "title": (c.get("title") or "")[:90], "quote": c.get("quote")} for c in (message_ev or {}).get("citations", [])],
            "unverified": (message_ev or {}).get("unverified", 0), "suggestions": suggestions,
            "events": [e["type"] for e in events if e["type"] != "text_delta"],
            "tried": [t for t in (none_ev or {}).get("searches_tried", [])],
            "errors": [e for e in events if e["type"] == "error"], "path_seen": "pipeline" if log else "adk-or-cloud", "log": log, "searches": searches,
            "other_events": [e for e in events if e["type"] in ("draft_comment", "not_available")],
        }
        self.results.append(row)
        mark = "PASS" if passed else "FAIL"
        print(f"{group:8} {chat:6} {kind:7} {got:13} {mark} {ms:6d} ms {tokens_in:6d} in  {message[:58]!r}  {'; '.join(why)[:110]}", flush=True)
        return row

    # ------------------------------------------------------------------ judging
    def judge(self, expect: dict, events: list[dict], log: dict | None, message_ev: dict | None) -> tuple[bool, list[str], str]:
        route = expect.get("route", "answer")
        kinds = [e["type"] for e in events]
        if route in ("answer", "no_source"):
            turn = {"route": route, "docs": expect.get("docs", []), "facts": expect.get("facts", []), "forbid_docs": expect.get("forbid_docs", []),
                    "forbid_text": []}
            return rd.score(turn, events, log)
        passed, why, got = rd.score({"route": "answer"}, events, log)  # only for `got`
        errors = [e for e in events if e["type"] == "error"]
        reasons: list[str] = []
        if errors:
            reasons.append(f"error event: {errors[0].get('code')}: {errors[0].get('message', '')[:100]}")
        if route == "refusal":  # nothing in the library: a no_source, a fixed reply or a not_available. Never an answer with sources.
            if got == "answer":
                reasons.append("answered with citations where nothing in the library answers")
        elif route == "blocks":
            ok = message_ev and message_ev.get("citations") and not message_ev.get("unverified")
            if not ok:
                reasons.append(f"want a cited answer (got {got})")
            elif not re.search(r"QUAL-07|held", message_ev["text"], re.I):
                reasons.append("answer does not name QUAL-07 or the held check")
        elif route == "why":
            if not (message_ev and message_ev.get("citations")):
                reasons.append(f"want a cited answer (got {got})")
            elif message_ev.get("unverified"):
                reasons.append(f"{message_ev['unverified']} unverified citations")
        elif route == "pages":
            if not (message_ev and message_ev.get("citations")) and got != "not_available":
                reasons.append(f"want an answer with the cited pages, or a stated reason (got {got})")
            elif message_ev and message_ev.get("unverified"):
                reasons.append(f"{message_ev['unverified']} unverified citations")
        elif route == "draft":
            if "draft_comment" not in kinds:
                reasons.append(f"want a draft_comment event (got {got})")
        return not reasons, reasons, ("draft_comment" if "draft_comment" in kinds else got)

    # ------------------------------------------------------------------ the parts
    def chat(self, person: str, steps: list[tuple[str, str, dict]], group: str, chat: str, case_id: str | None = None, focus: str | None = None) -> None:
        """steps: (kind, text or 'chip:<n>', expect). A chip picks that suggestion of the latest answer."""
        session = self.open_session(person, case_id)
        latest: list[str] = []
        for kind, text, expect in steps:
            if text.startswith("chip:"):
                idx = int(text.split(":")[1])
                if idx >= len(latest):
                    self.results.append({"group": group, "chat": chat, "kind": kind, "q": None, "got": "no_chip", "passed": False,
                                         "why": [f"no suggestion #{idx + 1} to click (latest answer gave {len(latest)})"], "suggestions": latest})
                    print(f"{group:8} {chat:6} {kind:7} no suggestion #{idx + 1} to click (latest gave {len(latest)})", flush=True)
                    return
                text = latest[idx]
            row = self.ask(session, person, text, focus, group, kind, expect, chat)
            latest = row["suggestions"]

    def starters(self, flows: list[str], person: str, only: set[str] | None, smoke: bool = False) -> None:
        for s in SPEC["starters"]:
            if only and s["id"] not in only:
                continue
            base = {"route": s["route"], "docs": s["docs"], "facts": s["facts"], "forbid_docs": s["forbid_docs"]}
            chip_expect = {"route": "answer", "docs": [], "facts": []}  # a suggested question must be answerable
            if smoke:
                self.chat(person, [("starter", s["q"], base), ("chip", "chip:0", chip_expect)], s["id"], "chips")
                continue
            if "chips" in flows:
                self.chat(person, [("starter", s["q"], base), ("chip", "chip:0", chip_expect), ("chip", "chip:1", chip_expect)], s["id"], "chips")
            if "typed" in flows:
                steps = [("starter", s["q"], base)]
                for t in s["typed"]:
                    steps.append(("typed", t["q"], t))
                self.chat(person, steps, s["id"], "typed")

    def case(self, focus_on: bool, only_checks: set[str] | None, kinds: set[str] | None) -> None:
        c = SPEC["case"]
        person, case_id = c["person"], c["case_id"]
        checks = {x["check_id"]: x for x in self.client.get(f"/api/v1/cases/{case_id}/checks", headers=self.headers(person)).json()["items"]}
        for g in c["general"]:
            if not kinds or g["kind"] in kinds:
                self.chat(person, [(g["kind"], g["q"], {"route": g["kind"]})], "case", "general", case_id)
        for check in c["checks"]:
            if only_checks and check not in only_checks:
                continue
            status = checks[check]["status"]
            steps = []
            for p in c["per_check"]:
                if not kinds or p["kind"] in kinds:
                    text = why_question(check, status) if p["kind"] == "why" else p["fmt"].format(check=check)
                    steps.append((p["kind"], text, {"route": p["kind"]}))
            if steps:
                self.chat(person, steps, check, "case", case_id, focus=check if focus_on else None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--path", required=True, help="adk, pipeline or cloud: what the stack under test is (a label only)")
    ap.add_argument("--parts", default="starters", help="starters, case, smoke (comma-separated)")
    ap.add_argument("--flows", default="chips,typed")
    ap.add_argument("--only", help="starter ids, such as S1,S3")
    ap.add_argument("--checks", help="case checks to ask about, such as QUAL-07,SAME-01")
    ap.add_argument("--kinds", help="case question kinds: blocks, why, pages, draft")
    ap.add_argument("--focus", action="store_true", help="case: send the check as the focus, as the panel does on a check's page")
    ap.add_argument("--base", default="http://127.0.0.1:8310")
    ap.add_argument("--person", default="p-alex")
    ap.add_argument("--turn-log", default=str(RUNS / "golden-turns.jsonl"))
    ap.add_argument("--tap", default=str(RUNS / "golden-mcp.jsonl"))
    ap.add_argument("--no-log", action="store_true", help="no turn log or tap (the live site)")
    ap.add_argument("--cap", type=int, default=2_900_000)
    args = ap.parse_args()

    runner = Runner(args)
    only = set(args.only.split(",")) if args.only else None
    started = datetime.now()
    parts = args.parts.split(",")
    try:
        if "smoke" in parts:
            runner.starters([], args.person, only, smoke=True)
        if "starters" in parts:
            runner.starters(args.flows.split(","), args.person, only)
        if "case" in parts:
            runner.case(args.focus, set(args.checks.split(",")) if args.checks else None, set(args.kinds.split(",")) if args.kinds else None)
    finally:
        out = RUNS / f"golden-{args.label}-{started.strftime('%Y%m%d-%H%M%S')}.json"
        res = runner.results
        ms = [r["ms"] for r in res if "ms" in r]
        summary = {
            "turns": len(res), "passed": sum(r["passed"] for r in res), "input_tokens": sum(r.get("input_tokens") or 0 for r in res),
            "output_tokens": sum(r.get("output_tokens") or 0 for r in res), "cost_usd": round(sum(r.get("cost_usd") or 0 for r in res), 4),
            "p50_ms": int(__import__("statistics").median(ms)) if ms else 0, "p95_ms": int(rd.pct(ms, 0.95)),
            "errors": sum(1 for r in res if r.get("got") == "error"), "unverified": sum(r.get("unverified") or 0 for r in res),
        }
        RUNS.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"label": args.label, "path": args.path, "parts": parts, "started": started.isoformat(), "summary": summary,
                                   "turns": res}, indent=1, ensure_ascii=False))
        print(json.dumps(summary), f"\nwritten {out}. Budget used so far: {budget_used()} input tokens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
