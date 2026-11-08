#!/usr/bin/env python3
"""Runs the Dossier Reader on the sample cases and checks its answers against samples/answers/*.json.

    python3 agents/dossier-reader/samples/check_samples.py [CASE-4001 ...] [--server URL] [--reader URL] [--no-start]

For each case it:
  1. starts a pipeline run through the server, as p-alex (not with --no-start);
  2. waits for the Dossier Reader run to finish (the other agents may show Error: it ignores them);
  3. fetches the reader's own result from GET {reader}/dossier-reads/{run_id}, and what the server accepted
     from GET /api/v1/cases/{id}/facts and /documents;
  4. compares both with the answer key and prints one PASS or FAIL line per fact, document, forbidden
     value and issue.

It exits 0 only if every line passes. Standard library only.

It starts a real run, so it changes the case on the server it talks to. Point it at a sandbox or at the
demo stack, never at live data.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT = "1.6.0"  # the contract version the agent routes ask for in X-PharmAlign-Contract
TERMINAL = {"completed", "failed", "cancelled", "lost", "stopped_early"}
FIRST_TYPED_CHECK = "SAME-01"  # the first check that needs the Reader: re-checking it opens the Reader once SAFE-01 is waived


class Api:
    """A small JSON client. Errors come back as (status, body) and never raise on an HTTP status."""

    def __init__(self, base: str, headers: dict):
        self.base, self.headers = base.rstrip("/"), headers

    def call(self, method: str, path: str, body=None, who: str | None = None, timeout: float = 20):
        headers = {**self.headers, "Content-Type": "application/json"}
        if who:
            headers["Authorization"] = f"Bearer {who}"
        if method == "POST":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, str(e)


def problem(body) -> str:
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("title") or body)[:200]
    return str(body)[:200]


class Report:
    def __init__(self):
        self.failed = 0
        self.passed = 0

    def line(self, ok: bool, case: str, kind: str, name: str, reason: str):
        self.passed += ok
        self.failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {case}  {kind:<10} {name:<30} {reason}")

    def note(self, case: str, text: str):
        print(f"NOTE  {case}  {text}")


# ---------------------------------------------------------------- comparing text

def squash(text) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").casefold())


def same_unit(a, b) -> bool:
    clean = lambda u: str(u or "").replace("°", "").strip().casefold()  # noqa: E731 (°C counts as C)
    return clean(a) == clean(b)


def forbidden_in(item: str, text: str) -> bool:
    return re.search(r"(?<![\w.])" + re.escape(item) + r"(?![\w]|\.\d)", text, re.IGNORECASE) is not None


def describe(fact: dict, files: dict) -> str:
    parts = [fact.get("status", "?")]
    shown = fact.get("value") if fact.get("value") is not None else fact.get("number")
    if shown is not None:
        parts.append(repr(shown) + (f" {fact['unit']}" if fact.get("unit") else ""))
    where = [f"{files.get(e['anchor'].split(':')[0], e['anchor'].split(':')[0])} p{e['anchor'].rsplit(':p', 1)[-1]}" for e in fact.get("evidence", [])]
    if where:
        parts.append("from " + ", ".join(where))
    return " ".join(parts)


def judge_fact(expected: dict, fact: dict | None, files: dict) -> tuple[bool, str]:
    """Whether one extracted fact matches its key, and why."""
    if fact is None:
        return False, "no such fact in the result"
    seen = describe(fact, files)
    if fact.get("status") != expected["status"]:
        return False, f"expected {expected['status']}, got: {seen}"
    cited = [files.get(e["anchor"].split(":")[0], "?") for e in fact.get("evidence", [])]
    if expected["status"] == "not_found":
        extra = fact.get("value") is not None or fact.get("number") is not None or cited
        return (False, f"expected no value and no evidence, got: {seen}") if extra else (True, seen)
    if not cited:
        return False, f"expected evidence, got: {seen}"
    if "number" in expected and fact.get("number") != expected["number"]:
        return False, f"expected number {expected['number']}, got: {seen}"
    if "unit" in expected and not same_unit(fact.get("unit"), expected["unit"]):
        return False, f"expected unit {expected['unit']}, got: {seen}"
    if "value" in expected and squash(expected["value"]) not in squash(fact.get("value")):
        return False, f"expected value containing {expected['value']!r}, got: {seen}"
    if expected.get("evidence_in") and not set(cited) & set(expected["evidence_in"]):
        return False, f"expected a quote from {' or '.join(expected['evidence_in'])}, got: {seen}"
    missing = [f for f in expected.get("evidence_from", []) if f not in cited]
    if missing:
        return False, f"expected a quote from each side, missing {', '.join(missing)}; got: {seen}"
    return True, seen


# ---------------------------------------------------------------- talking to the server

def case_documents(server: Api, case: str, who: str) -> tuple[list[dict], str]:
    items, cursor = [], ""
    while True:
        status, body = server.call("GET", f"/api/v1/cases/{case}/documents?limit=200" + (f"&cursor={cursor}" if cursor else ""), who=who)
        if status != 200:
            return [], f"documents: HTTP {status} {problem(body)}"
        items += body["items"]
        cursor = body.get("next_cursor") or ""
        if not cursor:
            return items, ""


def case_facts(server: Api, case: str, who: str) -> tuple[list[dict], str]:
    status, body = server.call("GET", f"/api/v1/cases/{case}/facts?limit=200", who=who)
    return (body["items"], "") if status == 200 else ([], f"facts: HTTP {status} {problem(body)}")


def reader_runs(server: Api, case: str, admin: str) -> list[dict]:
    status, body = server.call("GET", f"/api/v1/admin/agent-runs?case_id={case}&agent=dossier-reader&limit=200", who=admin)
    return body["items"] if status == 200 else []


def open_reader_after_review(server: Api, case: str, who: str, report: Report) -> bool:
    """A file with hidden text holds at SAFE-01 before any agent reads it. A case officer reviews the flag and waives it,
    which is the designed route. Re-checking the first check that needs the Reader then opens the Reader."""
    status, check = server.call("GET", f"/api/v1/check-results/cr-{case[5:]}-safe-01", who=who)
    if status != 200 or check.get("status") != "held":
        return False
    reason = "Reviewed the flagged page. Hidden text is removed before agents read the file. Sample check."
    status, body = server.call("POST", f"/api/v1/check-results/{check['id']}/waive", {"fingerprint": check["fingerprint"], "reason": reason}, who=who)
    if status != 200:
        report.line(False, case, "start", "waive SAFE-01", f"HTTP {status} {problem(body)}")
        return False
    status, body = server.call("POST", f"/api/v1/check-results/cr-{case[5:]}-{FIRST_TYPED_CHECK.lower()}/recheck",
                               {"comment": "SAFE-01 was waived after review. Read the file."}, who=who)
    if status not in (200, 202):
        report.line(False, case, "start", f"recheck {FIRST_TYPED_CHECK}", f"HTTP {status} {problem(body)}")
        return False
    report.note(case, "SAFE-01 held the case because a page has hidden text. Waived it as the case officer and re-checked "
                      f"{FIRST_TYPED_CHECK}, so the Reader runs.")
    return True


def wait_for_reader(server: Api, case: str, generation: int | None, who: str, admin: str, timeout: float, report: Report, may_waive: bool) -> dict | None:
    """The server's view of the newest Reader run of this pipeline generation, once it has finished. None on failure (reported)."""
    deadline = time.monotonic() + timeout
    waived = False
    last = "no Reader run yet"
    while time.monotonic() < deadline:
        runs = [r for r in reader_runs(server, case, admin) if generation is None or r.get("generation") == generation]
        if runs:
            run = max(runs, key=lambda r: (r.get("generation") or 0, r.get("attempt") or 0))
            last = f"{run['run_id']} is {run['status']}"
            if run["status"] in TERMINAL:
                if run["status"] != "completed":
                    report.line(False, case, "reader", run["run_id"], f"the Reader run ended as {run['status']} ({run.get('error_code') or run.get('finish_reason')})")
                    return None
                return run
        elif may_waive and not waived:
            waived = open_reader_after_review(server, case, who, report)
        time.sleep(1)
    report.line(False, case, "reader", "wait", f"timed out after {timeout:.0f}s; last seen: {last}")
    return None


