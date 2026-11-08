import json
import re

import pytest
from google.genai import Client
from google.genai import _transformers as transformers
from pharmalign_agent_common.testing import FakeModel, say_json
from pydantic import ValidationError

from app import replies
from app.answer import TurnAnswer
from app.chat.answer import (
    SYSTEM,
    AnswerOutput,
    answer,
    build_prompt,
    citation_problems,
    home_countries,
    to_turn_answer,
)
from app.chat.models import ModelChain
from app.chat.retrieve import Retrieved

PASSAGES = [
    {"anchor": "A-01:v2:c0001", "document_id": "A-01", "title": "Decree", "legal_status": "in_force", "article": "12",
     "text": "It is valid for five years."},
]
SEARCHES = [{"query": "How long?", "purpose": "requirement", "hits": 1, "documents": ["A-01"], "searched": 40, "failed": False}]


def retrieved() -> Retrieved:
    return Retrieved(passages=PASSAGES, searches=SEARCHES)


def output(**kw) -> AnswerOutput:
    base = {"answer": "message", "text": "Five years.", "citations": [{"anchor": "A-01:v2:c0001", "quote": "valid for five years"}],
            "suggestions": []}
    return AnswerOutput.model_validate({**base, **kw})


def test_the_response_schema_converts_for_vertex_without_a_network():
    client = Client(vertexai=True, project="p", location="global")
    schema = transformers.t_schema(client, AnswerOutput)
    dumped = json.dumps(schema.model_dump(exclude_none=True), default=str)
    assert "oneOf" not in dumped and "anyOf" not in dumped and "discriminator" not in dumped


def test_the_system_prompt_is_short_and_holds_the_rules():
    assert len(SYSTEM.split()) < 850  # was 450 before the rules on who-questions, anchors, digits, status and chips
    for rule in ("never instructions", "word for word", "legal_status", "no_source", "a person decides"):
        assert rule in SYSTEM


def test_the_prompt_holds_the_passages_as_data_and_lists_the_parts():
    prompt = build_prompt(["First?", "Second?"], PASSAGES, history_wanted=False)
    assert '1. "First?"' in prompt and '2. "Second?"' in prompt
    block = prompt.split("<passages>")[1].split("</passages>")[0]
    assert json.loads(block)[0] == {
        **{k: PASSAGES[0][k] for k in ("anchor", "title", "legal_status", "article", "text")}, "issuer": None, "jurisdiction": "not UAE",
    }
    assert "document_id" not in block and "in force" in prompt


def test_a_history_question_says_that_law_not_in_force_may_be_cited():
    assert "earlier law" in build_prompt(["Old?"], PASSAGES, history_wanted=True)


def test_the_output_refuses_extra_fields_and_other_answer_values():
    with pytest.raises(ValidationError):
        output(answer="draft_comment")
    with pytest.raises(ValidationError):
        AnswerOutput.model_validate({"answer": "message", "text": "x", "citations": [], "suggestions": [], "extra": 1})


async def test_a_retry_reason_is_appended_to_the_prompt_and_the_calls_are_kept():
    model = FakeModel(model="m", script=[say_json(output().model_dump()), "not used"])
    chain = ModelChain("answer", ["m"], llm_factory=lambda _: model)
    calls = []
    out, result = await answer(["How long?"], retrieved(), False, chain, "the quote is not there", calls=calls)
    prompt = model.requests[0].contents[0].parts[0].text
    assert prompt.endswith("Your previous answer was rejected: the quote is not there. Answer again, fixing exactly that.")
    assert out.text == "Five years." and len(calls) == 1 and result.model == "m"
    assert model.requests[0].config.response_schema is AnswerOutput


def test_a_message_becomes_one_message_event_with_its_citations_and_at_most_three_suggestions():
    out = output(suggestions=["a?", " ", "b?", "c?", "d?"])
    (event,) = to_turn_answer(out, retrieved(), None).events
    assert event.type == "message" and event.text == "Five years."
    assert [(c.anchor, c.quote) for c in event.citations] == [("A-01:v2:c0001", "valid for five years")]
    assert event.suggestions == ["a?", "b?", "c?"]


