"""Runs a case's checks, stage by stage, from the jobs table.

Each step has three parts: a short transaction that writes what is about to happen, the agent calls with no transaction
open, and a short transaction per reply. A run's row exists before its agent is called, so a crash at any point leaves
something a restart can pick up. Results only change a check through `rules.decide_result`.

Handlers call `start_pipeline`, `rerun`, `evidence_changed` and `cancel` inside their own transaction. `tick` is for the scheduler.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import AsyncIterator, Literal, Optional, Sequence

from pharmalign_contracts.agents.run_agents import AgentFinding, AssessmentResult, BriefResult, ChallengeResult, DossierReadResult, FactConflict, FactKey, OpenComment, SamenessResult, TypedExtraction
from pharmalign_contracts.common import Evidence, FactValue, RunResponse
from pharmalign_contracts.server.models import (
    AgentRunView,
    CaseDetail,
    ChallengeSummary,
    CheckDefinition,
    CheckDetail,
    CheckTrace,
    ConflictView,
    DossierDocument,
    GateDecisionView,
    PackDocument,
    PipelineRun,
    RecommendationVersion,
    Route,
    RuleOutcome,
    StageStatus,
)
from pydantic import ValidationError
from sqlalchemy import select

from ..context import Ctx, Runtime, Tenant
from ..domain import rules
from ..domain.fingerprints import CaseFacts, case_input_fingerprint, check_fingerprint
from ..domain.guards import FROZEN, guard
from ..domain.status import NOT_A_VERDICT, UNFINISHED, check_status, is_clear, not_run_status
from ..identity import Actor
from ..jobs import TICK_TASKS, task
from ..problems import enforce_guards
from ..stores import STORES
from ..tables import ROW, JobRow
from . import requests as build
from .citations import CitationContext, CitationFailure, SourceChunk, check_citations, normalise, unsupported_numbers, value_in_its_quotes
from .client import AgentReply
from .output_control import Verdict, refused, result_problem, review
from .records import RunKind, RunWork
from .sources import kb_chunks, notice_chunks, reference_chunks
from .typed_rules import ALL_MODULES, RuleError, evaluate, inputs_used

STAGE_ORDER = ("safe_to_read", "complete", "route", "same_product", "knowledge", "review")
STAGE_LABELS = {"safe_to_read": "Safe to read", "complete": "Complete", "route": "Route", "same_product": "Same product", "knowledge": "Knowledge checks", "review": "Review"}
GATE_STAGES = frozenset({"safe_to_read", "complete"})
WAITING = rules.WAITING_FOR_RESULT
ADVANCE = "pipeline.advance"
CANCEL_REMOTE = "pipeline.cancel_remote"
READER, ASSESSOR, SAMENESS, COORDINATOR, CHALLENGER = "dossier-reader", "checklist-assessor", "sameness", "coordinator", "challenger"
SYSTEM = {"type": "system", "id": "pipeline"}
UNREACHED = (0, 429, 500, 502, 503, 504)

CASES = STORES["case"]
PACKS = STORES["pack_version"]
CHECKS = STORES["check_result"]
TRACES = STORES["check_trace"]
RUNS = STORES["agent_run"]
WORK = STORES["agent_run_work"]
PIPELINES = STORES["pipeline_run"]
DOCUMENTS = STORES["dossier_document"]
FACTS = STORES["fact"]
RECOMMENDATIONS = STORES["recommendation_version"]

log = logging.getLogger(__name__)


@asynccontextmanager
async def _tx(rt: Runtime, tenant_id: str) -> AsyncIterator[Ctx]:
    """One of the runner's own short transactions."""
    async with rt.sessions() as db, db.begin():
        ctx = Ctx(rt, db, Actor(type="system", id="pipeline", tenant_id=tenant_id), Tenant(tenant_id, rt.settings.env))
        yield ctx
        await ctx.flush()


system_tx = _tx  # for work outside the pipeline that also needs a short transaction of its own


@dataclass(frozen=True)
class Call:
    op: Literal["start", "poll"]
    run_id: str
    agent: str
    case_id: str
    request: Optional[dict] = None


@dataclass(frozen=True)
class Pinned:
    """A case with its pinned pack, the rules this run lives by."""

    case: CaseDetail
    pack: PackDocument
    definitions: dict[str, CheckDefinition]
    product_type: Optional[str]


def _route_of(case: CaseDetail, pack: PackDocument) -> Optional[Route]:
    case_type = next((t for t in pack.case_types if t.id == case.case_type), None)
    if case_type:
        route = next((r for r in case_type.routes if r.id == case.route), None)
        if route:
            return route
    return pack.case_types[0].routes[-1] if pack.case_types else None


async def _pinned(ctx: Ctx, case: CaseDetail) -> Pinned:
    version = await PACKS.require(ctx, case.pack_version_id, "Pack version")
    medicine = await STORES["medicine"].get(ctx, case.medicine_id)
    return Pinned(case, version.document, {d.id: d for d in version.document.checks}, medicine.product_type if medicine else None)


async def _current_checks(ctx: Ctx, case_id: str) -> list[CheckDetail]:
    row = ROW["check_result"]
    return await CHECKS.list(ctx, row.case_id == case_id, row.is_current.is_(True), order_by=row.id)


async def _work_of(ctx: Ctx, case_id: str) -> list[RunWork]:
    return await WORK.list(ctx, ROW["agent_run_work"].case_id == case_id)


async def _active_runs(ctx: Ctx, case_id: str) -> list[AgentRunView]:
    row = ROW["agent_run"]
    return await RUNS.list(ctx, row.case_id == case_id, row.status.in_(tuple(UNFINISHED)))


async def _schedule_advance(ctx: Ctx, case_id: str, at: Optional[datetime] = None) -> None:
    """One waiting job per case is enough. An extra one would be harmless."""
    waiting = await ctx.db.scalar(
        select(JobRow.id).where(JobRow.kind == ADVANCE, JobRow.status == "queued", JobRow.tenant_id == ctx.tenant.id, JobRow.payload["case_id"].astext == case_id).limit(1)
    )
    if waiting is None:
        ctx.schedule(ADVANCE, {"case_id": case_id}, at or ctx.now())


# ---------------------------------------------------------------- starting and re-running


def _wanted_definitions(p: Pinned) -> list[CheckDefinition]:
    route = _route_of(p.case, p.pack)
    wanted = {i for s in (route.steps if route else []) for i in s.check_ids}
    return [d for d in p.pack.checks if d.id in wanted and (not d.product_types or (p.product_type or "generic") in d.product_types)]


def _agent_for(d: CheckDefinition) -> Optional[str]:
    if d.method not in ("agent", "typed"):
        return None
    return SAMENESS if d.stage == "same_product" else ASSESSOR


def _stage_order(definitions: Sequence[CheckDefinition]) -> list[str]:
    extra = sorted({d.stage for d in definitions} - set(STAGE_ORDER))
    return [*STAGE_ORDER, *extra]


def _new_check(ctx: Ctx, case: CaseDetail, d: CheckDefinition, run_id: str) -> CheckDetail:
    detail = CheckDetail(
def _by_a_person(ctx: Ctx, d: CheckDefinition) -> bool:
    """True when the agent that would read this check is switched off, so a person does it."""
    agent = _agent_for(d)
    return agent is not None and ctx.rt.settings.is_off(agent)


        id=f"cr-{case.id[5:]}-{d.id.lower()}", check_id=d.id, case_id=case.id, pipeline_run_id=run_id, name=d.id, method=d.method,
        agent=None if _by_a_person(ctx, d) else _agent_for(d),
        status=check_status(d.method, None), is_current=True, blocking=d.severity == "blocking", summary="Waiting to run.", generation=0, attempt=1, updated_at=ctx.now(),
        question=d.question, source=d.source, legal_force=d.legal_force, may_pass_alone=d.may_pass_alone, fingerprint="sha256:0000",
    )
    return detail.model_copy(update={"fingerprint": check_fingerprint(detail)})


async def _keep_superseded(ctx: Ctx, c: CheckDetail, d: Optional[CheckDefinition]) -> None:
    """Keeps the result a re-run replaces, so a person can still read it. Call it after the current row has moved on."""
    if c.status in WAITING or c.generation == 0:
        return
    old_id = f"{c.id}~g{c.generation}a{c.attempt}"
    CHECKS.add(ctx, c.model_copy(update={"id": old_id, "is_current": False, "current_result_id": c.id}), stage=d.stage if d else "knowledge", reads=d.reads if d else [])
    await ctx.db.flush()
    trace = await TRACES.get(ctx, c.id)
    if trace:
        TRACES.add(ctx, trace.model_copy(update={"check_result_id": old_id}))


async def _void_rows(ctx: Ctx, c: CheckDetail, waiver_valid: Optional[bool], confirmation_valid: Optional[bool]) -> dict:
    """Writes the voided acts to their own tables, and returns the fields for the check's doc."""
    fields: dict = {}
    if c.waiver and waiver_valid is False and c.waiver.valid:
        row = await STORES["waiver"].get(ctx, c.waiver.id)
        if row:
            await STORES["waiver"].save(ctx, row.model_copy(update={"valid": False}))
        fields["waiver"] = c.waiver.model_copy(update={"valid": False})
        ctx.audit("waiver_voided", "waiver", c.waiver.id, c.case_id, reason="The evidence changed.")
    if c.confirmation and confirmation_valid is False and c.confirmation.valid:
        row = await STORES["confirmation"].get(ctx, c.confirmation.id)
        if row:
            await STORES["confirmation"].save(ctx, row.model_copy(update={"valid": False}))
        fields["confirmation"] = c.confirmation.model_copy(update={"valid": False})
        ctx.audit("confirmation_voided", "confirmation", c.confirmation.id, c.case_id, reason="The evidence changed.")
    return fields


