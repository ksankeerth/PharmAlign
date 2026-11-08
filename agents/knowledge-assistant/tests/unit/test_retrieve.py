import asyncio
import json

import pytest
from pharmalign_agent_common import ToolLog

from app.chat.retrieve import Retrieved, home_countries, local_first_on, retrieve, searches_tried, widen


@pytest.fixture(autouse=True)
def whole_library(monkeypatch):
    """These tests are about passages, counts and labels, not scope: search the whole library at once, as before."""
    monkeypatch.setenv("PHARMALIGN_KA_LOCAL_FIRST", "off")


def hit(anchor: str, text: str, doc: str = "A-01") -> dict:
    return {"anchor": anchor, "text": text, "document_id": doc, "title": "T", "legal_status": "in_force", "article": "1",
            "score": 0.9, "issuer": "x"}


def reply(hits: list[dict], searched: int = 30) -> dict:
    """A tool result in the shape ADK records for an MCP call."""
    payload = {"no_source_found": not hits, "hits": hits, "searched": searched, "purpose": "requirement"}
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}



def fake(by_query: dict):
    seen: list[tuple[str, dict]] = []

    async def call_tool(name: str, args: dict) -> dict:
        seen.append((name, args))
        value = by_query[args["query"]]
        if isinstance(value, Exception):
            raise value
        return value

    call_tool.seen = seen  # type: ignore[attr-defined]
    return call_tool


async def test_one_search_per_part_runs_together_and_records_the_results_in_the_log():
    arrived = {"a": asyncio.Event(), "b": asyncio.Event()}

    async def call_tool(name, args):  # each search waits for the other: they only finish if they run together
        mine, other = ("a", "b") if args["query"] == "qa" else ("b", "a")
        arrived[mine].set()
        await asyncio.wait_for(arrived[other].wait(), 2)
        return reply([hit(f"D-{mine}:v1:c1", f"text {mine}", f"D-{mine}")])

    log = ToolLog()
    got = await retrieve(["qa", "qb"], False, call_tool, log, "qa")
    assert [p["anchor"] for p in got.passages] == ["D-a:v1:c1", "D-b:v1:c1"]
    assert [(c.tool, c.args["purpose"], c.args["top_k"]) for c in log.calls] == [("search_kb", "requirement", 10)] * 2
    assert log.returned("D-a:v1:c1") and log.returned("D-b:v1:c1")


async def test_history_wanted_searches_with_context():
    call_tool = fake({"q": reply([hit("A:v1:c1", "t")])})
    await retrieve(["q"], True, call_tool, ToolLog())
    assert call_tool.seen == [("search_kb", {"query": "q", "purpose": "context", "top_k": 10})]


async def test_passages_with_identical_text_are_kept_once_in_part_order():
    call_tool = fake({
        "q1": reply([hit("A:v1:c1", "same"), hit("A:v1:c2", "other")]),
        "q2": reply([hit("B:v1:c9", "same", "B"), hit("B:v1:c3", "new", "B")]),
    })
    got = await retrieve(["q1", "q2"], False, call_tool, ToolLog())
    assert [p["anchor"] for p in got.passages] == ["A:v1:c1", "A:v1:c2", "B:v1:c3"]
    assert set(got.passages[0]) == {"anchor", "document_id", "title", "legal_status", "text", "article", "jurisdiction", "issuer"}
    assert got.searches[1]["documents"] == ["B"] and got.searches[1]["hits"] == 2 and got.searches[0]["searched"] == 30


async def test_nothing_found_means_every_search_had_no_hits():
    call_tool = fake({"q1": reply([]), "q2": reply([hit("A:v1:c1", "t")])})
    got = await retrieve(["q1", "q2"], False, call_tool, ToolLog())
    assert not got.nothing_found
    assert (await retrieve(["q1"], False, call_tool, ToolLog())).nothing_found


