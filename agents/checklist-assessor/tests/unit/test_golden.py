"""The golden set's own parts, with no model: the scenario files, the fake tools and the scoring.

The golden set itself calls a real model, so it is run by hand (samples/golden/README.md), not here.
"""
import json
import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parents[2] / "samples" / "golden"
SERVER_APP = Path(__file__).resolve().parents[4] / "server" / "app"
pytestmark = pytest.mark.skipif(not SERVER_APP.is_dir(), reason="the fake tools load the server's own files from server/app")

if SERVER_APP.is_dir():
    sys.path.insert(0, str(GOLDEN))
    import check_fidelity  # noqa: E402
    import run_golden  # noqa: E402
    import scoring  # noqa: E402
    import server_like as tools  # noqa: E402

SCENARIOS = {p.stem: json.loads(p.read_text()) for p in sorted((GOLDEN / "scenarios").glob("*.json"))}


def world(name: str) -> "tools.World":
    s = SCENARIOS[name]
    return run_golden.build_world(s, run_golden.build_request(s, "unit-test-1"))


def test_every_angle_has_a_scenario():
    angles = {a for s in SCENARIOS.values() for a in s["angles"]}
    assert angles == set(range(1, 18))


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_scenario_file_is_consistent(name):
    s = SCENARIOS[name]
    docs = {d["doc_id"]: d for d in s["documents"]}
    assert len(docs) == len(s["documents"]), "document ids must be unique"
    request = run_golden.build_request(s, "unit-test-1")  # a valid contract request
    expect = s["expect"]
    assert (request.check.method == "typed") == (expect["kind"] == "extraction")
    if expect["kind"] == "extraction":
        assert set(expect["facts"]) == {f.key for f in request.check.extract}, "every asked key has an expectation"
        cited = {d for f in expect["facts"].values() for field in ("evidence_in", "evidence_from", "evidence_only_in") for d in f.get(field, [])}
        assert cited <= set(docs)
    else:
        assert set(expect.get("dossier_quote_from", [])) <= set(docs)
        law_ids = {d["doc_id"] for d in s["law"]}
        assert set(expect.get("law_quote_from", [])) <= law_ids
        reach = tools.reach(request.model_dump(mode="json"))
        assert reach is None or set(reach) <= law_ids, "the check's law must be in the scenario"
    assert set(expect.get("never_read", [])) <= set(docs)
    for d in docs.values():
        assert all(p <= len(d["pages"]) for p in d.get("suspect_pages", []))
    for fact in s["case_facts"]:
        assert all(e["anchor"].split(":")[0] in docs for e in fact["evidence"])


def test_the_fake_tools_match_the_servers_source():
    assert check_fidelity.main() == 0


def test_a_check_with_a_source_reads_only_its_sources_and_a_moved_check_only_the_new_law():
    w = world("moved-source")
    assert w.documents == ("SYN-NEW-1", "SYN-DEF-1")
    with pytest.raises(tools.ToolFailure) as outside:
        tools.read_clause(w, tools.ReadClause(document_id="SYN-OLD-1", article="Storage statement"))
    assert outside.value.code == "outside_check_sources"
    with pytest.raises(tools.ToolFailure):
        tools.search_kb(w, tools.SearchKb(query="storage statement leaflet", document_ids=["SYN-OLD-1"]))
    hits = tools.search_kb(w, tools.SearchKb(query="storage statement leaflet below 25 C"))["hits"]
    assert hits and {h["document_id"] for h in hits} <= {"SYN-NEW-1", "SYN-DEF-1"}
    assert tools.get_doc_status(w, tools.DocStatus(document_id="SYN-OLD-1"))["in_force"] is True  # status is not law text: it is not scoped


def test_search_kb_returns_law_in_force_only_for_a_requirement_but_read_clause_shows_every_version():
    w = world("law-status")
    found = tools.search_kb(w, tools.SearchKb(query="long-term stability data 12 months submission"))
    assert {h["legal_status"] for h in found["hits"]} == {"in_force"} and found["purpose"] == "requirement"
    context = tools.search_kb(w, tools.SearchKb(query="long-term stability data 12 months submission", purpose="context", top_k=10))
    assert {h["legal_status"] for h in context["hits"]} == {"in_force", "superseded", "draft"}
    clauses = tools.read_clause(w, tools.ReadClause(document_id="SYN-STAB-2", article="3.2"))["clauses"]
    assert [c["document_version_id"] for c in clauses] == ["SYN-STAB-2:v1", "SYN-STAB-2:v2", "SYN-STAB-2:v3"]
    assert [c["usable_as_requirement"] for c in clauses] == [False, True, False]
    status = tools.get_doc_status(w, tools.DocStatus(document_id="SYN-STAB-2"))
    assert [v["legal_status"] for v in status["versions"]] == ["superseded", "in_force", "draft"] and status["in_force"] is True


def test_search_kb_says_no_source_found_with_the_searches_it_tried():
    w = world("no-source")
    found = tools.search_kb(w, tools.SearchKb(query="cold chain validated whole transport route"))
    assert found["no_source_found"] is True and found["hits"] == [] and found["searches_tried"][0]["candidates"] > 0
    assert tools.lookup_term(w, tools.LookupTerm(term="cold chain"))["no_source_found"] is True


