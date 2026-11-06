"""Promote and decide, government proposals and connections. Signing re-tests every guard at the moment of signing,
against the fingerprint the Decision Maker read. Sending a proposal to the government is not here."""
from types import SimpleNamespace
from typing import Optional

from pharmalign_contracts.server import models as m

from ..domain import rules
from ..domain.fingerprints import case_checks, case_package_fingerprint, current_recommendation, open_case_matches
from ..domain.guards import SigningInput, fingerprint_guard
from ..ops import op
from ..paging import paginate
from ..problems import enforce_guards, guard, invalid, not_found, require_reason
from ..stores import STORES
from ..tables import ROW
from .support import CASES, DECISIONS, PACKS, PROMOTIONS, PROPOSALS, RELIANCE, SIGNERS, SOURCES, as_result, facts_of, get_case, name_of, people_in_view, with_limit

SEND_JOB = "decisions.send_proposal"
LICENCES = STORES["licence"]
CONNECTIONS = STORES["connection"]
UNHEALTHY = ("failing", "zero_yield", "layout_changed")


async def foreign_status_of(ctx, case: m.CaseDetail, facts) -> m.ForeignStatus:
    row = ROW["reliance_basis"]
    basis = next(iter(await RELIANCE.list(ctx, row.target_type == "case", row.target_id == case.id)), None)
    every = await SOURCES.list(ctx)
    sources = [s for s in every if not basis or s.regulator == basis.regulator]
    unhealthy = [s for s in every if s.health in UNHEALTHY]
    seen = sorted(s.last_success_at for s in sources if s.last_success_at)
    warning = None
    if unhealthy:
        names = ", ".join(s.regulator for s in unhealthy)
        states = ", ".join(s.health.replace("_", " ") for s in unhealthy)
        warning = f"{names}: source unhealthy ({states}). Foreign status may be out of date."
    return m.ForeignStatus(
        reliance_basis_id=basis.id if basis else None, last_checked_at=seen[-1] if seen else None, sources_healthy=not unhealthy,
        open_notices=len(open_case_matches(facts)), warning=warning,
    )


async def sources_for(ctx, case: m.CaseDetail) -> list[m.WatchSource]:
    """The sources a grant on this case relies on: those of the regulators it is linked to. An abridged case with no link relies on all of them."""
    row = ROW["reliance_basis"]
    regulators = {b.regulator for b in await RELIANCE.list(ctx, row.target_type == "case", row.target_id == case.id)}
    every = await SOURCES.list(ctx)
    if regulators:
        return [s for s in every if s.regulator in regulators]
    return every if case.route == "abridged" else []


async def proposed_action(ctx, case: m.CaseDetail) -> str:
    pack = await PACKS.get(ctx, case.pack_version_id)
    years = pack.document.licence_validity_years if pack else None
    if case.case_type == "reliance_rereview":
        return "Keep, vary or suspend the licence after re-review"
    return f"Grant a marketing authorisation for {years} years" if years else "Grant a marketing authorisation"


@op("GET", "/api/v1/cases/{case_id}/promotion-check")
async def promotion_check(req, ctx):
    facts = await facts_of(ctx, await get_case(ctx, req.path.case_id))
    guards = rules.promote_guard(facts)
    return m.PromotionCheck(can_promote=all(g.passed for g in guards), guards=guards, fingerprint=case_package_fingerprint(facts))


