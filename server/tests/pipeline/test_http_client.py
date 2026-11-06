"""HttpAgentClient against a small fake agent built from the contract's own request and response models."""
import httpx
import pytest
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pharmalign_contracts.agents.run_agents import AssessmentRequest, RUN_AGENTS
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION, Problem, completed_response
from pydantic import ValidationError

from app.config import Settings
from app.pipeline.client import HttpAgentClient, agent_urls, make_agent_client, static_bearer
from app.pipeline.replay import ReplayAgentClient

AGENT = "checklist-assessor"
SPEC = next(a for a in RUN_AGENTS if a.name == AGENT)
BODY = SPEC.request_example
RUN_ID = BODY["run_id"]


def problem(status: int, code: str) -> JSONResponse:
    return JSONResponse(Problem(title=code, status=status, code=code).model_dump(), status_code=status, media_type="application/problem+json")


def fake_agent():
    """Follows the contract: 202 on a new run, 200 on the same body, 409 on a different one, 404 for an unknown id."""
    app = FastAPI()
    app.state.runs, app.state.seen, app.state.fail_next = {}, [], []

    def running():
        return {"run_id": RUN_ID, "status": "running", "agent": {"name": AGENT, "version": "1", "prompt_hash": "sha256:7ab2"}}

    @app.post("/assessments")
    async def start(request: Request):
        app.state.seen.append(dict(request.headers))
        if app.state.fail_next:
            return problem(app.state.fail_next.pop(0), "unavailable")
        raw = await request.json()
        try:
            AssessmentRequest.model_validate(raw)
        except ValidationError:
            return problem(422, "invalid")
        known = app.state.runs.get(raw["run_id"])
        if known and known != raw:
            return problem(409, "conflict")
        status = 200 if known else 202
        app.state.runs[raw["run_id"]] = raw
        return JSONResponse(running(), status_code=status)

    @app.get("/assessments/{run_id}")
    async def poll(run_id: str, request: Request, x_pharmalign_contract: str = Header()):
        app.state.seen.append(dict(request.headers))
        if run_id not in app.state.runs:
            return problem(404, "unknown")
        return JSONResponse(completed_response(run_id, AGENT, SPEC.result_example))

    @app.delete("/assessments/{run_id}")
    async def stop(run_id: str, request: Request):
        app.state.seen.append(dict(request.headers))
        if run_id not in app.state.runs:
            return problem(404, "unknown")
        return JSONResponse({**running(), "status": "cancelled", "finish_reason": "cancelled"})

    return app


@pytest.fixture
def fake():
    return fake_agent()


def client_for(fake, retries=1, token="static-token", **kw) -> HttpAgentClient:
    async def no_wait(seconds):
        pass

    return HttpAgentClient({AGENT: "http://agent.test/"}, static_bearer(token), transport=httpx.ASGITransport(app=fake), retries=retries, sleep=no_wait, **kw)


async def test_it_sends_the_contract_header_and_the_token(fake):
    reply = await client_for(fake).start(AGENT, BODY)
    assert reply.status == 202 and reply.run.status == "running"
    sent = fake.state.seen[0]
    assert sent[CONTRACT_HEADER.lower()] == CONTRACT_VERSION
    assert sent["authorization"] == "Bearer static-token"


async def test_a_token_provider_can_be_replaced_and_is_asked_per_agent(fake):
    audiences = []

    async def identity_token(audience):
        audiences.append(audience)
        return "minted-for-this-agent"

    client = HttpAgentClient({AGENT: "http://agent.test/"}, identity_token, transport=httpx.ASGITransport(app=fake))
    await client.start(AGENT, BODY)
    assert fake.state.seen[0]["authorization"] == "Bearer minted-for-this-agent"
    assert audiences == ["http://agent.test/"]


async def test_the_same_body_again_answers_200(fake):
    client = client_for(fake)
    await client.start(AGENT, BODY)
    reply = await client.start(AGENT, BODY)
    assert reply.status == 200 and reply.run.run_id == RUN_ID


async def test_poll_returns_a_finished_run_checked_by_its_contract_model(fake):
    client = client_for(fake)
    await client.start(AGENT, BODY)
    reply = await client.poll(AGENT, RUN_ID)
    assert reply.status == 200 and reply.run.status == "completed"
    assert type(reply.run) is SPEC.run


