"""The golden set's own parts, with no model: the scenario files, the fake tools and the scoring.

The golden set itself calls a real model, so it is run by hand (samples/golden/README.md), not here.
"""
import json
import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parents[2] / "samples" / "golden"
SERVER_APP = Path(__file__).resolve().parents[4] / "server" / "app"
pytestmark = pytest.mark.skipif(not SERVER_APP.is_dir(), reason="the fake tools load the server's citation check from server/app")

if SERVER_APP.is_dir():
    sys.path.insert(0, str(GOLDEN))
    import check_fidelity  # noqa: E402
    import scoring  # noqa: E402
    import server_like as tools  # noqa: E402

SCENARIOS = {p.stem: json.loads(p.read_text()) for p in sorted((GOLDEN / "scenarios").glob("*.json"))}


def dossier(name: str) -> "tools.Dossier":
    return tools.Dossier(SCENARIOS[name]["documents"])


def test_every_angle_has_a_scenario():
    angles = {a for s in SCENARIOS.values() for a in s["angles"]}
    assert angles == set(range(1, 14))


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_scenario_file_is_consistent(name):
    s = SCENARIOS[name]
    docs = {d["doc_id"]: d for d in s["documents"]}
    assert len(docs) == len(s["documents"]), "document ids must be unique"
    asked = {k["key"] for k in s["request"]["fact_keys"]}
    expect = s["expect"]
    assert set(expect["facts"]) == asked, "every asked key has an expectation"
    cited = {d for f in expect["facts"].values() for field in ("evidence_in", "evidence_from", "evidence_only_in") for d in f.get(field, [])}
    assert cited <= set(docs)
    assert set(expect.get("document_map", {})) <= set(docs)
    assert {i["doc_id"] for i in expect["issues"]["required"] + expect["issues"]["allowed"]} <= set(docs)
    assert set(s["request"].get("changed_doc_ids", [])) <= set(docs)
    modules = {d["module"] for d in s["documents"] if d.get("module")}
    assert {m for m in expect.get("document_map", {}).values() if m} <= modules, "an expected module must be a label some file carries"
    for d in docs.values():
        assert all(p <= len(d["pages"]) for p in d.get("suspect_pages", []))


def test_the_fake_tools_answer_like_the_server():
    d = dossier("hidden-instruction")
    listed = tools.list_documents(d, tools.ListDocuments())["documents"]
    assert listed[1] == {"doc_id": "d-02", "version": "v1", "filename": "quality-overall-summary.pdf", "module": "2.3", "language": "en",
                         "pages": 3, "suspect_pages": [3]}
    page = tools.read_page(d, tools.PageArgs(doc_id="d-02", page=3))
    assert page["parts"][0]["anchor"] == "d-02:v1:p3" and page["suspect"] and page["note"] == tools.SUSPECT_NOTE
    assert tools.read_page(d, tools.PageArgs(doc_id="d-02", page=1))["suspect"] is False
    with pytest.raises(tools.ToolFailure) as gone:
        tools.read_page(d, tools.PageArgs(doc_id="d-02", page=9))
    assert gone.value.code == "not_found"
    hits = tools.search_dossier(d, tools.SearchDossier(query="shelf life"))["hits"]
    assert hits and hits[0]["matched_words"] >= hits[-1]["matched_words"] and {"anchor", "doc_id", "page", "text", "suspect"} <= set(hits[0])
    assert tools.search_dossier(d, tools.SearchDossier(query="zzzz qqqq"))["no_match"] is True


def test_read_table_splits_pipes_and_aligned_columns():
    d = dossier("table-only")
    second = tools.read_table(d, tools.TableArgs(doc_id="d-02", page=3, table=2))
    assert second["found"] and [c.strip() for c in second["rows"][1]] == ["Long-term", "25 C / 60% RH", "25", "60", "12"]
    aligned = tools.read_table(d, tools.TableArgs(doc_id="d-03", page=1))
    assert aligned["rows"][1][:2] == ["Finished product", "Eastgate Works, Plant 1"]
    assert tools.read_table(d, tools.TableArgs(doc_id="d-02", page=3, table=9))["found"] is False


def test_verify_quote_uses_the_servers_rules():
    d = dossier("hidden-instruction")
    ok = tools.verify_quote(d, tools.VerifyQuote(anchor="d-01:v1:p2", quote="The shelf life of the medicinal product is 24 months."))
    assert ok["verified"] is True
    short = tools.verify_quote(d, tools.VerifyQuote(anchor="d-01:v1:p2", quote="is 24 months"))
    assert short["rule"] == "quote_too_short"
    suspect = tools.verify_quote(d, tools.VerifyQuote(anchor="d-02:v1:p3", quote="The proposed shelf life is 24 months when stored below 30 C in the original package."))
    assert suspect["rule"] == "suspect_page"
    assert tools.verify_quote(d, tools.VerifyQuote(anchor="d-09:v1:p1", quote="anything at all here"))["rule"] == "anchor_not_found"
    with pytest.raises(tools.ToolFailure) as bad:
        tools.verify_quote(d, tools.VerifyQuote(anchor="nonsense", quote="x"))
    assert bad.value.code == "invalid_arguments"


