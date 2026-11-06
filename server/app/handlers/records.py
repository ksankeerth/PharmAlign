"""Getting a signed decision into the government system of record, exactly once.

The proposal and its job are written in the signing transaction. The job sends outside any transaction, under its lease:
a short transaction marks the proposal sent, the call is made, and the outcome is saved with the job's completion.
If the outcome is unknown, the government system is asked for the key's result before anything is sent again.
"""
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

from pharmalign_contracts.regulator import ProposalCreate
from pharmalign_contracts.server import models as m

from ..connector import ConnectorError, Outcome
from ..context import Ctx
from ..domain import rules
from ..jobs import task
from ..stores import STORES
from .decisions import CASES, DECISIONS, LICENCES, PROPOSALS, SEND_JOB, record_outcome
from .support import name_of, people_in_view

MEDICINES = STORES["medicine"]
PARTIES = STORES["party"]
CONNECTOR_ID = "conn-regulator"
BY = {"type": "connector", "id": CONNECTOR_ID}
ACTION_OF = {"grant_licence": "grant", "record_refusal": "refuse"}
MAX_RETRY_SECONDS = 300


@dataclass
class Plan:
    step: str
    key: str
    prior_attempts: int
    request: Optional[ProposalCreate]


def _own(ctx: Ctx, db) -> Ctx:
    """The same caller in a transaction of its own."""
    return Ctx(ctx.rt, db, ctx.actor, ctx.tenant)


async def hold_for_notice(ctx: Ctx, decision_id: str) -> m.Decision:
    """A foreign notice arrived after signing. A grant not yet written to the record pauses until a Decision Maker decides."""
    dec = await DECISIONS.require(ctx, decision_id, "Decision", for_update=True)
    if not rules.post_sign_hold(dec):
        return dec
    held = await DECISIONS.save(ctx, dec.model_copy(update={"state": "on_hold"}))
    ctx.timeline(dec.case_id, "decision_on_hold", "Record update paused: a foreign notice arrived after signing. A Decision Maker must reconfirm or revoke.",
                 {"type": "system", "id": "watch"}, dec.id)
    ctx.audit("decision_on_hold", "decision", dec.id, dec.case_id, before={"state": dec.state}, after={"state": "on_hold"}, actor_type="system", actor_id="watch")
    return held


async def _request(ctx: Ctx, dec: m.Decision, proposal: m.Proposal) -> ProposalCreate:
    case = await CASES.require(ctx, dec.case_id, "Case")
    medicine = await MEDICINES.get(ctx, case.medicine_id)
    holder = await PARTIES.get(ctx, case.applicant_party_id)
    row = LICENCES.row
    known = await LICENCES.list(ctx, row.medicine_id == case.medicine_id, row.holder_party_id == case.applicant_party_id)
    signer = dec.signers[0]
    return ProposalCreate(
        action=ACTION_OF.get(proposal.action, "update"), decision_ref=dec.id, signed_payload_hash=dec.signed_payload_hash,
        signed_by=await name_of(ctx, signer.person_id), signed_at=signer.signed_at,
        licence_id=known[0].gov_ref if known and known[0].gov_ref else None, based_on_version=proposal.based_on_gov_version,
        payload={
            "case_ref": case.id, "medicine_name": medicine.name if medicine else case.medicine_id, "substances": medicine.substances if medicine else [],
            "holder": holder.name if holder else case.applicant_party_id, "conditions": dec.conditions,
        },
    )


async def _look(ctx: Ctx, proposal_id: str) -> Optional[Plan]:
    """What the code says to do next, from rows read in a short transaction of their own."""
    async with ctx.rt.sessions() as db, db.begin():
        c = _own(ctx, db)
        p = await PROPOSALS.get(c, proposal_id)
        dec = await DECISIONS.get(c, p.decision_id) if p else None
        if p is None or dec is None:
            return None
        step = rules.next_record_step(dec, p)
        if step == "tell_person" and p.state in ("unknown", "rejected"):
            return None  # already shown to a person
        request = await _request(c, dec, p) if step == "send_same_key" else None
        return Plan(step, p.idempotency_key, p.attempts, request)


