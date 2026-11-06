"""Cases: the queue, case detail, assignment, timeline, dossier documents and pages, company sequences, reliance basis and pipeline runs."""
from pharmalign_contracts.server import models as m
from sqlalchemy import func, select

from ..context import TIMELINE
from ..domain.guards import assign_guards
from ..ops import op
from ..paging import paginate
from ..pipeline import runner
from ..problems import enforce_guards, not_found, problem
from ..stores import STORES
from ..tables import ROW, DomainEventRow, PersonRow
from .support import CASES, FROZEN, RELIANCE, add_assessor, as_summary, facts_of, get_case, with_limit

DOCUMENTS = STORES["dossier_document"]
PAGES = STORES["dossier_page"]
SEQUENCES = STORES["case_sequence"]
PIPELINES = STORES["pipeline_run"]


@op("GET", "/api/v1/cases")
async def list_cases(req, ctx):
    q = req.query
    assigned = ctx.person_id if q.assigned_to == "me" else q.assigned_to
    statuses = q.status.split(",") if q.status else None
    row = ROW["case"]
    where = [c for c in (
        row.status.in_(statuses) if statuses else None, row.assigned_to == assigned if assigned else None,
        row.case_type == q.case_type if q.case_type else None, row.environment == q.environment if q.environment else None,
    ) if c is not None]
    found = await CASES.list(ctx, *where)
    # Signed and closed cases go last. Then the nearest deadline, then the newest.
    found.sort(key=lambda d: d.id, reverse=True)
    found.sort(key=lambda d: d.next_deadline.isoformat() if d.next_deadline else "9")
    found.sort(key=lambda d: 1 if d.status in FROZEN and d.status != "promoted" else 0)
    return paginate([as_summary(d) for d in found], q)


@op("POST", "/api/v1/cases")
async def open_case(req, ctx):
    b = req.body
    workspace = await STORES["workspace"].get(ctx, ctx.tenant.id)
    if await STORES["medicine"].get(ctx, b.medicine_id) is None:
        not_found("Medicine", b.medicine_id)
    if await STORES["party"].get(ctx, b.applicant_party_id) is None:
        not_found("Company", b.applicant_party_id)
    if workspace is None or not workspace.active_pack_version_id or not workspace.active_kb_snapshot_id:
        problem(409, "no_active_pack", "No active pack", "A case is pinned to the active pack and knowledge snapshot, and this workspace has neither yet.")
    await ctx.db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended("case-number", 0))))
    # A case number is unique across countries, so the next one counts every country's cases.
    ids = (await ctx.db.scalars(select(ROW["case"].id))).all()
    numbers = [int(i[5:]) for i in ids if i[5:].isdigit() and int(i[5:]) < 2000]
    case_id = f"CASE-{max([1051, *numbers]) + 1}"
    detail = m.CaseDetail(
        id=case_id, case_type=b.case_type, route=b.route, status="intake", environment="simulation", medicine_id=b.medicine_id,
        applicant_party_id=b.applicant_party_id, sequence=1, assigned_to=None, blocking_checks_open=0, next_deadline=None, created_at=ctx.now(),
        pack_version_id=workspace.active_pack_version_id, kb_snapshot_id=workspace.active_kb_snapshot_id, current_pipeline_run_id=None,
        current_recommendation_version=None, decision_id=None, clock_running=True, days_used=0,
    )
    CASES.add(ctx, detail)
    await ctx.db.flush()
    for i, d in enumerate(b.documents, start=1):
        DOCUMENTS.add(ctx, m.DossierDocument(
            id=f"d-{case_id[5:]}-{i:02d}", case_id=case_id, version="v1", sequence=1, filename=d.filename, module=d.module_hint, pages=10, language=d.language, suspect_pages=[],
        ))
    ctx.timeline(case_id, "case_opened", "Case opened.", {"type": "system", "id": "intake"})
    ctx.audit("case_opened", "case", case_id, case_id, after=detail, actor_type="connector", actor_id="portal")
    return detail


@op("GET", "/api/v1/cases/{case_id}")
async def get_case_detail(req, ctx):
    return await get_case(ctx, req.path.case_id)


@op("POST", "/api/v1/cases/{case_id}/assign")
async def assign(req, ctx):
    case = await get_case(ctx, req.path.case_id, for_update=True)
    person = await ctx.db.get(PersonRow, req.body.person_id)
    if person is None or person.tenant_id != ctx.tenant.id:
        not_found("Person", req.body.person_id)
    enforce_guards(assign_guards(await facts_of(ctx, case), req.body.person_id), "Assignment refused")
    before = case.assigned_to
    case = await CASES.save(ctx, case.model_copy(update={"assigned_to": req.body.person_id}))
    if req.body.role == "assessor":
        await add_assessor(ctx, case.id, req.body.person_id)
    ctx.timeline(case.id, "assigned", f"Assigned to {person.display_name} as {req.body.role.replace('_', ' ')}.", {"type": "person", "id": ctx.person_id})
    ctx.audit("case_assigned", "case", case.id, case.id, before={"assigned_to": before}, after={"assigned_to": req.body.person_id, "role": req.body.role})
    ctx.notify(req.body.person_id, "case_assigned", "info", f"Assigned: {case.id}", link=f"/cases/{case.id}")
    return case


