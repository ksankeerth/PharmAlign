#!/usr/bin/env python3
"""Re-runs checks of a case through a real server and the real knowledge base, and records what each run cost.

    cd agents/checklist-assessor
    .venv/bin/python samples/golden/run_e2e.py CASE-1042 LBL-02 LEGAL-01 PV-01 QUAL-07 --runs 2 \\
        --server http://127.0.0.1:8200 --db-container pharmalign-eval-db --db golden_assessor_a

This is not part of `pytest`. It needs a server you started yourself, on a throwaway database that holds the library and the case,
with the Assessor running live on Vertex AI and PHARMALIGN_AGENT_MAX_MODEL_CALLS=40, PHARMALIGN_AGENT_MAX_TOOL_CALLS=80 and
PHARMALIGN_AGENT_RUN_DEADLINE_SECONDS=600 on the server (the cloud budget). It starts a re-check through the server's API (the same call
a reviewer's "check again" makes), waits for the run, and reads the run and the check from the database. Results go to
`samples/golden/results/e2e-<time>/`, which is git-ignored: they hold quotes of real law.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TERMINAL = {"completed", "stopped_early", "failed", "cancelled", "lost"}


def sql(args, query: str) -> list[dict]:
    out = subprocess.run(
        ["docker", "exec", args.db_container, "psql", "-U", "pharmalign", "-d", args.db, "-At", "-c", f"select coalesce(json_agg(t), '[]') from ({query}) t"],
        capture_output=True, text=True, check=True).stdout.strip()
    return json.loads(out)


def post(args, path: str, body: dict) -> dict:
    req = urllib.request.Request(args.server + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def current(args, case: str, check: str) -> dict:
    rows = sql(args, f"select id, doc from check_result where doc->>'case_id' = '{case}' and doc->>'check_id' = '{check}' and (doc->>'is_current')::boolean order by doc->>'updated_at' desc limit 1")
    if not rows:
        sys.exit(f"{case} has no current {check}")
    return rows[0]


def one_run(args, case: str, check: str, n: int, out: Path) -> dict:
    before = current(args, case, check)
    known = {r["id"] for r in sql(args, f"select id from agent_run where case_id = '{case}' and check_id = '{check}' and agent = 'checklist-assessor'")}
    t0 = time.monotonic()
    post(args, f"/api/v1/check-results/{before['id']}/recheck", {"comment": args.comment})
    run = None
    while time.monotonic() - t0 < args.timeout:
        time.sleep(4)
        fresh = [r for r in sql(args, f"select id, status, doc from agent_run where case_id = '{case}' and check_id = '{check}' and agent = 'checklist-assessor'") if r["id"] not in known]
        run = fresh[0] if fresh else None
        if run and run["status"] in TERMINAL:
            break
    seconds = time.monotonic() - t0
    time.sleep(3)  # the server applies the result after the run ends
    after = current(args, case, check)["doc"]
    usage = (run or {}).get("doc", {}).get("usage") or {}
    record = {
        "case": case, "check": check, "run": n, "seconds": round(seconds, 1), "run_id": run["id"] if run else None, "run_status": run["status"] if run else "no run",
        "finish_reason": (run or {}).get("doc", {}).get("finish_reason"), "usage": usage, "check_status": after.get("status"),
        "agent_status": after.get("agent_status"), "summary": after.get("summary"), "evidence": after.get("evidence"), "extracted": after.get("extracted"),
        "no_source_found": after.get("no_source_found"), "rule_outcome": after.get("rule_outcome"),
    }
    (out / f"{case}-{check}-{n}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    print(f"{case} {check:<9} run {n}  {seconds:5.0f}s  {usage.get('model_calls', '-'):>3} model  {usage.get('tool_calls', '-'):>3} tool  "
          f"{usage.get('input_tokens', 0):>7} in  run {record['run_status']}  check {record['check_status']} / {record['agent_status']}", flush=True)
    return record


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("case")
    p.add_argument("checks", nargs="+")
    p.add_argument("--runs", type=int, default=2)
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--server", default="http://127.0.0.1:8200")
    p.add_argument("--db-container", default="pharmalign-eval-db")
    p.add_argument("--db", required=True)
    p.add_argument("--comment", default="Please check again.")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--out", default=str(HERE / "results" / ("e2e-" + datetime.now().strftime("%Y%m%d-%H%M%S"))))
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    records = [one_run(args, args.case, c, n, out) for n in range(args.start, args.start + args.runs) for c in args.checks]
    total = sum(r["usage"].get("input_tokens", 0) for r in records)
    print(f"\ninput tokens in this set: {total:,}")
    (out / "summary.json").write_text(json.dumps({"input_tokens": total, "runs": [{k: r[k] for k in ("check", "run", "seconds", "usage", "check_status", "agent_status")} for r in records]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
