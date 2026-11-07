"""The fixed texts of the chat, the three example questions, and the contract event for each fixed reply. No model writes any of this."""
from app.chat.labels import NOT_AVAILABLE_REPLIES, Reply, Route

GREETING = "Hello. I answer questions about UAE medicines regulation from this workspace's regulatory library, and every answer shows its sources. You could ask:"
GREETING_AGAIN = "You're welcome. Ask me anything else about the regulations."
CLARIFY = "Could you say a bit more about what you'd like to know? For example:"
SPLIT = "Please ask at most 3 questions at a time, so each answer can show its own sources. Here are your first questions, one at a time:"
LANGUAGE = "Please ask your question in English. I can still read and quote Arabic sources for you."
DISTRESS = "I'm sorry you're going through this. I can't help with how you're feeling, but a person can: please talk to your manager, a colleague you trust, or your organisation's support service. If anyone is in immediate danger, call 999 (police) or 998 (ambulance)."
DECISION = "I can't make that decision. A person decides. I can show you what the sources say."
DECISION_EXAMPLES = DECISION + " For example, you could ask:"
OFF_TOPIC = "I can only answer questions about medicines regulation from this workspace's regulatory library."
UNSAFE = "I can't do that. I answer only from the regulatory library, and every answer cites its sources."
OWN_DATA = "This chat answers from the regulatory library only and can't read cases. Open the case to see its checks and findings."
SUGGESTIONS = (
    "How long does a marketing approval for a medicine stay valid?",
    "What is the fee for the marketing authorization of a conventional pharmaceutical product?",
    "What is the deadline for reporting a serious adverse reaction?",
)

MAX_SUGGESTION_CHARS = 500  # the contract's ShortText limit

_NOT_AVAILABLE_TEXT = {"off_topic": OFF_TOPIC, "unsafe": UNSAFE, "own_data": OWN_DATA}
_PLAIN_TEXT = {"clarify": CLARIFY, "split": SPLIT, "language": LANGUAGE, "distress": DISTRESS, "decision": DECISION_EXAMPLES}


def texts_for(reply: Reply) -> tuple[str, ...]:
    """Every text this reply may send."""
    if reply == "greeting":
        return (GREETING, GREETING_AGAIN)
    if reply in _NOT_AVAILABLE_TEXT:
        return (_NOT_AVAILABLE_TEXT[reply],)
    return (_PLAIN_TEXT[reply],)


def _message(text: str, suggestions: tuple[str, ...] | list[str] = ()) -> dict:
    return {
        "type": "message",
        "text": text,
        "citations": [],
        "trace_ids": [],
        "suggestions": [s[:MAX_SUGGESTION_CHARS] for s in suggestions],
    }


def fixed_event(route: Route) -> dict:
    """The one contract event for a fixed route, as a plain dict."""
    if route.kind != "fixed" or route.reply is None:
        raise ValueError("fixed_event needs a fixed route")
    reply = route.reply
    if reply in NOT_AVAILABLE_REPLIES:
        return {"type": "not_available", "reason": "out_of_scope", "detail": _NOT_AVAILABLE_TEXT[reply]}
    if reply == "greeting":
        if route.has_history:
            return _message(GREETING_AGAIN)
        return _message(GREETING, SUGGESTIONS)
    if reply == "split":
        return _message(SPLIT, route.suggestions)
    if reply == "distress":
        return _message(DISTRESS)
    return _message(_PLAIN_TEXT[reply], SUGGESTIONS)
