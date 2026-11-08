"""The Gap Analyst's run: asked for the signals no gap covers yet, with today's code grouping as the fallback.

A run is for the whole workspace, so it has an agent_run row with no case and no agent_run_work row. The runner's tick
ignores it; the `gaps.agent_step` job below posts the request, polls, and ends the analysis. No transaction is held during
a call to the agent. Whatever goes wrong, the analysis ends with the code's candidates, so the page always works.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from pharmalign_contracts.agents.run_agents import GapAnalysisRequest, GapAnalysisResult, GapSignal
from pharmalign_contracts.common import RunResponse
from pharmalign_contracts.server import models as m

from ..config import Settings
from ..context import Ctx, Runtime
from ..domain.status import UNFINISHED
from ..jobs import task
from ..mcp.token import run_token
from ..stores import STORES
from .citations import CitationContext, check_citations
from .client import AgentReply
from .output_control import Verdict, review
from .runner import CANCEL_REMOTE, UNREACHED, system_tx
from .sources import kb_chunks

log = logging.getLogger(__name__)

AGENT = "gap-analyst"
AGENT_STEP = "gaps.agent_step"
FINISH_ANALYSIS = "gaps.finish_analysis"  # the code's grouping, bound in handlers/gaps.py
CODE_DELAY_MS = 2600  # the pause before the code's grouping, so the page shows the analysis running

PIPELINE_NODES = ["source", "collector", "preprocessor", "model", "policy", "distributor", "sink"]
READINESS_DIMENSIONS = [
    "Human interface", "Collaboration with AI", "Impact of humans in AI integration", "AI policy and policy for AI",
    "Contextualization and regional impact", "Data and model marketplace",
]

ANALYSES = STORES["gap_analysis"]
GAPS = STORES["gap"]
SIGNALS = STORES["gap_signal"]
RUNS = STORES["agent_run"]


def gap_request(settings: Settings, run_id: str, workspace: m.Workspace, signals: list[GapSignal], now: datetime) -> GapAnalysisRequest:
    """Like a case run's request, with no case. `run_token` scopes the run's tools to this workspace and its active snapshot."""
    simulated = settings.env == "simulation"
    expires_at = now + timedelta(seconds=settings.agent_run_deadline_seconds + settings.agent_deadline_grace_seconds)
    return GapAnalysisRequest(
        run_id=run_id,
        run_token=run_token(settings, run_id=run_id, agent=AGENT, tenant=workspace.id, case_id=None, snapshot_id=workspace.active_kb_snapshot_id,
                            max_calls=settings.agent_max_tool_calls, expires_at=expires_at),
        tenant=workspace.id.lower(),
        mode="simulation" if simulated else "live",
        execution_profile={"model_set": "default", "gate": "on", "data_class": "synthetic" if simulated else "confidential"},
        as_of=now,
        versions={"pack": workspace.active_pack_version_id or "none", "kb_snapshot": workspace.active_kb_snapshot_id},
        priority="batch",
        language="en",
        budget={"max_model_calls": settings.agent_max_model_calls, "max_tool_calls": settings.agent_max_tool_calls,
                "deadline_seconds": settings.agent_run_deadline_seconds},
        signals=signals,
        pipeline_nodes=PIPELINE_NODES,
        readiness_dimensions=READINESS_DIMENSIONS,
    )


async def fresh_signals(ctx: Ctx, signal_ids: list[str]) -> list[GapSignal]:
    """The wanted signals no gap covers yet."""
    covered = {i for g in await GAPS.list(ctx) for i in g.signal_ids}
    wanted = set(signal_ids)
    return [s for s in await SIGNALS.list(ctx) if s.id in wanted and s.id not in covered]


def _not_asked(rt: Runtime, workspace: m.Workspace, fresh: list[GapSignal]) -> Optional[str]:
    """Why the agent is not asked this time, or None."""
    if rt.settings.is_off(AGENT):
        return "agents_off"
    if not workspace.active_kb_snapshot_id:
        return "no_snapshot"
    if not fresh:
        return "no_fresh_signals"
    urls = getattr(rt.agents, "urls", None)
    if rt.agents is None or (urls is not None and not urls.get(AGENT)):
        return "agent_not_configured"
    return None


async def begin(ctx: Ctx, analysis_id: str, signal_ids: list[str]) -> None:
    """Called inside the request that starts an analysis: queues the agent's run, or the code's grouping if the agent can't be asked."""
    workspace = await STORES["workspace"].require(ctx, ctx.tenant.id, "Workspace")
    fresh = await fresh_signals(ctx, signal_ids)
    why = _not_asked(ctx.rt, workspace, fresh)
    if why:
        ctx.schedule(FINISH_ANALYSIS, {"analysis_id": analysis_id, "signal_ids": signal_ids, "fallback": why}, ctx.after(CODE_DELAY_MS))
        return
    settings, now = ctx.rt.settings, ctx.now()
    run_id = f"{analysis_id}-a1"
    body = gap_request(settings, run_id, workspace, fresh, now).model_dump(mode="json")
    RUNS.add(ctx, m.AgentRunView(run_id=run_id, agent=AGENT, case_id=None, status="queued", generation=1, attempt=1, is_current=True))
    deadline = now + timedelta(seconds=settings.agent_run_deadline_seconds + settings.agent_deadline_grace_seconds)
    ctx.schedule(AGENT_STEP, {"analysis_id": analysis_id, "signal_ids": signal_ids, "run_id": run_id, "request": body,
                              "deadline_at": deadline.isoformat(), "op": "start", "reposts": 0}, now)
    ctx.audit(action="agent_run_written", object_type="agent_run", object_id=run_id, after={"agent": AGENT, "generation": 1, "attempt": 1})