async def test_a_failed_call_counts_as_a_search_with_no_hits_marked_failed():
    error = {"content": [{"type": "text", "text": "boom"}], "isError": True}
    call_tool = fake({"q1": RuntimeError("down"), "q2": error, "q3": reply([hit("A:v1:c1", "t")])})
    got = await retrieve(["q1", "q2", "q3"], False, call_tool, ToolLog())
    assert [s["failed"] for s in got.searches] == [True, True, False] and [s["hits"] for s in got.searches] == [0, 0, 1]
    assert not got.all_failed and not got.nothing_found
    both = await retrieve(["q1", "q2"], False, call_tool, ToolLog())
    assert both.all_failed and both.nothing_found


def test_searches_tried_lists_only_real_searches():
    got = Retrieved(searches=[
        {"query": "a" * 600, "purpose": "requirement", "hits": 0, "documents": [], "searched": 12, "failed": False},
        {"query": "b", "purpose": "context", "hits": 3, "documents": ["X"], "searched": None, "failed": False},
        {"query": "c", "purpose": "context", "hits": 0, "documents": [], "searched": None, "failed": True},
    ])
    first, second = searches_tried(got)
    assert len(first["search"]) == 500 and first["result"] == "nothing similar enough" and first["candidates"] == 12
    assert second == {"search": "search_kb (context): b", "result": "3 passages, none answers the question", "candidates": None}


async def test_the_original_message_is_searched_too_when_it_differs_from_the_rewrite():
    call_tool = fake({
        "rewrite": reply([hit("A:v1:c1", "one")]),
        "what the person typed": reply([hit("B:v1:c1", "two", "B")]),
    })
    log = ToolLog()
    got = await retrieve(["rewrite"], False, call_tool, log, "what the person typed")
    assert [a["query"] for _, a in call_tool.seen] == ["rewrite", "what the person typed"]
    assert {a["purpose"] for _, a in call_tool.seen} == {"requirement"}
    assert [s["query"] for s in got.searches] == ["rewrite", "what the person typed"]
    assert [p["anchor"] for p in got.passages] == ["A:v1:c1", "B:v1:c1"]
    assert len(log.calls) == 2
    assert [s["search"] for s in searches_tried(got)] == [
        "search_kb (requirement): rewrite", "search_kb (requirement): what the person typed"]


async def test_one_query_when_the_rewrite_is_the_message_up_to_case_space_and_punctuation():
    call_tool = fake({"how long is it valid?": reply([hit("A:v1:c1", "t")])})
    got = await retrieve(["how long is it valid?"], False, call_tool, ToolLog(), "  How long   is it VALID?? ")
    assert len(call_tool.seen) == 1 and len(got.searches) == 1


async def test_the_message_is_searched_when_it_differs_from_every_part_even_if_it_matches_none():
    call_tool = fake({"q1": reply([]), "q2": reply([]), "both, q1 and q2": reply([])})
    got = await retrieve(["q1", "q2"], False, call_tool, ToolLog(), "both, q1 and q2")
    assert len(call_tool.seen) == 3 and got.nothing_found
    same_as_second = fake({"q1": reply([]), "q2": reply([])})
    await retrieve(["q1", "q2"], False, same_as_second, ToolLog(), "Q2.")
    assert len(same_as_second.seen) == 2


async def test_the_same_anchor_from_two_searches_is_kept_once_in_search_order():
    call_tool = fake({
        "part": reply([hit("A:v1:c1", "first text"), hit("A:v1:c2", "second")]),
        "message": reply([hit("A:v1:c1", "different text, same anchor"), hit("C:v1:c4", "second", "C"), hit("C:v1:c5", "new", "C")]),
    })
    got = await retrieve(["part"], False, call_tool, ToolLog(), "message")
    assert [(p["anchor"], p["text"]) for p in got.passages] == [
        ("A:v1:c1", "first text"), ("A:v1:c2", "second"), ("C:v1:c5", "new")]
    assert [s["hits"] for s in got.searches] == [2, 3]


async def test_top_k_comes_from_the_environment_and_defaults_to_ten(monkeypatch):
    call_tool = fake({"q": reply([])})
    monkeypatch.delenv("PHARMALIGN_KA_TOP_K", raising=False)
    await retrieve(["q"], False, call_tool, ToolLog())
    monkeypatch.setenv("PHARMALIGN_KA_TOP_K", "4")
    await retrieve(["q"], False, call_tool, ToolLog())
    assert [a["top_k"] for _, a in call_tool.seen] == [10, 4]