async def _start_again(ctx: Ctx, c: CheckDetail, d: Optional[CheckDefinition], why: Literal["recheck", "new_run", "evidence"], run_id: str) -> CheckDetail:
    """A new attempt or generation of one check. The fingerprint changes, so acts on the old evidence stop counting."""
    generation, attempt = (c.generation, c.attempt + 1) if why == "recheck" else (c.generation + 1, 1)
    moved = c.model_copy(update=dict(
        generation=generation, attempt=attempt, pipeline_run_id=run_id, agent_status=None, extracted=[], rule_outcome=None, evidence=[], question_for_company=None,
        no_source_found=False, challenge=None, evidence_opened_at=None, updated_at=ctx.now(),
        summary="Evidence changed. Re-running." if why == "evidence" else "Waiting to run.",
    ))
    new_fingerprint = check_fingerprint(moved)
    change = rules.void_on_evidence_change(c, new_fingerprint, rerun_pending=True)
    voided = await _void_rows(ctx, c, change.waiver_valid, change.confirmation_valid)
    # A person's check has nothing to wait for. Evidence that changed shows as Stale; a plain new run as Pending.
    status = check_status(c.method, "queued", evidence_changed=why == "evidence" and c.method != "person")
    moved = moved.model_copy(update={**voided, "status": status, "fingerprint": new_fingerprint})
    saved = await CHECKS.save(ctx, moved)
    await ctx.db.flush()
    await _keep_superseded(ctx, c, d)
    return saved


async def _new_pipeline_run(ctx: Ctx, case: CaseDetail) -> tuple[str, int]:
    row = ROW["pipeline_run"]
    earlier = await PIPELINES.list(ctx, row.case_id == case.id)
    generation = max((r.generation for r in earlier), default=0) + 1
    for r in earlier:
        if r.status in ("queued", "running"):
            await PIPELINES.save(ctx, r.model_copy(update={"status": "superseded", "finished_at": ctx.now()}))
    return f"run-{case.id[5:]}-s{case.sequence}-g{generation}", generation


def _stages(checks: Sequence[CheckDetail], definitions: dict[str, CheckDefinition]) -> list[StageStatus]:
    stages = []
    for key in _stage_order(list(definitions.values())):
        ids = [c.id for c in checks if (definitions[c.check_id].stage if c.check_id in definitions else "knowledge") == key]
        if ids:
            stages.append(StageStatus(key=key, label=STAGE_LABELS.get(key, key), check_ids=ids))
    return stages


async def start_pipeline(ctx: Ctx, case_id: str, reason: Optional[str] = None) -> PipelineRun:
    """Starts a full run of a case, or the first one. Commits with the caller's transaction."""
    case = await CASES.require(ctx, case_id, "Case", for_update=True)
    checks = await _current_checks(ctx, case_id)
    enforce_guards([
        guard("case_not_frozen", case.status not in FROZEN, f"The case is {case.status}."),
        guard("no_run_in_progress", not any(c.status == "running" for c in checks), "A run is already in progress."),
    ], "A new run is refused")
    p = await _pinned(ctx, case)
    run_id, generation = await _new_pipeline_run(ctx, case)
    pipeline = PIPELINES.add(ctx, PipelineRun(
        id=run_id, case_id=case_id, sequence=case.sequence, generation=generation, status="running", stages=[], started_at=ctx.now(),
    ))
    await ctx.db.flush()
    if not checks:
        for d in _wanted_definitions(p):
            CHECKS.add(ctx, _new_check(ctx, case, d, run_id), stage=d.stage, reads=d.reads)
        await ctx.db.flush()
        checks = await _current_checks(ctx, case_id)
    started = [await _start_again(ctx, c, p.definitions.get(c.check_id), "new_run", run_id) for c in checks]
    pipeline = await PIPELINES.save(ctx, pipeline.model_copy(update={"stages": _stages(started, p.definitions)}))
    case = await _switch_to(ctx, case, run_id)
    ctx.timeline(case_id, "pipeline_started", f"Pipeline run {generation} started.", SYSTEM if not ctx.person_id else None, run_id)
    ctx.audit("pipeline_started", "pipeline_run", run_id, case_id, reason=reason)
    await _refresh_case(ctx, case)
    await _schedule_advance(ctx, case_id)
    return pipeline


async def _switch_to(ctx: Ctx, case: CaseDetail, run_id: str) -> CaseDetail:
    return await CASES.save(ctx, case.model_copy(update={
        "current_pipeline_run_id": run_id, "status": "in_pipeline" if case.status == "intake" else case.status,
    }))


async def _running_pipeline(ctx: Ctx, case: CaseDetail) -> Optional[PipelineRun]:
    if not case.current_pipeline_run_id:
        return None
    return await PIPELINES.get(ctx, case.current_pipeline_run_id)


async def rerun(ctx: Ctx, case_id: str, check_ids: Sequence[str], comment: Optional[str] = None) -> list[CheckDetail]:
    """Re-checks named checks of a case: the same evidence, a new attempt. A failed Reader reads again first."""
    case = await CASES.require(ctx, case_id, "Case", for_update=True)
    checks = {c.check_id: c for c in await _current_checks(ctx, case_id)}
    chosen = [checks[i] for i in check_ids if i in checks]
    enforce_guards([
        guard("case_not_frozen", case.status not in FROZEN, f"The case is {case.status}."),
        *[guard("not_running", c.status != "running", "This check is already running.", c.id) for c in chosen],
        guard("checks_exist", len(chosen) == len(set(check_ids)), "Every check named must exist on this case."),
    ], "A re-check is refused")
    p = await _pinned(ctx, case)
    pipeline = await _running_pipeline(ctx, case)
    if pipeline is None:
        return []
    done = [await _start_again(ctx, c, p.definitions.get(c.check_id), "recheck", pipeline.id) for c in chosen]
    all_work = await _work_of(ctx, case_id)
    active = {v.run_id for v in await _active_runs(ctx, case_id)}
    latest = _latest_reader(all_work, pipeline.generation)
    reader_failed = latest is not None and latest.run_id not in active and not latest.delivered
    if "reader" in rules.recheck_steps(reader_failed):
        documents = await DOCUMENTS.list(ctx, ROW["dossier_document"].case_id == case_id)
        _open_reader(ctx, p, pipeline, documents, _fact_keys(_wanted_definitions(p)), latest.attempt + 1)
    if comment:
        for c in done:
            ctx.audit("recheck_comment", "check_result", c.id, case_id, reason=comment[:500])
    await PIPELINES.save(ctx, pipeline.model_copy(update={"status": "running", "finished_at": None}))
    await _refresh_case(ctx, case)
    await _schedule_advance(ctx, case_id)
    return done


def _module_matches(read: str, module: Optional[str]) -> bool:
    """A check reads labels such as `3.2.P.8`, `1.3 Product information` or `All files`. A changed file touches the ones it falls under."""
    if read in ("All files", "Module index"):
        return True
    if not module:
        return False
    if read == "Reference approval":
        return module == "1.10"
    label = read.split(" ")[0]
    return module == label or module.startswith(f"{label}.")


def _dependencies(definitions: Sequence[CheckDefinition]) -> list[rules.CheckDependencies]:
    dependencies = []
    for d in definitions:
        reads_here = [k.key for k in d.extract if d.rule and f"here.{k.key}" in d.rule]
        extracts = [k.key for k in d.extract if k.key not in reads_here]
        dependencies.append(rules.CheckDependencies(d.id, reads=d.reads, reads_facts=reads_here, extracts_facts=extracts))
    return dependencies


async def evidence_changed(ctx: Ctx, case_id: str, changed_doc_ids: Sequence[str], reason: Optional[str] = None) -> list[str]:
    """New files or versions arrived. Dependants re-run, their waivers and confirmations void, and everything else stays. A frozen case only logs it."""
    case = await CASES.require(ctx, case_id, "Case", for_update=True)
    if rules.after_sign_action(case.status, "reply") != "proceed":
        ctx.audit("late_reply_logged", "case", case_id, case_id, reason=reason)
        return []
    p = await _pinned(ctx, case)
    documents = await DOCUMENTS.list(ctx, ROW["dossier_document"].case_id == case_id)
    changed = [d for d in documents if d.id in set(changed_doc_ids)]
    checks = await _current_checks(ctx, case_id)
    used = [p.definitions[c.check_id] for c in checks if c.check_id in p.definitions]
    labels = {read for d in used for read in d.reads for doc in changed if _module_matches(read, doc.module)}
    chosen = rules.rerun_closure(labels, _dependencies(used))
    run_id, generation = await _new_pipeline_run(ctx, case)
    affected = [c for c in checks if c.check_id in chosen]
    pipeline = PIPELINES.add(ctx, PipelineRun(
        id=run_id, case_id=case_id, sequence=case.sequence, generation=generation, status="running", stages=_stages(affected, p.definitions), started_at=ctx.now(),
    ))
    await ctx.db.flush()
    for c in affected:
        await _start_again(ctx, c, p.definitions.get(c.check_id), "evidence", run_id)
    case = await _switch_to(ctx, case, run_id)
    keys = _fact_keys(used)
    if keys and any(_agent_for(p.definitions[c.check_id]) for c in affected if c.check_id in p.definitions):
        _open_reader(ctx, p, pipeline, documents, keys, 1, changed_doc_ids)
    ctx.timeline(case_id, "evidence_changed", f"Evidence changed: {len(chosen)} check(s) re-run, the rest stand.", SYSTEM)
    ctx.audit("evidence_changed", "pipeline_run", run_id, case_id, after={"rerun": chosen, "changed": list(changed_doc_ids)}, reason=reason)
    await _refresh_case(ctx, case)
    await _schedule_advance(ctx, case_id)
    return chosen


