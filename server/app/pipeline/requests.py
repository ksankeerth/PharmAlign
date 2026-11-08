"""Builds each agent's request from the case and its pinned pack. Every request is checked by its contract model here."""
from datetime import datetime, timedelta
from typing import Optional, Sequence

from pharmalign_contracts.agents.run_agents import (
    AssessmentRequest,
    BriefRequest,
    ChallengeRequest,
    ChallengeTarget,
    CheckSource,
    CheckSummary,
    CheckToAssess,
    DocumentRef,
    DossierReadRequest,
    FactConflict,
    FieldToCompare,
    KnownFact,
    OpenComment,
    PreviousRecommendation,
    ReferenceApproval,
    SamenessRequest,
)
from pharmalign_contracts.common import CaseRef, FactValue, RunRequest
from pharmalign_contracts.server.models import CaseDetail, CheckDefinition, CheckDetail, DossierDocument, PackDocument, RecommendationVersion, RelianceBasis

from ..config import Settings
from ..domain.rules import TransitionRule, law_doc_for
from ..mcp.token import run_token


def _base(settings: Settings, agent: str, run_id: str, case: CaseDetail, tenant: str, pack: PackDocument, now: datetime) -> dict:
    """`run_token` scopes the run's tools: this case, its pinned snapshot, this agent, until the deadline and its grace."""
    simulated = case.environment == "simulation"
    expires_at = now + timedelta(seconds=settings.agent_run_deadline_seconds + settings.agent_deadline_grace_seconds)
    return dict(
        run_id=run_id,
        run_token=run_token(settings, run_id=run_id, agent=agent, tenant=tenant, case_id=case.id, snapshot_id=case.kb_snapshot_id,
                            max_calls=settings.agent_max_tool_calls, expires_at=expires_at),
        tenant=tenant.lower(),
        mode="simulation" if simulated else "live",
        execution_profile={"model_set": "default", "gate": "on", "data_class": "synthetic" if simulated else "confidential"},
        as_of=now,
        versions={"pack": case.pack_version_id, "kb_snapshot": case.kb_snapshot_id},
        language=pack.languages[0],
        budget={"max_model_calls": settings.agent_max_model_calls, "max_tool_calls": settings.agent_max_tool_calls,
                "deadline_seconds": settings.agent_run_deadline_seconds},
    )


def _case_ref(case: CaseDetail, product_type: Optional[str]) -> CaseRef:
    return CaseRef(case_id=case.id, sequence=case.sequence, case_type=case.case_type, route=case.route, product_type=product_type)


def known_facts(facts: Sequence[FactValue]) -> list[KnownFact]:
    return [KnownFact(key=f.key, value=f.value, unit=f.unit) for f in facts if f.status == "found" and f.value]


def reader_request(settings: Settings, run_id: str, case: CaseDetail, tenant: str, pack: PackDocument, now: datetime, product_type: Optional[str],
                   documents: Sequence[DossierDocument], fact_keys: Sequence, changed_doc_ids: Sequence[str] = ()) -> RunRequest:
    modules = sorted({d.module for d in documents if d.module})
    refs = [DocumentRef(doc_id=d.id, version=d.version, filename=d.filename, pages=d.pages, language=d.language, suspect_pages=d.suspect_pages) for d in documents]
    return DossierReadRequest(**_base(settings, "dossier-reader", run_id, case, tenant, pack, now), case=_case_ref(case, product_type), documents=refs,
                              changed_doc_ids=list(changed_doc_ids), modules=modules, fact_keys=list(fact_keys))


def assessment_request(settings: Settings, run_id: str, case: CaseDetail, tenant: str, pack: PackDocument, now: datetime, product_type: Optional[str],
                       definition: CheckDefinition, facts: Sequence[FactValue], reviewer_comment: Optional[str] = None,
                       transition: Optional[TransitionRule] = None) -> RunRequest:
    s = definition.source
    check = CheckToAssess(
        id=definition.id, version=definition.version, question=definition.question,
        source=CheckSource(doc_id=s.doc_id, clause=s.clause or "Whole document") if s else None,
        supporting_sources=[CheckSource(doc_id=x.doc_id, clause=x.clause) for x in definition.supporting_sources if x.clause],
        legal_force=definition.legal_force, method=definition.method, reads=definition.reads, extract=definition.extract,
    )
    moved = law_doc_for(s.doc_id, transition) if s else None
    moved_source = CheckSource(doc_id=moved, clause="Whole document") if s and moved != s.doc_id else None
    # A typed check only extracts, so it may have no profile.
    return AssessmentRequest(**_base(settings, "checklist-assessor", run_id, case, tenant, pack, now), case=_case_ref(case, product_type), profile=definition.profile,
                             check=check, moved_source=moved_source, facts=known_facts(facts), reviewer_comment=reviewer_comment)


def sameness_request(settings: Settings, run_id: str, case: CaseDetail, tenant: str, pack: PackDocument, now: datetime, product_type: Optional[str],
                     definition: CheckDefinition, basis: RelianceBasis, here: dict[str, FactValue]) -> RunRequest:
    fields = []
    for key in definition.extract:
        f = here[key.key]
        fields.append(FieldToCompare(key=key.key, compare="number" if key.value_type == "number" else "exact", unit=key.unit, here=f.value, here_number=f.number))
    reference = ReferenceApproval(approval_id=basis.approval_ref, regulator=basis.regulator, approved_on=basis.approved_on, documents=basis.document_refs)
    return SamenessRequest(**_base(settings, "sameness", run_id, case, tenant, pack, now), case=_case_ref(case, product_type), reference=reference, fields=fields)


def brief_request(settings: Settings, run_id: str, case: CaseDetail, tenant: str, pack: PackDocument, now: datetime, product_type: Optional[str],
                  input_fingerprint: str, checks: Sequence[CheckDetail], conflicts: Sequence[FactConflict], open_comments: Sequence[OpenComment],
                  previous: Optional[RecommendationVersion], options: Sequence[str]) -> RunRequest:
    results = [
        CheckSummary(check_id=c.check_id, status=c.status, summary=c.summary or c.status, challenge=c.challenge.verdict if c.challenge else None)
        for c in checks
    ]
    earlier = PreviousRecommendation(version=previous.version, choice=previous.choice, reason=previous.reason) if previous else None
    return BriefRequest(**_base(settings, "coordinator", run_id, case, tenant, pack, now), input_fingerprint=input_fingerprint, case=_case_ref(case, product_type), results=results,
                        conflicts=list(conflicts), open_comments=list(open_comments), previous_recommendation=earlier,
                        recommendation_options=list(options), output_languages=settings.languages_for_brief(pack.languages))


def challenge_request(settings: Settings, run_id: str, case: CaseDetail, tenant: str, pack: PackDocument, now: datetime, product_type: Optional[str],
                      check: CheckDetail, transition: Optional[TransitionRule] = None) -> RunRequest:
    """The claim and its quotes, without the original reasoning, so the second reading stays independent. It carries the law the check cites now."""
    target = ChallengeTarget(finding_id=check.id, kind="finding", question=check.question, claimed_status="met", claim=check.summary or "The check is met.",
                             evidence=list(check.evidence))
    source = check.source
    doc = law_doc_for(source.doc_id, transition) if source else None
    law = CheckSource(doc_id=doc, clause=(source.clause if doc == source.doc_id else None) or "Whole document") if source else None
    return ChallengeRequest(**_base(settings, "challenger", run_id, case, tenant, pack, now), case=_case_ref(case, product_type), target=target, check_source=law)
