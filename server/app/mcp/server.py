"""The agent tool server: MCP over streamable HTTP at /mcp.

Every request carries `Authorization: Bearer <run token>`. The token alone sets the scope. Each tool call:
  1. checks the run is still current and has budget left (server login),
  2. reads through the agent_read views in a read-only transaction scoped with set_config(..., true) (agent login),
  3. writes one agent_tool_log row, refused or not (agent login, its one INSERT grant).
"""
import hashlib
import json
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Optional

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.shared.exceptions import McpError
from pharmalign_contracts.agents.knowledge_assistant import KNOWLEDGE_TOOLS, TOOLS as ASSISTANT_TOOLS
from pharmalign_contracts.agents.run_agents import RUN_AGENTS
from pharmalign_contracts.common import ToolError
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ..db import CONNECT_ARGUMENTS, pool_arguments
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse

from ..config import Settings
from ..context import Runtime
from ..domain.status import UNFINISHED
from ..knowledge.embedding import get_embedder
from ..tables import ROW
from .limits import fit
from .token import ASSISTANT, BadToken, RunScope, read
from .tools import TOOLS, Limits, ToolFailure, schema

log = logging.getLogger(__name__)

# What each agent may call, from its contract.
CONTRACT_TOOLS: dict[str, tuple[str, ...]] = {a.name: a.tools for a in RUN_AGENTS} | {ASSISTANT: ASSISTANT_TOOLS}
CASELESS = {"reliance-watch", "gap-analyst"}  # may run for the whole workspace, with no case
SLUG = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SCOPED_AGENTS = frozenset({"checklist-assessor"})  # their knowledge tools read only the documents their check names
DEV_AGENT_PASSWORD = "pharmalign_agent"


def allowed(agent: str, case: Optional[str] = None) -> list[str]:
    """The agent's contract tools that are built. A tool not built yet is not listed. A chat with no case gets only the knowledge tools."""
    names = KNOWLEDGE_TOOLS if agent == ASSISTANT and not case else CONTRACT_TOOLS.get(agent, ())
    return [t for t in names if t in TOOLS]


def not_built() -> dict[str, list[str]]:
    return {agent: [t for t in tools if t not in TOOLS] for agent, tools in CONTRACT_TOOLS.items() if any(t not in TOOLS for t in tools)}


def agent_database_url(settings: Settings) -> str:
    if settings.agent_database_url:
        return settings.agent_database_url
    url = make_url(settings.database_url).set(username="pharmalign_agent", password=DEV_AGENT_PASSWORD)
    return url.render_as_string(hide_password=False)


def _bearer(headers: Headers) -> str:
    value = headers.get("authorization", "")
    return value[7:].strip() if value[:7].lower() == "bearer " else ""