async def cancel(ctx: Ctx, case_id: str, check_id: Optional[str] = None, reason: Optional[str] = None) -> int:
    """Stops a case's runs, or one check's. The check shows Error, since nothing was found. A result that arrives later is ignored."""
    case = await CASES.require(ctx, case_id, "Case", for_update=True)
    cancelled = 0
    for view in await _active_runs(ctx, case_id):
        if check_id and view.check_id != check_id:
            continue
        await RUNS.save(ctx, view.model_copy(update={"status": "cancelled", "finish_reason": "cancelled", "error_code": "cancelled"}))
        ctx.schedule(CANCEL_REMOTE, {"agent": view.agent, "run_id": view.run_id})
        cancelled += 1
    p = await _pinned(ctx, case)
    for c in await _current_checks(ctx, case_id):
        if c.status in WAITING and (check_id is None or c.check_id == check_id):
            status = check_status(c.method, "cancelled")
            moved = c.model_copy(update={"status": status, "summary": "Cancelled. Re-check it to get an answer.", "updated_at": ctx.now()})
            await CHECKS.save(ctx, moved.model_copy(update={"fingerprint": check_fingerprint(moved)}))
    pipeline = await _running_pipeline(ctx, case)
    if check_id is None and pipeline and pipeline.status in ("queued", "running"):
        await PIPELINES.save(ctx, pipeline.model_copy(update={"status": "cancelled", "finished_at": ctx.now()}))
    ctx.audit("pipeline_cancelled", "case", case_id, case_id, after={"check_id": check_id, "runs": cancelled}, reason=reason)
    await ctx.db.flush()
    await _refresh_case(ctx, p.case)
    if check_id:
        await _schedule_advance(ctx, case_id)
    return cancelled


async def refresh_case(ctx: Ctx, case_id: str) -> None:
    """For handlers: recomputes a case's status and counts after a person's act."""
    await _refresh_case(ctx, await CASES.require(ctx, case_id, "Case"))


async def rebrief(ctx: Ctx, case_id: str) -> None:
    """A person's act changed the results. The Coordinator is asked again, unless a run is still going or the case is frozen."""
    case = await CASES.require(ctx, case_id, "Case", for_update=True)
    if case.status not in FROZEN:
        await _reopen(ctx, case)


async def _reopen(ctx: Ctx, case: CaseDetail) -> None:
    pipeline = await _running_pipeline(ctx, case)
    if pipeline is None:
        return
    if pipeline.status not in ("queued", "running"):
        await PIPELINES.save(ctx, pipeline.model_copy(update={"status": "running", "finished_at": None}))
    await _schedule_advance(ctx, case.id)


# ---------------------------------------------------------------- case state


async def _refresh_case(ctx: Ctx, case: CaseDetail) -> None:
    """Recomputes a case's derived fields after anything about it changes."""
    checks = await _current_checks(ctx, case.id)
    definitions = {d.id: d for d in (await PACKS.require(ctx, case.pack_version_id, "Pack version")).document.checks}
    row = ROW["recommendation_version"]
    current = next(iter(await RECOMMENDATIONS.list(ctx, row.case_id == case.id, row.is_current.is_(True))), None)
    fields: dict = {
        "blocking_checks_open": sum(1 for c in checks if c.blocking and not is_clear(c)),
        "current_recommendation_version": current.version if current else None,
    }
    if case.status not in FROZEN and case.status != "intake":
        question = ROW["question"]
        sent = await STORES["question"].list(ctx, question.case_id == case.id, question.status == "sent")
        if any(c.status in WAITING for c in checks):
            next_status = "in_pipeline"
        elif any(c.status == "held" and c.check_id in definitions and definitions[c.check_id].stage in GATE_STAGES for c in checks):
            next_status = "held"
        elif sent:
            next_status = "with_company"
        else:
            next_status = "in_review"
        fields["clock_running"] = not sent
        fields["status"] = next_status
        if next_status != case.status:
            ctx.timeline(case.id, "status_changed", f"Status changed from {case.status.replace('_', ' ')} to {next_status.replace('_', ' ')}.", SYSTEM)
    await CASES.save(ctx, case.model_copy(update=fields))


# ---------------------------------------------------------------- deciding what happens next


def _fact_keys(definitions: Sequence[CheckDefinition]) -> list[FactKey]:
    keys: dict[str, FactKey] = {}
    for d in definitions:
        if d.method == "typed":
            for k in d.extract:
                keys.setdefault(k.key, k)
    return list(keys.values())


def _write_run(ctx: Ctx, kind: RunKind, agent: str, request, case_id: str, pipeline_id: str, generation: int, attempt: int, check: Optional[CheckDetail] = None) -> Call:
    """The row goes in before the agent hears anything. A crash after this restarts from it, with the same id and body."""
    now = ctx.now()
    body = request.model_dump(mode="json")
    run_id = body["run_id"]
    RUNS.add(ctx, AgentRunView(run_id=run_id, agent=agent, case_id=case_id, check_id=check.check_id if check else None, status="queued", generation=generation, attempt=attempt, is_current=True))
    WORK.add(ctx, RunWork(
        run_id=run_id, agent=agent, kind=kind, case_id=case_id, pipeline_run_id=pipeline_id, generation=generation, attempt=attempt,
        check_id=check.check_id if check else None, result_id=check.id if check else None, request=body, next_poll_at=now, deadline_at=_deadline(ctx, body),
    ))
    ctx.audit("agent_run_written", "agent_run", run_id, case_id, after={"agent": agent, "generation": generation, "attempt": attempt})
    return Call("start", run_id, agent, case_id, body)


def _deadline(ctx: Ctx, body: dict) -> datetime:
    return ctx.now() + timedelta(seconds=body["budget"]["deadline_seconds"] + ctx.rt.settings.agent_deadline_grace_seconds)


def _reader_id(case: CaseDetail, generation: int, attempt: int) -> str:
    return rules.run_id_for(case.id, case.sequence, "reader", generation, attempt)


def _open_reader(ctx: Ctx, p: Pinned, pipeline: PipelineRun, documents: Sequence[DossierDocument], keys: Sequence[FactKey], attempt: int, changed: Sequence[str] = ()) -> Optional[Call]:
    in_scope = [d for d in documents if d.sequence <= p.case.sequence]
    run_id = _reader_id(p.case, pipeline.generation, attempt)
    try:
        request = build.reader_request(ctx.rt.settings, run_id, p.case, ctx.tenant.id, p.pack, ctx.now(), p.product_type, in_scope, keys, changed)
    except ValidationError:
        return None
    return _write_run(ctx, "reader", READER, request, p.case.id, pipeline.id, pipeline.generation, attempt)


async def _plan(ctx: Ctx, case_id: str) -> list[Call]:
    """Writes what is about to happen, and lists the agent calls to make once this transaction has closed."""
    case = await CASES.require(ctx, case_id, "Case", for_update=True)
    views = await _active_runs(ctx, case_id)
    active = {v.run_id for v in views}
    all_work = await _work_of(ctx, case_id)
    work = {w.run_id: w for w in all_work}
    now = ctx.now()
    calls = []
    # Polling comes from the database, so a restart loses nothing. Old runs are polled too; their results get fenced.
    for action in rules.reconcile_on_boot(views):
        w = work.get(action.run_id)
        if w is None:
            continue
        if w.next_poll_at > now:
            continue
        calls.append(Call("start", w.run_id, w.agent, case_id, w.request) if w.posted_at is None else Call("poll", w.run_id, w.agent, case_id))
    if case.status in FROZEN:
        return calls
    pipeline = await _running_pipeline(ctx, case)
    if pipeline and pipeline.status in ("queued", "running"):
        p = await _pinned(ctx, case)
        calls += await _advance_stages(ctx, p, pipeline, all_work, active)
    return calls


