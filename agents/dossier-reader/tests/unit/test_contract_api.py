"""The contract routes behave as openapi.yaml says, whatever the agent does.

These tests replace `read_dossier` with fakes, so they need no model and no credentials.
"""
import asyncio
import copy
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import reader
from app.contract import api
from app.contract.models import DossierReadResult

HEADERS = {"Authorization": "Bearer local-dev-token", "X-PharmAlign-Contract": "1.0.0"}
REQUEST = {
    "run_id": "test-run-001",
    "run_token": "example-signed-token",
    "tenant": "example-country",
    "mode": "simulation",
    "execution_profile": {"model_set": "default", "gate": "on", "data_class": "synthetic"},
    "as_of": "2026-11-01T10:00:00Z",
    "versions": {"pack": "pack-3", "kb_snapshot": "snap-14"},
    "budget": {"max_model_calls": 12, "max_tool_calls": 20, "deadline_seconds": 5},
    "case": {"case_id": "case-1042", "sequence": 2, "case_type": "new_registration"},
    "documents": [{"doc_id": "d-17", "version": "v1", "filename": "stability-report.pdf", "pages": 4, "language": "en"}],
    "modules": ["3.2.P.8"],
    "fact_keys": [{"key": "shelf_life_months", "description": "Proposed shelf life", "value_type": "number", "unit": "months"}],
}
GOOD_RESULT = {
    "document_map": [{"doc_id": "d-17", "module": "3.2.P.8", "basis": "Title on page 1"}],
    "facts": [{
        "key": "shelf_life_months", "status": "found", "value": "36 months", "number": 36, "unit": "months",
        "evidence": [{"anchor": "d-17:v1:p2", "quote": "a shelf life of 36 months"}],
    }],
}


@pytest.fixture
def client():
    api._runs.clear()
    app = FastAPI()
    api.attach_contract_routes(app)
    with TestClient(app) as c:
        yield c


def body(**changes) -> dict:
    b = copy.deepcopy(REQUEST)
    b.update(changes)
    return b


def wait(client, run_id: str) -> dict:
    for _ in range(100):
        run = client.get(f"/dossier-reads/{run_id}", headers=HEADERS).json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.02)
    raise AssertionError("run didn't finish")


def fake(result=None, error=None, delay=0.0):
    async def read_dossier(request):
        await asyncio.sleep(delay)
        if error:
            raise error
        return reader.ReadOutcome(result=DossierReadResult.model_validate(result))
    return read_dossier


def test_completed_run(client, monkeypatch):
    monkeypatch.setattr(reader, "read_dossier", fake(GOOD_RESULT))
    started = client.post("/dossier-reads", json=body(), headers=HEADERS)
    assert started.status_code == 202
    assert started.headers["X-PharmAlign-Contract"] == "1.0.0"
    run = wait(client, "test-run-001")
    assert run["status"] == "completed" and run["finish_reason"] == "stop"
    assert run["result"]["facts"][0]["number"] == 36


def test_not_built_yet_fails_honestly(client):
    client.post("/dossier-reads", json=body(), headers=HEADERS)
    run = wait(client, "test-run-001")
    assert run["status"] == "failed" and run["error"]["code"] == "not_implemented" and run["result"] is None


@pytest.mark.parametrize("bad", [
    {"facts": [{**GOOD_RESULT["facts"][0], "key": "batch_size"}]},  # not asked for
    {"document_map": [{"doc_id": "d-17", "module": "3.2.P.3", "basis": "x"}]},  # module not offered
    {"facts": [{**GOOD_RESULT["facts"][0], "evidence": [{"anchor": "d-99:v1:p1", "quote": "x"}]}]},  # unknown doc
])
def test_result_breaking_a_rule_fails(client, monkeypatch, bad):
    monkeypatch.setattr(reader, "read_dossier", fake({**GOOD_RESULT, **bad}))
    client.post("/dossier-reads", json=body(), headers=HEADERS)
    run = wait(client, "test-run-001")
    assert run["status"] == "failed" and run["error"]["code"] == "invalid_output"


def test_budget_stops_early(client, monkeypatch):
    monkeypatch.setattr(reader, "read_dossier", fake(error=reader.BudgetExceeded()))
    client.post("/dossier-reads", json=body(), headers=HEADERS)
    run = wait(client, "test-run-001")
    assert run["status"] == "stopped_early" and run["finish_reason"] == "budget"


def test_same_id_same_body_is_200_and_different_body_is_409(client, monkeypatch):
    monkeypatch.setattr(reader, "read_dossier", fake(GOOD_RESULT))
    assert client.post("/dossier-reads", json=body(), headers=HEADERS).status_code == 202
    assert client.post("/dossier-reads", json=body(), headers=HEADERS).status_code == 200
    again = client.post("/dossier-reads", json=body(tenant="other-country"), headers=HEADERS)
    assert again.status_code == 409
    assert again.headers["content-type"] == "application/problem+json"
    assert again.json()["code"] == "run_id_reused"


def test_cancel(client, monkeypatch):
    monkeypatch.setattr(reader, "read_dossier", fake(GOOD_RESULT, delay=3))
    client.post("/dossier-reads", json=body(), headers=HEADERS)
    cancelled = client.delete("/dossier-reads/test-run-001", headers=HEADERS)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


@pytest.mark.parametrize("headers, request_body, status, code", [
    ({"X-PharmAlign-Contract": "1.0.0"}, REQUEST, 401, "unauthorized"),
    ({"Authorization": "Bearer local-dev-token"}, REQUEST, 422, "contract_header_missing"),
    ({**HEADERS, "X-PharmAlign-Contract": "2.0.0"}, REQUEST, 422, "contract_version_unsupported"),
    (HEADERS, {**REQUEST, "fact_keys": []}, 422, "contract_violation"),
    (HEADERS, {**REQUEST, "mode": "live"}, 422, "contract_violation"),
    (HEADERS, {**REQUEST, "surprise": True}, 422, "contract_violation"),
])
def test_refusals(client, headers, request_body, status, code):
    response = client.post("/dossier-reads", json=request_body, headers=headers)
    assert response.status_code == status
    assert response.json()["code"] == code


def test_unknown_and_malformed_run_ids(client):
    assert client.get("/dossier-reads/never-started", headers=HEADERS).status_code == 404
    assert client.get("/dossier-reads/!!", headers=HEADERS).status_code == 422


def test_health(client):
    assert client.get("/health", headers=HEADERS).json() == {"status": "ok"}
    details = client.get("/health/details", headers=HEADERS).json()
    assert details["agent"] == "dossier-reader" and details["contract_version"] == "1.0.0"