# ---------------------------------------------------------------- ending the analysis


def _numbers(verdict: Verdict, run: Optional[RunResponse], view: m.AgentRunView, now: datetime) -> dict:
    """What the agent_run row records about how the run ended, as the runner does for a case run."""
    fields: dict = {"status": verdict.state, "error_code": verdict.code}
    if run:
        finished = run.finished_at or now
        started = view.started_at or run.started_at
        fields.update(
            finish_reason=run.finish_reason, usage=run.usage, fallback_used=any(c.fallback for c in run.model_calls), started_at=started,
            duration_ms=int((finished - started).total_seconds() * 1000) if started and started.tzinfo and finished.tzinfo else None,
        )
    return fields


async def _close_row(ctx: Ctx, run_id: Optional[str], verdict: Verdict, run: Optional[RunResponse] = None) -> None:
    view = await RUNS.get(ctx, run_id) if run_id else None
    if view is not None and view.status in UNFINISHED:
        await RUNS.save(ctx, view.model_copy(update=_numbers(verdict, run, view, ctx.now())))


async def fall_back(ctx: Ctx, analysis_id: str, signal_ids: list[str], reason: str, run_id: Optional[str] = None, verdict: Optional[Verdict] = None,
                    run: Optional[RunResponse] = None) -> None:
    """The code's candidates, one for each fresh signal, and the analysis completed. `reason` says why the agent's answer wasn't used."""
    from ..handlers.gaps import add_candidate, candidate_from

    analysis = await ANALYSES.get(ctx, analysis_id, for_update=True)
    if analysis is None or analysis.status != "running":
        return
    await _close_row(ctx, run_id, verdict or Verdict("failed", code=reason), run)
    fresh = await fresh_signals(ctx, signal_ids)
    for s in fresh:
        await add_candidate(ctx, candidate_from(ctx, s))
    await ANALYSES.save(ctx, analysis.model_copy(update={"status": "completed", "finished_at": ctx.now(), "candidates_created": len(fresh)}))
    ctx.audit(action="gap_analysis_completed", object_type="gap_analysis", object_id=analysis_id, after={"candidates_created": len(fresh), "fallback": reason},
              actor_type="system", actor_id="code")


