"""Scores one Reader result against a scenario's expected outcome.

Each check is (name, ok, reason). A run passes when it completed and every check passes.

Two views of each fact are scored:
- the Reader's own answer (`fact:`), and
- what the server would keep (`server:`): the server re-checks every citation with its own rules and keeps a found fact whose
  quote fails as `ambiguous` (see `_store_facts` in server/app/pipeline/runner.py). A quote that is too short, repeated on
  another page of the same file, or taken from a suspect page fails there.
"""
import re
from typing import Any

from server_like import Dossier, check_citations, quote_context, value_in_its_quotes
from pharmalign_contracts.common import Evidence, FactValue

Check = tuple[str, bool, str]


def squash(text: Any) -> str:
    return re.sub(r"[\W_]+", "", str(text or "").casefold())


def same_unit(a: Any, b: Any) -> bool:
    clean = lambda u: str(u or "").replace("°", "").strip().casefold()  # noqa: E731 (°C counts as C)
    return clean(a) == clean(b)


def forbidden_in(item: str, text: str) -> bool:
    return re.search(r"(?<![\w.])" + re.escape(item) + r"(?![\w]|\.\d)", text, re.IGNORECASE) is not None


def doc_of(anchor: str) -> str:
    return anchor.split(":")[0]


def page_of(anchor: str) -> int:
    return int(anchor.rsplit(":p", 1)[-1])


def describe(fact: dict) -> str:
    parts = [fact.get("status", "?")]
    shown = fact.get("value") if fact.get("value") is not None else fact.get("number")
    if shown is not None:
        parts.append(repr(shown) + (f" {fact['unit']}" if fact.get("unit") else ""))
    where = [f"{doc_of(e['anchor'])} p{page_of(e['anchor'])}" for e in fact.get("evidence", [])]
    if where:
        parts.append("from " + ", ".join(where))
    return " ".join(parts)


def server_view(dossier: Dossier, fact: dict) -> dict:
    """The fact as the server would store it."""
    model = FactValue.model_validate(fact)
    if model.status != "found":
        return fact
    evidence = [Evidence(anchor=e.anchor, quote=e.quote) for e in model.evidence]
    failures = []
    for e in evidence:
        failures += check_citations([e], quote_context(dossier, e.anchor))
    if failures or not value_in_its_quotes(model):
        return {**fact, "status": "ambiguous", "_demoted": "; ".join(f.message for f in failures) or "the value is not in its quote"}
    return fact


def judge_fact(expect: dict, fact: dict | None, dossier: Dossier) -> tuple[bool, str]:
    if fact is None:
        return False, "no such fact in the result"
    seen = describe(fact)
    want = expect.get("status_in") or [expect["status"]]
    if fact.get("status") not in want:
        return False, f"expected {' or '.join(want)}, got: {seen}" + (f" ({fact['_demoted']})" if fact.get("_demoted") else "")
    cited = [doc_of(e["anchor"]) for e in fact.get("evidence", [])]
    if fact["status"] == "not_found":
        extra = fact.get("value") is not None or fact.get("number") is not None or cited
        return (False, f"expected no value and no evidence, got: {seen}") if extra else (True, seen)
    if fact["status"] == "ambiguous":
        if expect.get("no_pick", True) and fact.get("number") is not None:
            return False, f"picked a side (number {fact['number']}): {seen}"
        sides = expect.get("sides") or {}
        if fact.get("value") is not None and not all(s.lower() in str(fact["value"]).lower() for s in sides.values()):
            return False, f"picked a side: {seen}"
    elif not cited:
        return False, f"expected evidence, got: {seen}"
    if "number" in expect and fact.get("number") != expect["number"]:
        return False, f"expected number {expect['number']}, got: {seen}"
    if "number_in" in expect and fact.get("number") not in expect["number_in"]:
        return False, f"expected number in {expect['number_in']}, got: {seen}"
    if "unit" in expect and not same_unit(fact.get("unit"), expect["unit"]):
        return False, f"expected unit {expect['unit']}, got: {seen}"
    if "unit_in" in expect and not any(same_unit(fact.get("unit"), u) for u in expect["unit_in"]):
        return False, f"expected unit in {expect['unit_in']}, got: {seen}"
    if "unit_not" in expect and same_unit(fact.get("unit"), expect["unit_not"]):
        return False, f"unit must not be {expect['unit_not']}, got: {seen}"
    if "value_contains" in expect and squash(expect["value_contains"]) not in squash(fact.get("value")):
        return False, f"expected value containing {expect['value_contains']!r}, got: {seen}"
    if expect.get("evidence_in") and not set(cited) & set(expect["evidence_in"]):
        return False, f"expected a quote from {' or '.join(expect['evidence_in'])}, got: {seen}"
    if expect.get("evidence_only_in") and not set(cited) <= set(expect["evidence_only_in"]):
        return False, f"expected quotes only from {', '.join(expect['evidence_only_in'])}, got: {seen}"
    missing = [x for x in expect.get("evidence_from", []) if x not in cited]
    if missing:
        return False, f"expected a quote from each side, missing {', '.join(missing)}; got: {seen}"
    return True, seen