@op("GET", "/api/v1/cases/{case_id}/timeline")
async def case_timeline(req, ctx):
    await get_case(ctx, req.path.case_id)
    return await TIMELINE.page(ctx, with_limit(req.query, 200), DomainEventRow.case_id == req.path.case_id, order_by=(DomainEventRow.at, DomainEventRow.seq))


@op("POST", "/api/v1/cases/{case_id}/sequences")
async def receive_sequence(req, ctx):
    case = await get_case(ctx, req.path.case_id, for_update=True)
    number = case.sequence + 1
    doc_ids = []
    for i, d in enumerate(req.body.documents, start=1):
        doc_id = f"d-{case.id[5:]}-s{number}-{i}"
        doc_ids.append(doc_id)
        DOCUMENTS.add(ctx, m.DossierDocument(
            id=doc_id, case_id=case.id, version="v1", sequence=number, filename=d.filename, module=d.module_hint, pages=8, language=d.language, suspect_pages=[],
        ))
    await CASES.save(ctx, case.model_copy(update={"sequence": number}))
    await ctx.db.flush()
    question = ROW["question"]
    for q in await STORES["question"].list(ctx, question.case_id == case.id, question.status == "sent"):
        await STORES["question"].save(ctx, q.model_copy(update={"status": "answered"}))
    changed = await runner.evidence_changed(ctx, case.id, doc_ids, req.body.note)
    seq = m.Sequence(case_id=case.id, number=number, received_at=ctx.now(), document_ids=doc_ids, changed_check_ids=changed)
    SEQUENCES.add(ctx, seq)
    ctx.timeline(case.id, "sequence_received", f"Sequence {number} received: {len(doc_ids)} document(s). {len(changed)} check(s) re-run.", {"type": "system", "id": "connector"})
    ctx.audit("sequence_received", "case", case.id, case.id, after=seq, reason=req.body.note, actor_type="connector", actor_id="portal")
    return seq


@op("GET", "/api/v1/cases/{case_id}/documents")
async def list_documents(req, ctx):
    await get_case(ctx, req.path.case_id)
    row = ROW["dossier_document"]
    return await DOCUMENTS.page(ctx, with_limit(req.query, 200), row.case_id == req.path.case_id, order_by=(func.coalesce(row.module, ""), row.id))


@op("GET", "/api/v1/dossier-documents/{document_id}/pages/{page}")
async def dossier_page(req, ctx):
    doc = await DOCUMENTS.require(ctx, req.path.document_id, "Dossier document")
    try:
        page = int(req.path.page)
    except ValueError:
        page = 0
    if page < 1 or (doc.pages is not None and page > doc.pages):
        not_found("Page", f"{doc.id} p{req.path.page}")
    curated = await PAGES.get(ctx, f"{doc.id}:{page}")
    if curated:
        return curated
    suspect = page in doc.suspect_pages
    anchor = f"{doc.id}:{doc.version}:p{page}"
    row = ROW["check_result"]
    quotes: list[str] = []
    for c in await STORES["check_result"].list(ctx, row.case_id == doc.case_id, row.is_current.is_(True)):
        for e in [*c.evidence, *(e for f in c.extracted for e in f.evidence), *(c.challenge.evidence if c.challenge else [])]:
            if e.anchor == anchor and e.quote not in quotes:
                quotes.append(e.quote)
    cited = "".join(f"{q}.\n\n" for q in quotes)
    text = f"{doc.filename}, page {page} of {doc.pages if doc.pages is not None else '?'} (Module {doc.module or 'unsorted'}).\n\n{cited}Synthetic dossier text for the demo. No real company data."
    return m.DossierPage(
        document_id=doc.id, page=page, anchor=anchor, text=text, suspect=suspect,
        hidden_text="Hidden text was found on this page and removed from what agents read." if suspect else None,
    )


@op("GET", "/api/v1/cases/{case_id}/reliance-basis")
async def list_reliance(req, ctx):
    await get_case(ctx, req.path.case_id)
    row = ROW["reliance_basis"]
    return await RELIANCE.page(ctx, req.query, row.target_type == "case", row.target_id == req.path.case_id)


@op("POST", "/api/v1/cases/{case_id}/reliance-basis")
async def link_reliance(req, ctx):
    case = await get_case(ctx, req.path.case_id)
    rb = m.RelianceBasis(**req.body.model_dump(), id=ctx.id("rb"), target_type="case", target_id=case.id, verified=False, declared_same_by=None)
    RELIANCE.add(ctx, rb)
    ctx.timeline(case.id, "reliance_linked", f"Linked to {rb.regulator} approval {rb.approval_ref}.", {"type": "person", "id": ctx.person_id})
    ctx.audit("reliance_linked", "reliance_basis", rb.id, case.id, after=rb)
    return rb


@op("POST", "/api/v1/cases/{case_id}/pipeline-runs")
async def start_pipeline_run(req, ctx):
    await get_case(ctx, req.path.case_id)
    return await runner.start_pipeline(ctx, req.path.case_id, req.body.reason if req.body else None)


@op("GET", "/api/v1/pipeline-runs/{run_id}")
async def get_pipeline_run(req, ctx):
    return await PIPELINES.require(ctx, req.path.run_id, "Pipeline run")
