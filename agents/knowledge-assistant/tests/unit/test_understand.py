"""understand(): the gate and suggestion picks never call the model; the rest is the classifier plus code rules."""
import pytest
from pharmalign_agent_common.testing import FakeModel
from pharmalign_agent_common.testing.fake_model import say_json

from app.chat.labels import HistoryEntry
from app.chat.models import ModelChain, reset_cooldowns
from app.chat.understand import looks_english, understand

SUGGESTIONS = ("How long is a clinic licence valid?", "What is the fee for a clinic licence?", "Who can renew a licence?")
SHOWN = [HistoryEntry("hello", "Hello.", "greeting", SUGGESTIONS)]
OUT = {"kind": "question", "follow_up": True, "parts": ["What is the fee?"], "history_wanted": False, "english": True}


@pytest.fixture(autouse=True)
def _cooldowns():
    reset_cooldowns()
    yield
    reset_cooldowns()


def chain_of(*script) -> tuple[ModelChain, FakeModel]:
    model = FakeModel(model="m1", script=list(script))
    return ModelChain("classifier", ["m1"], llm_factory=lambda _: model, retry_seconds=0), model


@pytest.mark.parametrize("message, index", [
    ("yes", 0), ("Yes!", 0), ("yeah", 0), ("yep", 0), ("sure", 0), ("ok", 0), ("okay", 0), ("please", 0), ("go ahead", 0),
    ("do it", 0), ("that one", 0), ("sounds good", 0), ("ok, yes please", 0), ("Yes, please.", 0),
    ("first", 0), ("1st", 0), ("1", 0), ("one", 0), ("first one", 0), ("the first one", 0), ("number 1", 0),
    ("start with the first", 0), ("ok the first", 0),
    ("second", 1), ("2nd", 1), ("2", 1), ("the second one", 1), ("third", 2), ("3rd", 2), ("3", 2),
    ("last", 2), ("last one", 2), ("the last one", 2),
])
async def test_a_short_reply_picks_a_suggestion_without_a_model_call(message, index):
    chain, model = chain_of()
    got = await understand(message, SHOWN, chain)
    assert model.calls == 0 and got.calls == [] and got.how == "suggestion_pick"
    assert got.labels.kind == "question" and got.labels.follow_up and got.labels.parts == (SUGGESTIONS[index],)
    assert got.route.kind == "answer" and got.route.parts == (SUGGESTIONS[index],)


@pytest.mark.parametrize("message", ["ok, the fee one", "great, thank you", "yes and also the fee", "what about the second law?"])
async def test_any_other_words_go_to_the_classifier(message):
    chain, model = chain_of(say_json(OUT))
    got = await understand(message, SHOWN, chain)
    assert model.calls == 1 and got.how == "classifier"
    prompt = model.requests[0].contents[0].parts[0].text
    assert SUGGESTIONS[1] in prompt  # the classifier sees what we suggested


async def test_yes_with_no_suggestions_goes_to_the_classifier():
    chain, model = chain_of(say_json(OUT))
    got = await understand("yes", [HistoryEntry("q", "an answer", "answer")], chain)
    assert model.calls == 1 and got.how == "classifier"
    chain, model = chain_of(say_json(OUT))
    await understand("yes", [], chain)
    assert model.calls == 1


async def test_a_pick_past_the_last_suggestion_goes_to_the_classifier():
    chain, model = chain_of(say_json(OUT))
    got = await understand("third", [HistoryEntry("q", "r", "answer", SUGGESTIONS[:2])], chain)
    assert model.calls == 1 and got.how == "classifier"


async def test_arabic_is_the_language_reply_with_no_model_call():
    chain, model = chain_of()
    got = await understand("ما هي مدة صلاحية تسجيل الدواء؟", SHOWN, chain)
    assert model.calls == 0 and got.how == "gate" and got.calls == []
    assert (got.route.kind, got.route.reply, got.route.has_history) == ("fixed", "language", True)
    assert not got.labels.english


async def test_typo_english_labelled_foreign_is_treated_as_english():
    chain, _ = chain_of(say_json({**OUT, "english": False}))
    got = await understand("wat is teh fee for a licence we must pay", [], chain)
    assert got.labels.english and got.route.kind == "answer"


