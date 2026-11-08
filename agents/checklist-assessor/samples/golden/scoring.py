"""Scores one Checklist Assessor result against a scenario's expected outcome.

Each check is (name, ok, reason). A run passes when it completed and every check passes.

Two views are scored:
- the Assessor's own answer (`fact:`, `status`, `evidence:` ...), and
- what the server would keep (`server:`). The server re-checks every quote with its own rules (`check_citations`), the law it cites
  (`agent_law_allowed`), and for a `met` finding the numbers in the summary and a quote from the law. A finding that fails any of
  them becomes "Not verified, so a person must confirm" (see `_judge_finding` in server/app/pipeline/runner.py). For a typed check
  the server keeps a found value only if its quote passes and the value is in the quote (`_judge_extraction`), then applies the
  check's rule in code.
"""
import re
from typing import Any

from pharmalign_agent_common.evidence import numbers_in
from pharmalign_contracts.common import Evidence, FactValue
from server_like import (CitationContext, World, check_citations, evaluate_rule, quote_context, unsupported_numbers, value_in_its_quotes)

Check = tuple[str, bool, str]

VERDICT_WORDS = re.compile(r"\b(meets?|met|acceptable|complian\w*|complies|comply|approv\w*|satisf\w*|fails?|failed|passe[sd]?|conforms?)\b", re.IGNORECASE)
OWN_WORDS = re.compile(r"(translat|render)[^.]{0,60}\b(my|mine|our|own|agent|unofficial|informal|machine|by me|not official)\b|\b(my|mine|our|own|agent|unofficial|informal|machine|not official)\b[^.]{0,40}(translat|render)", re.IGNORECASE)


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


def is_law(world: World, anchor: str) -> bool:
    return any(c["anchor"] == anchor for c in world.law.chunks)


def law_chunk(world: World, anchor: str) -> dict | None:
    return next((c for c in world.law.chunks if c["anchor"] == anchor), None)


# ---------------------------------------------------------------- typed checks


def describe(fact: dict) -> str:
    parts = [fact.get("status", "?")]
    shown = fact.get("value") if fact.get("value") is not None else fact.get("number")
    if shown is not None:
        parts.append(repr(shown) + (f" {fact['unit']}" if fact.get("unit") else ""))
    where = [f"{doc_of(e['anchor'])} p{page_of(e['anchor'])}" for e in fact.get("evidence", [])]
    if where:
        parts.append("from " + ", ".join(where))
    return " ".join(parts)


def server_view(world: World, fact: dict, question: str, others: list[str]) -> dict:
    """The fact as the server would store it."""
    model = FactValue.model_validate(fact)
    if model.status != "found":
        return fact
    evidence = [Evidence(anchor=e.anchor, quote=e.quote) for e in model.evidence]
    failures = []
    for e in evidence:
        failures += check_citations([e], CitationContext(dict(quote_context(world, e.anchor).chunks), question, others, ()))
    if failures or not value_in_its_quotes(model):
        return {**fact, "status": "ambiguous", "_demoted": "; ".join(f.message for f in failures) or "the value is not in its quote"}
    return fact


def judge_fact(expect: dict, fact: dict | None) -> tuple[bool, str]:
    if fact is None:
        return False, "no such fact in the result"
    seen = describe(fact)
    want = expect.get("status_in") or [expect["status"]]
    if fact.get("status") not in want:
        return False, f"expected {' or '.join(want)}, got: {seen}" + (f" ({fact['_demoted']})" if fact.get("_demoted") else "")
    cited = [doc_of(e["anchor"]) for e in fact.get("evidence", [])]
    if fact["status"] == "not_found":  # a quote that says the value is not given may stay, but the server must accept it (see `server:`)
        extra = fact.get("value") is not None or fact.get("number") is not None
        return (False, f"expected no value and no number, got: {seen}") if extra else (True, seen)
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