async def _advance_stages(ctx: Ctx, p: Pinned, pipeline: PipelineRun, all_work: Sequence[RunWork], active: set[str]) -> list[Call]:
    """Stages run in the pack's order, and checks within a stage run side by side. A later stage waits for the one before it."""
    checks = await _current_checks(ctx, p.case.id)
    by_stage: dict[str, list[CheckDetail]] = {}
    for c in checks:
        d = p.definitions.get(c.check_id)
        by_stage.setdefault(d.stage if d else "knowledge", []).append(c)
    documents = await DOCUMENTS.list(ctx, ROW["dossier_document"].case_id == p.case.id)
    facts = await _facts(ctx, p.case.id)
    calls: list[Call] = []
    held = False
    for stage in _stage_order(list(p.definitions.values())):
        group = by_stage.get(stage, [])
        unsettled = False
        for c in group:
            if c.status not in WAITING:
                continue
            d = p.definitions.get(c.check_id)
            if held:
                await _save_settled(ctx, c, not_run_status(), "Not run: an earlier stage is held.")
                continue
            if d is None:
                await _save_settled(ctx, c, check_status(c.method, "failed"), "The pinned pack no longer has this check.")
                continue
            own = next((w for w in all_work if w.kind == "check" and w.result_id == c.id and (w.generation, w.attempt) == (c.generation, c.attempt)), None)
            if own:
                if own.run_id in active:
                    unsettled = True
                else:
                    await _save_settled(ctx, c, check_status(d.method, "failed"), "The run ended without a result. Re-check it.")
                continue
            if d.method == "code":
                await _run_code_check(ctx, p, c, d, documents)
                continue
            if d.method == "person" or _by_a_person(ctx, d):
                await _save_settled(ctx, c, check_status("person", None), "Waiting for a person to confirm.")
                continue
            reader, call = _reader_state(ctx, p, pipeline, all_work, active, documents)
            if call:
                calls.append(call)
                await ctx.db.flush()
                all_work = await _work_of(ctx, p.case.id)
                active = {*active, call.run_id}
            if reader == "active":
                unsettled = True
                continue
            if reader == "failed":
                await _save_settled(ctx, c, check_status(d.method, "completed", reader_failed=True), "The Dossier Reader failed, so this check has no facts. Re-check it.")
                continue
            call = await _open_check_run(ctx, p, pipeline, c, d, facts)
            if call:
                calls.append(call)
                unsettled = True
        await ctx.db.flush()
        fresh = await _current_checks(ctx, p.case.id)
        group_now = [c for c in fresh if c.id in {g.id for g in group}]
        if unsettled or any(c.status in WAITING for c in group_now):
            await _refresh_case(ctx, p.case)
            return calls
        held = held or (stage in GATE_STAGES and any(c.blocking and c.status == "held" for c in group_now))
    await _refresh_case(ctx, p.case)
    calls += await _brief_step(ctx, p, pipeline, all_work, active)
    return calls


def _reader_state(ctx: Ctx, p: Pinned, pipeline: PipelineRun, all_work: Sequence[RunWork], active: set[str], documents: Sequence[DossierDocument]) -> tuple[str, Optional[Call]]:
    """What the Dossier Reader has done for this pipeline run: `none` means no facts are asked for at all."""
    keys = _fact_keys(_wanted_definitions(p))
    if not keys:
        return "none", None
    latest = _latest_reader(all_work, pipeline.generation)
    if latest is None:
        call = _open_reader(ctx, p, pipeline, documents, keys, 1)
        return ("failed", None) if call is None else ("active", call)
    if latest.run_id in active:
        return "active", None
    return ("done", None) if latest.delivered else ("failed", None)


def _latest_reader(all_work: Sequence[RunWork], generation: int) -> Optional[RunWork]:
    return max((w for w in all_work if w.kind == "reader" and w.generation == generation), key=lambda w: w.attempt, default=None)


async def _save_settled(ctx: Ctx, c: CheckDetail, status: str, summary: str) -> None:
    moved = c.model_copy(update={"status": status, "summary": summary, "updated_at": ctx.now()})
    await CHECKS.save(ctx, moved.model_copy(update={"fingerprint": check_fingerprint(moved)}))
    ctx.audit("check_result_applied", "check_result", c.id, c.case_id, after={"status": status, "generation": c.generation, "attempt": c.attempt})


def _months_between(earlier: date, later: datetime) -> int:
    months = (later.year - earlier.year) * 12 + later.month - earlier.month
    return months - 1 if later.day < earlier.day else months


async def _code_inputs(ctx: Ctx, p: Pinned, documents: Sequence[DossierDocument]) -> dict:
    """The facts a code check compares, read from the server's own records."""
    in_scope = [d for d in documents if d.sequence <= p.case.sequence]
    modules = [d.module for d in in_scope if d.module]
    page = ROW["dossier_page"]
    suspect_pages = set((await ctx.db.execute(select(page.document_id, page.page).where(page.case_id == p.case.id, page.suspect.is_(True)))).all())
    suspect_pages |= {(d.id, n) for d in in_scope for n in d.suspect_pages}
    basis_row = ROW["reliance_basis"]
    basis = next(iter(await STORES["reliance_basis"].list(ctx, basis_row.target_type == "case", basis_row.target_id == p.case.id)), None)
    route = _route_of(p.case, p.pack)
    rule = route.reliance_rule if route else None
    match = ROW["watch_match"]
    open_matches = await STORES["watch_match"].list(ctx, match.target_type == "case", match.target_id == p.case.id, match.status == "open")
    return {
        ALL_MODULES: modules,
        "hidden_text_pages": len(suspect_pages),
        "modules_present": sorted({m.split(".")[0] for m in modules}),
        # No payment record exists yet. A filename can't prove payment, so outside simulation a person confirms it.
        "fee_receipt.valid": (
            any(d.module and d.module.startswith("1.2") and "fee" in d.filename.lower() for d in in_scope)
            if ctx.rt.settings.env == "simulation"
            else None
        ),
        "regulator": basis.regulator if basis else None,
        "trusted_regulators": rule.trusted_regulators if rule else None,
        "approval_age_months": _months_between(basis.approved_on, ctx.now()) if basis else None,
        "max_age_months": rule.max_age_months if rule else None,
        # An open match on this case blocks, whichever kind of notice it came from.
        "open_suspensions": len(open_matches),
        "open_withdrawals": 0,
    }


async def _run_code_check(ctx: Ctx, p: Pinned, c: CheckDetail, d: CheckDefinition, documents: Sequence[DossierDocument]) -> None:
    inputs = await _code_inputs(ctx, p, documents)
    try:
        passed = evaluate(d.rule or "", inputs)
    except RuleError:
        passed = None
    outcome = RuleOutcome(rule=d.rule, inputs=inputs_used(d.rule, inputs), passed=passed) if passed is not None else None
    status = check_status("code", "completed", may_pass_alone=d.may_pass_alone, rule_passed=passed, on_fail=d.on_fail)
    summary = "The rule is met." if passed else "The rule is not met." if passed is False else "The rule could not be evaluated."
    moved = c.model_copy(update={"status": status, "summary": summary, "rule_outcome": outcome, "updated_at": ctx.now()})
    moved = await CHECKS.save(ctx, moved.model_copy(update={"fingerprint": check_fingerprint(moved)}))
    await _write_trace(ctx, p, moved, "rule_trace")
    ctx.timeline(c.case_id, "check_finished", f"{c.check_id} finished: {status.replace('_', ' ')}.", SYSTEM, c.id)
    ctx.audit("check_result_applied", "check_result", c.id, c.case_id, after={"status": status, "generation": c.generation, "attempt": c.attempt})


async def _facts(ctx: Ctx, case_id: str) -> list[FactValue]:
    return await FACTS.list(ctx, ROW["fact"].case_id == case_id)


async def _open_check_run(ctx: Ctx, p: Pinned, pipeline: PipelineRun, c: CheckDetail, d: CheckDefinition, facts: Sequence[FactValue]) -> Optional[Call]:
    run_id = rules.run_id_for(p.case.id, p.case.sequence, c.check_id, c.generation, c.attempt)
    settings, now, tenant = ctx.rt.settings, ctx.now(), ctx.tenant.id
    try:
        if _agent_for(d) == SAMENESS:
            here = {f.key: f for f in facts if f.status == "found" and f.value}
            missing = [k.key for k in d.extract if k.key not in here]
            row = ROW["reliance_basis"]
            basis = next(iter(await STORES["reliance_basis"].list(ctx, row.target_type == "case", row.target_id == p.case.id)), None)
            if missing or basis is None:
                why = f"Values not found in the file: {', '.join(missing)}." if missing else "This case has no reference approval to compare with."
                await _save_settled(ctx, c, check_status("typed", "completed", no_source_found=True), why)
                return None
            request = build.sameness_request(settings, run_id, p.case, tenant, p.pack, now, p.product_type, d, basis, here)
            agent = SAMENESS
        else:
            request = build.assessment_request(settings, run_id, p.case, tenant, p.pack, now, p.product_type, d, facts)
            agent = ASSESSOR
    except ValidationError:
        await _save_settled(ctx, c, check_status(d.method, "failed"), "The request for this check could not be built.")
        return None
    await _retire_older_runs(ctx, c)
    return _write_run(ctx, "check", agent, request, p.case.id, pipeline.id, c.generation, c.attempt, c)


async def _retire_older_runs(ctx: Ctx, c: CheckDetail) -> None:
    row = ROW["agent_run"]
    for view in await RUNS.list(ctx, row.case_id == c.case_id, row.check_id == c.check_id, row.is_current.is_(True)):
        await RUNS.save(ctx, view.model_copy(update={"is_current": False}))


# ---------------------------------------------------------------- the brief


def _conflicts(checks: Sequence[CheckDetail], facts: Sequence[FactValue]) -> list[FactConflict]:
    """The same fact with different values, found by code across the file and every check's extraction."""
    values: dict[str, dict[str, str]] = {}
    who: dict[str, set[str]] = {}
    known = {f.key: f for f in facts}
    for c in checks:
        for f in c.extracted:
            if f.status == "found" and f.value:
                values.setdefault(f.key, {})[normalise(str(f.number if f.number is not None else f.value))] = f.value
                who.setdefault(f.key, set()).add(c.check_id)
                here = known.get(f.key)
                if here and here.status == "found" and here.value:
                    values[f.key].setdefault(normalise(str(here.number if here.number is not None else here.value)), here.value)
    return [FactConflict(fact=k, values=list(v.values()), check_ids=sorted(who[k])) for k, v in sorted(values.items()) if len(v) > 1]


