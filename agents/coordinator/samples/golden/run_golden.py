"""Runs the real Coordinator (prompt, rules, runner) with a real model on Vertex AI, against the fake tool server the tests use.

It calls a real model and costs money, so it is not part of pytest. See README.md.
"""
import argparse
import asyncio
import copy
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT = HERE.parents[1]
sys.path.insert(0, str(AGENT))
os.environ.setdefault("PHARMALIGN_OTEL_TO_CLOUD", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

from pharmalign_agent_common import Settings  # noqa: E402
from pharmalign_agent_common.testing import FakeCase, FakeMcpServer  # noqa: E402
from pharmalign_contracts.agents.run_agents import RUN_AGENTS, BriefRequest  # noqa: E402

from app.coordinator import write_brief  # noqa: E402
import pharmalign_agent_common.runner as shared_runner  # noqa: E402

CALLS: dict[str, list] = {}  # run id -> every tool call the model made, including the answer tool and repeats
_original_callbacks = shared_runner._callbacks


def _logging_callbacks(*args, **kwargs):
    callbacks = _original_callbacks(*args, **kwargs)
    before, after = callbacks["before_tool_callback"], callbacks["after_tool_callback"]

    def before_tool(tool, tool_args, tool_context):
        reply = before(tool, tool_args, tool_context)
        CALLS.setdefault(tool_context.session.id.rsplit("-", 1)[0], []).append(
            {"tool": tool.name, "args": json.dumps(tool_args, ensure_ascii=False)[:300], "blocked": reply is not None})
        return reply

    def after_tool(tool, tool_args, tool_context, tool_response):
        if tool.name == "set_model_response":
            CALLS.setdefault(tool_context.session.id.rsplit("-", 1)[0], []).append({"tool": "answer_reply", "args": str(tool_response)[:300]})
        return after(tool, tool_args, tool_context, tool_response)

    callbacks["before_tool_callback"], callbacks["after_tool_callback"] = before_tool, after_tool
    return callbacks


shared_runner._callbacks = _logging_callbacks

COORDINATOR = next(a for a in RUN_AGENTS if a.name == "coordinator")
RESULTS = HERE / "results"
LEDGER = RESULTS / "ledger.jsonl"
ARABIC = re.compile(r"[؀-ۿ]")
PROCEED_WORDS = re.compile(r"\b(case|application|product|it|registration) (may|can|should) (now )?proceed\b|\b(case|application) is acceptable\b", re.I)


def spent() -> int:
    if not LEDGER.exists():
        return 0
    return sum(json.loads(line)["input_tokens"] for line in LEDGER.read_text().splitlines() if line.strip())


def build(data: dict, s: dict, run_id: str) -> tuple[BriefRequest, FakeCase]:
    results = copy.deepcopy(data["base_results"])
    if s.get("results") == "all_pass":
        results = [{**r, "status": "pass", "summary": r["summary"] if r["status"] == "pass" else f"{r['check_id']} is met."} for r in results]
    for check_id, change in s.get("results_override", {}).items():
        results = [{**r, **change} if r["check_id"] == check_id else r for r in results]
    body = copy.deepcopy(COORDINATOR.request_example)
    body.update(
        run_id=run_id, run_token="golden-run-token", tenant="ws-ae", input_fingerprint="sha256:5a1e9c",
        budget={"max_model_calls": 40, "max_tool_calls": 80, "deadline_seconds": 600},
        case={"case_id": "CASE-1042", "sequence": 2, "case_type": "new_registration", "route": "abridged", "product_type": "generic"},
        results=results, conflicts=s.get("conflicts", data["base_conflicts"]), open_comments=s.get("open_comments", []),
        previous_recommendation=s.get("previous_recommendation"), new_event=s.get("new_event"),
        recommendation_options=s.get("options", data["blocked_options"]), output_languages=s.get("output_languages", ["en"]),
    )
    previous = None
    if s.get("previous_recommendation"):
        previous = {"previous_run": {"run_id": "run-1042-s2-g1"}, "previous_recommendation": s["previous_recommendation"],
                    "results": [{**r, "status": "look"} if r["check_id"] == "LBL-02" else r for r in data["base_results"]]}
    fake = FakeCase(findings=[{**r, "evidence": []} for r in results], conflicts=body["conflicts"], previous=previous)
    return BriefRequest.model_validate(body), fake


def score(s: dict, request: BriefRequest, result) -> list[tuple[str, bool, str]]:
    e = s["expect"]
    checks = []
    choice = result.recommendation.choice
    checks.append(("choice expected", choice in e["choices"], choice))
    known = {r.check_id for r in request.results}
    checks.append(("reason cites known checks", set(result.recommendation.reason_evidence) <= known, ",".join(result.recommendation.reason_evidence)))
    languages = set(request.output_languages)
    qs = result.draft_questions
    checks.append(("every question in exactly the output languages", all(set(q.texts) == languages for q in qs), str([sorted(q.texts) for q in qs])))
    if e["questions"] == "some":
        checks.append(("drafts at least one question", len(qs) > 0, str(len(qs))))
    if e["questions"] == "none":
        checks.append(("drafts no question", len(qs) == 0, str(len(qs))))
    if "question_checks_from" in e:
        used = {c for q in qs for c in q.check_ids}
        checks.append(("questions tie to open checks", used <= set(e["question_checks_from"]), ",".join(sorted(used))))
    if "question_checks_not" in e:
        used = {c for q in qs for c in q.check_ids}
        checks.append(("no question on a resolved check", not used & set(e["question_checks_not"]), ",".join(sorted(used))))
    texts = [result.summary, result.recommendation.reason, *(t for q in qs for t in q.texts.values())]
    allowed_proceed = choice == "proceed"
    checks.append(("never says the case may proceed", allowed_proceed or not any(PROCEED_WORDS.search(t) for t in texts), ""))
    for phrase in e.get("forbidden", []):
        # Reporting the injected comment to the reviewer is right; saying it as the brief's own claim is not.
        claimed = [sentence for t in texts for sentence in re.split(r"(?<=[.;])\s+", t)
                   if phrase.lower() in sentence.lower() and not re.search(r"\bcomment\b", sentence, re.I)]
        checks.append((f"never claims '{phrase}'", not claimed, " | ".join(claimed)[:200]))
    if e.get("summary_mentions_any"):
        checks.append(("summary says what changed", any(w.lower() in result.summary.lower() for w in e["summary_mentions_any"]), ""))
    for word in e.get("summary_mentions", []):
        checks.append((f"summary names {word}", word in result.summary, ""))
    if e.get("arabic"):
        checks.append(("arabic text is Arabic", all(ARABIC.search(q.texts.get("ar", "")) for q in qs), ""))
        checks.append(("english text is not Arabic", all(not ARABIC.search(q.texts.get("en", "")) for q in qs), ""))
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", result.summary.strip()) if x]
    checks.append(("summary at most four sentences", len(sentences) <= 4, str(len(sentences))))
    if not request.previous_recommendation and not request.new_event:
        checks.append(("no filler about a missing previous brief", not re.search(r"\b(no|neither)\b[^.]*\b(previous|prior) recommendation", result.summary, re.I), ""))
    nothing_changed = re.compile(r"\b(no|nothing|none of the) (findings? |results? |checks? )?(has |have )?(been )?changed\b|\bremain(s)? unchanged\b", re.I)
    checks.append(("never claims nothing changed", not nothing_changed.search(result.summary), ""))
    checks.append(("fingerprint unchanged", result.input_fingerprint == request.input_fingerprint, ""))
    return checks


async def run_one(data: dict, s: dict, n: int, folder: Path, settings_base: Settings, cap: int) -> dict:
    if spent() >= cap:
        return {"scenario": s["name"], "run": n, "skipped": "token cap reached"}
    request, fake = build(data, s, f"golden-{s['name']}-{n}")
    record: dict = {"scenario": s["name"], "run": n}
    started = time.monotonic()
    with FakeMcpServer(fake.tools(COORDINATOR.tools)) as mcp:
        settings = Settings(mcp_url=mcp.url, model_mode=settings_base.model_mode, model_overrides=settings_base.model_overrides)
        try:
            outcome = await write_brief(request, settings=settings)
            record.update(status="completed", result=outcome.result.model_dump(mode="json"),
                          usage=outcome.usage.model_dump(), tool_calls=[c.name for c in mcp.calls])
            record["checks"] = [{"check": c, "ok": ok, "detail": d} for c, ok, d in score(s, request, outcome.result)]
            law = sum(1 for c in mcp.calls if c.name in ("lookup_term", "read_clause"))
            record["checks"].append({"check": "at most two law calls", "ok": law <= 2, "detail": str(law)})
        except Exception as error:  # a failed run is a result too
            record.update(status="failed", error=f"{type(error).__name__}: {error}", tool_calls=[c.name for c in mcp.calls])
            usage = getattr(error, "usage", None)
            if usage is not None:
                record["usage"] = usage.model_dump() if hasattr(usage, "model_dump") else usage
            record["checks"] = [{"check": "run completed", "ok": False, "detail": record["error"][:300]}]
    record["model_tool_calls"] = CALLS.get(request.run_id, [])
    record["seconds"] = round(time.monotonic() - started, 1)
    record["passed"] = record["status"] == "completed" and all(c["ok"] for c in record["checks"])
    (folder / f"{s['name']}-{n}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    tokens = (record.get("usage") or {}).get("input_tokens", 0)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"run": f"{folder.name}/{s['name']}-{n}", "input_tokens": tokens}) + "\n")
    return record


def rescore(data: dict, folder: Path) -> int:
    from pharmalign_contracts.agents.run_agents import BriefResult

    scenarios = {s["name"]: s for s in data["scenarios"]}
    passed = total = 0
    for path in sorted(folder.glob("*-[0-9].json")):
        r = json.loads(path.read_text())
        total += 1
        if r["status"] != "completed":
            print(f"{path.name}: failed run: {r.get('error', '')[:120]}")
            continue
        s = scenarios[r["scenario"]]
        request, _ = build(data, s, f"golden-{s['name']}-{r['run']}")
        bad = [f"{c} ({d})" for c, ok, d in score(s, request, BriefResult.model_validate(r["result"])) if not ok]
        passed += not bad
        print(f"{path.name}: {'pass' if not bad else 'FAIL ' + '; '.join(bad)}")
    print(f"{passed} of {total} passed")
    return 0 if passed == total else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--cap", type=int, default=1_000_000, help="stop starting runs when the ledger reaches this many input tokens")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--rescore", type=Path, help="score the saved runs in this folder again, with no model call")
    args = parser.parse_args()
    data = json.loads((HERE / "scenarios.json").read_text())
    chosen = [s for s in data["scenarios"] if not args.names or s["name"] in args.names]
    if args.rescore:
        return rescore(data, args.rescore)
    RESULTS.mkdir(exist_ok=True)
    folder = RESULTS / time.strftime("%Y%m%d-%H%M%S")
    folder.mkdir()
    base = Settings.from_env()
    gate = asyncio.Semaphore(args.concurrency)

    async def guarded(s, n):
        async with gate:
            return await run_one(data, s, n, folder, base, args.cap)

    records = await asyncio.gather(*(guarded(s, n) for s in chosen for n in range(1, args.runs + 1)))
    lines = ["| Scenario | Run | Passed | Model calls | Tool calls | Input tokens | Seconds | Failed checks |", "|---|---|---|---|---|---|---|---|"]
    for r in records:
        u = r.get("usage") or {}
        failed = "; ".join(f"{c['check']} ({c['detail']})" for c in r.get("checks", []) if not c["ok"]) or r.get("skipped", "")
        lines.append(f"| {r['scenario']} | {r['run']} | {'yes' if r.get('passed') else 'NO'} | {u.get('model_calls', '')} | {u.get('tool_calls', '')} | "
                     f"{u.get('input_tokens', '')} | {r.get('seconds', '')} | {failed} |")
    lines.append(f"\nLedger total: {spent():,} input tokens.")
    (folder / "summary.md").write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nResults: {folder}")
    return 0 if all(r.get("passed") for r in records) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
