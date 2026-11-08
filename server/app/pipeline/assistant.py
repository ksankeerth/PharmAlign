"""The Knowledge Assistant: the server opens a session with the agent and relays its streamed answer.

A session is knowledge-only, or it is also about one case. What the assistant says is stored and shown. The server checks each quoted
citation against its source and adds the document's title and legal status itself. The assistant never changes a status: nothing here
writes a check, a thread or a case. A draft comment becomes a comment only when a person posts it through the threads route.
"""
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any, AsyncIterator, Mapping, Optional, Protocol

import httpx
from pharmalign_contracts.agents.knowledge_assistant import EVENT_ADAPTER, SessionCreate, SessionCreated, TurnRequest
from pharmalign_contracts.common import CONTRACT_HEADER, CONTRACT_VERSION, Evidence
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..context import Ctx, Runtime, Tenant
from ..identity import Actor
from ..stores import STORES
from ..tables import ROW
from .citations import CitationContext, SourceChunk, check_quote
from .client import TokenProvider, google_id_token, static_bearer
from .runner import citation_context
from .sources import kb_chunks, notice_chunks

CHECKS = STORES["check_result"]
TRACES = STORES["check_trace"]
ASSISTANT_UNAVAILABLE = "The assistant isn't available. Saved traces still work."


class AssistantUnavailable(Exception):
    """The agent can't be reached or sent something unusable."""


class AssistantClient(Protocol):
    async def open(self, session: SessionCreate) -> str:
        """Starts a chat with the agent. Returns the agent's own session id."""

    def turn(self, agent_session_id: str, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]:
        """One turn, as the agent's events in order."""


# ---------------------------------------------------------------- over HTTP


class HttpAssistantClient:
    def __init__(self, base_url: Optional[str], token: Optional[str] = None, *, transport: Optional[httpx.AsyncBaseTransport] = None,
                 connect_seconds: float = 5.0, read_seconds: float = 60.0, token_provider: Optional[TokenProvider] = None):
        """`token_provider` gives the bearer for the agent's address, as for the run agents: a Google ID token on Cloud Run,
        where the agent is private. Without one, `token` is sent as it is."""
        self.base_url = base_url.rstrip("/") if base_url else None
        self.token_provider = token_provider or static_bearer(token)
        self.http = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(read_seconds, connect=connect_seconds))

    async def aclose(self) -> None:
        await self.http.aclose()

    async def _headers(self, accept: str) -> dict:
        headers = {CONTRACT_HEADER: CONTRACT_VERSION, "Content-Type": "application/json", "Accept": accept}
        token = await self.token_provider(self.base_url or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def open(self, session: SessionCreate) -> str:
        if not self.base_url:
            raise AssistantUnavailable("no URL is set for the Knowledge Assistant")
        try:
            response = await self.http.post(f"{self.base_url}/sessions", headers=await self._headers("application/json"), content=session.model_dump_json())
            if response.status_code not in (200, 201):
                raise AssistantUnavailable(f"the agent answered {response.status_code}")
            return SessionCreated.model_validate_json(response.content).agent_session_id
        except (httpx.TransportError, ValidationError) as e:
            raise AssistantUnavailable(str(e)) from e

    async def turn(self, agent_session_id: str, request: TurnRequest) -> AsyncIterator[Mapping[str, Any]]:
        if not self.base_url:
            raise AssistantUnavailable("no URL is set for the Knowledge Assistant")
        try:
            async with self.http.stream("POST", f"{self.base_url}/sessions/{agent_session_id}/turns", headers=await self._headers("text/event-stream"),
                                        content=request.model_dump_json()) as response:
                if response.status_code != 200:
                    raise AssistantUnavailable(f"the agent answered {response.status_code}")
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line.removeprefix("data:").strip())
        except (httpx.TransportError, ValueError) as e:
            raise AssistantUnavailable(str(e)) from e



# ---------------------------------------------------------------- the client factory


