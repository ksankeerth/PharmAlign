"""The reranker reads a question and a passage together, which vectors can't. It is slow, so it scores only a short list."""
from __future__ import annotations

from functools import lru_cache
from typing import Optional, Protocol, Sequence

from ..config import Settings


class Reranker(Protocol):
    name: str
    candidates: int  # how many passages it scores per search
    min_score: float  # below it a passage is not a source

    def score(self, question: str, passages: Sequence[str]) -> list[float]: ...


class BgeReranker:
    """BAAI/bge-reranker-v2-m3, on the bge-m3 extra. The model loads on first use. Scores are from 0 to 1."""

    name = "bge-reranker-v2-m3"
    max_length = 512

    def __init__(self, candidates: int, min_score: float):
        self.candidates = candidates
        self.min_score = min_score
        self._model = None

    def score(self, question: str, passages: Sequence[str]) -> list[float]:
        import torch
        from sentence_transformers import CrossEncoder

        if self._model is None:
            self._model = CrossEncoder("BAAI/bge-reranker-v2-m3", max_length=self.max_length, device="cpu")
        pairs = [(question, p) for p in passages]
        return [float(s) for s in self._model.predict(pairs, batch_size=8, show_progress_bar=False, activation_fn=torch.nn.Sigmoid())]


@lru_cache(maxsize=2)
def _build(candidates: int, floor: float) -> Reranker:
    return BgeReranker(candidates, floor)


def get_reranker(settings: Settings) -> Optional[Reranker]:
    return None if settings.reranker == "off" else _build(settings.rerank_candidates, settings.rerank_floor)