async def _open_comments(ctx: Ctx, case_id: str) -> list[OpenComment]:
    row = ROW["review_thread"]
    threads = await STORES["review_thread"].list(ctx, row.case_id == case_id, row.status == "open")
    return [OpenComment(thread_id=t.id, check_id=t.target.ref if t.target.type == "check" else None, text=t.comments[0].text[:4000]) for t in threads if t.comments and t.comments[0].text]


async def _brief_step(ctx: Ctx, p: Pinned, pipeline: PipelineRun, all_work: Sequence[RunWork], active: dict) -> list[Call]:
    if any(w.kind == "challenge" and w.run_id in active for w in all_work):
        return []  # a second reading may still change a result
    checks = await _current_checks(ctx, p.case.id)
    input_fingerprint = case_input_fingerprint(CaseFacts(p.case.id, p.case, checks=checks))
    row = ROW["recommendation_version"]
    versions = await RECOMMENDATIONS.list(ctx, row.case_id == p.case.id)
    current = next((r for r in versions if r.is_current), None)
    if not checks or (current and current.input_fingerprint == input_fingerprint):
        await _finish_pipeline(ctx, p.case, pipeline)
        return []
    briefs = [w for w in all_work if w.kind == "brief" and w.generation == pipeline.generation]
    if any(w.run_id in active for w in briefs):
        return []
    run_row = ROW["agent_run"]
    views = {v.run_id: v for v in await RUNS.list(ctx, run_row.case_id == p.case.id, run_row.agent == COORDINATOR)}
    restarts = 0
    for w in sorted(briefs, key=lambda b: b.attempt):  # counted since the last brief that was accepted
        restarts = 0 if w.applied_at else restarts + (w.run_id in views and views[w.run_id].status in NOT_A_VERDICT)
    if restarts:
        action = rules.restart_failed_coordinator(p.case.status, restarts)
        if action == "tell_person":
            if p.case.assigned_to:
                ctx.notify(p.case.assigned_to, "brief_failed", "warning", f"The brief for {p.case.id} could not be written", link=f"/cases/{p.case.id}/checks")
            ctx.audit("brief_given_up", "pipeline_run", pipeline.id, p.case.id, after={"restarts": restarts})
            await _finish_pipeline(ctx, p.case, pipeline)
            return []
        if action == "nothing":
            return []
    blocked = any(c.blocking and not is_clear(c) for c in checks)
    options = [o for o in (p.pack.recommendation_options or ["hold_officer_review"]) if not (blocked and o == "proceed")]
    attempt = len(briefs) + 1
    request = build.brief_request(
        ctx.rt.settings, rules.run_id_for(p.case.id, p.case.sequence, "brief", pipeline.generation, attempt), p.case, ctx.tenant.id, p.pack, ctx.now(), p.product_type,
        input_fingerprint, checks, _conflicts(checks, await _facts(ctx, p.case.id)), await _open_comments(ctx, p.case.id), current, options or ["hold_officer_review"],
    )
    return [_write_run(ctx, "brief", COORDINATOR, request, p.case.id, pipeline.id, pipeline.generation, attempt)]


async def _finish_pipeline(ctx: Ctx, case: CaseDetail, pipeline: PipelineRun) -> None:
    await PIPELINES.save(ctx, pipeline.model_copy(update={"status": "completed", "finished_at": ctx.now()}))
    ctx.audit("pipeline_completed", "pipeline_run", pipeline.id, case.id)


# ---------------------------------------------------------------- what an agent sent back


async def apply_reply(rt: Runtime, tenant_id: str, case_id: str, run_id: str, op: Literal["start", "poll"], reply: AgentReply) -> None:
    """Takes one reply, in its own transaction. The case row is locked first, so replies for one case never interleave."""
    async with _tx(rt, tenant_id) as ctx:
        case = await CASES.require(ctx, case_id, "Case", for_update=True)
        work = await WORK.get(ctx, run_id, for_update=True)
        view = await RUNS.get(ctx, run_id)
        if work is None or view is None:
            return
        if op == "poll" and reply.status == 404:
            await _on_not_found(ctx, case, work, view)
        elif reply.invalid:
            await _close_run(ctx, case, work, view, refused(reply.invalid))
        elif reply.run is not None:
            await _take_run(ctx, case, work, view, reply.run)
        elif op == "start" and reply.status in (409, 422):
            await _close_run(ctx, case, work, view, refused("request_refused"))
        else:
            await _no_answer(ctx, case, work, view, reply.status)


async def _on_not_found(ctx: Ctx, case: CaseDetail, work: RunWork, view: AgentRunView) -> None:
    """A 404 means the agent forgot the run. It is never held against the company."""
    action = rules.recover_lost_run(404, view.status, work.reposts)
    if action == "repost_same_id":
        ctx.audit("agent_run_reposted", "agent_run", work.run_id, work.case_id, after={"reposts": work.reposts + 1})
        await WORK.save(ctx, work.model_copy(update={"reposts": work.reposts + 1, "posted_at": None, "next_poll_at": ctx.now(), "deadline_at": _deadline(ctx, work.request)}))
        await RUNS.save(ctx, view.model_copy(update={"status": "queued"}))
    elif action == "mark_lost":
        await _close_run(ctx, case, work, view, Verdict("lost", code="lost"))


async def _no_answer(ctx: Ctx, case: CaseDetail, work: RunWork, view: AgentRunView, status: int) -> None:
    past = ctx.now() >= work.deadline_at
    if rules.recover_lost_run(status, view.status, work.reposts, past_deadline=past) == "mark_lost":
        await _close_run(ctx, case, work, view, Verdict("lost", code="agent_unreachable"))
    else:
        await WORK.save(ctx, work.model_copy(update={"next_poll_at": ctx.now() + timedelta(seconds=ctx.rt.settings.agent_poll_seconds)}))


async def _take_run(ctx: Ctx, case: CaseDetail, work: RunWork, view: AgentRunView, run: RunResponse) -> None:
    if run.run_id != work.run_id:
        await _close_run(ctx, case, work, view, refused("wrong_run"))
        return
    verdict = review(run)
    if not verdict.finished:
        await _still_running(ctx, case, work, view, run, verdict)
        return
    if verdict.accepted:
        problem = result_problem(work.agent, work.request, run.result)
        if problem:
            verdict = refused(problem)
    await _close_run(ctx, case, work, view, verdict, run)


async def _still_running(ctx: Ctx, case: CaseDetail, work: RunWork, view: AgentRunView, run: RunResponse, verdict: Verdict) -> None:
    now = ctx.now()
    past = now >= work.deadline_at
    if rules.recover_lost_run(200, view.status, work.reposts, past_deadline=past) == "mark_lost":
        await _close_run(ctx, case, work, view, Verdict("lost", code="deadline_passed"))
        return
    if view.status not in UNFINISHED:
        return
    await RUNS.save(ctx, view.model_copy(update={"status": "running", "started_at": view.started_at or run.started_at or now}))
    fields = {"next_poll_at": now + timedelta(seconds=ctx.rt.settings.agent_poll_seconds)}
    if work.posted_at is None:
        fields.update(posted_at=now, deadline_at=_deadline(ctx, work.request))
    await WORK.save(ctx, work.model_copy(update=fields))
    if work.kind == "check" and case.status not in FROZEN:
        c = await CHECKS.get(ctx, work.result_id, for_update=True)
        if c and rules.newest_run_only(work.generation, work.attempt, c) and c.status in WAITING:
            moved = c.model_copy(update={"status": check_status(c.method, "running"), "summary": "Running.", "updated_at": now})
            await CHECKS.save(ctx, moved.model_copy(update={"fingerprint": check_fingerprint(moved)}))
            await ctx.db.flush()
            await _refresh_case(ctx, case)


async def _close_run(ctx: Ctx, case: CaseDetail, work: RunWork, view: AgentRunView, verdict: Verdict, run: Optional[RunResponse] = None) -> None:
    """The run is over. Output control already ran, so `delivered` only ever holds a result that passed."""
    if view.status in UNFINISHED:
        view = view.model_copy(update=_run_numbers(verdict, run, work, ctx.now(), view))
        await RUNS.save(ctx, view)
    if verdict.accepted and work.delivered is None:
        work = work.model_copy(update={"delivered": run.model_dump(mode="json")})
    work = work.model_copy(update={"posted_at": work.posted_at or ctx.now()})
    await WORK.save(ctx, work)
    await ctx.db.flush()
    if work.kind == "check":
        await _finish_check(ctx, case, work, verdict, run)
    elif work.kind == "reader":
        await _finish_reader(ctx, case, work, verdict, run)
    elif work.kind == "challenge":
        await _finish_challenge(ctx, case, work, verdict, run)
    else:
        await _finish_brief(ctx, case, work, verdict, run)


def _run_numbers(verdict: Verdict, run: Optional[RunResponse], work: RunWork, now: datetime, view: AgentRunView) -> dict:
    fields: dict = {"status": verdict.state, "error_code": verdict.code}
    if run:
        finished = run.finished_at or now
        started = view.started_at or run.started_at or work.posted_at
        fields.update(
            finish_reason=run.finish_reason, usage=run.usage, fallback_used=any(m.fallback for m in run.model_calls),
            started_at=started, duration_ms=int((finished - started).total_seconds() * 1000) if started and started.tzinfo and finished.tzinfo else None,
        )
    return fields


