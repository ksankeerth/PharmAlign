"""How the server talks to agents. It knows them only through the OpenAPI contracts: POST, then poll, then DELETE to cancel."""
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol

import httpx
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION, RunResponse

from .output_control import AGENTS, read_run

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
HEALTH_SECONDS = 5.0
TokenProvider = Callable[[str], Awaitable[Optional[str]]]  # takes the agent's base URL, the audience of its token


@dataclass(frozen=True)
class AgentReply:
    status: int  # the HTTP status, or 0 if the agent never answered
    run: Optional[RunResponse] = None
    invalid: Optional[str] = None  # why the body was refused


class AgentClient(Protocol):
    async def start(self, agent: str, request: Mapping[str, Any]) -> AgentReply: ...
    async def poll(self, agent: str, run_id: str) -> AgentReply: ...
    async def cancel(self, agent: str, run_id: str) -> AgentReply: ...


def static_bearer(token: Optional[str]) -> TokenProvider:
    async def provide(audience: str) -> Optional[str]:
        return token

    return provide


ID_TOKEN_LIFETIME_SECONDS = 3300  # Google's tokens last an hour; one is replaced a little before that


def audience_of(url: str) -> str:
    """A Cloud Run service is called with a token for its address alone: no path, no trailing slash."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def google_id_token(*, fetch: Optional[Callable[[str], str]] = None, now: Callable[[], float] = time.monotonic) -> TokenProvider:
    """A Google ID token for the agents' address, from the service account the server runs as. Cloud Run checks it against the invoker permission.

    Fetching blocks, so it runs off the event loop. Tokens are kept per address until shortly before they end, and two calls at once share one fetch."""
    if fetch is None:
        def fetch(audience: str) -> str:
            import google.auth.transport.requests
            from google.oauth2 import id_token

            return id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)

    kept: dict[str, tuple[str, float]] = {}
    locks: dict[str, asyncio.Lock] = {}

    async def provide(url: str) -> Optional[str]:
        audience = audience_of(url)
        async with locks.setdefault(audience, asyncio.Lock()):
            held = kept.get(audience)
            if held is None or now() >= held[1]:
                kept[audience] = (await asyncio.to_thread(fetch, audience), now() + ID_TOKEN_LIFETIME_SECONDS)
            return kept[audience][0]

    return provide


def reply_from(agent: str, status: int, body: Any) -> AgentReply:
    if status not in (200, 202):
        return AgentReply(status)
    run, problem = read_run(agent, body)
    return AgentReply(status, run=run, invalid=problem)


class HttpAgentClient:
    """Retries and timeouts live here. The runner sees a reply, whatever happened on the wire."""

    def __init__(self, urls: Mapping[str, Optional[str]], token_provider: TokenProvider, *, transport: Optional[httpx.AsyncBaseTransport] = None,
                 connect_seconds: float = 5.0, read_seconds: float = 30.0, retries: int = 2, backoff_seconds: float = 0.5,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.urls = urls
        self.token_provider = token_provider
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.sleep = sleep
        self.http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(read_seconds, connect=connect_seconds))

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _send(self, method: str, agent: str, path: str, body: Optional[Mapping[str, Any]] = None) -> tuple[int, bytes]:
        base = self.urls.get(agent)
        if not base:
            return 0, b""
        status, content = 0, b""
        for attempt in range(self.retries + 1):
            if attempt:
                await self.sleep(self.backoff_seconds * 2 ** (attempt - 1))
            headers = {CONTRACT_HEADER: CONTRACT_VERSION, "Content-Type": "application/json"}
            token = await self.token_provider(base)
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                response = await self.http.request(method, f"{base.rstrip('/')}{path}", headers=headers, content=json.dumps(body) if body is not None else None)
            except httpx.TransportError:
                status, content = 0, b""
                continue
            status, content = response.status_code, response.content
            if status not in RETRY_STATUSES:
                break
        return status, content

    async def alive(self, agent: str) -> bool:
        """One quick question with no retries: a screen is waiting for the answer."""
        base = self.urls.get(agent)
        if not base:
            return False
        token = await self.token_provider(base)
        try:
            response = await self.http.get(f"{base.rstrip('/')}/health", headers={"Authorization": f"Bearer {token}"} if token else None, timeout=HEALTH_SECONDS)
        except httpx.TransportError:
            return False
        return response.status_code == 200

    async def start(self, agent: str, request: Mapping[str, Any]) -> AgentReply:
        status, body = await self._send("POST", agent, AGENTS[agent].path, request)
        return reply_from(agent, status, body)

    async def poll(self, agent: str, run_id: str) -> AgentReply:
        status, body = await self._send("GET", agent, f"{AGENTS[agent].path}/{run_id}")
        return reply_from(agent, status, body)

    async def cancel(self, agent: str, run_id: str) -> AgentReply:
        status, body = await self._send("DELETE", agent, f"{AGENTS[agent].path}/{run_id}")
        return reply_from(agent, status, body)


def agent_urls(settings) -> dict[str, Optional[str]]:
    return {name: getattr(settings, "agent_url_" + name.replace("-", "_")) for name in (*AGENTS, "knowledge-assistant")}


def make_agent_client(settings, clock) -> AgentClient:
    """Demo mode replays stored answers, and so does simulation unless told otherwise."""
    mode = settings.agent_mode or ("replay" if settings.env == "simulation" else "http")
    if mode == "replay":
        from .replay import ReplayAgentClient

        return ReplayAgentClient.from_files(clock=clock)
    token = google_id_token() if settings.agent_auth == "google_id_token" else static_bearer(settings.agent_token)
    return HttpAgentClient(
        agent_urls(settings), token, connect_seconds=settings.agent_connect_seconds,
        read_seconds=settings.agent_read_seconds, retries=settings.agent_retries,
    )