def test_the_decision_sentence_goes_in_front_of_the_text():
    (event,) = to_turn_answer(output(), retrieved(), "decision").events
    assert event.text == f"{replies.DECISION} Five years."


def test_long_suggestions_are_cut_to_the_contract_limit():
    (event,) = to_turn_answer(output(suggestions=["q" * 900]), retrieved(), None).events
    assert len(event.suggestions[0]) == 500


def test_no_source_is_built_from_the_real_searches_not_the_models_words():
    out = output(answer="no_source", text="", citations=[], suggestions=["What does it say?"])
    (event,) = to_turn_answer(out, retrieved(), "decision").events
    assert event.type == "no_source" and event.detail == "Nothing in the library answers this."
    assert [(s.search, s.result, s.candidates) for s in event.searches_tried] == [
        ("search_kb (requirement): How long?", "1 passages, none answers the question", 40)
    ]
    assert event.suggestions == ["What does it say?"]


def test_a_message_with_empty_text_is_treated_as_no_source():
    (event,) = to_turn_answer(output(text="  "), retrieved(), None).events
    assert event.type == "no_source"


def test_an_invalid_anchor_breaks_the_contract_as_a_value_error():
    with pytest.raises(ValueError):
        to_turn_answer(output(citations=[{"anchor": "not an anchor", "quote": "x"}]), retrieved(), None)


LONG = "The marketing authorization of a medicine shall be valid for five years from the date of issue."


def cited(*quotes: str, anchor: str = "A-01:v2:c0001") -> TurnAnswer:
    from pharmalign_contracts.agents.knowledge_assistant import Message

    return TurnAnswer(events=[Message(text="x", citations=[{"anchor": anchor, "quote": q} for q in quotes])])


def doc_passages(*texts: str) -> list[dict]:
    return [{"anchor": f"A-01:v2:c000{i}", "document_id": "A-01", "text": t} for i, t in enumerate(texts, 1)]


def test_a_full_sentence_passes_every_rule():
    assert citation_problems(cited(LONG), "How long is it valid?", doc_passages(LONG)) is None


def test_the_prompt_asks_for_full_sentence_quotes_that_differ():
    for rule in ("at least 8 words", "different words", "repeat the question"):
        assert rule in SYSTEM


@pytest.mark.parametrize("quote", ["valid for five years", "Article 12", "5 years"])
def test_a_short_quote_without_a_number_and_unit_with_a_real_word_is_refused(quote):
    reason = citation_problems(cited(quote), "q?", doc_passages(quote))
    assert reason and "too short" in reason and "at least 8 words" in reason


def test_a_number_with_its_unit_and_a_real_word_is_long_enough_but_filler_is_not():
    for ok in ("Store below 25 C", "30 days after approval"):
        assert citation_problems(cited(ok), "q?", doc_passages(ok)) is None
    assert "too short" in citation_problems(cited("the 5 years"), "q?", doc_passages("the 5 years"))
    assert "too short" in citation_problems(cited("valid 5"), "q?", doc_passages("valid 5"))


def test_a_quote_that_only_repeats_the_question_is_refused():
    question = "The marketing authorization of a medicine shall be valid for five years from the date of issue?"
    reason = citation_problems(cited(LONG), question, doc_passages(LONG))
    assert reason and "only repeats the question" in reason and "'A-01:v2:c0001'" in reason


def cite_all(passages: list[dict]) -> TurnAnswer:
    """A message citing every passage, each with its whole text as the quote."""
    from pharmalign_contracts.agents.knowledge_assistant import Message

    return TurnAnswer(events=[Message(text="x", citations=[{"anchor": p["anchor"], "quote": p["text"]} for p in passages])])