@op("POST", "/api/v1/cases/{case_id}/promote")
async def promote(req, ctx):
    case = await get_case(ctx, req.path.case_id, for_update=True)
    facts = await facts_of(ctx, case)
    current = case_package_fingerprint(facts)
    enforce_guards([*rules.promote_guard(facts), fingerprint_guard(req.body.fingerprint, current)], "Promotion refused", current)
    promotion = m.Promotion(id=ctx.id("pr"), case_id=case.id, promoted_by=ctx.person_id, fingerprint=current, at=ctx.now())
    PROMOTIONS.add(ctx, promotion)
    await CASES.save(ctx, case.model_copy(update={"status": "promoted"}))
    why = f": {req.body.reason}" if req.body.reason else "."
    ctx.timeline(case.id, "promoted", f"{await name_of(ctx, ctx.person_id)} promoted the case to the Decision Maker{why}", {"type": "person", "id": ctx.person_id}, promotion.id)
    ctx.audit("case_promoted", "case", case.id, case.id, before={"status": case.status}, after={"status": "promoted", "fingerprint": current}, reason=req.body.reason)
    for person_id in await people_in_view(ctx, "decision_maker"):
        ctx.notify(person_id, "ready_to_sign", "review", f"Ready to sign: {case.id}", link=f"/cases/{case.id}/signing")
    return promotion


@op("GET", "/api/v1/cases/{case_id}/signing-package")
async def signing_package(req, ctx):
    case = await get_case(ctx, req.path.case_id)
    facts = await facts_of(ctx, case)
    recommendation = current_recommendation(facts)
    if recommendation is None:
        not_found("Current recommendation for", case.id)
    fingerprint = case_package_fingerprint(facts)
    checks = case_checks(facts)
    return m.SigningPackage(
        case_id=case.id, fingerprint=fingerprint, recommendation=recommendation, checks=[as_result(c) for c in checks],
        waivers=[c.waiver for c in checks if c.waiver], foreign_status=await foreign_status_of(ctx, case, facts),
        pack_version_id=case.pack_version_id, kb_snapshot_id=case.kb_snapshot_id, proposed_action=await proposed_action(ctx, case),
        guards=rules.sign_guard(facts, SigningInput(ctx.person_id, fingerprint)), legal_effect="demonstration_only",
    )


@op("GET", "/api/v1/decisions")
async def list_decisions(req, ctx):
    q = req.query
    row = ROW["decision"]
    where = [c for c in (
        row.case_id == q.case_id if q.case_id else None, row.type == q.type if q.type else None,
        row.state == q.state if q.state else None, row.record_status == q.record_status if q.record_status else None,
    ) if c is not None]
    found = await DECISIONS.list(ctx, *where)
    found.sort(key=lambda d: d.signers[0].signed_at, reverse=True)
    return paginate(found, with_limit(q, 200))