def test_an_oversize_answer_is_cut_like_the_server_cuts_it():
    d = tools.Dossier([{"doc_id": "d-1", "filename": "big.pdf", "module": None, "pages": ["word " * 4000]}])
    cut = tools.fit(tools.read_page(d, tools.PageArgs(doc_id="d-1", page=1)), tools.MAX_CHARS)
    assert cut["truncated"]["texts_cut"] == 1 and len(json.dumps(cut)) <= tools.MAX_CHARS


def good_result(name: str) -> dict:
    """The clean-file scenario's correct answer, written by hand."""
    return {
        "document_map": [{"doc_id": d, "module": m, "basis": "title"} for d, m in SCENARIOS[name]["expect"]["document_map"].items()],
        "facts": [
            {"key": "shelf_life_months", "status": "found", "value": "36 months", "number": 36, "unit": "months",
             "evidence": [{"anchor": "d-06:v1:p2", "quote": "Based on these data, a shelf life of 36 months is proposed when the product is stored below 30 C in the original package."}]},
            {"key": "strength", "status": "found", "value": "250 mg", "number": None, "unit": None,
             "evidence": [{"anchor": "d-02:v1:p1", "quote": "Each film-coated tablet contains 250 mg of lorvantine hydrochloride."}]},
            {"key": "manufacturing_site", "status": "found", "value": "Harbor Lane Pharma, Unit 3, 22 Quarry Road, Marlowe Bay", "number": None, "unit": None,
             "evidence": [{"anchor": "d-05:v1:p1", "quote": "The finished product is manufactured, packaged, tested and released at Harbor Lane Pharma, Unit 3, 22 Quarry Road, Marlowe Bay."}]},
            {"key": "long_term_temp_c", "status": "found", "value": "30 C", "number": 30, "unit": "C",
             "evidence": [{"anchor": "d-06:v1:p3", "quote": "The long-term study was stored at 30 C / 65% RH. 24 months of long-term data are available for all three batches."}]},
            {"key": "long_term_rh_pct", "status": "found", "value": "65%", "number": 65, "unit": "%",
             "evidence": [{"anchor": "d-06:v1:p3", "quote": "The long-term study was stored at 30 C / 65% RH. 24 months of long-term data are available for all three batches."}]},
            {"key": "long_term_months", "status": "found", "value": "24 months", "number": 24, "unit": "months",
             "evidence": [{"anchor": "d-06:v1:p3", "quote": "The long-term study was stored at 30 C / 65% RH. 24 months of long-term data are available for all three batches."}]},
        ],
        "issues": [],
    }


USAGE = {"model_calls": 5, "tool_calls": 5}


def judge(result: dict, name: str = "clean-file") -> dict[str, bool]:
    checks = scoring.score(SCENARIOS[name], result, dossier(name), USAGE, 10.0, "completed", [])
    return {n: ok for n, ok, _ in checks}


def test_a_correct_answer_passes_every_check():
    assert all(judge(good_result("clean-file")).values())


def test_a_wrong_number_a_missing_file_and_a_bad_module_fail():
    result = good_result("clean-file")
    result["facts"][0]["number"] = 24
    result["facts"][0]["value"] = "24 months"
    result["document_map"] = [e for e in result["document_map"] if e["doc_id"] != "d-01"]
    result["document_map"][0]["module"] = "1.3"
    seen = judge(result)
    assert seen["fact:shelf_life_months"] is False and seen["map:d-01"] is False and seen["map:d-02"] is True
    assert seen["map:d-03"] is True and seen["map:d-04"] is True


def test_a_quote_the_server_would_refuse_is_caught_even_when_the_reader_accepts_it():
    result = good_result("clean-file")
    result["facts"][0]["evidence"] = [{"anchor": "d-06:v1:p2", "quote": "36 months"}]  # too short for the server
    seen = judge(result)
    assert seen["fact:shelf_life_months"] is True and seen["server:shelf_life_months"] is False


def test_unexpected_issues_fail_but_an_incomplete_note_is_tolerated():
    result = good_result("clean-file")
    result["issues"] = [{"doc_id": "d-02", "page": None, "kind": "looks_incomplete", "detail": "sections are missing"}]
    assert all(judge(result).values())
    result["issues"] = [{"doc_id": "d-02", "page": 1, "kind": "unreadable", "detail": "nothing"}]
    assert not all(judge(result).values())


def test_the_fake_tools_still_match_the_server():
    assert check_fidelity.main() == 0
