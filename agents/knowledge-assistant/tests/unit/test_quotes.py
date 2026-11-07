"""Quotes the server will keep: repeats across the whole document version, and widening a quote in code."""
import json

from pharmalign_agent_common import ToolLog
from pharmalign_agent_common.evidence import quote_in
from pharmalign_contracts.agents.knowledge_assistant import Message

from app.answer import TurnAnswer
from app.chat.quotes import document_chunks, elsewhere, points_here, repair, version_of, widen

# The real shape of B-01 (the EDE service page): the card (c0000) sits whole inside a longer card (c0006), and the FAQ (c0005)
# repeats the fee lines after its own question.
CARD = ("Issuance of Marketing Authorization for a Drug or Biopharmaceutical Product\nStart Service\nService completion duration\n"
        "45 Working Days\nService fees\nApplication: AED 100\nMarketing Authorization of a conventional pharmaceutical product: AED 7,000")
FAQ = ("FAQ's\nWhat is the fees for obtaining a registration service for an Issuance of Marketing Authorization for a Drug or "
       "Biopharmaceutical Product?\nApplication: AED 100.\nMarketing Authorization of a conventional pharmaceutical product: AED 7,000\n"
       "Analysis or re-analysis of a medical product: AED 3,500.\nPricing certificate after committee approval: AED 500.\nFAQ two")
LONG_CARD = ("Service completion duration\n45 Working Days\nService fees\nApplication: AED 100\nMarketing Authorization of a conventional "
             "pharmaceutical product: AED 7,000\nAnalysis or re-analysis of a medical product: AED 3,500\n"
             "Pricing certificate after committee approval: AED 500\nPV inspection fee applies separately")
B01 = {"B-01:v2:c0000": CARD, "B-01:v2:c0005": FAQ, "B-01:v2:c0006": LONG_CARD}
FEE = "Marketing Authorization of a conventional pharmaceutical product: AED 7,000"
QUESTION = "What is EDE's fee for the marketing authorization of a conventional product?"


def server_keeps(quote: str, anchor: str, chunks: dict[str, str]) -> bool:
    """The server's relay rule, written out independently: >= 8 words, not in the question, and repeated nowhere else in its
    document version (or once, when >= 12 words)."""
    words = len(quote.split())
    others = sum(1 for a, t in chunks.items() if a != anchor and a.rsplit(":", 1)[0] == anchor.rsplit(":", 1)[0] and quote_in(quote, t))
    return quote_in(quote, chunks[anchor]) and words >= 8 and not quote_in(quote, QUESTION) and (others == 0 or (others == 1 and words >= 12))


def answer_citing(*pairs: tuple[str, str]) -> TurnAnswer:
    return TurnAnswer(events=[Message(text="x", citations=[{"anchor": a, "quote": q} for a, q in pairs])])


def test_the_fee_line_repeats_in_two_other_chunks_so_the_server_drops_it():
    assert elsewhere(FEE, "B-01:v2:c0000", B01) == 2
    assert not points_here(FEE, "B-01:v2:c0000", B01, QUESTION) and not server_keeps(FEE, "B-01:v2:c0000", B01)


def test_the_repeated_fee_line_is_widened_to_a_quote_the_server_keeps():
    fixed, changes = repair(answer_citing(("B-01:v2:c0000", FEE)), B01, QUESTION)
    (citation,) = fixed.events[0].citations
    assert FEE.split(":")[0] in citation.quote and "7,000" in citation.quote  # it still holds the fact
    assert quote_in(citation.quote, B01[citation.anchor])  # word for word from its own chunk
    assert server_keeps(citation.quote, citation.anchor, B01)
    assert changes and changes[0]["from"] == {"anchor": "B-01:v2:c0000", "quote": FEE}


def test_a_quote_the_server_keeps_is_left_alone():
    quote = "What is the fees for obtaining a registration service for an Issuance of Marketing Authorization"
    fixed, changes = repair(answer_citing(("B-01:v2:c0005", quote)), B01, QUESTION)
    assert fixed.events[0].citations[0].quote == quote and changes == []