@op("POST", "/api/v1/cases/{case_id}/decisions")
async def sign(req, ctx):
    b = req.body
    case = await get_case(ctx, req.path.case_id)
    # A grant reads the foreign sources first, in transactions of their own, so a slow source holds no lock here.
    fetch_started_at = ctx.now()
    if b.type == "grant":
        async with ctx.rt.sessions() as other:
            ids = rules.sources_to_fetch(await sources_for(SimpleNamespace(db=other, tenant=ctx.tenant), case))
        await ctx.rt.fetcher.fetch(ctx.rt, ctx.tenant.id, ids)
    case = await get_case(ctx, case.id, for_update=True)
    facts = await facts_of(ctx, case)
    current = case_package_fingerprint(facts)
    signing = SigningInput(ctx.person_id, b.fingerprint, b.recommendation_version)
    if b.type == "grant":
        sources = await sources_for(ctx, case)
        row = ROW["reliance_basis"]
        relies_on_foreign = case.route == "abridged" or bool(await RELIANCE.list(ctx, row.target_type == "case", row.target_id == case.id))
        guards = rules.sign_grant_guards(facts, signing, sources, fetch_started_at, relies_on_foreign=relies_on_foreign, signer_reason=b.foreign_status_reason)
    elif any(c.blocking and c.status == "held" for c in case_checks(facts)):
        guards = rules.exit_path_guards(facts, ctx.person_id, b.type, ctx.view, b.fingerprint, b.reason, ctx.now())
    else:
        guards = rules.sign_other_guards(facts, signing)
    enforce_guards(guards, "Signing refused", current)

    decision = m.Decision(
        id=ctx.id("dec"), case_id=case.id, type=b.type, state="signed", signers=[m.Signer(person_id=ctx.person_id, role="decision_maker", signed_at=ctx.now())],
        signed_payload_hash=b.fingerprint, recommendation_version=b.recommendation_version, accepts_recommendation=b.accepts_recommendation, reason=b.reason,
        conditions=b.conditions, foreign_status_reason=b.foreign_status_reason, legal_effect="demonstration_only", record_status="not_needed" if b.type == "close" else "queued", proposal_id=None,
    )
    job = rules.record_job_for(decision)
    proposal = None
    if job:
        action = {"grant": "grant_licence", "refusal": "record_refusal"}.get(b.type, "licence_action")
        proposal = m.Proposal(
            id=ctx.id("prop"), decision_id=decision.id, action=action, state="queued", idempotency_key=job.idempotency_key, based_on_gov_version=None,
            attempts=0, gov_ref=None, last_error=None, created_at=ctx.now(), updated_at=ctx.now(),
        )
        decision = decision.model_copy(update={"proposal_id": proposal.id})
    DECISIONS.add(ctx, decision)
    await ctx.db.flush()
    SIGNERS.add(ctx, decision.signers[0], id=f"{decision.id}:{ctx.person_id}", decision_id=decision.id, case_id=case.id)
    if proposal:
        PROPOSALS.add(ctx, proposal)
        ctx.schedule(SEND_JOB, {"proposal_id": proposal.id})  # in this transaction, so a restart can't lose it
    await CASES.save(ctx, case.model_copy(update={"decision_id": decision.id, "status": "closed" if b.type == "close" else "recording", "clock_running": False}))
    override = "" if b.accepts_recommendation else " (overriding the recommendation)"
    ctx.timeline(case.id, "decision_signed", f"{await name_of(ctx, ctx.person_id)} signed: {b.type}{override}.", {"type": "person", "id": ctx.person_id}, decision.id)
    ctx.audit(
        "decision_signed", "decision", decision.id, case.id, before={"status": case.status},
        after={"type": decision.type, "fingerprint": decision.signed_payload_hash, "proposal": decision.proposal_id, "foreign_status_reason": b.foreign_status_reason},
        reason=decision.reason,
    )
    return decision


@op("GET", "/api/v1/decisions/{decision_id}")
async def get_decision(req, ctx):
    return await DECISIONS.require(ctx, req.path.decision_id, "Decision")


@op("POST", "/api/v1/decisions/{decision_id}/reconfirm")
async def reconfirm(req, ctx):
    dec = await DECISIONS.require(ctx, req.path.decision_id, "Decision", for_update=True)
    enforce_guards(rules.hold_resolution_guards(dec, ctx.view, req.body.fingerprint, dec.signed_payload_hash, req.body.reason), "Reconfirm refused", dec.signed_payload_hash)
    reason = require_reason(req.body.reason)
    signer = m.Signer(person_id=ctx.person_id, role="decision_maker", signed_at=ctx.now())
    again = any(s.person_id == ctx.person_id for s in dec.signers)  # the one who signed may reconfirm, and is already a signer
    done = await DECISIONS.save(ctx, dec.model_copy(update={"state": "reconfirmed", "signers": dec.signers if again else [*dec.signers, signer]}))
    if not again:
        SIGNERS.add(ctx, signer, id=f"{dec.id}:{ctx.person_id}", decision_id=dec.id, case_id=dec.case_id)
    proposal = await PROPOSALS.get(ctx, dec.proposal_id) if dec.proposal_id else None
    if proposal and proposal.state in ("queued", "sent", "unknown") and proposal.attempts < rules.MAX_RECORD_ATTEMPTS:
        ctx.schedule(SEND_JOB, {"proposal_id": proposal.id})  # the hold stopped it; a person has now said to go on
    case = await CASES.get(ctx, dec.case_id, for_update=True)
    if case and done.record_status == "acknowledged":
        await tell_applicant(ctx, case, done)
    ctx.timeline(dec.case_id, "decision_reconfirmed", f"Decision reconfirmed after a foreign notice: {reason}", {"type": "person", "id": ctx.person_id}, dec.id)
    ctx.audit("decision_reconfirmed", "decision", dec.id, dec.case_id, before={"state": "on_hold"}, after={"state": "reconfirmed"}, reason=reason)
    return done


