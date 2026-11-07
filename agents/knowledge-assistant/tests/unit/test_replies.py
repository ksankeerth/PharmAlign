"""The fixed replies: every route becomes an event the contract accepts, and the texts stay exact."""
import pytest
from pharmalign_contracts.agents.knowledge_assistant import EVENT_ADAPTER, Message, NotAvailable

from app import replies
from app.chat.labels import NOT_AVAILABLE_REPLIES, Reply, Route

REPLIES: tuple[Reply, ...] = Reply.__args__  # type: ignore[attr-defined]


def fixed(reply: str, **kwargs) -> Route:
    return Route(kind="fixed", reply=reply, **kwargs)  # type: ignore[arg-type]


def event_of(route: Route):
    data = replies.fixed_event(route)
    return data, EVENT_ADAPTER.validate_python(data)


def test_greeting_without_history_introduces_and_suggests():
    data, event = event_of(fixed("greeting"))
    assert isinstance(event, Message)
    assert (data["text"], data["citations"], data["trace_ids"]) == (replies.GREETING, [], [])
    assert data["suggestions"] == list(replies.SUGGESTIONS)


def test_greeting_with_history_says_welcome_without_suggestions():
    data, event = event_of(fixed("greeting", has_history=True))
    assert isinstance(event, Message)
    assert (data["text"], data["suggestions"]) == (replies.GREETING_AGAIN, [])


@pytest.mark.parametrize(
    ("reply", "text"),
    [("clarify", replies.CLARIFY), ("language", replies.LANGUAGE), ("decision", replies.DECISION_EXAMPLES)],
)
def test_messages_with_example_suggestions(reply, text):
    data, event = event_of(fixed(reply))
    assert isinstance(event, Message)
    assert (data["text"], data["citations"], data["trace_ids"]) == (text, [], [])
    assert data["suggestions"] == list(replies.SUGGESTIONS)


def test_split_suggests_the_rewritten_parts():
    data, event = event_of(fixed("split", suggestions=("a?", "b?", "c?")))
    assert isinstance(event, Message)
    assert (data["text"], data["suggestions"]) == (replies.SPLIT, ["a?", "b?", "c?"])


def test_split_suggestion_over_500_chars_is_cut():
    data, event = event_of(fixed("split", suggestions=("x" * 900, "short?")))
    assert [len(s) for s in data["suggestions"]] == [500, 6]
    assert isinstance(event, Message)


def test_distress_has_no_suggestions():
    data, event = event_of(fixed("distress"))
    assert isinstance(event, Message)
    assert (data["text"], data["suggestions"]) == (replies.DISTRESS, [])


@pytest.mark.parametrize(
    ("reply", "text"), [("off_topic", replies.OFF_TOPIC), ("unsafe", replies.UNSAFE), ("own_data", replies.OWN_DATA)]
)
def test_not_available_replies(reply, text):
    data, event = event_of(fixed(reply))
    assert data == {"type": "not_available", "reason": "out_of_scope", "detail": text}
    assert isinstance(event, NotAvailable)


def test_every_reply_has_an_event():
    for reply in REPLIES:
        event_of(fixed(reply, suggestions=("a?",)))


def test_not_available_set_matches_the_replies_module():
    assert NOT_AVAILABLE_REPLIES == {"off_topic", "unsafe", "own_data"}


def test_example_suggestions_are_short_questions():
    assert len(replies.SUGGESTIONS) == 3
    for text in replies.SUGGESTIONS:
        assert len(text) < 500 and text.endswith("?")


def test_texts_for_covers_every_reply():
    assert replies.texts_for("greeting") == (replies.GREETING, replies.GREETING_AGAIN)
    assert replies.texts_for("decision") == (replies.DECISION_EXAMPLES,)
    for reply in REPLIES:
        texts = replies.texts_for(reply)
        assert texts and all(isinstance(t, str) and t for t in texts)


def test_texts_for_includes_what_fixed_event_sends():
    for reply in REPLIES:
        for has_history in (False, True):
            data = replies.fixed_event(fixed(reply, has_history=has_history))
            assert (data.get("text") or data["detail"]) in replies.texts_for(reply)


def test_fixed_event_rejects_an_answer_route():
    with pytest.raises(ValueError):
        replies.fixed_event(Route(kind="answer", parts=("a?",)))
