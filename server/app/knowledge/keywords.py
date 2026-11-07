"""Keyword search for every question: stemmed words stored beside each chunk, and BM25 over the chunks a filter let through.

Scoring happens here and not in Postgres, whose ranking has no idea how rare a word is. Arabic attaches short words to the front of a
word and endings to the back, so one light stem makes المنتجات and للمنتج the same word. Pure, with no database.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Optional, Sequence

from .embedding import terms

QUESTION_WORDS = frozenset("هل كم كيف متى اين لماذا ماذا يجب يمكن يجوز لم لن اذا ان".split())
PREFIX = re.compile("^(وال|بال|كال|فال|لل|ال|و|ف|ب|ل)")
SUFFIX = re.compile("(ات|ون|ين|ان|ها|هم|ه|ي)$")
MIN_STEM = 3
K1, B = 1.2, 0.75
RRF_K = 60


def stem(word: str) -> str:
    """Strips one prefix and one ending from an Arabic word, unless too little would remain."""
    if word.isascii() or len(word) < 4:
        return word
    for pattern in (PREFIX, SUFFIX):
        shorter = pattern.sub("", word)
        word = shorter if len(shorter) >= MIN_STEM else word
    return word


def tokens(text: str) -> list[str]:
    return [stem(t) for t in terms(text) if t not in QUESTION_WORDS]


def tsquery(question: str) -> Optional[str]:
    """Any of the question's words, for to_tsquery('simple', ...). The words hold only letters and digits."""
    words = sorted(set(tokens(question)))
    return " | ".join(words) or None


def bm25(question: str, rows: Sequence[Any], total: int, limit: int = 30) -> list[Any]:
    """The rows best matching the question. Each row has its stored `tokens`. `total` is how many chunks the filter let through."""
    wanted = set(tokens(question))
    docs = [Counter(r["tokens"].split()) for r in rows]
    if not docs:
        return []
    average = sum(sum(d.values()) for d in docs) / len(docs)
    found_in = Counter(t for d in docs for t in wanted & d.keys())
    size = max(total, len(docs))

    def score(doc: Counter) -> float:
        length = sum(doc.values())
        return sum(
            math.log(1 + (size - found_in[t] + 0.5) / (found_in[t] + 0.5)) * doc[t] * (K1 + 1) / (doc[t] + K1 * (1 - B + B * length / average))
            for t in wanted & doc.keys()
        )

    order = sorted(range(len(rows)), key=lambda i: -score(docs[i]))
    return [rows[i] for i in order[:limit]]


def fuse(*ranked: Sequence[Any]) -> list[Any]:
    """Reciprocal rank fusion of ranked lists of rows, by chunk. A chunk high on either list comes out high."""
    scores: dict[str, float] = {}
    first: dict[str, Any] = {}
    for rows in ranked:
        for rank, row in enumerate(rows, 1):
            scores[row["chunk_id"]] = scores.get(row["chunk_id"], 0.0) + 1 / (RRF_K + rank)
            first.setdefault(row["chunk_id"], row)
    return [first[k] for k in sorted(scores, key=lambda k: (-scores[k], k))]
