"""Embedders. bge-m3 runs locally when the `bge-m3` extra is installed. gemini-embedding-001 runs on Vertex AI with the `gemini` extra.
Tests and simulation use a small deterministic one.

Vectors of different models are never mixed: each stored vector names its model, and a search uses one model.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Callable, Optional, Protocol, Sequence

from ..config import Settings


class Embedder(Protocol):
    reranker: Optional["Reranker"]  # set by get_embedder, so every search path ranks the same way
    name: str  # stored with every vector
    dims: int
    max_chars: int  # longer text is split before it is embedded, never cut off
    min_score: float  # the similarity floor: below it a hit is not a source

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for passages that will be stored."""

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for questions. The same as `embed` unless the model treats questions and passages differently."""


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

    reranker = None

    def __init__(self, dims: int = 256, min_score: float = 0.2):
        self.name = f"hash-{dims}"
        self.dims = dims
        self.max_chars = 4000
        self.min_score = min_score

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t[: self.max_chars]) for t in texts]

    embed_query = embed

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

    reranker = None

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

    embed_query = embed


log = logging.getLogger(__name__)
GEMINI_MODEL = "gemini-embedding-001"
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


class GeminiEmbedder:
    """gemini-embedding-001 on Vertex AI. It signs in with Application Default Credentials: the developer's login on a laptop, the service account in the cloud.

    Vectors are 1536 numbers, cut down from the model's 3072 and then scaled to length 1, because pgvector can index at most 2000 and a cut vector is no longer unit length.
    Passages and questions are embedded for those two jobs (RETRIEVAL_DOCUMENT and RETRIEVAL_QUERY). Texts go in batches, a few batches at a time, each retried.
    Every call blocks, so async code calls it through asyncio.to_thread. There is no reranker with this embedder.
    """

    reranker = None
    name = GEMINI_MODEL
    dims = 1536
    max_chars = 4000  # the model reads 2048 tokens; chunks are split to this size, as for bge-m3, so one library serves both

    def __init__(self, min_score: float = 0.65, project: Optional[str] = None, location: Optional[str] = None, batch: int = 32, workers: int = 4,
                 retries: int = 5, backoff_seconds: float = 1.0, sleep: Callable[[float], None] = time.sleep, client=None):
        self.min_score = min_score
        self.batch = batch
        self.workers = workers
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._project, self._location = project, location
        self._client = client
        self._lock = threading.Lock()

    def _models(self):
        with self._lock:  # threads share one client: a second one, built at the same moment and dropped, would close the first one's connection
            if self._client is None:
                try:
                    from google import genai
                except ImportError as e:
                    raise EmbedderUnavailable("The Google client is not installed. Install the gemini extra.") from e
                self._client = genai.Client(vertexai=True, project=self._project, location=self._location)
            return self._client.models

    def _call(self, texts: list[str], task: str) -> list[list[float]]:
        from google.genai import errors, types

        config = types.EmbedContentConfig(task_type=task, output_dimensionality=self.dims)
        for attempt in range(self.retries + 1):
            try:
                response = self._models().embed_content(model=self.name, contents=texts, config=config)
                vectors = [list(e.values) for e in response.embeddings]
                if len(vectors) != len(texts):
                    raise ValueError(f"{len(texts)} texts went in and {len(vectors)} vectors came back.")
                return [_unit(v[: self.dims]) for v in vectors]  # the service already returns this size; cutting is only a guard, and a cut vector must be scaled again
            except errors.APIError as e:
                if e.code not in RETRYABLE or attempt == self.retries:
                    raise EmbedderUnavailable(f"Vertex AI refused the embedding request ({e.code}): {str(e)[:200]}") from e
            except (ConnectionError, TimeoutError, OSError):
                if attempt == self.retries:
                    raise
            wait = self.backoff_seconds * 2 ** attempt
            log.warning("embedding call failed, retrying in %.1fs (attempt %d of %d)", wait, attempt + 1, self.retries)
            self._sleep(wait)
        raise AssertionError("unreachable")

    def _embed(self, texts: Sequence[str], task: str) -> list[list[float]]:
        clipped = [t[: self.max_chars] or " " for t in texts]  # the model refuses an empty text
        parts = [clipped[i:i + self.batch] for i in range(0, len(clipped), self.batch)]
        if len(parts) <= 1 or self.workers <= 1:
            return [v for part in parts for v in self._call(part, task)]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return [v for done in pool.map(lambda part: self._call(part, task), parts) for v in done]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, texts: Sequence[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_QUERY")


def _unit(v: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def bge_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("sentence_transformers") is not None


@lru_cache(maxsize=4)
def _build(which: str, floor: Optional[float], project: Optional[str] = None, location: Optional[str] = None) -> Embedder:
    kw = {"min_score": floor} if floor is not None else {}
    if which == "bge-m3":
        return BgeM3Embedder(**kw)
    if which == GEMINI_MODEL:
        return GeminiEmbedder(project=project, location=location, **kw)
    return HashEmbedder(**kw)


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
    from .rerank import get_reranker

    embedder = _build(which, settings.similarity_floor, settings.google_cloud_project, settings.google_cloud_location)
    embedder.reranker = None if which == GEMINI_MODEL else get_reranker(settings)  # the reranker is built for bge-m3; with Gemini, vector and keyword search stand alone
    return embedder


def vector_text(v: Sequence[float]) -> str:
    """A vector as pgvector reads it."""
    return "[" + ",".join(f"{x:.6g}" for x in v) + "]"
