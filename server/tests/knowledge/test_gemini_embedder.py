"""gemini-embedding-001 on Vertex AI, with the Google client replaced by a fake. Nothing here calls the network."""
import math
import threading
from types import SimpleNamespace

import pytest
from google.genai import errors

from app.config import Settings
from app.knowledge import embedding
from app.knowledge.embedding import GEMINI_MODEL, EmbedderUnavailable, GeminiEmbedder, HashEmbedder, get_embedder
from app.tables import EMBEDDING_DIMS

from .conftest import law


class FakeModels:
    """Answers with 3072 numbers per text, as the model does, and can fail on chosen calls."""

    def __init__(self, fail: dict[int, int] | None = None):
        self.calls = []
        self.fail = fail or {}  # call number -> HTTP status to fail with

    def embed_content(self, model, contents, config):
        n = len(self.calls)
        self.calls.append(SimpleNamespace(model=model, contents=list(contents), task=config.task_type, dims=config.output_dimensionality))
        if n in self.fail:
            raise errors.APIError(self.fail[n], {"error": {"message": "try again", "status": "UNAVAILABLE"}})
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[float((hash(t) >> i) % 7 + 1) for i in range(3072)]) for t in contents])


def embedder(models: FakeModels, **kw) -> GeminiEmbedder:
    return GeminiEmbedder(client=SimpleNamespace(models=models), sleep=lambda s: None, **{"workers": 1, **kw})


def test_vectors_have_1536_numbers_and_unit_length():
    vectors = embedder(FakeModels()).embed(["one", "two"])
    assert len(vectors) == 2 and all(len(v) == 1536 for v in vectors)
    assert all(math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, abs_tol=1e-9) for v in vectors)


def test_passages_and_questions_use_their_own_task_types():
    models = FakeModels()
    e = embedder(models)
    e.embed(["a passage"])
    e.embed_query(["a question"])
    assert [c.task for c in models.calls] == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
    assert all(c.model == "gemini-embedding-001" and c.dims == 1536 for c in models.calls)


def test_texts_go_in_batches_and_come_back_in_order():
    models = FakeModels()
    texts = [f"text {i}" for i in range(70)]
    vectors = embedder(models, batch=32).embed(texts)
    assert [len(c.contents) for c in models.calls] == [32, 32, 6] and len(vectors) == 70
    assert vectors == [v for t in texts for v in embedder(FakeModels()).embed([t])]  # same text, same vector, wherever it sat in the batch


def test_batches_can_run_side_by_side_and_still_come_back_in_order():
    texts = [f"text {i}" for i in range(100)]
    assert embedder(FakeModels(), batch=10, workers=4).embed(texts) == embedder(FakeModels(), batch=10, workers=1).embed(texts)


def test_a_busy_service_is_retried_with_a_growing_wait():
    waits = []
    models = FakeModels(fail={0: 429, 1: 503})
    e = GeminiEmbedder(client=SimpleNamespace(models=models), sleep=waits.append, workers=1, backoff_seconds=1.0)
    assert len(e.embed(["x"])[0]) == 1536
    assert len(models.calls) == 3 and waits == [1.0, 2.0]


def test_a_refusal_is_not_retried():
    models = FakeModels(fail={0: 400})
    with pytest.raises(EmbedderUnavailable, match="400"):
        embedder(models).embed(["x"])
    assert len(models.calls) == 1


def test_it_gives_up_after_its_retries():
    models = FakeModels(fail={i: 503 for i in range(10)})
    with pytest.raises(EmbedderUnavailable, match="503"):
        embedder(models, retries=2).embed(["x"])
    assert len(models.calls) == 3


def test_long_text_is_cut_to_the_limit_and_empty_text_is_not_sent_empty():
    models = FakeModels()
    embedder(models).embed(["y" * 9000, ""])
    assert len(models.calls[0].contents[0]) == 4000 and models.calls[0].contents[1] == " "


async def test_a_search_embeds_its_question_as_a_question_and_off_the_event_loop(lib, monkeypatch):
    seen = []

    class Recording(HashEmbedder):
        def embed(self, texts):
            seen.append(("passage", threading.get_ident()))
            return super().embed(texts)

        def embed_query(self, texts):
            seen.append(("question", threading.get_ident()))
            return super().embed(texts)

    monkeypatch.setattr(embedding, "_build", lambda *a, **k: Recording())
    await lib.publish("Storage rules", law("cold storage of vaccines"))
    seen.clear()
    await lib.search("cold storage of vaccines")
    assert [kind for kind, _ in seen] == ["question"] and seen[0][1] != threading.get_ident()


def test_the_model_is_registered_with_its_size_and_never_uses_the_reranker():
    assert EMBEDDING_DIMS[GEMINI_MODEL] == 1536 == GeminiEmbedder.dims
    e = get_embedder(Settings(embedder=GEMINI_MODEL, reranker="bge-reranker-v2-m3"))
    assert e.name == GEMINI_MODEL and e.reranker is None
    assert get_embedder(Settings(embedder="hash", reranker="off")).reranker is None


def test_the_project_and_location_reach_the_client(monkeypatch):
    monkeypatch.setattr(embedding, "_build", embedding._build.__wrapped__)  # no cache, so this call builds a new one
    e = get_embedder(Settings(embedder=GEMINI_MODEL, google_cloud_project="p-1", google_cloud_location="me-central1"))
    assert (e._project, e._location) == ("p-1", "me-central1")


def test_hash_and_bge_questions_are_embedded_like_passages():
    h = HashEmbedder()
    assert h.embed_query(["validity of approval"]) == h.embed(["validity of approval"])


class WordModels:
    """A stand-in for the service that turns words into a 3072-number vector, so meaning-by-words can be searched for real."""

    def embed_content(self, model, contents, config):
        hasher = HashEmbedder(dims=3072)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=hasher._one(t)) for t in contents])


async def test_a_library_ingested_and_searched_with_gemini_vectors(lib, engine, monkeypatch):
    from sqlalchemy import text

    from app.db import make_sessions

    monkeypatch.setattr(embedding, "_build", lambda *a, **k: GeminiEmbedder(client=SimpleNamespace(models=WordModels()), sleep=lambda s: None, min_score=0.2))
    doc, version = await lib.publish("Storage rules", law("cold storage of vaccines"))
    await lib.publish("Other rules", law("labelling of tablets"))
    async with make_sessions(engine)() as db:
        rows = (await db.execute(text("SELECT model, dims, vector_dims(embedding) AS n FROM chunk_embedding WHERE chunk_id LIKE :p"), {"p": f"{doc}:%"})).all()
    assert rows and {tuple(r) for r in rows} == {(GEMINI_MODEL, 1536, 1536)}
    found = await lib.search("cold storage of vaccines")
    assert found["no_source_found"] is False and found["hits"][0]["document_id"] == doc
    assert found["filters_applied"]["embedder"] == GEMINI_MODEL and "reranker" not in found["filters_applied"]
    assert (await lib.search("child resistant closure for oral liquids"))["no_source_found"] is True
