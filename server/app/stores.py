"""One DocStore per contract model that has a table of its own. STORES is keyed by table name.

Typed columns come from the doc, so they can't disagree with it. A column the doc lacks, such as the case a page
belongs to, is passed as a keyword to `add`.
A parent keeps its full contract object in `doc`; a child table (comments, signers, revisions) holds the rows to query on.
"""
from pharmalign_contracts.agents.run_agents import GapSignal
from pharmalign_contracts.common import FactValue
from pharmalign_contracts.server import models as m

from .pipeline.records import RunWork
from .store import DocStore
from .watch.logic import notice_key
from .tables import ROW

# Timeline events and notification events keep the T1 stores in context.py.
STORES: dict[str, DocStore] = {
    "workspace": DocStore(ROW["workspace"], m.Workspace, lambda w: {
        "country_code": w.country_code, "mode": w.mode, "active_pack_version_id": w.active_pack_version_id,
        "active_kb_snapshot_id": w.active_kb_snapshot_id, "created_at": w.created_at}),
    "setup_stage": DocStore(ROW["setup_stage"], m.SetupStageStatus, lambda s: {"stage": s.stage, "status": s.status},
                            key=lambda ctx, s: f"{ctx.tenant.id}:{s.stage}"),
    "document": DocStore(ROW["document"], m.Document, lambda d: {
        "shelf": d.shelf, "doc_type": d.doc_type, "issuer": d.issuer, "latest_version_id": d.latest_version_id, "created_at": d.created_at}, scoped=False),
    "document_version": DocStore(ROW["document_version"], m.DocumentVersion, lambda v: {
        "document_id": v.document_id, "file_hash": v.file_hash, "review_status": v.review_status, "quality_status": v.quality_status,
        "published_on": v.published_on, "effective_on": v.effective_on, "added_at": v.added_at}, scoped=False),
    "chunk": DocStore(ROW["chunk"], m.ChunkView, lambda c: {
        "document_version_id": c.document_version_id, "position": c.position, "chunk_type": c.chunk_type, "page": c.page, "page_end": c.page_end,
        "article": c.article, "section_path": c.section_path, "language": c.language, "text": c.text, "text_hash": c.text_hash},
        key=lambda ctx, c: c.anchor, scoped=False),
    "tenant_document_adoption": DocStore(ROW["tenant_document_adoption"], m.Adoption, lambda a: {
        "document_version_id": a.document_version_id, "adopted": a.adopted, "legal_status": a.legal_status,
        "authority_level": a.authority_level, "decided_by": a.decided_by, "decided_at": a.decided_at},
        key=lambda ctx, a: f"{ctx.tenant.id}|{a.document_version_id}"),
    "kb_snapshot": DocStore(ROW["kb_snapshot"], m.Snapshot, lambda s: {
        "content_hash": s.content_hash, "label": s.label, "item_count": s.item_count, "created_by": s.created_by, "created_at": s.created_at}),
    # Needs snapshot_id. The id is `snapshot|version`.
    "kb_snapshot_item": DocStore(ROW["kb_snapshot_item"], m.SnapshotItem, lambda i: {
        "document_version_id": i.document_version_id, "legal_status": i.legal_status, "authority_level": i.authority_level}),
    "document_slot": DocStore(ROW["document_slot"], m.Slot, lambda s: {"key": s.key, "status": s.status, "document_id": s.document_id}),
    "ingest_job": DocStore(ROW["ingest_job"], m.IngestJob, lambda j: {"document_version_id": j.document_version_id, "status": j.status, "started_at": j.started_at}),
    "document_review": DocStore(ROW["document_review"], m.DocumentReview, lambda r: {
        "document_version_id": r.document_version_id, "decision": r.decision, "reviewed_by": r.reviewed_by, "reviewed_at": r.reviewed_at}),
    "retrieval_test_run": DocStore(ROW["retrieval_test_run"], m.RetrievalTestRun, lambda r: {"status": r.status}),
    "pack_version": DocStore(ROW["pack_version"], m.PackVersion, lambda p: {
        "version": p.version, "state": p.state, "content_hash": p.content_hash, "based_on_version_id": p.based_on_version_id,
        "created_by": p.created_by, "created_at": p.created_at, "approved_by": p.approved_by, "approved_at": p.approved_at}),
    "readiness_run": DocStore(ROW["readiness_run"], m.ReadinessRun, lambda r: {"status": r.status, "started_at": r.started_at}),
    "sign_off": DocStore(ROW["sign_off"], m.SignOff, lambda s: {
        "readiness_run_id": s.readiness_run_id, "pack_version_id": s.pack_version_id, "kb_snapshot_id": s.kb_snapshot_id,
        "target_mode": s.target_mode, "signed_by": s.signed_by, "signed_at": s.signed_at}),
    "party": DocStore(ROW["party"], m.Party, lambda p: {"kind": p.kind, "gov_ref": p.gov_ref}),
    "medicine": DocStore(ROW["medicine"], m.Medicine, lambda x: {"name": x.name, "product_type": x.product_type}),
    "licence": DocStore(ROW["licence"], m.Licence, lambda l: {
        "medicine_id": l.medicine_id, "holder_party_id": l.holder_party_id, "gov_ref": l.gov_ref, "gov_status": l.gov_status,
        "gov_version": l.gov_version, "gov_as_of": l.gov_as_of, "valid_to": l.valid_to}),
    "reliance_basis": DocStore(ROW["reliance_basis"], m.RelianceBasis, lambda r: {
        "target_type": r.target_type, "target_id": r.target_id, "regulator": r.regulator, "verified": r.verified}),
    "delegation": DocStore(ROW["delegation"], m.Delegation, lambda d: {
        "from_person_id": d.from_person_id, "to_person_id": d.to_person_id, "role": d.role, "valid_from": d.valid_from, "valid_to": d.valid_to}),
    # Needs assessors (a list of person ids) for a new case.
    "case": DocStore(ROW["case"], m.CaseDetail, lambda c: {
        "status": c.status, "environment": c.environment, "case_type": c.case_type, "route": c.route, "medicine_id": c.medicine_id,
        "applicant_party_id": c.applicant_party_id, "assigned_to": c.assigned_to, "pack_version_id": c.pack_version_id,
        "kb_snapshot_id": c.kb_snapshot_id, "current_pipeline_run_id": c.current_pipeline_run_id, "decision_id": c.decision_id, "created_at": c.created_at}),
    "conflict": DocStore(ROW["conflict"], m.Conflict, lambda c: {"person_id": c.person_id, "party_id": c.party_id, "case_id": c.case_id, "declared_at": c.declared_at}),
    "case_sequence": DocStore(ROW["case_sequence"], m.Sequence, lambda s: {"case_id": s.case_id, "number": s.number, "received_at": s.received_at},
                              key=lambda ctx, s: f"{s.case_id}:{s.number}"),
    "dossier_document": DocStore(ROW["dossier_document"], m.DossierDocument, lambda d: {
        "case_id": d.case_id, "sequence": d.sequence, "filename": d.filename, "module": d.module, "language": d.language, "pages": d.pages}),
    # Needs case_id.
    "dossier_page": DocStore(ROW["dossier_page"], m.DossierPage, lambda p: {"document_id": p.document_id, "page": p.page, "anchor": p.anchor, "suspect": p.suspect},
                             key=lambda ctx, p: f"{p.document_id}:{p.page}"),
    "pipeline_run": DocStore(ROW["pipeline_run"], m.PipelineRun, lambda r: {
        "case_id": r.case_id, "sequence": r.sequence, "generation": r.generation, "status": r.status, "started_at": r.started_at}),
    # Needs stage and reads. Earlier generations and attempts sit in the same table with is_current false.
    "check_result": DocStore(ROW["check_result"], m.CheckDetail, lambda c: {
        "case_id": c.case_id, "check_id": c.check_id, "pipeline_run_id": c.pipeline_run_id, "method": c.method, "status": c.status,
        "is_current": c.is_current, "blocking": c.blocking, "generation": c.generation, "attempt": c.attempt, "updated_at": c.updated_at}),
    "check_trace": DocStore(ROW["check_trace"], m.CheckTrace, lambda t: {"check_result_id": t.check_result_id}, key=lambda ctx, t: t.check_result_id),
    "agent_run": DocStore(ROW["agent_run"], m.AgentRunView, lambda r: {
        "agent": r.agent, "case_id": r.case_id, "check_id": r.check_id, "status": r.status, "generation": r.generation,
        "attempt": r.attempt, "is_current": r.is_current, "started_at": r.started_at}, key=lambda ctx, r: r.run_id),
    "agent_run_work": DocStore(ROW["agent_run_work"], RunWork, lambda w: {
        "case_id": w.case_id, "posted_at": w.posted_at, "next_poll_at": w.next_poll_at, "applied_at": w.applied_at}, key=lambda ctx, w: w.run_id),
    # Needs check_result_id and case_id.
    "waiver": DocStore(ROW["waiver"], m.WaiverView, lambda w: {"waived_by": w.by, "fingerprint": w.fingerprint, "valid": w.valid, "at": w.at}),
    "confirmation": DocStore(ROW["confirmation"], m.ConfirmationView, lambda c: {"confirmed_by": c.by, "fingerprint": c.fingerprint, "valid": c.valid, "at": c.at}),
    # Needs case_id. The id is `case:key`.
    "fact": DocStore(ROW["fact"], FactValue, lambda f: {"key": f.key, "status": f.status}),
    "recommendation_version": DocStore(ROW["recommendation_version"], m.RecommendationVersion, lambda r: {
        "case_id": r.case_id, "version": r.version, "is_current": r.is_current, "input_fingerprint": r.input_fingerprint, "created_at": r.created_at},
        key=lambda ctx, r: f"{r.case_id}:v{r.version}"),
    "spot_check": DocStore(ROW["spot_check"], m.SpotCheck, lambda s: {
        "case_id": s.case_id, "check_result_id": s.check_result_id, "assigned_to": s.assigned_to, "status": s.status}),
    "review_thread": DocStore(ROW["review_thread"], m.Thread, lambda t: {
        "case_id": t.case_id, "status": t.status, "blocks_promotion": t.blocks_promotion, "target_type": t.target.type,
        "target_ref": t.target.ref, "created_at": t.created_at}),
    # Needs thread_id and case_id.
    "review_comment": DocStore(ROW["review_comment"], m.Comment, lambda c: {"author_type": c.author_type, "author_id": c.author_id, "at": c.at}),
    "question": DocStore(ROW["question"], m.Question, lambda q: {"case_id": q.case_id, "status": q.status}),
    # Needs person_id.
    "assistant_session": DocStore(ROW["assistant_session"], m.AssistantSession, lambda s: {"case_id": s.case_id, "created_at": s.created_at}),
    "promotion": DocStore(ROW["promotion"], m.Promotion, lambda p: {"case_id": p.case_id, "promoted_by": p.promoted_by, "fingerprint": p.fingerprint, "at": p.at}),
    "decision": DocStore(ROW["decision"], m.Decision, lambda d: {
        "case_id": d.case_id, "type": d.type, "state": d.state, "signed_payload_hash": d.signed_payload_hash,
        "recommendation_version": d.recommendation_version, "legal_effect": d.legal_effect, "record_status": d.record_status, "proposal_id": d.proposal_id,
        "foreign_status_reason": d.foreign_status_reason}),
    # Needs decision_id and case_id. The id is `decision:person`.
    "decision_signer": DocStore(ROW["decision_signer"], m.Signer, lambda s: {"person_id": s.person_id, "role": s.role, "signed_at": s.signed_at}),
    "proposal": DocStore(ROW["proposal"], m.Proposal, lambda p: {
        "decision_id": p.decision_id, "action": p.action, "state": p.state, "idempotency_key": p.idempotency_key,
        "created_at": p.created_at, "updated_at": p.updated_at}),
    "connection": DocStore(ROW["connection"], m.Connection, lambda c: {"name": c.name, "kind": c.kind, "status": c.status}),
    "watch_source": DocStore(ROW["watch_source"], m.WatchSource, lambda s: {"regulator": s.regulator, "kind": s.kind, "health": s.health, "last_success_at": s.last_success_at,
        "severity_floor": s.severity_floor}),
    "foreign_notice": DocStore(ROW["foreign_notice"], m.NoticeDetail, lambda n: {
        "regulator": n.regulator, "published_on": n.published_on, "fetched_at": n.fetched_at, "latest_revision": n.latest_revision,
        "retracted": n.retracted, "severity": n.severity, "notice_key": notice_key(n.regulator, n.title, n.reference)}),
    # Needs notice_id. The id is `notice:rN`.
    "foreign_notice_revision": DocStore(ROW["foreign_notice_revision"], m.NoticeRevision, lambda r: {
        "revision": r.revision, "fetched_at": r.fetched_at, "text_anchor": r.text_anchor, "content_hash": r.content_hash, "is_retraction": r.is_retraction, "text": r.text}),
    "watch_match": DocStore(ROW["watch_match"], m.WatchMatch, lambda w: {
        "notice_id": w.notice_id, "target_type": w.target_type, "target_id": w.target_id, "status": w.status, "match_confidence": w.match_confidence,
        "rereview_case_id": w.rereview_case_id}),
    "watch_incident": DocStore(ROW["watch_incident"], m.WatchIncident, lambda i: {"notice_id": i.notice_id, "status": i.status, "severity": i.severity}),
    # Needs recipient. Delivery state, kept apart from the notification event in domain_event.
    "notification_delivery": DocStore(ROW["notification_delivery"], m.Notification, lambda n: {
        "event": n.event, "severity": n.severity, "state": n.state, "ack_due_at": n.ack_due_at, "escalation_level": n.escalation_level, "created_at": n.created_at}),
    "gap_signal": DocStore(ROW["gap_signal"], GapSignal, lambda s: {"kind": s.kind}),
    "gap": DocStore(ROW["gap"], m.Gap, lambda g: {"kind": g.kind, "status": g.status, "pack_version_id": g.pack_version_id}),
    "gap_analysis": DocStore(ROW["gap_analysis"], m.GapAnalysis, lambda a: {"status": a.status, "started_at": a.started_at}),
    "scenario": DocStore(ROW["scenario"], m.Scenario, lambda s: {"kind": s.kind}),
    "simulation": DocStore(ROW["simulation"], m.Simulation, lambda s: {
        "scenario_id": s.scenario_id, "pack_version_id": s.pack_version_id, "kb_snapshot_id": s.kb_snapshot_id, "status": s.status, "started_at": s.started_at}),
    "simulation_comparison": DocStore(ROW["simulation_comparison"], m.SimulationComparison, lambda c: {"scenario_id": c.scenario_id}, key=lambda ctx, c: c.scenario_id),
    "agent_health": DocStore(ROW["agent_health"], m.AgentHealthView, lambda h: {"agent": h.agent, "status": h.status}, key=lambda ctx, h: h.agent),
    # Needs id.
    "usage_report": DocStore(ROW["usage_report"], m.UsageReport, lambda u: {"period_to": u.period_to}),
}