async def _fail(rt: Runtime, tenant_id: str, analysis_id: str, run_id: Optional[str]) -> None:
    """Even the fallback broke: the record still ends right."""
    async with system_tx(rt, tenant_id) as ctx:
        analysis = await ANALYSES.get(ctx, analysis_id, for_update=True)
        if analysis is None or analysis.status != "running":
            return
        await _close_row(ctx, run_id, Verdict("failed", code="fallback_failed"))
        await ANALYSES.save(ctx, analysis.model_copy(update={"status": "failed", "finished_at": ctx.now(), "candidates_created": 0}))
        ctx.audit(action="gap_analysis_failed", object_type="gap_analysis", object_id=analysis_id, after={"candidates_created": 0},
                  actor_type="system", actor_id="code")


async def run_fallback(rt: Runtime, tenant_id: str, analysis_id: str, signal_ids: list[str], reason: str, run_id: Optional[str] = None,
                       verdict: Optional[Verdict] = None) -> None:
    """The fallback in a short transaction of its own. If it raises, the analysis is marked failed instead of staying running."""
    try:
        async with system_tx(rt, tenant_id) as ctx:
            await fall_back(ctx, analysis_id, signal_ids, reason, run_id, verdict)
    except Exception:
        log.exception("gap analysis %s: the fallback failed", analysis_id)
        await _fail(rt, tenant_id, analysis_id, run_id)


# ---------------------------------------------------------------- applying the agent's answer


async def _checked_examples(ctx: Ctx, snapshot_id: str, candidates: list) -> tuple[list[list], int]:
    """Each candidate's global examples that pass the server's citation check, and how many were dropped."""
    anchors = [e.anchor for c in candidates for e in c.global_examples]
    chunks = await kb_chunks(ctx.db, ctx.tenant.id, snapshot_id, anchors) if anchors else {}
    context = CitationContext(chunks)
    good = [[e for e in c.global_examples if not check_citations([e], context)] for c in candidates]
    return good, sum(len(c.global_examples) for c in candidates) - sum(len(g) for g in good)


def _described(description: str, pack_rule: Optional[str]) -> str:
    """The server's Gap has no pack rule field, so the rule is named in the description."""
    return f"{description} Pack rule: {pack_rule}." if pack_rule and pack_rule not in description else description


async def _apply(ctx: Ctx, analysis: m.GapAnalysis, payload: dict, run: RunResponse) -> None:
    from ..handlers.gaps import add_candidate

    result: GapAnalysisResult = run.result
    covered = {i for g in await GAPS.list(ctx) for i in g.signal_ids}
    wanted, used = set(payload["signal_ids"]), set()
    kept = []
    for c in result.candidates:
        ids = [i for i in dict.fromkeys(c.signal_ids) if i in wanted and i not in covered and i not in used]
        if ids:
            used.update(ids)
            kept.append((c, ids))
    examples, dropped = await _checked_examples(ctx, payload["request"]["versions"]["kb_snapshot"], [c for c, _ in kept])
    for (c, ids), good in zip(kept, examples):
        await add_candidate(ctx, m.Gap(
            id=ctx.id("gap"), workspace_id=ctx.tenant.id, title=c.title, kind=c.kind, status="candidate", signal_ids=ids,
            description=_described(c.description, c.pack_rule), global_examples=good,
            pipeline_node=c.pipeline_node if c.pipeline_node in PIPELINE_NODES else None,
            readiness_dimension=c.readiness_dimension if c.readiness_dimension in READINESS_DIMENSIONS else None,
            suggested_next_step=c.suggested_next_step,
        ))
    await _close_row(ctx, payload["run_id"], Verdict("completed", accepted=True), run)
    await ANALYSES.save(ctx, analysis.model_copy(update={"status": "completed", "finished_at": ctx.now(), "candidates_created": len(kept)}))
    ctx.audit(action="gap_analysis_completed", object_type="gap_analysis", object_id=analysis.id,
              after={"candidates_created": len(kept), "run_id": payload["run_id"], "examples_dropped": dropped}, actor_type="agent", actor_id=AGENT)


# ---------------------------------------------------------------- the step


def _later(ctx: Ctx, payload: dict, op: str, at: datetime, **fields) -> None:
    ctx.schedule(AGENT_STEP, {**payload, "op": op, **fields}, at)