async def _applied_ids(ctx: Ctx, case_id: str) -> list[str]:
    row = ROW["agent_run_work"]
    return [w.run_id for w in await WORK.list(ctx, row.case_id == case_id, row.applied_at.is_not(None))]


async def _mark(ctx: Ctx, work: RunWork, outcome: str, applied: bool) -> None:
    fields: dict = {"outcome": outcome}
    if applied:
        fields["applied_at"] = ctx.now()
    await WORK.save(ctx, work.model_copy(update=fields))


async def _ignore(ctx: Ctx, work: RunWork, action: str, object_type: str = "agent_run") -> None:
    """Stored, and logged. It changes nothing a person relies on."""
    if work.applied_at is None:
        await _mark(ctx, work, action, applied=False)
    ctx.audit("late_result_ignored", object_type, work.run_id, work.case_id, after={"why": action})


async def _finish_check(ctx: Ctx, case: CaseDetail, work: RunWork, verdict: Verdict, run: Optional[RunResponse]) -> None:
    c = await CHECKS.get(ctx, work.result_id, for_update=True)
    if c is None:
        return
    action = rules.decide_result(work.run_id, work.generation, work.attempt, c, case.status, await _applied_ids(ctx, case.id))
    if action != "apply":
        await _ignore(ctx, work, action)
        return
    p = await _pinned(ctx, case)
    d = p.definitions.get(c.check_id)
    if verdict.accepted and d is not None:
        moved, gates = await _judge_result(ctx, p, work, c, d, run)
    else:
        moved, gates = _system_failure(ctx, c, verdict), [_gate(ctx, False, verdict.code or "failed", "The run did not produce a usable result.")]
    moved = await CHECKS.save(ctx, moved.model_copy(update={"updated_at": ctx.now(), "fingerprint": check_fingerprint(moved)}))
    await ctx.db.flush()
    await _write_trace(ctx, p, moved, "saved_reasoning" if moved.method == "agent" and moved.agent_status else "rule_trace", work, run if verdict.accepted else None, gates)
        if _needs_second_check(moved, gates) and ctx.rt.settings.is_off(CHALLENGER):
            moved, gates = _no_second_check(ctx, moved, gates)
    await _mark(ctx, work, "applied", applied=True)
    if _needs_second_check(moved, gates) and not ctx.rt.settings.is_off(CHALLENGER):
        _open_challenge(ctx, p, moved)
    actor = {"type": "agent", "id": work.agent}
    ctx.timeline(case.id, "check_finished", f"{c.check_id} finished: {moved.status.replace('_', ' ')}.", actor, c.id)
    ctx.audit("check_result_applied", "check_result", c.id, case.id, after={"status": moved.status, "generation": c.generation, "attempt": c.attempt, "run_id": work.run_id}, actor_type="agent", actor_id=work.agent)
    await _refresh_case(ctx, case)


# ---------------------------------------------------------------- the Challenger


def _open_challenge(ctx: Ctx, p: Pinned, c: CheckDetail) -> None:
    """A blocking check an agent found met gets a second reading that tries to disprove it. The row goes in now; the next step calls the agent."""
    run_id = rules.run_id_for(p.case.id, p.case.sequence, f"{c.check_id}-challenge", c.generation, c.attempt)
    try:
NO_SECOND_CHECK = "No second check: a person confirms."


def _needs_second_check(c: CheckDetail, gates: Sequence[GateDecisionView]) -> bool:
    """A blocking check that an agent found met, with every gate passed, gets a second reading."""
    return c.blocking and c.agent_status == "met" and bool(gates) and all(g.allowed for g in gates)


def _no_second_check(ctx: Ctx, c: CheckDetail, gates: Sequence[GateDecisionView]) -> tuple[CheckDetail, list[GateDecisionView]]:
    """With the Challenger off, nothing disputes the finding, so a person confirms it. A check that already needs a person is left as it is."""
    if c.status != "pass":
        return c, list(gates)
    status = check_status("agent", "completed", may_pass_alone=False, finding="met")
    return c.model_copy(update={"status": status, "summary": f"{NO_SECOND_CHECK} {c.summary}"[:500]}), [*gates, _gate(ctx, False, "no_second_check", NO_SECOND_CHECK)]

        request = build.challenge_request(ctx.rt.settings, run_id, p.case, ctx.tenant.id, p.pack, ctx.now(), p.product_type, c)
    except ValidationError:
        return
    _write_run(ctx, "challenge", CHALLENGER, request, p.case.id, c.pipeline_run_id, c.generation, c.attempt, c)


async def _finish_challenge(ctx: Ctx, case: CaseDetail, work: RunWork, verdict: Verdict, run: Optional[RunResponse]) -> None:
    """Only a dispute with a verified quote that differs from the claim's own changes the check, and then only to Person confirms.
    Upheld, cannot_verify, a failed run and a dispute with an invented quote all leave the check as it was."""
    c = await CHECKS.get(ctx, work.result_id, for_update=True)
    if not rules.apply_once(work.run_id, await _applied_ids(ctx, case.id)):
        await _ignore(ctx, work, "ignore_duplicate")
        return
    if rules.after_sign_action(case.status, "result") != "proceed":
        await _ignore(ctx, work, "ignore_frozen")
        return
    if c is None or not c.is_current or not rules.newest_run_only(work.generation, work.attempt, c):
        await _ignore(ctx, work, "ignore_old_run")
        return
    if not verdict.accepted or not isinstance(run.result, ChallengeResult):
        await _mark(ctx, work, "failed", applied=False)
        return
    result = run.result
    if result.verdict != "disputed" or c.status not in ("pass", "person_confirms"):
        await _mark(ctx, work, result.verdict, applied=True)
        ctx.audit(f"challenge_{result.verdict}", "check_result", c.id, case.id, after={"run_id": work.run_id}, actor_type="agent", actor_id=work.agent)
        return
    context = await citation_context(ctx, case, result.evidence, c.question, (), ())
    failures = check_citations(result.evidence, context)
    claimed = {(e.anchor, normalise(e.quote)) for e in c.evidence}
    standing = [e for e in result.evidence if not any(f.anchor == e.anchor for f in failures) and (e.anchor, normalise(e.quote)) not in claimed]
    if not standing:
        await _mark(ctx, work, "refused", applied=True)
        why = [f.message for f in failures] or ["The quote only repeats the claim's own evidence."]
        ctx.audit("challenge_refused", "check_result", c.id, case.id, after={"run_id": work.run_id, "why": why[:3]}, actor_type="agent", actor_id=work.agent)
        return
    moved = c.model_copy(update=dict(
        challenge=ChallengeSummary(verdict="disputed", reason=result.reason, evidence=standing, counted=True),
        status=check_status("agent", "completed", may_pass_alone=c.may_pass_alone, finding="met", disputed=True),
        summary=f"A second reading disputes this. {result.reason}"[:500], updated_at=ctx.now(),
    ))
    await CHECKS.save(ctx, moved.model_copy(update={"fingerprint": check_fingerprint(moved)}))
    await ctx.db.flush()
    await _mark(ctx, work, "disputed", applied=True)
    ctx.timeline(case.id, "check_disputed", f"{c.check_id} was disputed on a second reading: a person must confirm.", {"type": "agent", "id": work.agent}, c.id)
    ctx.audit("challenge_disputed", "check_result", c.id, case.id, before={"status": c.status}, after={"status": moved.status, "evidence": len(standing)}, actor_type="agent", actor_id=work.agent)
    await _refresh_case(ctx, case)
    await _reopen(ctx, case)


def _system_failure(ctx: Ctx, c: CheckDetail, verdict: Verdict) -> CheckDetail:
    status = check_status(c.method, verdict.state)
    return c.model_copy(update={"status": status, "summary": "The run did not finish, so there is no finding. This is a system failure, not the company's. Re-check it."})


def _gate(ctx: Ctx, allowed: bool, rule: str, detail: Optional[str] = None) -> GateDecisionView:
    return GateDecisionView(at=ctx.now(), checkpoint="output", allowed=allowed, rule=rule, detail=detail[:500] if detail else None)


def _refusal_gates(ctx: Ctx, failures: Sequence[CitationFailure]) -> list[GateDecisionView]:
    return [_gate(ctx, False, f.rule, f.message) for f in failures]


def _refused_summary(failures: Sequence[CitationFailure]) -> str:
    return ("Not verified, so a person must confirm. " + " ".join(f.message for f in failures))[:500]


async def citation_context(ctx: Ctx, case: CaseDetail, evidence: Sequence[Evidence], question: str, others: Sequence[str], other_evidence: Sequence[Evidence]) -> CitationContext:
    """Only what is pinned to this case can back a quote: its snapshot's versions, the file up to its sequence, the approval it relies on, and a foreign notice's latest text."""
    anchors = {e.anchor for e in evidence}
    chunks: dict[str, SourceChunk] = await kb_chunks(ctx.db, ctx.tenant.id, case.kb_snapshot_id, anchors)
    chunks.update(await notice_chunks(ctx.db, ctx.tenant.id, anchors))
    chunks.update(await reference_chunks(ctx.db, ctx.tenant.id, case.id, anchors))
    documents = {d.id: d for d in await DOCUMENTS.list(ctx, ROW["dossier_document"].case_id == case.id) if d.sequence <= case.sequence}
    cited_docs = {a.split(":")[0] for a in anchors}
    dossier, page = ROW["dossier_chunk"], ROW["dossier_page"]
    for row in (await ctx.db.scalars(select(dossier).where(dossier.case_id == case.id, dossier.tenant_id == ctx.tenant.id, dossier.document_id.in_(cited_docs)))).all():
        _add_dossier_text(chunks, documents, row.id, row.document_id, row.text, row.page, row.suspect)
    for row in (await ctx.db.scalars(select(page).where(page.case_id == case.id, page.tenant_id == ctx.tenant.id, page.document_id.in_(cited_docs)))).all():
        if row.anchor not in chunks:
            _add_dossier_text(chunks, documents, row.anchor, row.document_id, row.doc["text"], row.page, row.suspect)
    return CitationContext(chunks, question, others, other_evidence)


