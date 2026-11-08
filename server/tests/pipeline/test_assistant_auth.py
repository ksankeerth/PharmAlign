"""The Knowledge Assistant is private on Cloud Run, so the server must send it the same bearer the run agents get."""
import httpx
import pytest
from pharmalign_contracts.agents.knowledge_assistant import SessionCreate

from app.config import Settings
from app.pipeline import assistant as assistant_module
from app.pipeline.assistant import HttpAssistantClient, make_assistant_client

pytestmark = pytest.mark.asyncio

BASE = "https://pharmalign-knowledge-assistant-example.a.run.app"


def _session() -> SessionCreate:
    return SessionCreate(
        run_token="pa1.test", tenant="ws-ae", mode="simulation",
        execution_profile={"model_set": "default", "gate": "on", "data_class": "synthetic"}, as_of="2026-11-08T10:00:00Z",
        versions={"pack": "pv-ae-3", "kb_snapshot": "snap-1"}, case_id=None, user_id="p-alex", view="case_officer", language="en",
    )


def _recorder(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"agent_session_id": "as-1"})

    return httpx.MockTransport(handle)


async def test_a_token_provider_gives_the_bearer_for_the_agents_address():
    seen: list[httpx.Request] = []
    asked: list[str] = []

    async def provider(url: str):
        asked.append(url)
        return "id-token-for-the-agent"

    client = HttpAssistantClient(BASE + "/", transport=_recorder(seen), token_provider=provider)
    try:
        await client.open(_session())
    finally:
        await client.aclose()
    assert seen[0].headers["Authorization"] == "Bearer id-token-for-the-agent"
    assert asked == [BASE]


async def test_without_a_provider_the_static_token_is_sent_and_none_means_no_header():
    seen: list[httpx.Request] = []
    with_token = HttpAssistantClient(BASE, "local-token", transport=_recorder(seen))
    without = HttpAssistantClient(BASE, None, transport=_recorder(seen))
    try:
        await with_token.open(_session())
        await without.open(_session())
    finally:
        await with_token.aclose()
        await without.aclose()
    assert seen[0].headers["Authorization"] == "Bearer local-token"
    assert "Authorization" not in seen[1].headers


async def test_the_cloud_setting_picks_google_id_tokens(monkeypatch):
    made: list[str] = []

    def fake_google_id_token():
        made.append("google")

        async def provide(url: str):
            return "google-id-token"

        return provide

    monkeypatch.setattr(assistant_module, "google_id_token", fake_google_id_token)
    cloud = Settings(agent_mode="http", agent_auth="google_id_token", agent_url_knowledge_assistant=BASE)
    local = Settings(agent_mode="http", agent_auth="static", agent_url_knowledge_assistant=BASE, agent_token="local-token")
    a = make_assistant_client(cloud, sessions=None)  # type: ignore[arg-type]
    b = make_assistant_client(local, sessions=None)  # type: ignore[arg-type]
    try:
        assert made == ["google"]
        assert await a.token_provider(BASE) == "google-id-token"
        assert await b.token_provider(BASE) == "local-token"
    finally:
        await a.aclose()
        await b.aclose()