def test_a_short_quote_is_widened_inside_its_own_chunk():
    chunks = {"A-01:v2:c0037": "Article (21) The Marketing Approval shall be valid for a period of (5) five years, and shall be renewed."}
    out = widen("valid for a period", "A-01:v2:c0037", chunks, "How long is it valid?")
    assert out and "valid for a period" in out and len(out.split()) >= 8 and quote_in(out, chunks["A-01:v2:c0037"])


def test_widening_keeps_the_raw_text_of_the_chunk_with_its_punctuation():
    chunks = {"A-03:v2:c0006": "Fees: (1%) of the invoice value, with a minimum of AED 200, for import of raw materials."}
    out = widen("of the invoice value", "A-03:v2:c0006", chunks, "q?")
    assert out in chunks["A-03:v2:c0006"]  # a slice of the chunk itself, not a re-spelling


def test_a_chunk_repeated_whole_three_times_cannot_be_fixed():
    same = "The marketing authorization fee is AED 7,000 for a conventional product."
    chunks = {f"B-01:v2:c000{i}": same for i in range(3)}
    fixed, changes = repair(answer_citing(("B-01:v2:c0000", same)), chunks, QUESTION)
    assert changes == [] and fixed.events[0].citations[0].quote == same  # left for citation_problems and a retry


def test_other_versions_and_documents_do_not_count():
    chunks = {"B-01:v2:c0000": CARD, "B-01:v1:c0005": FAQ, "B-01:v1:c0006": LONG_CARD, "B-02:v2:c0001": LONG_CARD}
    assert elsewhere(FEE, "B-01:v2:c0000", chunks) == 0
    assert version_of("B-01:v2:c0005") == "B-01:v2" and version_of("B-01") == "B-01"


async def test_document_chunks_searches_inside_each_cited_document_once_per_quote():
    calls = []

    async def call_tool(name, args):
        calls.append(args)
        hits = [{"anchor": a, "text": t, "document_id": "B-01"} for a, t in B01.items()]
        return {"content": [{"type": "text", "text": json.dumps({"hits": hits, "no_source_found": False})}], "isError": False}

    passages = [{"anchor": "B-01:v2:c0000", "document_id": "B-01", "text": CARD}]
    log = ToolLog()
    chunks, checks = await document_chunks(answer_citing(("B-01:v2:c0000", FEE), ("B-01:v2:c0000", FEE)), passages, call_tool, log)
    assert calls == [{"query": FEE, "purpose": "context", "top_k": 10, "document_ids": ["B-01"]}]  # the same quote is checked once
    assert set(chunks) == set(B01) and checks[0]["scope"] == "quote_check" and checks[0]["hits"] == 3
    assert log.returned("B-01:v2:c0005")  # recorded, so a quote moved there still passes check_turn


async def test_a_failed_check_search_keeps_the_passages_and_does_not_raise():
    async def call_tool(name, args):
        raise RuntimeError("down")

    passages = [{"anchor": "B-01:v2:c0000", "document_id": "B-01", "text": CARD}]
    chunks, checks = await document_chunks(answer_citing(("B-01:v2:c0000", FEE)), passages, call_tool, ToolLog())
    assert chunks == {"B-01:v2:c0000": CARD} and checks[0]["failed"]


def test_a_widened_quote_is_whole_lines_a_person_can_read():
    fixed, _ = repair(answer_citing(("B-01:v2:c0000", FEE)), B01, QUESTION)
    (citation,) = fixed.events[0].citations
    text = B01[citation.anchor]
    start = text.index(citation.quote)
    end = start + len(citation.quote)
    assert start == 0 or text[start - 1] == "\n" or text[:start].rstrip()[-1] in ".?!"  # starts a line or a sentence
    assert end == len(text) or text[end] == "\n" or citation.quote[-1] in ".?!"  # ends one
    assert server_keeps(citation.quote, citation.anchor, B01)


def test_a_colon_inside_a_line_does_not_cut_the_quote():
    chunks = {"B-01:v2:c0005": FAQ, "B-01:v2:c0000": CARD, "B-01:v2:c0006": LONG_CARD}
    out = widen(FEE, "B-01:v2:c0005", chunks, QUESTION)
    assert out and not out.startswith("AED") and not out.endswith(":")