def score(scenario: dict, result: dict | None, dossier: Dossier, usage: dict, seconds: float, outcome: str, calls: list[dict]) -> list[Check]:
    expect = scenario["expect"]
    checks: list[Check] = [("run", outcome == "completed", f"the run ended as {outcome}")]
    limits = {"model_calls": 40, "seconds": 300, **expect.get("limits", {})}
    checks.append(("budget:model_calls", usage["model_calls"] <= limits["model_calls"], f"{usage['model_calls']} model calls (limit {limits['model_calls']})"))
    checks.append(("budget:seconds", seconds <= limits["seconds"], f"{seconds:.0f} s (limit {limits['seconds']})"))
    if result is None:
        return checks

    facts = {f["key"]: f for f in result.get("facts", [])}
    asked = {k["key"] for k in scenario["request"]["fact_keys"]}
    checks.append(("facts:keys", set(facts) == asked, "every asked key once, no others" if set(facts) == asked else f"asked {sorted(asked)}, got {sorted(facts)}"))
    for key, want in expect["facts"].items():
        ok, why = judge_fact(want, facts.get(key), dossier)
        checks.append((f"fact:{key}", ok, why))
        if facts.get(key) and facts[key]["status"] == "found":
            view = server_view(dossier, facts[key])
            if view["status"] != "found":
                checks.append((f"server:{key}", False, f"the server would keep it as ambiguous: {view['_demoted']}"))
            else:
                checks.append((f"server:{key}", True, "citation accepted"))
        if want.get("verbatim") and facts.get(key):
            bad = []
            for e in facts[key].get("evidence", []):
                doc = dossier.by_id()[doc_of(e["anchor"])]
                text = dossier.page_text(doc, page_of(e["anchor"])) or ""
                if " ".join(e["quote"].split()) not in " ".join(text.split()):
                    bad.append(e["quote"][:60])
            checks.append((f"verbatim:{key}", not bad, "quote is exactly as written" if not bad else f"not exactly as written: {bad}"))

    entries = {e["doc_id"]: e for e in result.get("document_map", [])}
    for doc_id, module in expect.get("document_map", {}).items():
        entry = entries.get(doc_id)
        if entry is None:
            checks.append((f"map:{doc_id}", False, f"expected module {module}, but the file map has no entry"))
        else:
            checks.append((f"map:{doc_id}", entry.get("module") == module, f"expected module {module}, got {entry.get('module')}"))
    if expect.get("map_only"):
        extra = sorted(set(entries) - set(expect["document_map"]))
        checks.append(("map:only", not extra, "only the changed documents" if not extra else f"also mapped {extra}"))

    issues = result.get("issues", [])
    matched: set[int] = set()
    for want in expect.get("issues", {}).get("required", []):
        found = next((i for i, x in enumerate(issues) if x["doc_id"] == want["doc_id"] and x["kind"] == want["kind"]
                      and ("page" not in want or x.get("page") in (None, want["page"]))), None)
        if found is not None:
            matched.add(found)
        checks.append((f"issue:{want['kind']}:{want['doc_id']}", found is not None, "reported" if found is not None else "missing"))
    allowed = expect.get("issues", {}).get("allowed", [])
    for i, x in enumerate(issues):
        if i in matched:
            continue
        ok = any(x["doc_id"] == a["doc_id"] and x["kind"] == a["kind"] for a in allowed)
        if x["kind"] == "looks_incomplete":  # the synthetic files are short extracts, so this note is tolerated everywhere
            checks.append((f"issue:extra:{x['kind']}:{x['doc_id']}", True, f"tolerated: {x.get('detail', '')[:100]}"))
            continue
        checks.append((f"issue:extra:{x['kind']}:{x['doc_id']}", ok, "an allowed extra" if ok else f"not expected: {x.get('detail', '')[:120]}"))

    for item in expect.get("forbidden", []):
        hits = []
        for f in result.get("facts", []):
            texts = [str(f.get("value") or ""), *[e.get("quote", "") for e in f.get("evidence", [])]]
            if any(forbidden_in(item, t) for t in texts):
                hits.append(f["key"])
        checks.append((f"never:{item}", not hits, "does not appear in any fact or quote" if not hits else f"appears in {', '.join(hits)}"))
    for key, numbers in expect.get("forbidden_numbers", {}).items():
        f = facts.get(key)
        bad = f is not None and f.get("number") in numbers
        checks.append((f"never:{key}={numbers}", not bad, "not reported" if not bad else f"{key} number is {f['number']}"))

    unread = set(expect.get("never_read", []))
    if unread:
        touched = sorted({c["args"].get("doc_id") for c in calls if c["tool"] in ("read_page", "read_table") and c["args"].get("doc_id") in unread})
        checks.append(("never_read", not touched, "no unchanged document was read" if not touched else f"read {touched}"))
    return checks