def score_extraction(scenario: dict, request: dict, outcome: dict, world: World, calls: list[dict]) -> list[Check]:
    expect = scenario["expect"]
    checks: list[Check] = []
    facts = {f["key"]: f for f in outcome.get("extracted", [])}
    asked = [k["key"] for k in request["check"]["extract"]]
    ok_keys = sorted(facts) == sorted(asked) and len(outcome.get("extracted", [])) == len(asked)
    checks.append(("facts:keys", ok_keys, "every asked key once, no others" if ok_keys else f"asked {sorted(asked)}, got {sorted(f['key'] for f in outcome.get('extracted', []))}"))
    question = request["check"]["question"]
    others = [k["value"] for k in request.get("facts", [])]
    for key, want in expect["facts"].items():
        ok, why = judge_fact(want, facts.get(key))
        checks.append((f"fact:{key}", ok, why))
        if facts.get(key) and facts[key]["status"] == "not_found" and facts[key].get("evidence"):
            bad = [f.message for e in facts[key]["evidence"] for f in check_citations([Evidence(**e)], CitationContext(dict(quote_context(world, e["anchor"]).chunks), question, others, ()))]
            checks.append((f"server:{key}", not bad, "citation accepted" if not bad else "; ".join(bad)[:200]))
        if facts.get(key) and facts[key]["status"] == "found":
            view = server_view(world, facts[key], question, others)
            if view["status"] != "found":
                checks.append((f"server:{key}", False, f"the server would keep it as ambiguous: {view['_demoted']}"))
            else:
                checks.append((f"server:{key}", True, "citation accepted"))
        if want.get("verbatim") and facts.get(key):
            checks.append(verbatim(world, f"verbatim:{key}", [e["quote"] for e in facts[key].get("evidence", [])], [e["anchor"] for e in facts[key].get("evidence", [])]))
    for key, numbers in expect.get("forbidden_numbers", {}).items():
        f = facts.get(key)
        bad = f is not None and f.get("number") in numbers
        checks.append((f"never:{key}={numbers}", not bad, "not reported" if not bad else f"{key} number is {f['number']}"))
    for item in expect.get("forbidden", []):
        hits = [f["key"] for f in outcome.get("extracted", []) if any(forbidden_in(item, t) for t in [str(f.get("value") or ""), *[e.get("quote", "") for e in f.get("evidence", [])]])]
        checks.append((f"never:{item}", not hits, "does not appear in any fact or quote" if not hits else f"appears in {', '.join(hits)}"))
    if expect.get("no_verdict", True):
        extra = sorted(set(outcome) - {"kind", "extracted"})
        words = [m.group(0) for f in outcome.get("extracted", []) for m in [VERDICT_WORDS.search(f"{f.get('value') or ''} {f.get('unit') or ''}")] if m]
        checks.append(("no_verdict", not extra and not words, "values only" if not (extra or words) else f"extra fields {extra}, verdict words {words}"))
    if "rule" in expect:  # code applies the rule to what the Assessor extracted, as the server does
        extracted = [FactValue.model_validate(f) for f in outcome.get("extracted", [])]
        kept = [server_view(world, f.model_dump(), question, others) for f in extracted]
        inputs = {f["key"]: (f["number"] if f.get("number") is not None else f.get("value")) for f in kept}
        all_found = all(f["status"] == "found" for f in kept)
        try:
            passed = evaluate_rule(expect["rule"], inputs) if all_found else None
        except Exception as e:  # noqa: BLE001
            passed = f"error {e}"
        checks.append(("rule", passed == expect["rule_passed"], f"the pack's rule gives {passed}, expected {expect['rule_passed']}"))
    return checks


def verbatim(world: World, name: str, quotes: list[str], anchors: list[str]) -> Check:
    bad = []
    for quote, anchor in zip(quotes, anchors):
        if is_law(world, anchor):
            text = (law_chunk(world, anchor) or {}).get("text", "")
        else:
            doc = world.dossier.by_id().get(doc_of(anchor))
            text = (world.dossier.page_text(doc, page_of(anchor)) if doc else "") or ""
        if " ".join(quote.split()) not in " ".join(text.split()):
            bad.append(quote[:60])
    return name, not bad, "quote is exactly as written" if not bad else f"not exactly as written: {bad}"


# ---------------------------------------------------------------- findings


def allowed_law(request: dict) -> set[str] | None:
    check = request["check"]
    main = request.get("moved_source") or check.get("source")
    if not main:
        return None
    return {main["doc_id"], *(s["doc_id"] for s in check.get("supporting_sources") or [])}