@op("POST", "/api/v1/decisions/{decision_id}/revoke")
async def revoke(req, ctx):
    dec = await DECISIONS.require(ctx, req.path.decision_id, "Decision", for_update=True)
    enforce_guards(rules.hold_resolution_guards(dec, ctx.view, req.body.fingerprint, dec.signed_payload_hash, req.body.reason), "Revoke refused", dec.signed_payload_hash)
    reason = require_reason(req.body.reason)
    fields: dict = {"state": "revoked"}
    proposal = await PROPOSALS.get(ctx, dec.proposal_id, for_update=True) if dec.proposal_id else None
    if proposal and proposal.state in ("queued", "unknown"):
        await PROPOSALS.save(ctx, proposal.model_copy(update={"state": "rejected", "last_error": "Withdrawn: the decision was revoked before the record changed.", "updated_at": ctx.now()}))
        fields["record_status"] = "rejected"
    done = await DECISIONS.save(ctx, dec.model_copy(update=fields))
    case = await CASES.get(ctx, dec.case_id, for_update=True)
    if case:
        await CASES.save(ctx, case.model_copy(update={"status": "closed"}))
    ctx.timeline(dec.case_id, "decision_revoked", f"Decision revoked: {reason}", {"type": "person", "id": ctx.person_id}, dec.id)
    ctx.audit("decision_revoked", "decision", dec.id, dec.case_id, before={"state": "on_hold"}, after={"state": "revoked"}, reason=reason)
    return done


@op("GET", "/api/v1/proposals")
async def list_proposals(req, ctx):
    row = ROW["proposal"]
    where = [row.state == req.query.state] if req.query.state else []
    return await PROPOSALS.page(ctx, req.query, *where, order_by=row.updated_at.desc())


@op("GET", "/api/v1/proposals/{proposal_id}")
async def get_proposal(req, ctx):
    return await PROPOSALS.require(ctx, req.path.proposal_id, "Proposal")


async def tell_applicant(ctx, case: m.CaseDetail, dec: m.Decision) -> None:
    """Closes the case and tells the applicant, but only once the record shows the same thing."""
    if not rules.may_tell_applicant(dec):
        return
    await CASES.save(ctx, case.model_copy(update={"status": "closed"}))
    ctx.timeline(case.id, "applicant_told", "Applicant told after the record was confirmed.", {"type": "system", "id": "notifications"})