def fetch_reader_result(agent: Api, run_id: str, timeout: float, report: Report, case: str) -> dict | None:
    deadline = time.monotonic() + timeout
    while True:
        status, body = agent.call("GET", f"/dossier-reads/{run_id}")
        if status == 200 and isinstance(body, dict) and body.get("status") in TERMINAL:
            if body["status"] != "completed" or not body.get("result"):
                report.line(False, case, "reader", run_id, f"the agent says {body['status']}: {problem(body.get('error') or body)}")
                return None
            return body["result"]
        if status not in (200, 202) or time.monotonic() > deadline:
            report.line(False, case, "reader", run_id, f"could not read the agent's result: HTTP {status} {problem(body)}")
            return None
        time.sleep(1)


# ---------------------------------------------------------------- the comparison

def check_case(case: str, key: dict, files: dict, modules_now: dict, reader: dict, accepted: list[dict], report: Report):
    by_key_reader = {f["key"]: f for f in reader.get("facts", [])}
    by_key_server = {f["key"]: f for f in accepted}
    by_id = {doc_id: name for doc_id, name in files.items()}

    for name, expected in key["facts"].items():
        ok, why = judge_fact(expected, by_key_reader.get(name), by_id)
        report.line(ok, case, "fact", name, f"[reader] {why}")
        ok, why = judge_fact(expected, by_key_server.get(name), by_id)
        report.line(ok, case, "fact", name, f"[server] {why}")
    for name in sorted(set(by_key_reader) - set(key["facts"])):
        report.line(False, case, "fact", name, "[reader] a fact that was not in the key")

    map_by_file = {by_id.get(e["doc_id"], e["doc_id"]): e for e in reader.get("document_map", [])}
    for filename, module in key["documents"].items():
        entry = map_by_file.get(filename)
        want = module if module is not None else "null"
        if entry is None:
            report.line(False, case, "document", filename, f"[reader] expected module {want}, but the file map has no entry")
        else:
            got = entry.get("module") if entry.get("module") is not None else "null"
            report.line(got == want, case, "document", filename, f"[reader] expected module {want}, got {got}")
        got = modules_now.get(filename) if modules_now.get(filename) is not None else "null"
        report.line(got == want, case, "document", filename, f"[server] expected module {want}, now {got}")
    for filename in sorted(set(map_by_file) - set(key["documents"])):
        report.line(False, case, "document", filename, "[reader] a file that was not in the key")

    for item in key.get("never", []):
        for source, facts in (("reader", reader.get("facts", [])), ("server", accepted)):
            hits = []
            for f in facts:
                texts = [str(f.get("value") or ""), str(f.get("number") if f.get("number") is not None else ""), *[e.get("quote", "") for e in f.get("evidence", [])]]
                if any(forbidden_in(item, t) for t in texts):
                    hits.append(f["key"])
            report.line(not hits, case, "never", item, f"[{source}] " + (f"appears in {', '.join(hits)}" if hits else "does not appear in any fact or quote"))

    issues = reader.get("issues", [])
    matched = set()
    for want in key["issues"]["required"]:
        found = next((i for i, issue in enumerate(issues) if by_id.get(issue["doc_id"]) == want["file"] and issue["kind"] == want["kind"]
                      and issue.get("page") in (None, want.get("page"))), None)
        if found is not None:
            matched.add(found)
        report.line(found is not None, case, "issue", f"{want['kind']} {want['file']}", "[reader] " + ("reported" if found is not None else "missing"))
    allowed = key["issues"].get("allowed", [])
    for i, issue in enumerate(issues):
        name = by_id.get(issue["doc_id"], issue["doc_id"])
        if i in matched:
            continue
        ok = issue["kind"] in key["issues"].get("allowed_kinds", []) or any(name == a["file"] and issue["kind"] == a["kind"] for a in allowed)
        report.line(ok, case, "issue", f"{issue['kind']} {name}", "[reader] " + ("an allowed extra" if ok else f"not expected: {issue.get('detail', '')[:120]}"))
    if not key["issues"]["required"] and not issues:
        report.line(True, case, "issue", "none", "[reader] no issues, as expected")