def make_assistant_client(settings, sessions: async_sessionmaker[AsyncSession]) -> AssistantClient:
    mode = settings.agent_mode or ("replay" if settings.env == "simulation" else "http")
    if mode == "replay":
        from .assistant_replay import ReplayAssistantClient  # it imports this module

        return ReplayAssistantClient(sessions, settings)
    provider = google_id_token() if settings.agent_auth == "google_id_token" else None
    return HttpAssistantClient(settings.agent_url_knowledge_assistant, settings.agent_token, connect_seconds=settings.agent_connect_seconds,
                               token_provider=provider)


# ---------------------------------------------------------------- the relay


@asynccontextmanager
async def _as_person(rt: Runtime, actor: Actor) -> AsyncIterator[Ctx]:
    async with rt.sessions() as db, db.begin():
        ctx = Ctx(rt, db, actor, Tenant(actor.tenant_id, rt.settings.env))
        yield ctx
        await ctx.flush()


@dataclass(frozen=True)
class Chat:
    """What the relay knows about one session."""

    id: str
    agent_session_id: str
    case_id: Optional[str]
    snapshot_id: str


async def _basis(rt: Runtime, actor: Actor, chat: Chat, trace_ids: list[str]) -> str:
    """`saved` only if every trace the answer read is a real saved trace of this case. `knowledge` when the chat has no case. The model never chooses it."""
    if not chat.case_id:
        return "knowledge"
    if not trace_ids:
        return "new_reading"
    async with _as_person(rt, actor) as ctx:
        for trace_id in trace_ids:
            trace = await TRACES.get(ctx, trace_id)
            check = await CHECKS.get(ctx, trace_id)
            if trace is None or trace.basis == "not_saved" or check is None or check.case_id != chat.case_id:
                return "new_reading"
    return "saved"


LABELS = {
    "knowledge": """SELECT ch.id AS anchor, dv.id AS version_id, dv.document_id, d.doc ->> 'title' AS title, i.legal_status, ch.article, ch.page
                    FROM chunk ch JOIN document_version dv ON dv.id = ch.document_version_id JOIN document d ON d.id = dv.document_id
                    JOIN kb_snapshot_item i ON i.document_version_id = dv.id AND i.snapshot_id = :snapshot AND i.tenant_id = :tenant
                    WHERE ch.id = ANY(:anchors)""",
    "notice": """SELECT r.text_anchor AS anchor, n.id || chr(58) || 'r' || r.revision AS version_id, n.id AS document_id,
                        coalesce(n.doc ->> 'title', n.reference, n.id) AS title, NULL AS legal_status, NULL AS article, NULL AS page
                 FROM foreign_notice_revision r JOIN foreign_notice n ON n.id = r.notice_id AND n.latest_revision = r.revision
                 WHERE n.tenant_id = :tenant AND r.text_anchor = ANY(:anchors)""",
    "case_file": """SELECT c.id AS anchor, NULL AS version_id, dd.id AS document_id, dd.filename AS title, NULL AS legal_status, NULL AS article, c.page
                    FROM dossier_chunk c JOIN dossier_document dd ON dd.id = c.document_id
                    WHERE c.tenant_id = :tenant AND c.case_id = :case AND c.id = ANY(:anchors)
                    UNION ALL
                    SELECT p.anchor, NULL, dd.id, dd.filename, NULL, NULL, p.page
                    FROM dossier_page p JOIN dossier_document dd ON dd.id = p.document_id
                    WHERE p.tenant_id = :tenant AND p.case_id = :case AND p.anchor = ANY(:anchors)
                    UNION ALL
                    SELECT r.id, NULL, r.document_id, 'Reference approval ' || (b.doc ->> 'approval_ref'), NULL, NULL, r.page
                    FROM reference_page r JOIN reliance_basis b ON b.id = r.basis_id
                    WHERE r.tenant_id = :tenant AND b.target_id = :case AND r.id = ANY(:anchors)""",
}
ORIGIN_KIND = {"kb": "knowledge", "dossier": "case_file", "notice": "notice", "reference": "case_file"}