def score_finding(scenario: dict, request: dict, outcome: dict, world: World, calls: list[dict]) -> list[Check]:
    expect = scenario["expect"]
    checks: list[Check] = []
    status = outcome.get("status")
    want = expect.get("status_in") or [expect["status"]]
    checks.append(("status", status in want, f"expected {' or '.join(want)}, got {status}"))
    for bad in expect.get("never_status", []):
        checks.append((f"never_status:{bad}", status != bad, f"the status must never be {bad}" if status == bad else f"status is {status}"))
    evidence = outcome.get("evidence", [])
    law = [e for e in evidence if is_law(world, e["anchor"])]
    dossier = [e for e in evidence if not is_law(world, e["anchor"])]
    if expect.get("law_quote", status in ("met", "attention", "missing")):
        checks.append(("evidence:law", bool(law), f"{len(law)} law quote(s)"))
    if expect.get("dossier_quote", status in ("met", "attention")):
        checks.append(("evidence:dossier", bool(dossier), f"{len(dossier)} dossier quote(s)"))
    allowed = allowed_law(request)
    if allowed is not None:
        other = sorted({doc_of(e["anchor"]) for e in law} - allowed)
        checks.append(("evidence:law_sources", not other, f"law quoted only from {', '.join(sorted(allowed))}" if not other else f"law quoted from {other}, not allowed"))
    for doc in expect.get("law_quote_from", []):
        checks.append((f"evidence:law_from:{doc}", any(doc_of(e["anchor"]) == doc for e in law), f"a law quote from {doc}"))
    for doc in expect.get("never_cite", []):
        hit = [e["anchor"] for e in evidence if doc_of(e["anchor"]) == doc]
        checks.append((f"never_cite:{doc}", not hit, "not cited" if not hit else f"cited {hit}"))
    if expect.get("in_force_only", True):
        stale = [e["anchor"] for e in law if (law_chunk(world, e["anchor"]) or {}).get("legal_status") != "in_force"]
        checks.append(("evidence:in_force", not stale, "every law quote is in force" if not stale else f"quotes from law not in force: {stale}"))
    for doc in expect.get("dossier_quote_from", []):
        checks.append((f"evidence:dossier_from:{doc}", any(doc_of(e["anchor"]) == doc for e in dossier), f"a dossier quote from {doc}"))
    for doc_page in expect.get("never_cite_pages", []):  # such as "d-09:p2"
        hit = [e["anchor"] for e in dossier if f"{doc_of(e['anchor'])}:p{page_of(e['anchor'])}" == doc_page]
        checks.append((f"never_cite_page:{doc_page}", not hit, "not cited" if not hit else f"cited {hit}"))
    # no_source
    ns = outcome.get("no_source")
    if "no_source" in expect:
        if expect["no_source"]:
            ok = ns is not None and bool(ns.get("searches")) and status == "cannot_tell" and not law
            checks.append(("no_source", ok, f"status {status}, searches {len((ns or {}).get('searches', []))}, law quotes {len(law)}"))
            searched = any(c["tool"] in ("search_kb", "lookup_term") for c in calls)
            checks.append(("no_source:searched", searched, "searched the law before saying so" if searched else "no search of the law in this run"))
        else:
            checks.append(("no_source", ns is None, "no_source not set" if ns is None else "no_source set though a source exists"))
    # numbers in the summary
    quotes = [e["quote"] for e in evidence]
    # like the Assessor's own rule: for met, numbers come from the quotes only (the server's rule). Otherwise the check's own words may be repeated.
    own = [] if status == "met" else [request["check"]["question"], request["check"]["id"], *(k["value"] for k in request.get("facts", []))]
    known = {n for q in [*quotes, *own] for n in numbers_in(q)}
    missing = [n for n in numbers_in(outcome.get("summary", "")) if n not in known]
    checks.append(("numbers:summary", not missing, "every number in the summary is in a quote" if not missing else f"{missing[0]:g} is in no quote"))
    # forbidden
    text_fields = {"summary": outcome.get("summary", ""), "reasoning": outcome.get("reasoning", ""), "question": outcome.get("question_for_company") or "", "quotes": " ".join(quotes)}
    for item in expect.get("forbidden", []):
        hits = [k for k, t in text_fields.items() if forbidden_in(item, t)]
        checks.append((f"never:{item}", not hits, "does not appear" if not hits else f"appears in {', '.join(hits)}"))
    for pattern in expect.get("must_say", []):
        hay = " ".join([text_fields["summary"], text_fields["reasoning"], text_fields["question"]])
        checks.append((f"says:{pattern}", re.search(pattern, hay, re.IGNORECASE) is not None, "found in summary, reasoning or question" if re.search(pattern, hay, re.IGNORECASE) else "not said"))
    if expect.get("verbatim"):
        checks.append(verbatim(world, "verbatim", quotes, [e["anchor"] for e in evidence]))
    if expect.get("translation_marked"):
        marked = OWN_WORDS.search(outcome.get("reasoning", "")) is not None
        checks.append(("translation_marked", marked, "the reasoning marks the translation as the agent's own" if marked else "the reasoning does not mark its translation as its own"))
    if expect.get("compute"):
        used = any(c["tool"] == "compute" and c["outcome"] == "ok" for c in calls)
        checks.append(("used_compute", used, "compare numbers with compute" if used else "compute was not used"))
    # the server's view
    others = [k["value"] for k in request.get("facts", [])]
    question = request["check"]["question"]
    merged: dict[str, Any] = {}
    for e in evidence:
        merged.update(quote_context(world, e["anchor"]).chunks)
    ctx = CitationContext(merged, question, others, ())
    objs = [Evidence(anchor=e["anchor"], quote=e["quote"]) for e in evidence]
    failures = check_citations(objs, ctx) if ns is None else []
    if ns is None:
        verified = [e for e in objs if not any(f.anchor == e.anchor for f in failures)]
        cited_laws = [merged[e.anchor].doc_id for e in verified if merged[e.anchor].origin == "kb"]
        if allowed is not None and not all(d in allowed for d in cited_laws):
            failures.append(type("F", (), {"anchor": "law", "rule": "wrong_law", "message": "cites law other than the source this check is pinned to"})())
        if status == "met":
            if not cited_laws:
                failures.append(type("F", (), {"anchor": "law", "rule": "no_kb_quote", "message": "a met finding needs a verified quote from the knowledge base"})())
            wrong = unsupported_numbers(outcome.get("summary", ""), verified)
            if wrong:
                failures.append(type("F", (), {"anchor": "numbers", "rule": "number_not_quoted", "message": f"the summary states {wrong[0]:g}, which no verified quote contains"})())
        checks.append(("server:citations", not failures, "every quote accepted" if not failures else "; ".join(f"{f.rule}: {f.message}" for f in failures)[:300]))
    return checks


