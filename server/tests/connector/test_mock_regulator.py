"""The mock regulator: simulation only, deduplicating, and valid against the regulator contract."""
import pytest
from pharmalign_contracts.common import Health, Problem
from pharmalign_contracts.regulator import REGULATOR_API, FailureModeSet, ProposalStatus, RecordEvent, RecordLicence, ReviewMetrics
from pharmalign_contracts.server.models import page_of

from app.main import UnsafeConfiguration
from app.mock_regulator.app import create_mock_app

from ..conftest import FixedIdentity, client_for
from .conftest import TOKEN

AUTH = {"Authorization": f"Bearer {TOKEN}"}
PROPOSAL = {
    "action": "grant", "decision_ref": "dec-1", "signed_payload_hash": "sha256:" + "a" * 64, "signed_by": "Dana", "signed_at": "2026-11-01T10:00:00Z",
    "payload": {"medicine_name": "Examplamab", "substances": ["examplamab"], "holder": "Example Pharma"},
}


@pytest.fixture(autouse=True)
def seeded():  # these tests need no cases at all: the mock has its own tables
    return None


@pytest.fixture
def mock_http(make_app):
    return client_for(make_app(settings={"connector_token": TOKEN}))


async def test_the_mock_regulator_is_not_mounted_outside_simulation(make_app):
    app = make_app(env="shadow", identity=FixedIdentity())
    async with client_for(app) as http:
        assert (await http.get("/mock/health")).status_code == 404
    with pytest.raises(UnsafeConfiguration):
        make_app(env="live", identity=FixedIdentity(), settings={"mock_regulator": True})
    with pytest.raises(RuntimeError):
        create_mock_app(app.state.rt)


async def test_the_mock_regulator_is_mounted_in_simulation(mock_http):
    async with mock_http as http:
        assert (await http.get("/mock/health")).json() == {"status": "ok"}


async def test_the_same_key_returns_the_first_result_and_applies_nothing_new(mock_http):
    async with mock_http as http:
        first = await http.post("/mock/proposals", json=PROPOSAL, headers={**AUTH, "Idempotency-Key": "idem-1"})
        again = await http.post("/mock/proposals", json=PROPOSAL, headers={**AUTH, "Idempotency-Key": "idem-1"})
        assert first.status_code == again.status_code == 202 and first.json() == again.json()
        assert first.json()["state"] == "applied"
        other = await http.post("/mock/proposals", json={**PROPOSAL, "signed_by": "Someone else"}, headers={**AUTH, "Idempotency-Key": "idem-1"})
        assert other.status_code == 409
        Problem.model_validate(other.json())
        assert len((await http.get("/mock/licences", headers=AUTH)).json()["items"]) == 1
        assert (await http.post("/mock/proposals", json=PROPOSAL, headers=AUTH)).status_code == 422  # a key is required


async def test_the_mock_regulator_asks_for_its_token(mock_http):
    async with mock_http as http:
        assert (await http.get("/mock/licences")).status_code == 401


async def test_mock_responses_validate_against_the_regulator_contract(mock_http):
    async with mock_http as http:
        sent = await http.post("/mock/proposals", json=PROPOSAL, headers={**AUTH, "Idempotency-Key": "idem-2"})
        status = ProposalStatus.model_validate(sent.json())
        found = page_of(ProposalStatus).model_validate((await http.get("/mock/proposals", params={"idempotency_key": "idem-2"}, headers=AUTH)).json())
        assert [i.id for i in found.items] == [status.id]
        assert page_of(ProposalStatus).model_validate((await http.get("/mock/proposals", params={"idempotency_key": "nope"}, headers=AUTH)).json()).items == []
        assert ProposalStatus.model_validate((await http.get(f"/mock/proposals/{status.id}", headers=AUTH)).json()) == status
        licences = page_of(RecordLicence).model_validate((await http.get("/mock/licences", headers=AUTH)).json())
        one = RecordLicence.model_validate((await http.get(f"/mock/licences/{licences.items[0].id}", headers=AUTH)).json())
        assert one.version == status.resulting_version and one.status == "licensed"
        page_of(RecordEvent).model_validate((await http.get("/mock/events", params={"since": "2026-01-01T00:00:00Z"}, headers=AUTH)).json())
        ReviewMetrics.model_validate((await http.get("/mock/metrics/review", params={"from": "2026-01-01", "to": "2026-12-31"}, headers=AUTH)).json())
        Health.model_validate((await http.get("/mock/health")).json())
        for mode in ("normal", "applied_but_timed_out", "reject_next", "unavailable", "slow"):
            FailureModeSet.model_validate((await http.post("/mock/test-controls/failure-mode", json={"mode": mode}, headers=AUTH)).json())


async def test_the_mock_serves_every_operation_in_the_contract(make_app):
    app = make_app()
    mounted = next(r.app for r in app.routes if getattr(r, "path", None) == "/mock")
    served = {(m, r.path) for r in mounted.routes for m in getattr(r, "methods", ())}
    wanted = {(r.method, r.path) for routes in REGULATOR_API.routes.values() for r in routes}
    assert wanted <= served, wanted - served
