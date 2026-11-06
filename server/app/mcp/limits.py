"""Keeps one tool result under the character limit. A cut result says so."""
import json
from typing import Any

ROOM = 400  # kept free for the note


def size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _strings(value: Any, path=()):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings(v, path + (i,))
    elif isinstance(value, str):
        yield path, value


def _set(value: Any, path: tuple, new: str) -> None:
    for step in path[:-1]:
        value = value[step]
    value[path[-1]] = new


def fit(result: dict, max_chars: int) -> dict:
    """Drops items from the end of the longest list, then shortens the longest texts, until the result fits."""
    if size(result) <= max_chars:
        return result
    result = json.loads(json.dumps(result, default=str))
    target = max(max_chars - ROOM, 200)
    dropped, cut = 0, 0
    lists = [k for k, v in result.items() if isinstance(v, list)]
    main = max(lists, key=lambda k: len(result[k]), default=None)
    while main and len(result[main]) > 1 and size(result) > target:
        result[main].pop()
        dropped += 1
    while size(result) > target:
        path, longest = max(_strings(result), key=lambda p: len(p[1]), default=((), ""))
        if len(longest) <= 40:
            break
        keep = max(len(longest) - (size(result) - target) - 20, 20)
        _set(result, path, longest[:keep] + " [cut]")
        cut += 1
    note = "Cut to fit the size limit."
    if dropped:
        note += f" {dropped} item(s) left out from the end of {main}; ask for fewer."
    if cut:
        note += " Long text was shortened where it says [cut]; read the page or clause for the rest."
    result["truncated"] = {"limit_chars": max_chars, "items_dropped": dropped, "texts_cut": cut, "note": note}
    return result