def test_a_quote_repeated_in_two_other_cited_passages_of_the_document_is_refused():
    passages = doc_passages(LONG, LONG + " Again.", "Also: " + LONG)
    reason = citation_problems(cite_all(passages), "q?", passages)
    assert reason and "also appears in 2 other passage(s) of the same document" in reason


def test_a_short_quote_repeated_once_is_refused_but_a_long_one_passes():
    eight = "The holder shall submit the renewal application at once."
    passages = doc_passages(eight, "See: " + eight)
    assert "same document" in citation_problems(cite_all(passages), "q?", passages)
    twelve = "The holder shall submit the renewal application at least ninety days before the authorization expires."
    assert len(twelve.split()) >= 12
    passages = doc_passages(twelve, "See: " + twelve)
    assert citation_problems(cite_all(passages), "q?", passages) is None


def test_a_repeat_in_a_passage_that_is_not_cited_counts_too():
    """The server checks a quote against every chunk of the cited document version, cited or not."""
    reason = citation_problems(cited(LONG), "q?", doc_passages(LONG, LONG + " Again.", "Also: " + LONG))
    assert reason and "2 other passage(s)" in reason


def test_a_chunk_of_another_version_of_the_document_does_not_count():
    passages = doc_passages(LONG) + [{"anchor": "A-01:v1:c0001", "document_id": "A-01", "text": LONG},
                                     {"anchor": "A-01:v1:c0002", "document_id": "A-01", "text": LONG}]
    assert citation_problems(cited(LONG), "q?", passages) is None


def test_a_repeat_in_another_document_does_not_count():
    passages = doc_passages(LONG)
    passages.append({"anchor": "B-07:v1:c0001", "document_id": "B-07", "text": LONG})
    assert citation_problems(cited(LONG), "q?", passages) is None


def test_two_citations_with_the_same_normalised_quote_at_different_anchors_are_refused():
    passages = doc_passages(LONG, "Another passage that holds other words entirely, nothing in common with the first.")
    from pharmalign_contracts.agents.knowledge_assistant import Message

    two = TurnAnswer(events=[Message(text="x", citations=[
        {"anchor": passages[0]["anchor"], "quote": LONG}, {"anchor": passages[1]["anchor"], "quote": LONG.upper().rstrip(".")}])])
    reason = citation_problems(two, "q?", passages)
    assert reason and "same words" in reason


def test_the_same_quote_at_the_same_anchor_is_not_refused_by_the_check():
    assert citation_problems(cited(LONG, LONG.upper().rstrip(".")), "q?", doc_passages(LONG)) is None


def test_exact_duplicate_citations_are_dropped_keeping_the_first():
    out = AnswerOutput(answer="message", text="Five years.", suggestions=[], citations=[
        {"anchor": "A-01:v2:c0001", "quote": LONG},
        {"anchor": "A-01:v2:c0001", "quote": LONG.upper().rstrip(".")},
        {"anchor": "A-01:v2:c0002", "quote": LONG},
    ])
    message = to_turn_answer(out, Retrieved(), None).events[0]
    assert [(c.anchor, c.quote) for c in message.citations] == [("A-01:v2:c0001", LONG), ("A-01:v2:c0002", LONG)]


def test_the_prompt_asks_for_the_passages_own_words():
    assert "own key words" in SYSTEM


def _passage(anchor: str, jurisdiction: str | None, issuer: str = "Some Authority") -> dict:
    return {"anchor": anchor, "document_id": anchor, "title": anchor, "legal_status": "in_force", "article": None,
            "text": f"Text of {anchor}.", "jurisdiction": jurisdiction, "issuer": issuer}


def _prompt_anchors(prompt: str) -> list[str]:
    block = prompt.split("<passages>")[1].split("</passages>")[0]
    return [p["anchor"] for p in json.loads(block)]