def _hash(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _error(tool: str, code: str, message: str, retryable: bool = False) -> dict:
    return ToolError(tool=tool if SLUG.match(tool) else "unknown", code=code, message=message[:500], retryable=retryable).model_dump()


def agent_pool_share(pool_max: Optional[int]) -> Optional[int]:
    """The tool server's own login gets about half the server's connection limit, and at least 2."""
    return None if pool_max is None else max(2, pool_max // 2)


class ToolServer:
    def __init__(self, rt: Runtime, agent_engine: Optional[AsyncEngine] = None):
        self.rt = rt
        self.agent_engine = agent_engine or create_async_engine(agent_database_url(rt.settings), pool_pre_ping=True, connect_args=CONNECT_ARGUMENTS, **pool_arguments(agent_pool_share(rt.settings.db_pool_max)))
        self._embedder = None
        self.server = Server("pharmalign-tools")
        self.server.request_handlers[types.ListToolsRequest] = self._list
        self.server.request_handlers[types.CallToolRequest] = self._call
        self.manager: Optional[StreamableHTTPSessionManager] = None

    @asynccontextmanager
    async def serving(self):
        """Runs a session manager for the app's lifetime. A manager runs once, so each start gets a new one."""
        self.manager = StreamableHTTPSessionManager(app=self.server, stateless=True, json_response=True)
        async with self.manager.run():
            yield

    # ------------------------------------------------------------ HTTP

    async def __call__(self, scope, receive, send) -> None:
        """Refuses a request whose token this server didn't sign before MCP sees it."""
        try:
            read(self.rt.settings, _bearer(Headers(scope=scope)))
        except BadToken as e:
            log.warning("tool call refused: %s", e)
            response = JSONResponse({"error": "invalid_token", "message": "Send a run token this server signed: Authorization: Bearer <run_token>."},
                                    status_code=401, headers={"WWW-Authenticate": "Bearer"})
            await response(scope, receive, send)
            return
        if self.manager is None:
            await JSONResponse({"error": "not_started", "message": "The tool server is starting."}, status_code=503)(scope, receive, send)
            return
        await self.manager.handle_request(scope, receive, send)

    def _run(self) -> RunScope:
        request: Request = self.server.request_context.request
        return read(self.rt.settings, _bearer(request.headers))

    # ------------------------------------------------------------ scope

    async def refusal(self, run: RunScope) -> Optional[tuple[str, str]]:
        """Why this token may not be used now, or None. Read with the server's login: the agent login can't see runs."""
        now = self.rt.clock.now()
        if run.expires_at <= now:
            return "expired", "This run token has expired."
        if run.agent not in CONTRACT_TOOLS:
            return "unknown_agent", f"{run.agent} is not an agent this server knows."
        async with self.rt.sessions() as db:
            if run.case:
                case = await db.get(ROW["case"], run.case)
                if case is None or case.tenant_id != run.tenant:
                    return "unknown_case", "The token names a case that isn't there."
                if case.kb_snapshot_id != run.snapshot:
                    return "wrong_snapshot", "The token's snapshot is not the case's pinned snapshot."
            if run.kind == "run":
                why = await self._run_refusal(db, run, now)
            else:
                why = await self._session_refusal(db, run)
            if why:
                return why
            used = await db.scalar(select(func.count()).select_from(ROW["agent_tool_log"]).where(
                ROW["agent_tool_log"].tenant_id == run.tenant, ROW["agent_tool_log"].run_id == run.id))
            if used >= run.calls:
                return "budget_spent", f"This run has used its {run.calls} tool calls."
        return None

    async def _run_refusal(self, db, run: RunScope, now) -> Optional[tuple[str, str]]:
        if not run.case and run.agent not in CASELESS:
            return "needs_case", f"A {run.agent} run needs a case."
        row = await db.get(ROW["agent_run"], run.id)
        if row is None or row.tenant_id != run.tenant or row.case_id != run.case:
            return "unknown_run", "The token names a run that isn't there."
        if row.agent != run.agent:
            return "wrong_agent", "The token's agent is not the agent of this run."
        if not row.is_current:
            return "superseded", "A newer run has replaced this one."
        if row.status == "cancelled":
            return "cancelled", "This run was cancelled."
        if row.status not in UNFINISHED:
            started, ms = row.started_at, row.doc.get("duration_ms")
            grace = timedelta(seconds=self.rt.settings.agent_deadline_grace_seconds)
            if started is None or ms is None or now > started + timedelta(milliseconds=ms) + grace:
                return "finished", "This run has finished."
        return None

    async def _session_refusal(self, db, run: RunScope) -> Optional[tuple[str, str]]:
        if run.agent != ASSISTANT:
            return "wrong_agent", "Only the assistant has sessions."
        row = await db.get(ROW["assistant_session"], run.id)
        if row is None or row.tenant_id != run.tenant or row.case_id != run.case:
            return "unknown_session", "The token names a session that isn't there."
        return None

    async def check_documents(self, run: RunScope) -> Optional[tuple[str, ...]]:
        """The knowledge documents an assessor run may read: the source its request names (or the law a person moved the check to) and the
        check's supporting sources. None means the whole snapshot: other agents, sessions, and a check with no source."""
        if run.kind != "run" or run.agent not in SCOPED_AGENTS:
            return None
        async with self.rt.sessions() as db:
            row = await db.get(ROW["agent_run_work"], run.id)
        if row is None or row.tenant_id != run.tenant:
            return None
        request = row.doc.get("request") or {}
        check = request.get("check") or {}
        main = request.get("moved_source") or check.get("source")
        if not main:
            return None
        return tuple(dict.fromkeys([main["doc_id"], *(s["doc_id"] for s in check.get("supporting_sources") or [])]))

    # ------------------------------------------------------------ MCP

    async def _list(self, req: types.ListToolsRequest) -> types.ServerResult:
        run = self._run()
        why = await self.refusal(run)
        if why:
            raise McpError(types.ErrorData(code=types.INVALID_REQUEST, message=why[1]))
        tools = [types.Tool(name=n, description=TOOLS[n].description, inputSchema=schema(TOOLS[n])) for n in allowed(run.agent, run.case)]
        return types.ServerResult(types.ListToolsResult(tools=tools))

    async def _call(self, req: types.CallToolRequest) -> types.ServerResult:
        run = self._run()
        name, arguments = req.params.name, req.params.arguments or {}
        started = time.monotonic()
        outcome, result = await self._answer(run, name, arguments)
        body = json.dumps(result, ensure_ascii=False, default=str)
        hits = next((len(v) for v in result.values() if isinstance(v, list)), None) if outcome == "ok" else None
        if not await self._log(run, name, arguments, body, outcome, hits, int((time.monotonic() - started) * 1000)):
            outcome, body = "error:log_failed", json.dumps(_error(name, "log_failed", "The call could not be logged, so its result is withheld.", retryable=True))
        ok = outcome == "ok"
        return types.ServerResult(types.CallToolResult(content=[types.TextContent(type="text", text=body)],
                                                       structuredContent=json.loads(body) if ok else None, isError=not ok))

    async def _answer(self, run: RunScope, name: str, arguments: dict) -> tuple[str, dict]:
        why = await self.refusal(run)
        if why:
            return f"refused:{why[0]}", _error(name, why[0], why[1])
        if name not in allowed(run.agent, run.case):
            return "refused:not_allowed", _error(name, "not_allowed", f"{name[:64]} is not one of this agent's tools.")
        tool = TOOLS[name]
        if tool.needs_case and not run.case:
            return "refused:no_case", _error(name, "no_case", "This session has no case, so there is no case file to read.")
        try:
            args = tool.args.model_validate(arguments)
        except ValidationError as e:
            return "error:invalid_arguments", _error(name, "invalid_arguments", f"Invalid arguments: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
        documents = await self.check_documents(run)
        try:
            async with self.agent_engine.connect() as conn, conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await self._scope_connection(conn, run)
                result = await tool.run(conn, args, Limits(self.rt.settings.mcp_max_rows, self.embedder, self.rt.clock.now(), documents))
        except ToolFailure as f:
            return f"error:{f.code}", _error(name, f.code, f.message, f.retryable)
        except Exception:
            log.exception("tool %s failed", name)
            return "error:tool_failed", _error(name, "tool_failed", "The tool failed. Try again, or go on without it.", retryable=True)
        return "ok", fit(result, self.rt.settings.mcp_max_chars)

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder(self.rt.settings)
        return self._embedder

    @staticmethod
    async def _scope_connection(conn, run: RunScope) -> None:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true), set_config('app.case_id', :c, true), set_config('app.snapshot_id', :s, true)"),
                           {"t": run.tenant, "c": run.case or "", "s": run.snapshot})

    async def _log(self, run: RunScope, name: str, arguments: dict, body: str, outcome: str, hits: Optional[int], ms: int) -> bool:
        """Hashes and counts, never content. Written with the agent login's one INSERT grant, inside the run's scope."""
        try:
            async with self.agent_engine.begin() as conn:
                await self._scope_connection(conn, run)
                await conn.execute(text(
                    "INSERT INTO agent_tool_log (id, tenant_id, case_id, run_id, tool, args_hash, result_hash, outcome, hits, at, agent, result_chars, ms) "
                    "VALUES (:id, :t, :c, :r, :tool, :ah, :rh, :o, :h, :at, :agent, :n, :ms)"),
                    {"id": f"tl-{uuid.uuid4().hex}", "t": run.tenant, "c": run.case, "r": run.id, "tool": name[:64], "ah": _hash(arguments),
                     "rh": _hash(body), "o": outcome[:64], "h": hits, "at": self.rt.clock.now(), "agent": run.agent, "n": len(body), "ms": ms})
            return True
        except Exception:
            log.exception("could not log the %s call for %s", name, run.id)
            return False

    async def aclose(self) -> None:
        await self.agent_engine.dispose()


def mount(app, rt: Runtime) -> ToolServer:
    tools = ToolServer(rt)
    app.router.add_route("/mcp", tools, methods=["GET", "POST", "DELETE"], include_in_schema=False)
    return tools