def _add_dossier_text(chunks: dict, documents: dict, anchor: str, document_id: str, text: str, page: Optional[int], suspect: bool) -> None:
    doc = documents.get(document_id)
    parts = anchor.split(":")
    if doc is None or len(parts) != 3 or parts[1] != doc.version:
        return
    chunks[anchor] = SourceChunk(anchor, text, "dossier", f"{doc.id}:{doc.version}", doc.id, suspect=suspect or page in doc.suspect_pages)


async def _what_others_wrote(ctx: Ctx, c: CheckDetail, work: RunWork) -> tuple[list[str], list[Evidence]]:
    others = await _current_checks(ctx, c.case_id)
    texts = [f["value"] for f in work.request.get("facts", [])]
    texts += [o.summary for o in others if o.check_id != c.check_id and o.method == "agent" and o.agent_status and o.summary]
    return texts, [e for o in others if o.check_id != c.check_id for e in o.evidence]


async def _judge_result(ctx: Ctx, p: Pinned, work: RunWork, c: CheckDetail, d: CheckDefinition, run: RunResponse) -> tuple[CheckDetail, list[GateDecisionView]]:
    """Turns a result that passed output control into what the check shows. Only `check_status` chooses the status."""
    result = run.result
    tool_errors = len(run.tool_errors)
    if tool_errors:
        status = check_status(d.method, "completed", tool_errors=tool_errors)
        return c.model_copy(update={"status": status, "summary": "A tool failed during the run, so its result may be incomplete. Re-check it."}), [_gate(ctx, False, "tool_errors")]
    if isinstance(result, AssessmentResult) and isinstance(result.outcome, AgentFinding):
        return await _judge_finding(ctx, p, work, c, d, result.outcome)
    if isinstance(result, AssessmentResult) and isinstance(result.outcome, TypedExtraction):
        return await _judge_extraction(ctx, p, work, c, d, result.outcome.extracted)
    if isinstance(result, SamenessResult):
        here = {f.key: f for f in await _facts(ctx, c.case_id)}
        there = [_foreign_value(f) for f in result.fields]
        ours = [e for f in result.fields for e in f.dossier_evidence]
        return await _judge_extraction(ctx, p, work, c, d, there, here={k: here[k] for k in here if any(x.key == k for x in d.extract)}, dossier_side=ours)
    return c.model_copy(update={"status": check_status(d.method, "completed", rule_passed=None), "summary": "The result had no usable outcome."}), []


async def _judge_finding(ctx: Ctx, p: Pinned, work: RunWork, c: CheckDetail, d: CheckDefinition, finding: AgentFinding) -> tuple[CheckDetail, list[GateDecisionView]]:
    others, other_evidence = await _what_others_wrote(ctx, c, work)
    context = await citation_context(ctx, p.case, finding.evidence, d.question, others, other_evidence)
    failures = check_citations(finding.evidence, context)
    verified = [e for e in finding.evidence if not any(f.anchor == e.anchor for f in failures)]
    cited_laws = [context.chunks[e.anchor].doc_id for e in verified if context.chunks[e.anchor].origin == "kb"]
    if not rules.agent_law_allowed(c, cited_laws, supporting=[s.doc_id for s in d.supporting_sources]):
        failures.append(CitationFailure("law", "wrong_law", "The finding cites law other than the source this check is pinned to."))
    if finding.status == "met":
        if not cited_laws:
            failures.append(CitationFailure("law", "no_kb_quote", "A met finding needs a verified quote from the knowledge base."))
        missing = unsupported_numbers(finding.summary, verified)
        if missing:
            failures.append(CitationFailure("numbers", "number_not_quoted", f"The finding states {missing[0]:g}, which no verified quote contains."))
    if finding.no_source is not None:
        _note_no_source(ctx, c, d)
        status = check_status("agent", "completed", no_source_found=True)
        summary = finding.summary
    elif failures:
        # With nothing it can stand on, the finding goes to a person, whatever it claimed. It never becomes Pass.
        status = check_status("agent", "completed", no_source_found=True)
        summary = _refused_summary(failures)
    else:
        status = check_status("agent", "completed", may_pass_alone=d.may_pass_alone, finding=finding.status)
        summary = finding.summary
    moved = c.model_copy(update=dict(
        status=status, summary=summary, agent_status=finding.status, evidence=verified, no_source_found=finding.no_source is not None,
        question_for_company=finding.question_for_company,
    ))
    return moved, [*_refusal_gates(ctx, failures), *([] if failures else [_gate(ctx, True, "quotes_verified", f"{len(verified)} quote(s) found in their chunks.")])]


def _note_no_source(ctx: Ctx, c: CheckDetail, d: CheckDefinition) -> None:
    """A gap candidate: nothing in force answers this check."""
    STORES["gap_signal"].add(ctx, {"id": ctx.id("sig"), "kind": "no_source", "detail": f"No source found for {d.id}."[:500], "count": 1})
    ctx.audit("gap_signal_raised", "gap_signal", d.id, c.case_id)


def _foreign_value(f) -> FactValue:
    found = f.there_status != "not_found"
    return FactValue(key=f.key, status=f.there_status, value=f.there_value if found else None, number=f.there_number if found else None, evidence=f.reference_evidence)


def _value_of(f: FactValue):
    return f.number if f.number is not None else f.value


async def _judge_extraction(ctx: Ctx, p: Pinned, work: RunWork, c: CheckDetail, d: CheckDefinition, extracted: Sequence[FactValue], here: Optional[dict[str, FactValue]] = None,
                          dossier_side: Sequence[Evidence] = ()) -> tuple[CheckDetail, list[GateDecisionView]]:
    """The agent's job ends at the values. Code checks the quotes and applies the check's rule. `dossier_side` is the quotes from our own
    files that Sameness gives next to the reference's."""
    evidence = [e for f in extracted for e in f.evidence] + list(dossier_side)
    others, other_evidence = await _what_others_wrote(ctx, c, work)
    context = await citation_context(ctx, p.case, evidence, d.question, others, other_evidence)
    failures = check_citations(evidence, context)
    bad_anchors = {f.anchor for f in failures}
    for f in extracted:
        if not value_in_its_quotes(f):
            failures.append(CitationFailure(f.key, "value_not_in_quote", f"The value given for {f.key} is not in its quote."))
    bad_keys = {f.anchor for f in failures if f.rule == "value_not_in_quote"}
    kept = [f for f in extracted if f.status != "found" or (f.key not in bad_keys and not any(e.anchor in bad_anchors for e in f.evidence))]
    not_found = [f.key for f in extracted if f.status != "found"]
    if here is not None:
        not_found += [k.key for k in d.extract if k.key not in here or here[k.key].status != "found"]
    verified = [e for f in kept for e in f.evidence]
    verified_side = [e for e in dossier_side if e.anchor not in bad_anchors]
    if failures or not_found:
        reason = _refused_summary(failures) if failures else f"Not found, so a person must look: {', '.join(sorted(set(not_found)))}."
        moved = c.model_copy(update={"status": check_status(d.method, "completed", no_source_found=True), "summary": reason, "extracted": kept, "evidence": verified})
        return moved, _refusal_gates(ctx, failures)
    values = {f.key: _value_of(f) for f in extracted}
    inputs = {"here": {k: _value_of(v) for k, v in here.items()}, "there": values} if here is not None else values
    try:
        passed = evaluate(d.rule or "", inputs)
    except RuleError:
        passed = None
    status = check_status(d.method, "completed", may_pass_alone=d.may_pass_alone, rule_passed=passed, on_fail=d.on_fail)
    outcome = RuleOutcome(rule=d.rule, inputs=inputs_used(d.rule, inputs), passed=passed) if passed is not None else None
    summary = "The rule is met." if passed else "The rule is not met." if passed is False else "The rule could not be evaluated."
    if here is not None:
        verified = [*(e for f in here.values() for e in f.evidence), *verified_side, *verified]
    return c.model_copy(update={"status": status, "summary": summary, "extracted": kept, "evidence": verified, "rule_outcome": outcome}), [_gate(ctx, True, "quotes_verified", f"{len(verified)} quote(s) found in their chunks.")]


