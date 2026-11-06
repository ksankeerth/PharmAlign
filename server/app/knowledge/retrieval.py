"""Retrieval tests: a set of questions with a known right document, run through the same search the agents use.

A question with no right document must come back as "no source found". Passing it counts as much as finding a document.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence

from pharmalign_contracts.server import models as m

from .embedding import Embedder, terms
from .search import Scope, search

PASS_DOCUMENT_HIT_AT_5 = 0.8


@dataclass(frozen=True)
class Question:
    text: str
    language: str
    document_id: Optional[str]  # None: no source should be found
    passage: tuple[str, ...] = ()  # words the right passage must contain
    slot: Optional[str] = None  # the required slot the document fills. Each country fills it with its own document


QUESTION_SETS: dict[str, list[Question]] = {
    "core-questions": [
        Question("What long-term stability conditions apply in a hot climate?", "en", "E-S25", ("long", "term", "testing"), slot="stability_guideline"),
        Question("Who may operate a pharmaceutical establishment?", "en", "A-01", ("licence",), slot="medicines_law"),
        Question("What fee applies to registering a medicine?", "en", "A-03", slot="fees_schedule"),
        Question("Are vaping liquids with nicotine allowed?", "en", None),
        Question("How is a product's sameness with a reference approval judged?", "en", "F-01", ("sameness",), slot="reliance_guidance"),
    ]
}


def for_country(questions: Sequence[Question], slots: Sequence[m.Slot]) -> list[Question]:
    """The questions as one country must answer them: each asks for the document that country linked to the slot.
    The passage words were written for the default document, so they go with it. An empty slot keeps the default and misses."""
    linked = {s.key: s.document_id for s in slots if s.document_id}
    return [replace(q, document_id=linked[q.slot], passage=()) if linked.get(q.slot, q.document_id) != q.document_id else q for q in questions]


async def run_questions(db, scope: Scope, embedder: Embedder, questions: list[Question]) -> m.RetrievalTestRun:
    results: list[m.RetrievalTestResult] = []
    for q in questions:
        found = await search(db, scope, q.text, embedder, top_k=10)
        doc_rank = next((i for i, h in enumerate(found.hits, 1) if h.document_id == q.document_id), None) if q.document_id else None
        want = set(terms(" ".join(q.passage)))
        passage_rank = next((i for i, h in enumerate(found.hits, 1) if h.document_id == q.document_id and want <= set(terms(h.text))), None) if q.document_id else None
        results.append(m.RetrievalTestResult(
            question=q.text, language=q.language, expected_document_id=q.document_id, rank_of_document=doc_rank,
            rank_of_passage=passage_rank if q.passage else doc_rank, no_source_found=found.no_source_found,
        ))
    asked = [r for r in results if r.expected_document_id]

    def share(pick) -> float:
        return round(sum(1 for r in asked if pick(r)) / len(asked), 2) if asked else 0.0

    return m.RetrievalTestRun(
        id="run", status="completed",
        document_hit_at_1=share(lambda r: r.rank_of_document == 1), document_hit_at_5=share(lambda r: r.rank_of_document and r.rank_of_document <= 5),
        passage_hit_at_1=share(lambda r: r.rank_of_passage == 1), passage_hit_at_10=share(lambda r: r.rank_of_passage and r.rank_of_passage <= 10),
        results=results,
    )


def verdict(run: m.RetrievalTestRun, embedder: str = "") -> tuple[str, str]:
    """(status, detail) for the readiness check."""
    refused = [r for r in run.results if not r.expected_document_id]
    wrongly_answered = [r.question for r in refused if not r.no_source_found]
    n = len(run.results)
    detail = f"Document hit at 5: {run.document_hit_at_5:.2f} on {n} questions" + (f" (embedder: {embedder})." if embedder else ".")
    if wrongly_answered:
        return "fail", f"{detail} A question with no source was answered: {wrongly_answered[0]}"
    if (run.document_hit_at_5 or 0) < PASS_DOCUMENT_HIT_AT_5:
        return "fail", f"{detail} It needs {PASS_DOCUMENT_HIT_AT_5:.2f}."
    return "pass", detail