async def test_jurisdiction_and_issuer_of_a_hit_are_kept_in_the_passage():
    row = {**hit("A:v1:c1", "t"), "jurisdiction": "Abu Dhabi", "issuer": "Department of Health"}
    got = await retrieve(["q"], False, fake({"q": reply([row])}), ToolLog())
    assert got.passages[0]["jurisdiction"] == "Abu Dhabi" and got.passages[0]["issuer"] == "Department of Health"


# ------------------------------------------------------------------ the home country first


@pytest.fixture
def local_first(monkeypatch):
    monkeypatch.delenv("PHARMALIGN_KA_LOCAL_FIRST", raising=False)
    monkeypatch.delenv("PHARMALIGN_KA_HOME_COUNTRIES", raising=False)


def by_scope(local, whole):
    """A tool that answers differently when it is asked for the home country only."""
    seen: list[dict] = []

    async def call_tool(name, args):
        seen.append(args)
        value = local if args.get("countries") else whole
        if isinstance(value, Exception):
            raise value
        return value

    call_tool.seen = seen  # type: ignore[attr-defined]
    return call_tool


async def test_the_home_country_is_searched_first_and_enough_when_it_answers(local_first):
    tool = by_scope(reply([hit("A-01:v1:c1", "uae text")]), reply([hit("H-S21:v1:c1", "eu text", "H-S21")]))
    found = await retrieve(["q"], False, tool, ToolLog())
    assert len(tool.seen) == 1 and tool.seen[0]["countries"] == ["ae"]
    assert [p["document_id"] for p in found.passages] == ["A-01"]  # the foreign document was never asked for
    assert [s["scope"] for s in found.searches] == ["local"]


async def test_the_whole_library_is_searched_only_when_the_home_country_finds_nothing(local_first):
    tool = by_scope(reply([]), reply([hit("E-S02:v1:c1", "gcc text", "E-S02")]))
    found = await retrieve(["q"], False, tool, ToolLog())
    assert [bool(a.get("countries")) for a in tool.seen] == [True, False]
    assert [p["document_id"] for p in found.passages] == ["E-S02"]
    assert [(s["scope"], s["hits"]) for s in found.searches] == [("local", 0), ("all", 1)]
    assert not found.nothing_found


async def test_nothing_in_either_scope_lists_both_real_searches(local_first):
    found = await retrieve(["q"], False, by_scope(reply([]), reply([])), ToolLog())
    assert found.nothing_found
    steps = searches_tried(found)
    assert len(steps) == 2 and "home country" in steps[0]["search"] and "home country" not in steps[1]["search"]


async def test_a_server_that_refuses_the_filter_falls_back_to_the_whole_library(local_first):
    tool = by_scope(RuntimeError("invalid_arguments"), reply([hit("A-01:v1:c1", "text")]))
    found = await retrieve(["q"], False, tool, ToolLog())
    assert [p["anchor"] for p in found.passages] == ["A-01:v1:c1"]
    assert [(s["scope"], s["failed"]) for s in found.searches] == [("local", True), ("all", False)]
    assert not found.all_failed
    assert len(searches_tried(found)) == 1  # the failed search is not listed as one that found nothing


async def test_every_part_falls_back_on_its_own(local_first):
    async def tool(name, args):
        local = bool(args.get("countries"))
        if args["query"] == "qa":
            return reply([hit("A-01:v1:c1", "ta")]) if local else reply([])
        return reply([]) if local else reply([hit("B-01:v1:c1", "tb", "B-01")])

    found = await retrieve(["qa", "qb"], False, tool, ToolLog())
    assert [(s["query"], s["scope"]) for s in found.searches] == [("qa", "local"), ("qb", "local"), ("qb", "all")]
    assert [p["document_id"] for p in found.passages] == ["A-01", "B-01"]