async def _write_trace(ctx: Ctx, p: Pinned, c: CheckDetail, basis: str, work: Optional[RunWork] = None, run: Optional[RunResponse] = None, gates: Sequence[GateDecisionView] = ()) -> None:
    outcome = run.result.outcome if run and isinstance(run.result, AssessmentResult) else None
    trace = CheckTrace(
        check_result_id=c.id, basis=basis, reasoning=outcome.reasoning if isinstance(outcome, AgentFinding) else None, rule_outcome=c.rule_outcome,
        run_id=work.run_id if work else None, agent_version=run.agent.version if run else None, prompt_hash=run.agent.prompt_hash if run else None,
        pack_version_id=p.case.pack_version_id, kb_snapshot_id=p.case.kb_snapshot_id, usage=run.usage if run else None, model_calls=run.model_calls if run else [],
        gate_decisions=list(gates),
    )
    if await TRACES.get(ctx, c.id):
        await TRACES.save(ctx, trace)
    else:
        TRACES.add(ctx, trace)


async def _finish_reader(ctx: Ctx, case: CaseDetail, work: RunWork, verdict: Verdict, run: Optional[RunResponse]) -> None:
    if not rules.apply_once(work.run_id, await _applied_ids(ctx, case.id)):
        await _ignore(ctx, work, "ignore_duplicate")
        return
    if rules.after_sign_action(case.status, "result") != "proceed":
        await _ignore(ctx, work, "ignore_frozen")
        return
    pipeline = await _running_pipeline(ctx, case)
    if pipeline is None or pipeline.generation != work.generation:
        await _ignore(ctx, work, "ignore_old_run")
        return
    if verdict.accepted and isinstance(run.result, DossierReadResult):
        await _store_facts(ctx, case, work, run.result)
    await _mark(ctx, work, "applied", applied=True)


async def _store_facts(ctx: Ctx, case: CaseDetail, work: RunWork, result: DossierReadResult) -> None:
    """Only the keys asked for. A value whose quote fails is kept as ambiguous, so nothing compares it as if it were found."""
    asked = {k["key"] for k in work.request["fact_keys"]}
    row = ROW["fact"]
    for f in result.facts:
        if f.key not in asked:
            continue
        context = await citation_context(ctx, case, f.evidence, "", (), ())
        if check_citations(f.evidence, context) or not value_in_its_quotes(f):
            f = f.model_copy(update={"status": "ambiguous"}) if f.status == "found" else f
        fact_id = f"{case.id}:{f.key}"
        existing = await ctx.db.get(row, fact_id)
        if existing:
            existing.doc, existing.status = f.model_dump(mode="json"), f.status
        else:
            FACTS.add(ctx, f, id=fact_id, case_id=case.id)
    known = {d.id: d for d in await DOCUMENTS.list(ctx, ROW["dossier_document"].case_id == case.id)}
    allowed = set(work.request["modules"])
    for entry in result.document_map:
        doc = known.get(entry.doc_id)
        if doc and doc.module is None and entry.module in allowed:
            await DOCUMENTS.save(ctx, doc.model_copy(update={"module": entry.module}))
    await ctx.db.flush()
    ctx.audit("facts_stored", "case", case.id, case.id, after={"run_id": work.run_id, "facts": len(result.facts)}, actor_type="agent", actor_id=work.agent)


async def _finish_brief(ctx: Ctx, case: CaseDetail, work: RunWork, verdict: Verdict, run: Optional[RunResponse]) -> None:
    if not rules.apply_once(work.run_id, await _applied_ids(ctx, case.id)):
        await _ignore(ctx, work, "ignore_duplicate")
        return
    if not verdict.accepted or not isinstance(run.result, BriefResult):
        await _mark(ctx, work, "failed", applied=False)
        return
    checks = await _current_checks(ctx, case.id)
    current = case_input_fingerprint(CaseFacts(case.id, case, checks=checks))
    action = rules.coordinator_fence(run.result.input_fingerprint, current, case.status)
    if action != "accept":
        # The next step sees the recommendation is out of date, and asks again on today's results.
        await _ignore(ctx, work, action, "recommendation")
        return
    row = ROW["recommendation_version"]
    versions = await RECOMMENDATIONS.list(ctx, row.case_id == case.id)
    for r in versions:
        if r.is_current:
            await RECOMMENDATIONS.save(ctx, r.model_copy(update={"is_current": False}))
    await ctx.db.flush()
    version = max((r.version for r in versions), default=0) + 1
    conflicts = _conflicts(checks, await _facts(ctx, case.id))
    RECOMMENDATIONS.add(ctx, RecommendationVersion(
        version=version, case_id=case.id, choice=run.result.recommendation.choice, reason=run.result.recommendation.reason, summary=run.result.summary,
        items_to_confirm=[c.id for c in checks if c.status == "person_confirms"], conflicts=[ConflictView(fact=x.fact, values=x.values, check_ids=x.check_ids) for x in conflicts],
        input_fingerprint=current, is_current=True, created_at=ctx.now(),
    ))
    await ctx.db.flush()
    await _mark(ctx, work, "applied", applied=True)
    ctx.timeline(case.id, "recommendation", f"Recommendation v{version}: {run.result.recommendation.choice.replace('_', ' ')}.", {"type": "agent", "id": COORDINATOR})
    ctx.audit("recommendation_versioned", "recommendation", f"{case.id}:v{version}", case.id, after={"version": version, "choice": run.result.recommendation.choice}, actor_type="agent", actor_id=COORDINATOR)
    pipeline = await _running_pipeline(ctx, case)
    if pipeline and pipeline.status in ("queued", "running"):
        await _finish_pipeline(ctx, case, pipeline)
    await _refresh_case(ctx, case)


# ---------------------------------------------------------------- the step, the jobs and the tick


async def _call(agents, call: Call, off: frozenset[str] = frozenset()) -> AgentReply:
    try:
        if call.op == "start":
            return await agents.start(call.agent, call.request)
        return await agents.poll(call.agent, call.run_id)
    except Exception:
        # An unexpected client failure is an agent that didn't answer. The deadline bounds it.
        return AgentReply(0)


async def advance(rt: Runtime, tenant_id: str, case_id: str) -> None:
    """One step. No transaction is open during the agent calls."""
    async with _tx(rt, tenant_id) as ctx:
        calls = await _plan(ctx, case_id)
    replies = await asyncio.gather(*(_call(rt.agents, c, rt.settings.off_agents) for c in calls))
    for call, reply in zip(calls, replies):
        await apply_reply(rt, tenant_id, case_id, call.run_id, call.op, reply)
    async with _tx(rt, tenant_id) as ctx:
        await _schedule_next(ctx, case_id)


        if call.agent in off:
            return AgentReply(0)  # an agent that is switched off is never called, even for a run opened before it was switched off
async def _schedule_next(ctx: Ctx, case_id: str) -> None:
    case = await CASES.require(ctx, case_id, "Case")
    active = {v.run_id for v in await _active_runs(ctx, case_id)}
    work = [w for w in await _work_of(ctx, case_id) if w.run_id in active]
    pipeline = await _running_pipeline(ctx, case)
    unfinished = pipeline is not None and pipeline.status in ("queued", "running") and case.status not in FROZEN
    if not work and not unfinished:
        return
    due = max(ctx.now(), min(w.next_poll_at for w in work)) if work else ctx.now()
    await _schedule_advance(ctx, case_id, due)


# The worker runs a task inside a session, but a session takes a connection only at its first statement. These tasks
# never touch ctx.db, so nothing is held open across an agent call, and the job's completion is written at the end.
@task(ADVANCE)
async def _advance_task(payload: dict, ctx: Ctx) -> None:
    await advance(ctx.rt, ctx.tenant.id, payload["case_id"])


@task(CANCEL_REMOTE)
async def _cancel_remote_task(payload: dict, ctx: Ctx) -> None:
    reply = await ctx.rt.agents.cancel(payload["agent"], payload["run_id"])
    if reply.status in UNREACHED:
        raise RuntimeError(f"cancel of {payload['run_id']} got no answer")


async def tick(rt: Runtime) -> int:
    """At startup and on a scheduler tick: every case with unfinished work gets a job, whatever became of the last one. Returns how many."""
    run_row, work_row, pipeline_row, case_row = ROW["agent_run"], ROW["agent_run_work"], ROW["pipeline_run"], ROW["case"]
    async with rt.sessions() as db:
        from_runs = await db.execute(select(work_row.tenant_id, work_row.case_id).join(run_row, run_row.id == work_row.id).where(run_row.status.in_(tuple(UNFINISHED))))
        from_pipelines = await db.execute(
            select(pipeline_row.tenant_id, pipeline_row.case_id).join(case_row, case_row.current_pipeline_run_id == pipeline_row.id)
            .where(pipeline_row.status.in_(("queued", "running")), case_row.status.not_in(tuple(FROZEN)))
        )
        jobs = (await db.execute(select(JobRow.payload["case_id"].astext, JobRow.status).where(JobRow.kind == ADVANCE))).all()
        ever_started = {case_id for case_id, _ in jobs}
        # A pipeline row this runner never started, as in loaded demo data, is left as it is.
        open_cases = {*from_runs.all(), *(r for r in from_pipelines.all() if r[1] in ever_started)}
        with_job = {case_id for case_id, status in jobs if status in ("queued", "running")}
    queued = 0
    for tenant_id, case_id in sorted(open_cases):
        if case_id in with_job:
            continue
        async with _tx(rt, tenant_id) as ctx:
            await _schedule_advance(ctx, case_id)
        queued += 1
    return queued


async def pick_up_unfinished(rt: Runtime) -> None:
    """tick for the moments the whole database changes under the runner: startup and a demo reset. A failure is logged; the next tick tries again."""
    try:
        await tick(rt)
    except Exception:
        log.exception("could not pick up unfinished runs; the next tick will")


TICK_TASKS["reconcile_runs"] = tick
