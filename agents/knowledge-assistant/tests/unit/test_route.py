"""route(): which fixed reply or answer each label set gets, and in which order the rules apply."""
import pytest

from app.chat.labels import Labels, Route
from app.chat.route import route


def labels(kind="question", parts=("What is the fee?",), english=True, history_wanted=False) -> Labels:
    return Labels(kind=kind, follow_up=False, parts=tuple(parts), history_wanted=history_wanted, english=english)


def test_distress_is_fixed():
    assert route(labels("distress", parts=()), False) == Route(kind="fixed", reply="distress")


@pytest.mark.parametrize("kind", ["off_topic", "question", "small_talk", "vague"])
def test_not_english_is_the_language_reply(kind):
    result = route(labels(kind, english=False), False)
    assert (result.kind, result.reply) == ("fixed", "language")


def test_distress_beats_not_english():
    assert route(labels("distress", parts=(), english=False), False).reply == "distress"


def test_not_english_beats_off_topic():
    assert route(labels("off_topic", parts=(), english=False), False).reply == "language"


@pytest.mark.parametrize(
    ("kind", "reply"),
    [("small_talk", "greeting"), ("vague", "clarify"), ("off_topic", "off_topic"), ("unsafe", "unsafe"), ("own_data", "own_data")],
)
def test_simple_kinds_are_fixed(kind, reply):
    result = route(labels(kind, parts=()), False)
    assert (result.kind, result.reply, result.suggestions) == ("fixed", reply, ())


def test_decision_request_without_parts_is_the_decision_reply():
    result = route(labels("decision_request", parts=()), False)
    assert (result.kind, result.reply) == ("fixed", "decision")


def test_decision_request_with_parts_answers_with_a_preface():
    result = route(labels("decision_request", parts=("What does the law say about X?",), history_wanted=True), False)
    assert result == Route(kind="answer", parts=("What does the law say about X?",), history_wanted=True, preface="decision")


def test_decision_request_with_too_many_parts_is_split():
    result = route(labels("decision_request", parts=("a?", "b?", "c?", "d?")), False)
    assert (result.kind, result.reply, result.preface) == ("fixed", "split", None)


def test_question_with_one_part_answers():
    result = route(labels(parts=("What is the fee?",), history_wanted=True), False)
    assert result == Route(kind="answer", parts=("What is the fee?",), history_wanted=True, preface=None)


def test_question_with_three_parts_answers():
    result = route(labels(parts=("a?", "b?", "c?")), False)
    assert (result.kind, result.parts) == ("answer", ("a?", "b?", "c?"))


def test_question_with_four_parts_is_split_with_the_first_three_as_suggestions():
    result = route(labels(parts=("a?", "b?", "c?", "d?")), False)
    assert (result.kind, result.reply, result.suggestions) == ("fixed", "split", ("a?", "b?", "c?"))


def test_question_with_no_parts_is_clarify():
    result = route(labels(parts=()), False)
    assert (result.kind, result.reply) == ("fixed", "clarify")


@pytest.mark.parametrize("has_history", [True, False])
@pytest.mark.parametrize(
    "given",
    [labels("small_talk", parts=()), labels(), labels("decision_request", parts=()), labels(parts=("a?", "b?", "c?", "d?"))],
)
def test_has_history_is_passed_through(given, has_history):
    assert route(given, has_history).has_history is has_history