async def _citation_chunks(ctx: Ctx, chat: Chat, evidence: list[Evidence], question: str) -> dict[str, SourceChunk]:
    """What a quote is checked against. A status other than in force is shown on the citation, so it is not a reason to refuse one."""
    if chat.case_id:
        case = await STORES["case"].get(ctx, chat.case_id)
        chunks = dict((await citation_context(ctx, case, evidence, question, (), ())).chunks)
    else:
        anchors = {e.anchor for e in evidence}
        chunks = await kb_chunks(ctx.db, ctx.tenant.id, chat.snapshot_id, anchors)
        chunks.update(await notice_chunks(ctx.db, ctx.tenant.id, anchors))
    return {a: replace(c, in_force=True) for a, c in chunks.items()}


async def check_citations_of(ctx: Ctx, chat: Chat, evidence: list[Evidence], question: str) -> tuple[list[dict], int]:
    """Keeps each citation whose quote is really in its source, labelled with the title and legal status the knowledge base holds. Counts the rest."""
    chunks = await _citation_chunks(ctx, chat, evidence, question)
    context = CitationContext(chunks, question)
    kept = [e for e in evidence if e.anchor in chunks and check_quote(e, context) is None]
    anchors_of: dict[str, list[str]] = {}
    for e in kept:
        anchors_of.setdefault(ORIGIN_KIND[chunks[e.anchor].origin], []).append(e.anchor)
    labels: dict[str, Any] = {}
    for kind, anchors in anchors_of.items():
        params = {"tenant": ctx.tenant.id, "anchors": anchors, "snapshot": chat.snapshot_id, "case": chat.case_id}
        used = {k: v for k, v in params.items() if f":{k}" in LABELS[kind]}
        for row in (await ctx.db.execute(text(LABELS[kind]), used)).mappings().all():
            labels[row["anchor"]] = {**row, "source": kind}
    citations = [
        {"anchor": e.anchor, "quote": e.quote, "source": labels[e.anchor]["source"], "title": labels[e.anchor]["title"],
         "document_id": labels[e.anchor]["document_id"], "document_version_id": labels[e.anchor]["version_id"],
         "legal_status": labels[e.anchor]["legal_status"], "article": labels[e.anchor]["article"], "page": labels[e.anchor]["page"]}
        for e in kept if e.anchor in labels
    ]
    return citations, len(evidence) - len(citations)


def _said(event: Mapping[str, Any]) -> Optional[str]:
    kind = event["type"]
    if kind in ("text_delta", "message", "draft_comment"):
        return event["text"]
    return event.get("detail") if kind in ("not_available", "no_source") else None


async def relay_turn(rt: Runtime, actor: Actor, chat: Chat, request: TurnRequest) -> AsyncIterator[dict]:
    """Relays the agent's events to the browser, then stores the exchange. Runs after the request's own transaction has committed."""
    said: list[str] = []
    failed: Optional[str] = None
    try:
        async for raw in rt.assistant.turn(chat.agent_session_id, request):
            try:
                event = EVENT_ADAPTER.validate_python(raw).model_dump(mode="json")
            except ValidationError:
                failed = "bad_event"
                yield {"type": "error", "code": "bad_event", "message": "The assistant sent something the server can't use.", "retryable": False}
                return
            if event["type"] == "message":
                async with _as_person(rt, actor) as ctx:
                    citations, unverified = await check_citations_of(ctx, chat, [Evidence(**c) for c in event["citations"]], request.message)
                event = {"type": "message", "text": event["text"], "basis": await _basis(rt, actor, chat, event["trace_ids"]),
                         "citations": citations, "unverified": unverified, "suggestions": event["suggestions"]}
            part = _said(event)
            if part:
                said.append(part)
            yield event
    except AssistantUnavailable:
        failed = "assistant_unavailable"
        yield {"type": "error", "code": "assistant_unavailable", "message": ASSISTANT_UNAVAILABLE, "retryable": True}
    finally:
        async with _as_person(rt, actor) as ctx:
            ctx.db.add(ROW["assistant_exchange"](
                id=ctx.id("ax"), tenant_id=ctx.tenant.id, session_id=chat.id, person_id=actor.id, at=ctx.now(), message=request.message, answer="\n".join(said),
            ))
            ctx.audit("assistant_turn", "assistant_session", chat.id, chat.case_id, after={"answered": bool(said), "error": failed})