async def test_widen_searches_the_whole_library_for_queries_that_only_ran_at_home(local_first):
    tool = by_scope(reply([hit("A-01:v1:c1", "uae text")]), reply([hit("A-01:v1:c1", "uae text"), hit("W-01:v1:c1", "who text", "W-01")]))
    found = await retrieve(["qa", "qb"], False, tool, ToolLog())
    tool.seen.clear()
    ran = await widen(found, tool, ToolLog(), "requirement")
    assert ran == 2
    assert all("countries" not in a and a["purpose"] == "requirement" for a in tool.seen)
    assert sorted(a["query"] for a in tool.seen) == ["qa", "qb"]
    assert [(s["query"], s["scope"]) for s in found.searches] == [("qa", "local"), ("qb", "local"), ("qa", "all"), ("qb", "all")]
    assert [p["anchor"] for p in found.passages] == ["A-01:v1:c1", "W-01:v1:c1"]  # the old one keeps its place, no repeats


async def test_widen_skips_a_query_that_already_has_a_whole_library_row_and_a_quote_check_row(local_first):
    found = Retrieved(
        passages=[{"anchor": "A-01:v1:c1", "text": "uae text"}],
        searches=[
            {"query": "qa", "purpose": "requirement", "scope": "local", "hits": 0, "documents": [], "searched": 3, "failed": False},
            {"query": "qa", "purpose": "requirement", "scope": "all", "hits": 1, "documents": ["A-01"], "searched": 3, "failed": False},
            {"query": "qb", "purpose": "requirement", "scope": "local", "hits": 1, "documents": ["A-01"], "searched": 3, "failed": False},
            {"query": "a quote", "purpose": "context", "scope": "quote_check", "hits": 1, "documents": ["A-01"], "failed": False},
        ],
    )
    tool = by_scope(reply([]), reply([hit("W-01:v1:c1", "who text", "W-01")]))
    assert await widen(found, tool, ToolLog(), "requirement") == 1
    assert [a["query"] for a in tool.seen] == ["qb"]  # not qa (has an "all" row), not the quote check
    assert [p["anchor"] for p in found.passages] == ["A-01:v1:c1", "W-01:v1:c1"]


async def test_widen_drops_a_passage_whose_text_is_already_there_under_another_anchor(local_first):
    tool = by_scope(reply([hit("A-01:v1:c1", "same words")]), reply([hit("W-01:v1:c1", "same words", "W-01"), hit("W-01:v1:c2", "new words", "W-01")]))
    found = await retrieve(["q"], False, tool, ToolLog())
    await widen(found, tool, ToolLog(), "requirement")
    assert [p["anchor"] for p in found.passages] == ["A-01:v1:c1", "W-01:v1:c2"]


async def test_widen_returns_zero_and_searches_nothing_when_every_query_has_a_whole_library_row(local_first):
    tool = by_scope(reply([]), reply([hit("W-01:v1:c1", "who text", "W-01")]))
    found = await retrieve(["q"], False, tool, ToolLog())  # home found nothing, so the whole library was searched too
    tool.seen.clear()
    assert await widen(found, tool, ToolLog(), "requirement") == 0
    assert tool.seen == [] and len(found.searches) == 2


def test_the_home_country_list_comes_from_the_environment(monkeypatch):
    assert home_countries({}) == {"ae"}  # the code the library stores for the UAE, emirates included
    assert home_countries({"PHARMALIGN_KA_HOME_COUNTRIES": " OM , sa ,, "}) == {"om", "sa"}


def test_the_switch_turns_local_first_off(monkeypatch):
    assert local_first_on({}) and local_first_on({"PHARMALIGN_KA_LOCAL_FIRST": "on"})
    assert not local_first_on({"PHARMALIGN_KA_LOCAL_FIRST": " OFF "})


async def test_search_all_searches_the_whole_library_without_a_country_filter(monkeypatch):
    monkeypatch.setenv("PHARMALIGN_KA_LOCAL_FIRST", "on")
    call_tool = fake({"q": reply([hit("A:v1:c1", "t")])})
    got = await retrieve(["q"], False, call_tool, ToolLog(), search_all=True)
    (_, args), = call_tool.seen
    assert "countries" not in args and got.searches[0]["scope"] == "all"
    call_tool = fake({"q": reply([hit("A:v1:c1", "t")])})
    await retrieve(["q"], False, call_tool, ToolLog())
    assert call_tool.seen[0][1]["countries"] == ["ae"]
