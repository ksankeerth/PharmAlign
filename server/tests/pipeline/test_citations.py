"""The citation check on its own: true quotes pass in every spelling, and text that proves nothing is refused."""
import pytest
from pharmalign_contracts.common import Evidence, FactValue

from app.pipeline.citations import (
    CitationContext,
    SourceChunk,
    check_citations,
    normalise,
    quote_in,
    unsupported_numbers,
    value_in_its_quotes,
)

ANCHOR = "d-1:v1:p2"
TEXT = "Based on the data presented, a shelf life of 36 months is proposed when stored below 30 C in the original long-term package."


def context(text=TEXT, **fields) -> CitationContext:
    chunks = {ANCHOR: SourceChunk(ANCHOR, text, "dossier", "d-1:v1", "d-1"), **fields.pop("extra", {})}
    return CitationContext(chunks, **fields)


def failed_rules(quote, ctx=None, anchor=ANCHOR):
    return [f.rule for f in check_citations([Evidence(anchor=anchor, quote=quote)], ctx or context())]


def test_a_true_quote_passes():
    assert failed_rules("a shelf life of 36 months is proposed") == []


def test_an_invented_anchor_is_refused():
    assert failed_rules("a shelf life of 36 months", anchor="d-1:v1:p9") == ["anchor_not_found"]


def test_a_changed_number_is_refused_with_no_fuzzy_match():
    assert failed_rules("a shelf life of 24 months is proposed when stored below 30 C") == ["quote_not_found"]


def test_a_number_inside_another_number_is_not_a_match():
    assert not quote_in("6 months of data", "36 months of data")


@pytest.mark.parametrize("quote", ["shall", "12 months", "the original package", "36 months"])
def test_a_short_quote_is_refused(quote):
    text = "You shall keep it for 12 months in the original package, for 36 months."
    assert failed_rules(quote, context(text)) == ["quote_too_short"]


def test_a_number_with_its_unit_and_subject_is_long_enough():
    assert failed_rules("stored below 30 C") == []


def test_arabic_letter_variants_and_diacritics_still_match():
    text = "مُدَّة الصلاحية ٣٦ شهراً عند التخزين في درجة حرارة أقل من ٣٠ مئوية."
    quote = "مدة الصلاحيه 36 شهرا عند التخزين"
    assert failed_rules(quote, context(text)) == []


def test_arabic_alef_forms_and_ya_match():
    assert quote_in("الى اخر الملف", "إلى آخر الملف")


def test_hyphen_variants_and_wrapped_lines_still_match():
    text = "Data on long‑term\nstability are given for the pri-\nmary batches at each time point."
    assert quote_in("long-term stability are given", text)
    assert quote_in("long term stability are given", text)
    assert quote_in("primary batches at each time point", text)


def test_a_closed_up_number_range_is_not_a_new_number():
    assert not quote_in("1012 months of data", "10-12 months of data")


def test_a_lost_minus_sign_is_not_a_match():
    assert not quote_in("stored at 5 C in a freezer", "stored at -5 C in a freezer")


def test_spacing_case_and_invisible_characters_dont_matter():
    assert normalise("Shelf  life\n​of 30 °C") == normalise("shelf life of 30 C")


def test_a_quote_lifted_from_the_question_is_refused():
    text = "The file gives long-term testing at 30 C / 65% RH for 12 months on three batches."
    ctx = context(text, question="Is long-term testing at 30 C / 65% RH for 12 months given?")
    assert failed_rules("long-term testing at 30 C / 65% RH for 12 months", ctx) == ["quote_from_question"]


def test_a_quote_copied_from_another_agents_output_is_refused():
    ctx = context(other_outputs=["The Reader says a shelf life of 36 months is proposed when stored below 30 C."])
    assert failed_rules("a shelf life of 36 months is proposed", ctx) == ["quote_copied"]


def test_the_same_words_another_agent_cited_elsewhere_are_refused():
    ctx = context(other_evidence=[Evidence(anchor="d-9:v1:p1", quote="a shelf life of 36 months is proposed")])
    assert failed_rules("a shelf life of 36 months is proposed", ctx) == ["quote_copied"]


def test_the_same_place_cited_by_two_agents_is_allowed():
    ctx = context(other_evidence=[Evidence(anchor=ANCHOR, quote="a shelf life of 36 months is proposed")])
    assert failed_rules("a shelf life of 36 months is proposed", ctx) == []


def repeats(n: int) -> dict:
    return {f"d-1:v1:p{i + 10}": SourceChunk(f"d-1:v1:p{i + 10}", TEXT, "dossier", "d-1:v1", "d-1") for i in range(n)}


def test_text_repeated_elsewhere_in_the_document_is_refused():
    assert failed_rules("a shelf life of 36 months is proposed", context(extra=repeats(1))) == ["quote_repeated"]


def test_a_long_quote_repeated_once_is_allowed_but_boilerplate_is_not():
    long_quote = "a shelf life of 36 months is proposed when stored below 30 C in the original"
    assert failed_rules(long_quote, context(extra=repeats(1))) == []
    assert failed_rules(long_quote, context(extra=repeats(2))) == ["quote_repeated"]


def test_the_same_text_in_another_document_is_not_repetition():
    other = {"d-2:v1:p1": SourceChunk("d-2:v1:p1", TEXT, "dossier", "d-2:v1", "d-2")}
    assert failed_rules("a shelf life of 36 months is proposed", context(extra=other)) == []


def test_a_suspect_page_and_a_source_not_in_force_are_refused():
    suspect = CitationContext({ANCHOR: SourceChunk(ANCHOR, TEXT, "dossier", "d-1:v1", "d-1", suspect=True)})
    old = CitationContext({ANCHOR: SourceChunk(ANCHOR, TEXT, "kb", "A-1-v1", "A-1", in_force=False)})
    assert failed_rules("a shelf life of 36 months is proposed", suspect) == ["suspect_page"]
    assert failed_rules("a shelf life of 36 months is proposed", old) == ["not_in_force"]


def test_a_number_in_the_finding_must_be_in_a_quote():
    evidence = [Evidence(anchor=ANCHOR, quote="stored at 25 C for 12 months")]
    assert unsupported_numbers("Stored at 30 C for 12 months.", evidence) == [30.0]
    assert unsupported_numbers("Stored at 25 C for 12 months.", evidence) == []


def test_an_extracted_value_must_be_what_its_quote_says():
    good = FactValue(key="t", status="found", value="25", number=25, evidence=[Evidence(anchor=ANCHOR, quote="at 25 C / 60% RH")])
    bad = FactValue(key="t", status="found", value="30", number=30, evidence=[Evidence(anchor=ANCHOR, quote="at 25 C / 60% RH")])
    text = FactValue(key="s", status="found", value="Plant 2", evidence=[Evidence(anchor=ANCHOR, quote="made at Plant 3")])
    assert value_in_its_quotes(good)
    assert not value_in_its_quotes(bad)
    assert not value_in_its_quotes(text)
