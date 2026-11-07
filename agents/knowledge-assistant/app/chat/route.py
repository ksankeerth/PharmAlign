"""Decides what to do with a classified message: send a fixed reply, or search and answer. Pure code, no I/O."""
from app.chat.labels import MAX_PARTS, Labels, Route


def route(labels: Labels, has_history: bool) -> Route:
    """Distress first, then language, then the other labels."""
    def fixed(reply: str) -> Route:
        suggestions = labels.parts[:MAX_PARTS] if reply == "split" else ()
        return Route(kind="fixed", reply=reply, suggestions=suggestions, has_history=has_history)  # type: ignore[arg-type]

    def answer(preface: str | None = None) -> Route:
        return Route(
            kind="answer", parts=labels.parts, history_wanted=labels.history_wanted,
            preface=preface, has_history=has_history,  # type: ignore[arg-type]
        )

    if labels.kind == "distress":
        return fixed("distress")
    if not labels.english:
        return fixed("language")
    simple = {"small_talk": "greeting", "vague": "clarify", "off_topic": "off_topic", "unsafe": "unsafe", "own_data": "own_data"}
    if labels.kind in simple:
        return fixed(simple[labels.kind])
    if not labels.parts:
        return fixed("decision" if labels.kind == "decision_request" else "clarify")
    if len(labels.parts) > MAX_PARTS:
        return fixed("split")
    return answer("decision" if labels.kind == "decision_request" else None)