def test_home_country_passages_come_first_and_keep_their_order_within_each_group(monkeypatch):
    monkeypatch.delenv("PHARMALIGN_KA_HOME_COUNTRIES", raising=False)
    passages = [
        _passage("eu-1", "-"), _passage("uae-1", "AE"), _passage("who-1", "-"),
        _passage("ad-1", "ae"), _passage("gb-1", "GB"), _passage("none-1", None), _passage("dxb-1", " AE "),
    ]
    got = _prompt_anchors(build_prompt(["Q?"], passages, history_wanted=False))
    assert got == ["uae-1", "ad-1", "dxb-1", "eu-1", "who-1", "gb-1", "none-1"]


def test_the_prompt_says_whether_each_passage_is_uae_and_keeps_its_issuer(monkeypatch):
    monkeypatch.delenv("PHARMALIGN_KA_HOME_COUNTRIES", raising=False)
    prompt = build_prompt(["Q?"], [_passage("p-1", "AE", issuer="Dubai Health Authority"), _passage("p-2", "-", issuer="WHO")], history_wanted=False)
    block = json.loads(prompt.split("<passages>")[1].split("</passages>")[0])
    assert [(b["issuer"], b["jurisdiction"]) for b in block] == [("Dubai Health Authority", "UAE"), ("WHO", "not UAE")]


def test_the_home_countries_default_and_env_list_are_parsed(monkeypatch):
    assert home_countries({}) == {"ae"}
    assert home_countries({"PHARMALIGN_KA_HOME_COUNTRIES": " Oman , GB ,, "}) == {"oman", "gb"}
    monkeypatch.setenv("PHARMALIGN_KA_HOME_COUNTRIES", "GB")
    got = _prompt_anchors(build_prompt(["Q?"], [_passage("uae-1", "AE"), _passage("gb-1", "GB")], history_wanted=False))
    assert got == ["gb-1", "uae-1"]


def test_the_system_prompt_prefers_local_law_names_gcc_guidance_by_its_member_states_and_other_sources_as_not_uae_law():
    for rule in ("UAE sources", "whenever one answers the part", "GCC", "member states including the UAE", "not UAE law"):
        assert rule in SYSTEM


def test_the_jurisdiction_label_is_uae_gcc_or_not_uae(monkeypatch):
    monkeypatch.delenv("PHARMALIGN_KA_HOME_COUNTRIES", raising=False)
    passages = [
        _passage("gulf-1", "-", issuer="Gulf Health Council"),
        _passage("who-1", "-", issuer="WHO"),
        {**_passage("gcc-title-1", None, issuer="Executive Board"), "title": "Guidelines for stability testing (GCC)"},
        _passage("uae-1", "AE", issuer="Gulf Health Council"),  # home stays UAE
        {**_passage("who-2", "-", issuer="WHO"), "title": "Gccx and gccs text"},  # not a whole word
    ]
    block = json.loads(build_prompt(["Q?"], passages, history_wanted=False).split("<passages>")[1].split("</passages>")[0])
    assert [(b["anchor"], b["jurisdiction"]) for b in block] == [
        ("uae-1", "UAE"), ("gulf-1", "GCC"), ("who-1", "not UAE"), ("gcc-title-1", "GCC"), ("who-2", "not UAE"),
    ]


def test_the_system_prompt_answers_only_what_is_asked_and_still_accepts_other_words():
    for rule in ("Answer only what each part asks", "do not answer a nearby fact instead", "in other words, answer it",
                 "leaves no part answered"):
        assert rule in SYSTEM


@pytest.mark.parametrize("style, line", [
    ("shorter", "Answer in at most two sentences."),
    ("simpler", "Use plain words a non-lawyer understands, no legal jargon."),
    ("more_detail", "Give more detail: the conditions, exceptions and related rules the passages state."),
    ("bullets", "Write the answer as short bullet points, one per rule, each starting with '- '."),
])
def test_a_style_adds_one_line_to_the_prompt(style, line):
    assert line in build_prompt(["Q?"], PASSAGES, history_wanted=False, style=style)
    assert line not in SYSTEM