async def _mark_sent(ctx: Ctx, proposal_id: str) -> bool:
    """The last look before a call, in the transaction that records the attempt. A held or revoked decision is never sent."""
    async with ctx.rt.sessions() as db, db.begin():
        c = _own(ctx, db)
        p0 = await PROPOSALS.get(c, proposal_id)
        dec = await DECISIONS.get(c, p0.decision_id, for_update=True)
        p = await PROPOSALS.get(c, proposal_id, for_update=True)
        if not rules.connector_may_write(dec) or rules.next_record_step(dec, p) != "send_same_key":
            return False
        await PROPOSALS.save(c, p.model_copy(update={"state": "sent", "attempts": p.attempts + 1, "updated_at": c.now()}))
        await DECISIONS.save(c, dec.model_copy(update={"record_status": "sent"}))
        c.audit("proposal_sent", "proposal", p.id, dec.case_id, after={"state": "sent", "attempts": p.attempts + 1}, actor_type="connector", actor_id=CONNECTOR_ID)
        await c.flush()
        return True


async def _settle(ctx: Ctx, proposal_id: str, outcome: Outcome, counts: bool) -> None:
    """Saves what happened, with the job's completion. `counts`: this call was not a send, so it uses an attempt of its own."""
    p0 = await PROPOSALS.require(ctx, proposal_id, "Proposal")
    dec = await DECISIONS.require(ctx, p0.decision_id, "Decision", for_update=True)
    p = await PROPOSALS.require(ctx, proposal_id, "Proposal", for_update=True)
    if p.state in ("acknowledged", "rejected"):
        return  # a person settled it first
    if outcome.state != "unknown":
        done = await record_outcome(ctx, p, outcome.state, outcome.gov_ref, BY, outcome.reason)
        ctx.audit(f"proposal_{'acknowledged' if outcome.state == 'applied' else 'rejected'}", "proposal", p.id, dec.case_id,
                  after={"state": done.state, "gov_ref": done.gov_ref}, actor_type="connector", actor_id=CONNECTOR_ID)
        if outcome.state == "rejected":
            await _tell_signers(ctx, dec, "The government system rejected the record update", outcome.reason)
        return
    attempts = p.attempts + (1 if counts else 0)
    final = attempts >= rules.MAX_RECORD_ATTEMPTS
    await PROPOSALS.save(ctx, p.model_copy(update={"state": "unknown", "attempts": attempts, "last_error": outcome.reason, "updated_at": ctx.now()}))
    await DECISIONS.save(ctx, dec.model_copy(update={"record_status": "unknown"}))
    ctx.audit("proposal_unknown", "proposal", p.id, dec.case_id, before={"state": p.state}, after={"state": "unknown", "attempts": attempts, "final": final},
              reason=outcome.reason, actor_type="connector", actor_id=CONNECTOR_ID)
    if not final:
        wait = min(MAX_RETRY_SECONDS, ctx.rt.settings.connector_retry_seconds * 2 ** (attempts - 1))
        ctx.schedule(SEND_JOB, {"proposal_id": p.id}, ctx.now() + timedelta(seconds=wait))
        return
    ctx.timeline(dec.case_id, "record_unknown", "The government system didn't confirm the record update. A person must check it and enter the outcome.",
                 {"type": "system", "id": "connector"}, p.id)
    await _tell_signers(ctx, dec, "The record update is unconfirmed", "Check the government system, then enter the outcome by hand.")


async def _tell_signers(ctx: Ctx, dec: m.Decision, title: str, body: Optional[str]) -> None:
    for person_id in dict.fromkeys([*(s.person_id for s in dec.signers), *await people_in_view(ctx, "case_officer")]):
        ctx.notify(person_id, "record_needs_attention", "high", f"{title}: {dec.case_id}", body, link=f"/cases/decisions/{dec.id}")


@task(SEND_JOB)
async def send_proposal(payload: dict, ctx: Ctx) -> None:
    proposal_id = payload["proposal_id"]
    plan = await _look(ctx, proposal_id)
    if plan is None or plan.step in ("pause", "stop", "done"):
        return  # a hold or a reconfirm queues the job again
    connector = ctx.rt.connector
    outcome: Optional[Outcome] = None
    sent = False
    if plan.prior_attempts > 0 or plan.step == "tell_person":
        # An earlier call may have arrived. Ask first, and send again only if the government system holds nothing for the key.
        try:
            outcome = (await connector.read_back(plan.key)).outcome
        except ConnectorError as e:
            outcome = Outcome("unknown", reason=str(e)[:200])
    if outcome is None and plan.step == "send_same_key":
        if not await _mark_sent(ctx, proposal_id):
            return
        sent = True
        outcome = await connector.propose(plan.key, plan.request)
    if outcome is None:
        outcome = Outcome("unknown", reason="No record was found for the key after the last attempt.")
    await _settle(ctx, proposal_id, outcome, counts=not sent and plan.step != "tell_person")