async def _call(rt: Runtime, payload: dict) -> AgentReply:
    try:
        if payload["op"] == "start":
            return await rt.agents.start(AGENT, payload["request"])
        return await rt.agents.poll(AGENT, payload["run_id"])
    except Exception:
        return AgentReply(0)  # an unexpected client failure is an agent that didn't answer; the deadline bounds it


async def _take(ctx: Ctx, payload: dict, reply: AgentReply) -> None:
    """One reply, in one short transaction: the analysis ends, or the next step is queued."""
    analysis = await ANALYSES.get(ctx, payload["analysis_id"], for_update=True)
    if analysis is None or analysis.status != "running":
        return
    rt, now, op, run_id = ctx.rt, ctx.now(), payload["op"], payload["run_id"]
    deadline = datetime.fromisoformat(payload["deadline_at"])
    ids = payload["signal_ids"]

    async def over(reason: str, verdict: Optional[Verdict] = None, run: Optional[RunResponse] = None) -> None:
        await fall_back(ctx, analysis.id, ids, reason, run_id, verdict, run)

    if reply.invalid:
        return await over(reply.invalid)
    if reply.run is not None:
        run = reply.run
        if run.run_id != run_id:
            return await over("wrong_run")
        verdict = review(run)
        if verdict.accepted:
            return await _apply(ctx, analysis, payload, run)
        if verdict.finished:
            return await over(verdict.code or verdict.state, verdict, run)
        if now >= deadline:
            ctx.schedule(CANCEL_REMOTE, {"agent": AGENT, "run_id": run_id}, now)
            return await over("deadline_passed", Verdict("lost", code="deadline_passed"))
        view = await RUNS.get(ctx, run_id)
        if view is not None and view.status in UNFINISHED:
            await RUNS.save(ctx, view.model_copy(update={"status": "running", "started_at": view.started_at or run.started_at or now}))
        return _later(ctx, payload, "poll", now + timedelta(seconds=rt.settings.agent_poll_seconds))
    if op == "poll" and reply.status == 404:
        if payload["reposts"] >= 1:
            return await over("lost", Verdict("lost", code="lost"))
        view = await RUNS.get(ctx, run_id)
        if view is not None and view.status in UNFINISHED:
            await RUNS.save(ctx, view.model_copy(update={"status": "queued"}))
        new_deadline = now + timedelta(seconds=payload["request"]["budget"]["deadline_seconds"] + rt.settings.agent_deadline_grace_seconds)
        ctx.audit(action="agent_run_reposted", object_type="agent_run", object_id=run_id, after={"reposts": payload["reposts"] + 1})
        return _later(ctx, payload, "start", now, reposts=payload["reposts"] + 1, deadline_at=new_deadline.isoformat())
    if op == "start" and reply.status in (409, 422):
        return await over("request_refused")
    if reply.status not in UNREACHED:
        return await over(f"agent_status_{reply.status}")  # an answer that will not change by asking again, such as a refused login
    if now >= deadline:
        return await over("agent_unreachable", Verdict("lost", code="agent_unreachable"))
    _later(ctx, payload, op, now + timedelta(seconds=rt.settings.agent_poll_seconds))


@task(AGENT_STEP)
async def agent_step(payload: dict, ctx: Ctx) -> None:
    """Posts or polls the Gap Analyst once. It never touches ctx.db, so no connection is held open across the call to the agent."""
    rt, tenant_id = ctx.rt, ctx.tenant.id
    try:
        if rt.settings.is_off(AGENT):  # switched off since the run was queued: never called
            await run_fallback(rt, tenant_id, payload["analysis_id"], payload["signal_ids"], "agents_off", payload["run_id"], Verdict("cancelled", code="agents_off"))
            return
        reply = await _call(rt, payload)
        async with system_tx(rt, tenant_id) as tx:
            await _take(tx, payload, reply)
    except Exception:
        log.exception("gap analysis %s: step failed", payload.get("analysis_id"))
        await run_fallback(rt, tenant_id, payload["analysis_id"], payload["signal_ids"], "step_failed", payload.get("run_id"))