def run_case(case: str, args, server: Api, agent: Api, report: Report):
    path = args.answers / f"{case}.json"
    if not path.is_file():
        report.line(False, case, "start", "answer key", f"no key at {path}")
        return
    key = json.loads(path.read_text())
    generation = None
    if not args.no_start:
        status, body = server.call("POST", f"/api/v1/cases/{case}/pipeline-runs", {}, who=args.person)
        if status not in (200, 201, 202):
            report.line(False, case, "start", "pipeline run", f"HTTP {status} {problem(body)}")
            return
        generation = body["generation"]
        report.note(case, f"started {body['id']}")
    run = wait_for_reader(server, case, generation, args.person, args.admin, args.timeout, report, may_waive=not args.no_start)
    if run is None:
        return
    result = fetch_reader_result(agent, run["run_id"], args.timeout, report, case)
    if result is None:
        return
    docs, why = case_documents(server, case, args.person)
    if why:
        report.line(False, case, "start", "documents", why)
        return
    files = {d["id"]: d["filename"] for d in docs}
    modules_now = {d["filename"]: d.get("module") for d in docs}
    accepted, why = case_facts(server, case, args.person)
    if why:
        report.line(False, case, "start", "facts", why)
        return
    report.note(case, f"Reader run {run['run_id']} finished; checking its answer")
    check_case(case, key, files, modules_now, result, accepted, report)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cases", nargs="*", help="cases to check (default: every key in samples/answers)")
    parser.add_argument("--server", default="http://127.0.0.1:8000", help="the PharmAlign server")
    parser.add_argument("--reader", default="http://127.0.0.1:8101", help="the Dossier Reader agent")
    parser.add_argument("--no-start", action="store_true", help="do not start a run: check the newest Reader run each case already has")
    parser.add_argument("--timeout", type=float, default=120, help="seconds to wait for each Reader run (default 120)")
    parser.add_argument("--answers", type=Path, default=HERE / "answers", help="the folder of answer keys")
    parser.add_argument("--person", default="p-alex", help="the case officer to act as (simulation sign-in)")
    parser.add_argument("--admin", default="p-jordan", help="the platform admin who may list agent runs")
    args = parser.parse_args()

    cases = args.cases or sorted(p.stem for p in args.answers.glob("CASE-*.json"))
    server = Api(args.server, {})
    agent = Api(args.reader, {"X-PharmAlign-Contract": CONTRACT})
    status, body = server.call("GET", "/health")
    if status != 200:
        print(f"FAIL  the server at {args.server} did not answer /health: HTTP {status} {problem(body)}")
        return 1
    status, body = agent.call("GET", "/health")
    if status != 200:
        print(f"FAIL  the Dossier Reader at {args.reader} did not answer /health: HTTP {status} {problem(body)}")
        return 1

    report = Report()
    for case in cases:
        print(f"\n== {case}")
        run_case(case, args, server, agent, report)
    print(f"\n{report.passed} passed, {report.failed} failed")
    print("ALL PASS" if report.failed == 0 and report.passed else "FAILED")
    return 0 if report.failed == 0 and report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
