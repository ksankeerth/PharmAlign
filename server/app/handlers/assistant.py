"""Knowledge Assistant: chat sessions for one person. A session reads only the knowledge base, or is also scoped to one case. The server opens
each with the agent and relays its streamed answer. What the assistant says is stored. It never changes a status, and a draft comment is
posted only by a person."""
from datetime import timedelta

from pharmalign_contracts.agents.knowledge_assistant import SessionCreate
from pharmalign_contracts.server import models as m
from pharmalign_contracts.server.routes import CASE_VIEWS

from ..mcp.token import session_token
from ..ops import op
from ..pipeline.assistant import ASSISTANT_UNAVAILABLE, AssistantUnavailable, Chat, relay_turn
from ..problems import forbidden, not_found, problem
from ..stores import STORES
from .support import get_case

SESSIONS = STORES["assistant_session"]


@op("POST", "/api/v1/assistant/sessions")
async def start_session(req, ctx):
    """Without a case, any signed-in person may ask the knowledge base. With one, only a view that may see cases, and the case must be theirs to open."""
    case_id = req.body.case_id
    if case_id:
        if ctx.view not in CASE_VIEWS:
            forbidden("Your view can't open a chat on a case.")
        case = await get_case(ctx, case_id)
        simulated, snapshot_id, pack_version_id = case.environment == "simulation", case.kb_snapshot_id, case.pack_version_id
    else:
        workspace = await STORES["workspace"].require(ctx, ctx.tenant.id, "Workspace")
        if not workspace.active_kb_snapshot_id:
            problem(409, "no_knowledge_snapshot", "There is no knowledge base to ask yet", "This workspace has no active knowledge snapshot.")
        simulated, snapshot_id, pack_version_id = ctx.rt.settings.env == "simulation", workspace.active_kb_snapshot_id, workspace.active_pack_version_id or "none"
    session = m.AssistantSession(id=ctx.id("as"), case_id=case_id, created_at=ctx.now())
    settings = ctx.rt.settings
    token = session_token(settings, session_id=session.id, tenant=ctx.tenant.id, case_id=case_id, snapshot_id=snapshot_id,
                          max_calls=settings.assistant_max_tool_calls, expires_at=ctx.now() + timedelta(seconds=settings.assistant_session_seconds))
    try:
        agent_session_id = await ctx.rt.assistant.open(SessionCreate(
            run_token=token, tenant=ctx.tenant.id.lower(), mode="simulation" if simulated else "live",
            execution_profile={"model_set": "default", "gate": "on", "data_class": "synthetic" if simulated else "confidential"}, as_of=ctx.now(),
            versions={"pack": pack_version_id, "kb_snapshot": snapshot_id}, case_id=case_id, user_id=ctx.person_id, view=ctx.view, language=req.body.language,
        ))
    except AssistantUnavailable:
        problem(503, "assistant_unavailable", "The assistant isn't available", ASSISTANT_UNAVAILABLE)
    SESSIONS.add(ctx, session, person_id=ctx.person_id, agent_session_id=agent_session_id, kb_snapshot_id=snapshot_id)
    ctx.audit("assistant_session_started", "assistant_session", session.id, case_id)
    return session


@op("POST", "/api/v1/assistant/sessions/{session_id}/turns")
async def turn(req, ctx):
    """Checks the session before the stream starts. The stream runs after this request's transaction has committed."""
    session = await SESSIONS.require(ctx, req.path.session_id, "Assistant session")
    row = await ctx.db.get(SESSIONS.row, session.id)
    if row.person_id != ctx.person_id:
        not_found("Assistant session", session.id)  # a chat belongs to the person who started it
    if not row.agent_session_id:
        problem(503, "assistant_unavailable", "The assistant isn't available", ASSISTANT_UNAVAILABLE)
    return relay_turn(ctx.rt, ctx.actor, Chat(session.id, row.agent_session_id, session.case_id, row.kb_snapshot_id), req.body)
