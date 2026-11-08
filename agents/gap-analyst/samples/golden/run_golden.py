#!/usr/bin/env python3
"""Runs the Gap Analyst on the golden scenarios with a real model, and scores every run.

    cd agents/gap-analyst
    .venv/bin/python samples/golden/run_golden.py [scenario ...] [--runs 3] [--concurrency 3] [--out DIR]

This calls Vertex AI (a real model, with a cost). It is not part of `pytest`. Needs Application Default Credentials and
GOOGLE_CLOUD_PROJECT (GOOGLE_CLOUD_LOCATION defaults to global). A token ledger caps the total input tokens across every
call of this script that shares the ledger file.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))  # the agent folder, for `app`

os.environ.setdefault("PHARMALIGN_OTEL_TO_CLOUD", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.genai import types  # noqa: E402
from pharmalign_agent_common import BudgetExceeded, OutputInvalid, OutputTruncated, Settings  # noqa: E402
from pharmalign_agent_common.vertex_schema import VertexSchemaGemini  # noqa: E402
from pharmalign_contracts.agents.run_agents import GapAnalysisRequest  # noqa: E402
from pydantic import PrivateAttr  # noqa: E402

from scoring import score  # noqa: E402
from server_like import GapServerLikeMcp, GapWorld, Law  # noqa: E402

SCENARIOS = HERE / "scenarios"
BUDGET = {"max_model_calls": 40, "max_tool_calls": 80, "deadline_seconds": 600}  # what the server sends in the cloud
DEFAULT_CAP = 2_000_000  # input tokens, across every run that shares a ledger
PIPELINE_NODES = ["source", "collector", "preprocessor", "model", "policy", "distributor", "sink"]
READINESS_DIMENSIONS = ["Human interface", "Collaboration with AI", "Impact of humans in AI integration", "AI policy and policy for AI",
                        "Contextualization and regional impact", "Data and model marketplace"]


class RecordingGemini(VertexSchemaGemini):
    """The model the agent uses on Vertex, plus a record of every call: tokens, and whether the answer was sent back."""

    _calls: list = PrivateAttr(default_factory=list)
    _rejections: list = PrivateAttr(default_factory=list)

    @property
    def calls(self) -> list[dict]:
        return self._calls

    @property
    def rejections(self) -> list[str]:
        return self._rejections

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False):
        last = llm_request.contents[-1] if llm_request.contents else None
        text = "".join(p.text or "" for p in (last.parts or [])) if last else ""
        if text.startswith("Your answer was rejected"):
            self._rejections.append(text.split("\n")[0][:300])
        started = time.monotonic()
        async for response in super().generate_content_async(llm_request, stream=stream):
            usage = response.usage_metadata
            parts = response.content.parts if response.content and response.content.parts else []
            self._calls.append({
                "input_tokens": (usage.prompt_token_count or 0) if usage else 0,
                "output_tokens": (usage.candidates_token_count or 0) if usage else 0,
                "thought_tokens": (getattr(usage, "thoughts_token_count", 0) or 0) if usage else 0,
                "seconds": round(time.monotonic() - started, 2),
                "finish": str(response.finish_reason),
                "tools_asked": [p.function_call.name for p in parts if p.function_call],
            })
            yield response


def load_scenarios(ids: list[str]) -> list[dict]:
    found = [json.loads(p.read_text()) for p in sorted(SCENARIOS.glob("*.json"))]
    if ids:
        missing = set(ids) - {s["id"] for s in found}
        if missing:
            sys.exit(f"no such scenario: {', '.join(sorted(missing))}")
        found = [s for s in found if s["id"] in ids]
    return found


def build_request(scenario: dict, run_id: str) -> GapAnalysisRequest:
    req = scenario.get("request", {})
    wanted = req.get("signal_ids") or [s["id"] for s in scenario["signals"]]
    signals = [s for s in scenario["signals"] if s["id"] in wanted] + req.get("extra_signals", [])
    body = {
        "run_id": run_id, "run_token": "golden-run-token", "tenant": "ws-ae", "mode": "simulation",
        "execution_profile": {"model_set": "default", "gate": "on", "data_class": "synthetic"},
        "as_of": "2026-11-08T10:00:00Z", "versions": {"pack": "pv-ae-3", "kb_snapshot": "snap-1"},
        "priority": "batch", "language": "en", "budget": BUDGET, "signals": signals,
        "pipeline_nodes": req.get("pipeline_nodes", PIPELINE_NODES), "readiness_dimensions": req.get("readiness_dimensions", READINESS_DIMENSIONS),
    }
    return GapAnalysisRequest.model_validate(body)


def build_world(scenario: dict) -> GapWorld:
    library = {d["doc_id"]: d for d in json.loads((HERE / "law.json").read_text())}
    pack = scenario.get("pack") or json.loads((HERE / "pack.json").read_text())
    return GapWorld(copy.deepcopy(scenario["signals"]), copy.deepcopy(pack), Law([copy.deepcopy(library[d]) for d in scenario["law"]]))


def ledger_total(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(json.loads(line)["input_tokens"] for line in path.read_text().splitlines() if line.strip())


async def one_run(scenario: dict, n: int, args, inflight: dict) -> dict:
    from app.agent import MODEL
    from app.analyst import analyse

    ledger = Path(args.ledger)
    if ledger_total(ledger) + inflight["count"] * 60_000 >= args.cap:
        return {"scenario": scenario["id"], "run": n, "outcome": "skipped_cap", "checks": [], "passed": False}
    inflight["count"] += 1
    request = build_request(scenario, f"gold-{scenario['id']}-{n}")
    world = build_world(scenario)
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.monotonic()
    outcome, error, result = "completed", None, None
    mcp = GapServerLikeMcp(world)
    await asyncio.to_thread(mcp.__enter__)
    settings = Settings(mcp_url=mcp.url, model_mode="vertex")
    settings.apply_model_mode()
    model = RecordingGemini(model=settings.model_for("gap-analyst", MODEL), retry_options=types.HttpRetryOptions(attempts=3))
    try:
        try:
            run = await asyncio.wait_for(analyse(request, settings=settings, model=model), timeout=request.budget.deadline_seconds)
            result = run.result.model_dump(mode="json")
        except asyncio.TimeoutError:
            outcome = "deadline"
        except BudgetExceeded as e:
            outcome, error = "budget", str(e)
        except OutputInvalid as e:
            outcome, error = "invalid_output", str(e)[:600]
        except OutputTruncated as e:
            outcome, error = "truncated", str(e)
        except Exception as e:  # noqa: BLE001 - recorded, not hidden
            outcome, error = "error", f"{type(e).__name__}: {str(e)[:500]}"
    finally:
        await asyncio.to_thread(mcp.__exit__, None, None, None)
        inflight["count"] -= 1
    seconds = time.monotonic() - t0
    usage = {
        "model_calls": len(model.calls), "tool_calls": len(mcp.log),
        "input_tokens": sum(c["input_tokens"] for c in model.calls), "output_tokens": sum(c["output_tokens"] for c in model.calls),
        "thought_tokens": sum(c["thought_tokens"] for c in model.calls),
    }
    with ledger.open("a") as f:
        f.write(json.dumps({"at": started_at, "scenario": scenario["id"], "run": n, **usage}) + "\n")
    checks = score(scenario, request.model_dump(mode="json"), result, world, usage, seconds, outcome, mcp.log)
    record = {
        "scenario": scenario["id"], "run": n, "started_at": started_at, "outcome": outcome, "error": error, "seconds": round(seconds, 1),
        "usage": usage, "retried": len(model.rejections), "rejections": model.rejections,
        "passed": all(ok for _, ok, _ in checks), "checks": [{"name": a, "ok": b, "why": c} for a, b, c in checks],
        "tool_calls": mcp.log, "model_calls": model.calls, "result": result,
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{scenario['id']}-{n}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    bad = [c["name"] for c in record["checks"] if not c["ok"]]
    print(f"{'PASS' if record['passed'] else 'FAIL'}  {scenario['id']:<16} run {n}  {seconds:5.0f}s  {usage['model_calls']:>2} model  {usage['tool_calls']:>2} tool  "
          f"{usage['input_tokens']:>7} in  retried {len(model.rejections)}" + (f"  failed: {', '.join(bad)}" if bad else ""), flush=True)
    return record


def summarise(records: list[dict]) -> str:
    lines = ["| Scenario | Passed | Median s | Model calls | Tool calls | Input tokens | Retried runs |", "|---|---|---|---|---|---|---|"]
    for sid in dict.fromkeys(r["scenario"] for r in records):
        rs = [r for r in records if r["scenario"] == sid and r["outcome"] != "skipped_cap"]
        if not rs:
            continue
        med = lambda key: statistics.median(r["usage"][key] for r in rs)  # noqa: E731
        lines.append(f"| {sid} | {sum(r['passed'] for r in rs)}/{len(rs)} | {statistics.median(r['seconds'] for r in rs):.0f} | "
                     f"{med('model_calls'):.0f} | {med('tool_calls'):.0f} | {med('input_tokens'):.0f} | {sum(1 for r in rs if r['retried'])} |")
    done = [r for r in records if r["outcome"] != "skipped_cap"]
    if done:
        lines.append(f"| **all** | {sum(r['passed'] for r in done)}/{len(done)} | | | | {sum(r['usage']['input_tokens'] for r in done)} in total | |")
    failing: dict[tuple[str, str], list[str]] = {}
    for r in records:
        for c in r["checks"]:
            if not c["ok"]:
                failing.setdefault((r["scenario"], c["name"]), []).append(f"run {r['run']}: {c['why']}")
    if failing:
        lines += ["", "Failed checks:"]
        for (sid, name), whys in failing.items():
            lines.append(f"- {sid} / {name} ({len(whys)} run{'s' if len(whys) > 1 else ''}): {whys[0]}")
    return "\n".join(lines)


def rescore(folder: Path, scenarios: dict[str, dict]) -> int:
    records = []
    for path in sorted(folder.glob("*-[0-9]*.json")):
        r = json.loads(path.read_text())
        if r["scenario"] not in scenarios:
            continue
        s = scenarios[r["scenario"]]
        request = build_request(s, f"gold-{s['id']}-{r['run']}")
        checks = score(s, request.model_dump(mode="json"), r["result"], build_world(s), r["usage"], r["seconds"], r["outcome"], r["tool_calls"])
        r["checks"] = [{"name": a, "ok": b, "why": c} for a, b, c in checks]
        r["passed"] = all(ok for _, ok, _ in checks)
        path.write_text(json.dumps(r, indent=1, ensure_ascii=False))
        records.append(r)
    text = summarise(records)
    print(text)
    (folder / "summary.md").write_text(text + "\n")
    return 0


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("scenarios", nargs="*", help="scenario ids (default: all)")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--start", type=int, default=1, help="number of the first run (to add runs to an earlier set)")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--out", default=str(HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")))
    p.add_argument("--ledger", default=str(HERE / "results" / "ledger.jsonl"))
    p.add_argument("--cap", type=int, default=DEFAULT_CAP, help="stop starting runs when the ledger holds this many input tokens")
    p.add_argument("--list", action="store_true", help="list the scenarios and stop")
    p.add_argument("--rescore", metavar="DIR", help="score the saved runs in DIR again with the current scoring (no model call) and stop")
    args = p.parse_args()

    scenarios = load_scenarios(args.scenarios)
    if args.list:
        for s in scenarios:
            print(f"{s['id']:<16} angles {s['angles']}  {s['title']}")
        return 0
    if args.rescore:
        return rescore(Path(args.rescore), {s["id"]: s for s in scenarios})
    Path(args.ledger).parent.mkdir(parents=True, exist_ok=True)
    print(f"ledger holds {ledger_total(Path(args.ledger)):,} input tokens; cap {args.cap:,}", flush=True)
    sem = asyncio.Semaphore(args.concurrency)
    inflight = {"count": 0}

    async def guarded(s: dict, n: int) -> dict:
        async with sem:
            return await one_run(s, n, args, inflight)

    jobs = [guarded(s, n) for n in range(args.start, args.start + args.runs) for s in scenarios]
    records = await asyncio.gather(*jobs)
    text = summarise(list(records))
    print("\n" + text)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(text + "\n")
    print(f"\nledger now holds {ledger_total(Path(args.ledger)):,} input tokens")
    return 0 if all(r["passed"] for r in records) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