async def test_french_stays_not_english():
    chain, _ = chain_of(say_json({**OUT, "english": False}))
    got = await understand("Combien de temps dure l'autorisation de mise sur le marché ?", [], chain)
    assert not got.labels.english and got.route.reply == "language"
    chain, _ = chain_of(say_json({**OUT, "english": False}))
    got = await understand("Combien de temps dure la licence", [], chain)
    assert got.route.reply == "language"


def test_the_english_guard_needs_ascii_and_enough_function_words():
    assert looks_english("what is the fee for a licence")
    assert not looks_english("Combien de temps dure la licence")
    assert not looks_english("what is the fee for a licence é")
    assert not looks_english("12345 ???")


async def test_one_word_after_a_clarify_question_is_not_clarified_again():
    chain, _ = chain_of(say_json({**OUT, "kind": "vague", "parts": [], "follow_up": False}))
    got = await understand("fees", [HistoryEntry("help", "Could you say more?", "clarify", SUGGESTIONS)], chain)
    assert got.how == "classifier+clarify_followup"
    assert got.labels.kind == "question" and got.labels.parts == ("fees",) and got.route.kind == "answer"
    chain, _ = chain_of(say_json({**OUT, "kind": "vague", "parts": [], "follow_up": False}))
    got = await understand("fees", [HistoryEntry("help", "An answer.", "answer")], chain)
    assert got.route.reply == "clarify" and got.how == "classifier"


async def test_a_follow_up_after_nothing_found_searches_the_whole_library():
    last = [HistoryEntry("q", "Nothing in the library answers this.", "no_source")]
    chain, _ = chain_of(say_json(OUT))
    assert (await understand("try again", last, chain)).route.search_all
    chain, _ = chain_of(say_json({**OUT, "follow_up": False}))
    assert not (await understand("a new topic", last, chain)).route.search_all
    chain, _ = chain_of(say_json(OUT))
    assert not (await understand("try again", [HistoryEntry("q", "r", "answer")], chain)).route.search_all


async def test_naming_a_foreign_source_searches_the_whole_library_and_a_local_one_does_not():
    chain, _ = chain_of(say_json({**OUT, "sources_named": ["WHO"]}))
    got = await understand("what does who say", [HistoryEntry("q", "r", "answer")], chain)
    assert got.route.search_all and got.route.sources_named == ("WHO",)
    for local in (["DoH"], ["Abu Dhabi", "dubai"], ["the UAE"], ["MOHAP"]):
        chain, _ = chain_of(say_json({**OUT, "sources_named": local}))
        got = await understand("what does it say", [HistoryEntry("q", "r", "answer")], chain)
        assert not got.route.search_all and got.route.sources_named == tuple(local)
    chain, _ = chain_of(say_json({**OUT, "sources_named": ["DoH", "EU"]}))
    assert (await understand("x", [], chain)).route.search_all


async def test_style_reaches_the_route():
    chain, _ = chain_of(say_json({**OUT, "style": "shorter"}))
    got = await understand("shorter please", [HistoryEntry("q", "r", "answer")], chain)
    assert got.labels.style == "shorter" and got.route.style == "shorter" and not got.route.search_all



def test_a_request_for_another_language_is_not_turned_back_into_english():
    from app.chat.understand import looks_english
    assert not looks_english("can you answer in Arabic?") and not looks_english("please reply in French")
    assert looks_english("wat is the fee for MA of a conventional prodct")


async def test_a_decision_request_right_after_an_answer_takes_the_question_just_answered():
    from pharmalign_agent_common.testing import FakeModel, say_json

    from app.chat.labels import HistoryEntry
    from app.chat.models import ModelChain
    from app.chat.understand import understand

    model = FakeModel(script=[say_json({"kind": "decision_request", "follow_up": True, "parts": [], "history_wanted": False, "english": True})])
    chain = ModelChain("classifier", ["m"], llm_factory=lambda _: model, retry_seconds=None)
    last = HistoryEntry(user="How long is a marketing approval valid?", reply="Five years.", kind="answer")
    got = await understand("so are we fine then?", [last], chain)
    assert got.route.kind == "answer" and got.route.preface == "decision" and got.route.parts == ("How long is a marketing approval valid?",)
    assert got.how == "classifier+decision_followup"