def test_verify_quote_uses_the_servers_rules_for_law_and_dossier():
    w = world("law-status")
    old = w.law.anchor_of("SYN-STAB-2", "v1", "3.2")
    refused = tools.verify_quote(w, tools.VerifyQuote(anchor=old, quote="The applicant shall provide long-term stability data at 30 °C and 65% RH covering at least 6 months"))
    assert refused["verified"] is False and refused["rule"] == "not_in_force"
    current = w.law.anchor_of("SYN-STAB-2", "v2", "3.2")
    assert tools.verify_quote(w, tools.VerifyQuote(anchor=current, quote="The applicant shall provide long-term stability data at 30 °C and 65% RH covering at least 12 months"))["verified"] is True
    short = tools.verify_quote(w, tools.VerifyQuote(anchor="d-03:v1:p2", quote="30 °C"))
    assert short["verified"] is False and short["rule"] == "quote_too_short"


def test_the_small_tools_answer_like_the_server():
    w = world("hidden-instruction")
    page = tools.read_page(w, tools.PageArgs(doc_id="d-03", page=5))
    assert page["suspect"] is True and page["note"] == tools.SUSPECT_NOTE and page["parts"][0]["anchor"] == "d-03:v1:p5"
    assert tools.compute(w, tools.Compute(expression="(36 - 24) / 24 * 100"))["value"] == 50
    with pytest.raises(tools.ToolFailure) as bad:
        tools.compute(w, tools.Compute(expression="__import__('os')"))
    assert bad.value.code == "invalid_arguments"
    table = tools.read_table(world("typed-near-values"), tools.TableArgs(doc_id="d-03", page=2))
    assert table["found"] and table["rows"][1][0].strip() == "Long-term"
    given = tools.get_facts(world("typed-facts-given"), tools.GetFacts(keys=["long_term_months"]))
    assert [f["key"] for f in given["facts"]] == ["long_term_months"] and given["facts"][0]["evidence"][0]["anchor"] == "d-05:v1:p6"


# ---------------------------------------------------------------- the scoring, with answers made by hand

USAGE = {"model_calls": 3, "tool_calls": 5, "input_tokens": 10_000, "output_tokens": 100}
COMPUTE = [{"tool": "compute", "args": {}, "outcome": "ok"}, {"tool": "search_kb", "args": {}, "outcome": "ok"}]


def judge(name: str, outcome: dict, calls=COMPUTE):
    s = SCENARIOS[name]
    request = run_golden.build_request(s, "unit-test-1")
    return {n: ok for n, ok, _ in scoring.score(s, request.model_dump(mode="json"), {"outcome": outcome}, world(name), USAGE, 10, "completed", calls)}


def stability_finding(name: str, status: str, page_quote: str, summary: str) -> dict:
    law = world(name).law.anchor_of("SYN-STAB-1", "v1", "3.2")
    return {"kind": "finding", "status": status, "summary": summary, "reasoning": "The law is compared with the report.",
            "evidence": [{"anchor": law, "quote": "The applicant shall provide long-term stability data generated at 30 °C ± 2 °C and 65% RH ± 5% RH, covering at least 12 months at the time of submission, on at least three batches."},
                         {"anchor": "d-03:v1:p2", "quote": page_quote}]}


def test_a_good_met_finding_passes_every_check():
    answer = stability_finding("met-quality", "met", "Long-term studies: 30 °C ± 2 °C / 65% RH ± 5% RH. Data cover 18 months for all three batches.",
                               "Long-term data at 30 °C and 65% RH cover 18 months, above the 12 months required.")
    assert all(judge("met-quality", answer).values())


def test_met_on_a_near_miss_fails():
    answer = stability_finding("near-miss-conditions", "met", "Long-term studies: 25 °C ± 2 °C / 60% RH ± 5% RH. Data cover 12 months for all three batches.",
                               "Long-term data cover 12 months.")
    result = judge("near-miss-conditions", answer)
    assert result["never_status:met"] is False and result["status"] is False


def test_a_number_that_is_in_no_quote_and_a_quote_from_a_flagged_page_fail():
    answer = stability_finding("hidden-instruction", "attention", "Long-term data: 25 °C / 60% RH, 12 months.", "Data cover 36 months.")
    answer["evidence"][1]["anchor"] = "d-03:v1:p5"
    result = judge("hidden-instruction", answer)
    assert result["numbers:summary"] is False and result["never:36 months"] is False
    assert result["never_cite_page:d-03:p5"] is False and result["server:citations"] is False


def test_a_typed_answer_with_a_verdict_word_or_the_wrong_value_fails():
    quote = "Long-term studies: 30 °C ± 2 °C / 65% RH ± 5% RH. Data cover 12 months for all three batches."
    fact = lambda key, number, unit, value: {"key": key, "status": "found", "value": value, "number": number, "unit": unit, "evidence": [{"anchor": "d-03:v1:p2", "quote": quote}]}  # noqa: E731
    good = {"kind": "extraction", "extracted": [fact("long_term_temp_c", 30, "C", "30 °C"), fact("long_term_rh_pct", 65, "%", "65% RH"), fact("long_term_months", 12, "months", "12 months")]}
    assert all(judge("typed-clean", good).values())
    bad = {"kind": "extraction", "extracted": [fact("long_term_temp_c", 30, "C", "30 °C, which meets the rule"), fact("long_term_rh_pct", 65, "%", "65% RH"), fact("long_term_months", 18, "months", "12 months")]}
    result = judge("typed-clean", bad)
    assert result["no_verdict"] is False and result["fact:long_term_months"] is False