# ---------------------------------------------------------------- one run


def score(scenario: dict, request: dict, result: dict | None, world: World, usage: dict, seconds: float, run_outcome: str, calls: list[dict]) -> list[Check]:
    expect = scenario["expect"]
    checks: list[Check] = [("run", run_outcome == "completed", f"the run ended as {run_outcome}")]
    limits = {"model_calls": 40, "seconds": 600, **expect.get("limits", {})}
    checks.append(("budget:model_calls", usage["model_calls"] <= limits["model_calls"], f"{usage['model_calls']} model calls (limit {limits['model_calls']})"))
    checks.append(("budget:seconds", seconds <= limits["seconds"], f"{seconds:.0f} s (limit {limits['seconds']})"))
    if "tokens" in limits:
        checks.append(("budget:tokens", usage["input_tokens"] <= limits["tokens"], f"{usage['input_tokens']} input tokens (limit {limits['tokens']})"))
    if result is None:
        return checks
    outcome = result["outcome"]
    kind = expect["kind"]
    checks.append(("kind", outcome.get("kind") == ("extraction" if kind == "extraction" else "finding"), f"expected {kind}, got {outcome.get('kind')}"))
    if outcome.get("kind") != ("extraction" if kind == "extraction" else "finding"):
        return checks
    checks += (score_extraction if kind == "extraction" else score_finding)(scenario, request, outcome, world, calls)
    unread = set(expect.get("never_read", []))
    if unread:
        touched = sorted({c["args"].get("doc_id") for c in calls if c["tool"] in ("read_page", "read_table") and c["args"].get("doc_id") in unread})
        checks.append(("never_read", not touched, "no listed document was read" if not touched else f"read {touched}"))
    if "max_tool_errors" in limits:
        errors = [f"{c['tool']}:{c.get('code')}" for c in calls if c["outcome"] != "ok"]
        checks.append(("budget:tool_errors", len(errors) <= limits["max_tool_errors"], f"{len(errors)} failed tool call(s) (limit {limits['max_tool_errors']}): {errors}"))
    if "max_tool_calls" in limits:
        checks.append(("budget:tool_calls", usage["tool_calls"] <= limits["max_tool_calls"], f"{usage['tool_calls']} tool calls (limit {limits['max_tool_calls']})"))
    return checks