async def test_404_on_poll(fake):
    reply = await client_for(fake).poll(AGENT, "no-such-run")
    assert (reply.status, reply.run, reply.invalid) == (404, None, None)


async def test_409_for_a_different_body(fake):
    client = client_for(fake)
    await client.start(AGENT, BODY)
    reply = await client.start(AGENT, {**BODY, "language": "ar"})
    assert reply.status == 409 and reply.run is None


async def test_422_for_a_body_that_breaks_the_contract(fake):
    reply = await client_for(fake).start(AGENT, {**BODY, "extra_field": True})
    assert reply.status == 422 and len(fake.state.seen) == 1, "a 422 is not retried"


async def test_500_is_retried_with_the_same_id(fake):
    fake.state.fail_next = [500]
    reply = await client_for(fake, retries=1).start(AGENT, BODY)
    assert reply.status == 202 and len(fake.state.seen) == 2


async def test_500_that_doesnt_clear_comes_back_as_500(fake):
    fake.state.fail_next = [500, 500, 500]
    reply = await client_for(fake, retries=1).start(AGENT, BODY)
    assert reply.status == 500 and len(fake.state.seen) == 2


async def test_a_body_that_breaks_the_contract_is_refused_as_invalid():
    async def handler(request):
        return httpx.Response(200, json={"run_id": RUN_ID, "status": "completed", "result": {"outcome": {"status": "pass"}}})

    client = HttpAgentClient({AGENT: "http://agent.test"}, static_bearer(None), transport=httpx.MockTransport(handler))
    reply = await client.poll(AGENT, RUN_ID)
    assert reply.run is None and reply.invalid == "schema_invalid"


async def test_a_network_failure_is_retried_then_reported_as_no_answer():
    calls = []

    async def handler(request):
        calls.append(request)
        raise httpx.ConnectError("refused")

    async def no_wait(seconds):
        pass

    client = HttpAgentClient({AGENT: "http://agent.test"}, static_bearer(None), transport=httpx.MockTransport(handler), retries=2, sleep=no_wait)
    assert (await client.poll(AGENT, RUN_ID)).status == 0
    assert len(calls) == 3


async def test_an_agent_with_no_url_is_no_answer():
    client = HttpAgentClient({AGENT: None}, static_bearer(None))
    assert (await client.start(AGENT, BODY)).status == 0


async def test_alive_is_true_for_a_200_and_false_for_anything_else():
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(int(request.url.host.split(".")[0].removeprefix("s")))

    client = HttpAgentClient({"up": "http://s200.test", "broken": "http://s500.test"}, static_bearer("tok"), transport=httpx.MockTransport(handler))
    assert await client.alive("up") is True
    assert await client.alive("broken") is False
    assert seen[0].url.path == "/health" and seen[0].headers["Authorization"] == "Bearer tok"


async def test_alive_is_false_when_the_connection_fails_and_makes_one_try():
    calls = []

    async def handler(request):
        calls.append(request)
        raise httpx.ConnectError("refused")

    client = HttpAgentClient({AGENT: "http://agent.test"}, static_bearer(None), transport=httpx.MockTransport(handler), retries=2)
    assert await client.alive(AGENT) is False
    assert len(calls) == 1


async def test_alive_is_false_for_an_agent_with_no_url():
    assert await HttpAgentClient({AGENT: None}, static_bearer(None)).alive(AGENT) is False
    assert await HttpAgentClient({}, static_bearer(None)).alive("unknown") is False


async def test_cancel_uses_delete(fake):
    client = client_for(fake)
    await client.start(AGENT, BODY)
    reply = await client.cancel(AGENT, RUN_ID)
    assert reply.status == 200 and reply.run.status == "cancelled"


def test_each_agent_has_one_url_setting_and_simulation_replays():
    settings = Settings(env="simulation", agent_url_sameness="http://sameness.test")
    assert set(agent_urls(settings)) == {a.name for a in RUN_AGENTS} | {"knowledge-assistant"}
    assert agent_urls(settings)["sameness"] == "http://sameness.test"
    assert isinstance(make_agent_client(settings, None), ReplayAgentClient)
    assert isinstance(make_agent_client(Settings(env="live"), None), HttpAgentClient)
    assert isinstance(make_agent_client(Settings(env="simulation", agent_mode="http"), None), HttpAgentClient)
