"""Embedders. bge-m3 runs locally when the `bge-m3` extra is installed. Tests and simulation use a small deterministic one.

Vectors of different models are never mixed: each stored vector names its model, and a search uses one model.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from functools import lru_cache
from typing import Optional, Protocol, Sequence

from ..config import Settings


class Embedder(Protocol):
    name: str  # stored with every vector
    dims: int
    max_chars: int  # longer text is split before it is embedded, never cut off
    min_score: float  # the similarity floor: below it a hit is not a source

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class EmbedderUnavailable(Exception):
    pass


STOPWORDS = frozenset(
    "a an the of for to in on at by with and or not is are was were be been this that these those it its as from into than then there their "
    "which who whom what when where how do does did can may must shall should will would has have had any all each per under over about between "
    "من في على إلى الى أن ان التي الذي هذا هذه عن أو او لا ما كل بعد قبل كان ذلك تم مع بين أي إذا حتى منها فيها عليه عند هو هي".split()
)
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_AR_MARKS = re.compile("[ً-ٰٟـ]")
_AR_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})


def _stem(w: str) -> str:
    """A light English stem, so 'closures' finds 'closure' and 'testing' finds 'test'."""
    if not w.isascii() or len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("sses", "xes", "ches", "shes", "zes")):
        return w[:-2]
    for suffix in ("ing", "ed"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def terms(text: str) -> list[str]:
    """The words of a text that carry meaning: lower case, Arabic marks and letter variants folded, stop words dropped, stemmed."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = _AR_MARKS.sub("", t).translate(_AR_FOLD)
    return [_stem(w) for w in _TOKEN.findall(t) if w not in STOPWORDS and len(w) > 1]


class HashEmbedder:
    """Feature hashing of the text's terms into a fixed vector. Deterministic and offline. It matches words, not meaning."""

    def __init__(self, dims: int = 256, min_score: float = 0.2):
        self.name = f"hash-{dims}"
        self.dims = dims
        self.max_chars = 4000
        self.min_score = min_score

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t[: self.max_chars]) for t in texts]

    def _one(self, text: str) -> list[float]:
        counts: dict[str, int] = {}
        for w in terms(text):
            counts[w] = counts.get(w, 0) + 1
        v = [0.0] * self.dims
        for w, n in counts.items():
            h = int.from_bytes(hashlib.blake2b(w.encode(), digest_size=8).digest(), "big")
            v[h % self.dims] += (1.0 if (h >> 32) & 1 else -1.0) * (1.0 + math.log(n))
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]


class BgeM3Embedder:
    """BAAI/bge-m3 through sentence-transformers. Optional extra: bge-m3."""

    name = "bge-m3"
    dims = 1024
    max_chars = 4000

    def __init__(self, min_score: float = 0.45):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise EmbedderUnavailable("bge-m3 is not installed. Install the bge-m3 extra.") from e
        self.min_score = min_score
        self._model = SentenceTransformer("BAAI/bge-m3")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = self._model.encode([t[: self.max_chars] for t in texts], normalize_embeddings=True, batch_size=16)
        return [list(map(float, row)) for row in out]


def bge_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None


@lru_cache(maxsize=4)
def _build(which: str, floor: Optional[float]) -> Embedder:
    if which == "bge-m3":
        return BgeM3Embedder(**({"min_score": floor} if floor is not None else {}))
    return HashEmbedder(**({"min_score": floor} if floor is not None else {}))


def get_embedder(settings: Settings) -> Embedder:
    """`auto` is bge-m3 when it is installed, and the deterministic embedder in simulation. Anywhere else with no model, it refuses."""
    which = settings.embedder
    if which == "auto":
        if bge_installed():
            which = "bge-m3"
        elif settings.env == "simulation":
            which = "hash"
        else:
            raise EmbedderUnavailable("No embedding model is installed. Install the bge-m3 extra, or run in simulation.")
    return _build(which, settings.similarity_floor)


def vector_text(v: Sequence[float]) -> str:
    """A vector as pgvector reads it."""
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"