async def record_outcome(ctx, proposal: m.Proposal, outcome: str, gov_ref: Optional[str], by: dict, reason: Optional[str] = None) -> m.Proposal:
    """The government record said what happened to a proposal. It closes the case, and a grant makes a licence.
    The mirror changes only here, and only on an outcome the government system gave, or a person entered."""
    dec = await DECISIONS.get(ctx, proposal.decision_id, for_update=True)
    state = "acknowledged" if outcome == "applied" else "rejected"
    done = await PROPOSALS.save(ctx, proposal.model_copy(update={
        "state": state, "gov_ref": gov_ref or proposal.gov_ref, "updated_at": ctx.now(),
        "last_error": (reason or "Rejected by the government system.") if outcome == "rejected" else None,
    }))
    if dec:
        dec = await DECISIONS.save(ctx, dec.model_copy(update={"record_status": state}))
    case = await CASES.get(ctx, dec.case_id, for_update=True) if dec else None
    if case and outcome == "applied":
        row = ROW["licence"]
        known = await LICENCES.list(ctx, row.medicine_id == case.medicine_id, row.holder_party_id == case.applicant_party_id)
        if not known and dec.type == "grant":
            LICENCES.add(ctx, m.Licence(
                id=ctx.id("lic"), medicine_id=case.medicine_id, holder_party_id=case.applicant_party_id, gov_ref=gov_ref, gov_status="licensed",
                gov_version=f"{gov_ref}-v1" if gov_ref else None, gov_as_of=ctx.now(), valid_to=None,
            ))
        actor = {"type": "person" if by["type"] == "person" else "system", "id": by["id"]}
        ctx.timeline(case.id, "record_acknowledged", f"Government record acknowledged{f': {gov_ref}' if gov_ref else ''}.", actor, proposal.id)
        await tell_applicant(ctx, case, dec)
    elif case:
        ctx.timeline(case.id, "record_rejected", "The government system rejected the record update. A person must handle it.", {"type": "system", "id": "connector"}, proposal.id)
    return done


@op("POST", "/api/v1/proposals/{proposal_id}/manual-ack")
async def manual_ack(req, ctx):
    p = await PROPOSALS.require(ctx, req.path.proposal_id, "Proposal", for_update=True)
    if req.body.outcome == "applied" and not req.body.gov_ref:
        invalid("An applied outcome needs the government reference.")
    enforce_guards([guard("awaiting_outcome", p.state in ("sent", "unknown"), f"The proposal is {p.state}.")], "Manual outcome refused")
    done = await record_outcome(ctx, p, req.body.outcome, req.body.gov_ref, {"type": "person", "id": ctx.person_id})
    ctx.audit("proposal_manual_ack", "proposal", p.id, before={"state": p.state}, after={"state": done.state, "gov_ref": done.gov_ref}, reason=req.body.note)
    return done


@op("GET", "/api/v1/connections")
async def list_connections(req, ctx):
    return await CONNECTIONS.page(ctx, req.query)


@op("POST", "/api/v1/connections")
async def register_connection(req, ctx):
    c = m.Connection(**req.body.model_dump(), id=ctx.id("conn"), status="unchecked", last_checked_at=None, last_sync_at=None, results=[], approved_by=None)
    CONNECTIONS.add(ctx, c)
    ctx.audit("connection_registered", "connection", c.id, after=c)
    return c


@op("POST", "/api/v1/connections/{connection_id}/check")
async def check_connection(req, ctx):
    c = await CONNECTIONS.require(ctx, req.path.connection_id, "Connection", for_update=True)
    ok = c.kind != "api" or bool(c.base_url)
    results = [m.ConformanceResult(key=r.key, passed=ok, detail=None if ok else "No base URL.")
               for r in (c.results or [m.ConformanceResult(key="auth", passed=True), m.ConformanceResult(key="read_back", passed=True)])]
    status = c.status if c.status == "approved" else ("passing" if ok else "failing")
    done = await CONNECTIONS.save(ctx, c.model_copy(update={"results": results, "last_checked_at": ctx.now(), "status": status}))
    ctx.audit("connection_checked", "connection", c.id, after={"status": done.status})
    return done


@op("POST", "/api/v1/connections/{connection_id}/approve")
async def approve_connection(req, ctx):
    c = await CONNECTIONS.require(ctx, req.path.connection_id, "Connection", for_update=True)
    reason = require_reason(req.body.reason)
    enforce_guards([guard("conformance_passing", c.status == "passing", "Already approved." if c.status == "approved" else f"Conformance is {c.status}.")], "Approval refused")
    done = await CONNECTIONS.save(ctx, c.model_copy(update={"status": "approved", "approved_by": ctx.person_id}))
    ctx.audit("connection_approved", "connection", c.id, before={"status": "passing"}, after={"status": "approved"}, reason=reason)
    return done
