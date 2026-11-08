"""What does this message mean? One function, used by the pipeline and by the conversation eval.

Steps, in order:
    a  the language gate (Arabic script)        -> fixed "language", no model call
    b  a short reply that picks a suggestion    -> answer, no model call
    c  the classifier (fails open)
    d  the English guard: typo-laden English is not "another language"
    e  a clarify follow-up is never vague again
    f  route, then style, sources_named and search_all go on the Route
"""
import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.chat.classify import classify
from app.chat.gate import needs_english
from app.chat.labels import HistoryEntry, Labels, Route
from app.chat.models import ModelCall, ModelChain
from app.chat.route import route as decide


@dataclass
class Understanding:
    labels: Labels
    route: Route
    calls: list[ModelCall] = field(default_factory=list)
    how: str = "classifier"  # "gate", "suggestion_pick", "classifier", "classifier+clarify_followup" or "classifier+decision_followup"


# b: short replies that pick one of the suggestions we showed
LEADING = frozenset({"ok", "yes", "sure", "please", "the"})
AFFIRMATIONS = frozenset({"yes", "yeah", "yep", "sure", "ok", "okay", "please", "go ahead", "do it", "that one", "sounds good"})
ORDINALS: dict[str, int] = {  # index into the suggestions; -1 is the last one
    **dict.fromkeys(("first", "1st", "1", "one", "first one", "number 1", "number one", "start with the first",
                     "start with the first one"), 0),
    **dict.fromkeys(("second", "2nd", "2", "two", "second one", "number 2", "number two"), 1),
    **dict.fromkeys(("third", "3rd", "3", "three", "third one", "number 3", "number three"), 2),
    **dict.fromkeys(("last", "last one"), -1),
}


def _plain(message: str) -> str:
    """Lowercase, punctuation turned into spaces, spaces collapsed."""
    return " ".join(re.sub(r"[^\w\s]", " ", message.lower()).split())


def _picks(message: str, suggestions: Sequence[str]) -> str | None:
    """The suggestion this message picks, or None. The whole message must be an affirmation or an ordinal, apart from
    leading ok / yes / sure / please / the."""
    words = _plain(message).split()
    for skip in range(len(words)):
        if skip and words[skip - 1] not in LEADING:
            break
        text = " ".join(words[skip:])
        if text in AFFIRMATIONS:
            return suggestions[0]
        if text in ORDINALS:
            index = ORDINALS[text]
            if index == -1 or index < len(suggestions):
                return suggestions[index]
    return None


# d: a message in plain ASCII with enough English function words is English, whatever the classifier said
ENGLISH_WORDS = frozenset(
    "the is a an of for to what how can do does in and my we our i you it be are must when who which there any should will".split()
)
ENGLISH_SHARE = 0.30


# A message that names another language is asking for an answer in it, not written in it: the classifier's english=false
# stands (seen: "can you answer in Arabic?" turned back into English by this guard).
LANGUAGES = re.compile(r"\b(arabic|french|urdu|hindi|farsi|persian|spanish|german|chinese|russian|tagalog|malayalam|bengali|"
                       r"turkish|italian|portuguese)\b", re.I)


def looks_english(message: str) -> bool:
    if not message.isascii() or LANGUAGES.search(message):
        return False
    words = re.findall(r"[a-z']+", message.lower())
    return bool(words) and sum(w in ENGLISH_WORDS for w in words) / len(words) >= ENGLISH_SHARE


# f: names that are the home library's own, so naming them does not call for a search of the whole library
LOCAL_SOURCES = frozenset({
    "uae", "ede", "mohap", "doh", "dha", "abu dhabi", "dubai", "sharjah", "ajman", "umm al quwain", "ras al khaimah",
    "fujairah",
})


def _is_local(name: str) -> bool:
    plain = _plain(name)
    return (plain.removeprefix("the ") if plain.startswith("the ") else plain) in LOCAL_SOURCES


async def understand(message: str, history: Sequence[HistoryEntry], classifier: ModelChain) -> Understanding:
    has_history = bool(history)
    last = history[-1] if history else None

    # a the language gate
    if needs_english(message):
        labels = Labels(kind="off_topic", follow_up=False, parts=(), history_wanted=False, english=False)
        return Understanding(labels, Route(kind="fixed", reply="language", has_history=has_history), [], "gate")

    # b a pick among the suggestions we showed
    if last is not None and last.suggestions and (picked := _picks(message, last.suggestions)):
        labels = Labels(kind="question", follow_up=True, parts=(picked,), history_wanted=False, english=True)
        return Understanding(labels, _finish(decide(labels, has_history), labels, last), [], "suggestion_pick")

    # c classify, d the English guard, e a clarify follow-up
    labels, calls = await classify(message, history, classifier)
    how = "classifier"
    if not labels.english and looks_english(message):
        labels = dataclasses.replace(labels, english=True)
    if last is not None and last.kind == "clarify" and labels.kind == "vague":
        labels = dataclasses.replace(labels, kind="question", parts=(message,))
        how = "classifier+clarify_followup"
    # "so are we fine then?" right after an answer: the rule it depends on is the question just answered
    # (seen: a decision request with no parts got only "I can't make that decision")
    if last is not None and last.kind == "answer" and labels.kind == "decision_request" and not labels.parts and last.user.strip():
        labels = dataclasses.replace(labels, parts=(last.user,), follow_up=True)
        how = "classifier+decision_followup"

    # f route
    return Understanding(labels, _finish(decide(labels, has_history), labels, last), calls, how)


def _finish(route: Route, labels: Labels, last: HistoryEntry | None) -> Route:
    after_nothing = last is not None and last.kind == "no_source" and labels.follow_up and route.kind == "answer"
    abroad = any(not _is_local(n) for n in labels.sources_named)
    return dataclasses.replace(
        route, style=labels.style, sources_named=labels.sources_named, search_all=after_nothing or abroad
    )