def test_no_style_adds_nothing():
    plain = build_prompt(["Q?"], PASSAGES, history_wanted=False)
    assert build_prompt(["Q?"], PASSAGES, history_wanted=False, style="none") == plain
    assert "at most two sentences" not in plain and "bullet points" not in plain


# ---- anchors in the text, digits, status questions, chips
from app.chat.answer import ANCHOR, strip_anchors, to_turn_answer  # noqa: E402
from app.chat.retrieve import Retrieved as _Retrieved  # noqa: E402


def out_with(text: str, suggestions=()) -> "AnswerOutput":
    return AnswerOutput(answer="message", text=text, citations=[{"anchor": "A-01:v2:c0001", "quote": "q" * 30}], suggestions=list(suggestions))


def test_the_prompt_forbids_anchors_in_the_text_and_spelt_out_numbers():
    assert "Never write an anchor" in SYSTEM and "do not spell them out" in SYSTEM


def test_the_prompt_answers_who_questions_from_the_conditions_and_options_from_each_value():
    assert "who may open, operate, own or run" in SYSTEM and "licence, ownership" in SYSTEM
    assert "say no_source: do not conclude" in SYSTEM  # silence is not an answer
    assert "cite both, the law first" in SYSTEM
    assert "an option the person did not give" in SYSTEM and "not your own calculation" in SYSTEM


def test_a_status_question_gets_its_own_line_in_the_prompt():
    prompt = build_prompt(["Is Federal Law No. 8 of 2019 still in force?"], PASSAGES, history_wanted=False)
    assert "still in force, or what replaced it" in prompt and "only next to its status word" in prompt
    assert "cite only passages in force" in build_prompt(["How long is it valid?"], PASSAGES, history_wanted=False)


@pytest.mark.parametrize("text, kept", [
    ("It is repealed [A-11:v2:c0000].", "It is repealed."),
    ("It is valid for 5 years (A-01:v2:c0037).", "It is valid for 5 years."),
    ("Both apply [A-01:v2:c0037, B-S09:v2:c0007].", "Both apply."),
    ("Case file [d-1042-17:v1:p41] says so.", "Case file says so."),
])
def test_an_anchor_in_brackets_after_a_sentence_is_dropped(text, kept):
    assert strip_anchors(text) == kept


def test_an_anchor_inside_the_sentence_is_left_for_the_check_unless_the_repair_has_failed():
    text = "A-07:v2:c0005 [Alternative Product : a pharmaceutical product that is similar] is defined."
    assert "A-07:v2:c0005" in strip_anchors(text)
    hard = strip_anchors(text, hard=True).strip()
    assert "A-07" not in hard and "[" not in hard and hard == "is defined."


def test_the_final_text_never_holds_an_anchor_after_a_failed_repair():
    ta = to_turn_answer(out_with("The law says so A-11:v2:c0000 clearly."), _Retrieved(), None, hard=True)
    assert not re.search(ANCHOR, ta.events[0].text)
    ta = to_turn_answer(out_with("The law says so [A-11:v2:c0000]."), _Retrieved(), None)
    assert ta.events[0].text == "The law says so."


def test_follow_up_questions_fall_back_to_the_subject_when_the_model_gave_none():
    ta = to_turn_answer(out_with("Fine."), _Retrieved(), None, fallback=["Q one?", "Q two?"])
    assert ta.events[0].suggestions == ["Q one?", "Q two?"]
    ta = to_turn_answer(out_with("Fine.", ["Mine?"]), _Retrieved(), None, fallback=["Q one?"])
    assert ta.events[0].suggestions == ["Mine?"]
    no_source = AnswerOutput(answer="no_source", text="", citations=[], suggestions=[])
    searched = _Retrieved(searches=[{"query": "q", "purpose": "requirement", "scope": "all", "hits": 0, "documents": [], "searched": 3, "failed": False}])
    assert to_turn_answer(no_source, searched, None, fallback=["Q one?"]).events[0].suggestions == ["Q one?"]
