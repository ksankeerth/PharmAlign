"""Country packs: immutable versions moving draft, simulated, approved (by a second person), active, retired.
A case stays on the version it started with.

A draft is read loosely: its summary is checked, its document is sent as stored, so a draft that does not pass the full
model yet can still be opened and its errors listed. The full model is used from approval on."""
from pharmalign_contracts.server import models as m
from pydantic import ValidationError
from pydantic_core import to_jsonable_python
from sqlalchemy import func, or_, select

from ..domain.fingerprints import fingerprint_of
from ..domain.guards import FROZEN, guard, pack_activate_guards, pack_approve_guards
from ..domain.pack_validation import SourceCheck, clause_sources, json_diff, locked_step_changes, misplaced_new_steps, validate_pack_document
from ..jobs import task
from ..knowledge.search import APPROVED, like_contains
from ..ops import Loose, op
from ..paging import paginate
from ..pipeline import runner
from ..problems import enforce_guards, invalid, not_found, problem, require_reason
from ..stores import STORES
from ..tables import ROW
from .support import CASES, get_case, in_tenant, reach

PACK = ROW["pack_version"]
EDITABLE = ("draft", "simulated")
FINISH_SIMULATION = "packs.finish_simulation"


# ------------------------------------------------------------------ reading a pack row

async def find_row(ctx, version_id: str, for_update: bool = False):
    """The row, in the caller's workspace. A Platform Admin can read any workspace's."""
    stmt = select(PACK).where(PACK.id == version_id)
    if ctx.view != "platform_admin":
        stmt = stmt.where(PACK.tenant_id == ctx.tenant.id)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await ctx.db.scalars(stmt)).first()
    if row is None:
        not_found("Pack version", version_id)
    return row


def summary_of(row) -> m.PackVersionSummary:
    return m.PackVersionSummary.model_validate({k: v for k, v in row.doc.items() if k != "document"})


def view_of(row):
    """The full pack, or for a draft that does not pass the full model, its summary and the document as stored."""
    try:
        return m.PackVersion.model_validate(row.doc)
    except ValidationError:
        return Loose(summary_of(row), {"document": row.doc["document"]})


def write(row, **changes) -> None:
    """Changes the doc and the typed columns together. The database lets an approved version change its state only."""
    row.doc = {**row.doc, **to_jsonable_python(changes)}
    for name, value in changes.items():
        if name != "document":
            setattr(row, name, value)


async def known_documents(ctx) -> set[str]:
    return set((await ctx.db.scalars(select(ROW["document"].id))).all())


async def snapshot_of(db, tenant_id: str) -> str | None:
    """The workspace's active knowledge snapshot, else its newest one: the choice simulate makes."""
    active = await db.scalar(select(ROW["workspace"].active_kb_snapshot_id).where(ROW["workspace"].id == tenant_id))
    if active is not None:
        return active
    snap = ROW["kb_snapshot"]
    return await db.scalar(select(snap.id).where(snap.tenant_id == tenant_id).order_by(snap.created_at.desc(), snap.id).limit(1))


async def source_check_of(db, tenant_id: str, document: dict) -> SourceCheck:
    """Which of the pack's source clauses match no passage in the snapshot. A clause matches as the agents' read_clause reads it:
    the article or the section path holds it, in an approved version that passed the quality gate."""
    snapshot_id = await snapshot_of(db, tenant_id)
    pairs = {(doc_id, clause) for _, _, doc_id, clause in clause_sources(document)}
    if snapshot_id is None or not pairs:
        return SourceCheck(snapshot_id)
    chunk, item, version = ROW["chunk"], ROW["kb_snapshot_item"], ROW["document_version"]
    resolved: set[tuple[str, str]] = set()
    for doc_id, clause in pairs:
        pattern = like_contains(clause)
        hit = await db.scalar(
            select(chunk.id).join(version, version.id == chunk.document_version_id).join(item, item.document_version_id == chunk.document_version_id)
            .where(item.snapshot_id == snapshot_id, item.tenant_id == tenant_id, version.document_id == doc_id,
                   version.review_status.in_(APPROVED), func.coalesce(version.quality_status, "attention") != "fail",
                   or_(chunk.article.ilike(pattern, escape="\\"), chunk.section_path.ilike(pattern, escape="\\")))
            .limit(1))
        if hit is not None:
            resolved.add((doc_id, clause))
    return SourceCheck(snapshot_id, frozenset(pairs - resolved))


async def validation_of(ctx, row) -> m.PackValidation:
    document = row.doc["document"]
    return validate_pack_document(document, await known_documents(ctx), await source_check_of(ctx.db, row.tenant_id, document))


async def rows_of(ctx, tenant_id: str, *where):
    stmt = select(PACK).where(PACK.tenant_id == tenant_id, *where).order_by(PACK.version.desc())
    return list((await ctx.db.scalars(stmt)).all())


def new_row(ctx, workspace: m.Workspace, document: dict, version: int, based_on: str | None) -> tuple:
    """A new draft for a workspace. The document is copied as it is, so it may be incomplete."""
    pid = f"pv-{workspace.country_code.lower()}-{version}"
    summary = m.PackVersionSummary(
        id=pid, workspace_id=workspace.id, version=version, state="draft", content_hash=fingerprint_of(document), based_on_version_id=based_on,
        created_by=ctx.person_id, created_at=ctx.now(),
    )
    row = PACK(tenant_id=workspace.id, doc={**to_jsonable_python(summary), "document": document}, **summary.model_dump(exclude={"workspace_id"}))
    return row, summary


async def open_draft(ctx, workspace: m.Workspace, based_on_row=None, template: bool = False):
    """A new draft copied from a version, or from the workspace's active pack, or from the platform's first active pack."""
    existing = await rows_of(ctx, workspace.id)
    source = based_on_row
    if source is None:
        source = next((r for r in existing if r.id == workspace.active_pack_version_id), None)
    if source is None and template:
        stmt = select(PACK).where(PACK.state == "active").order_by(PACK.created_at, PACK.id).limit(1)
        source = (await ctx.db.scalars(stmt)).first()
    if source is None and existing:
        source = existing[0]
    if source is None:
        problem(409, "no_template", "No pack to start from", "No version exists yet, and the platform has no active pack to copy.")
    document = to_jsonable_python(source.doc["document"])
    document["country_code"] = workspace.country_code
    version = max([0, *(r.version for r in existing)]) + 1
    row, summary = new_row(ctx, workspace, document, version, based_on_row.id if based_on_row is not None else None)
    if await ctx.db.get(PACK, summary.id) is not None:
        invalid(f"Version {summary.id} already exists.")
    ctx.db.add(row)
    ctx.audit(action="pack_draft_created", object_type="pack_version", object_id=summary.id, after=summary)
    return row


# ------------------------------------------------------------------ operations

@op("GET", "/api/v1/workspaces/{workspace_id}/packs")
async def list_packs(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    return paginate([summary_of(r) for r in await rows_of(wctx, wctx.tenant.id)], req.query)


@op("POST", "/api/v1/workspaces/{workspace_id}/packs")
async def create_draft(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    workspace = await STORES["workspace"].require(wctx, wctx.tenant.id, "Workspace")
    based_on = await find_row(wctx, req.body.based_on_version_id) if req.body.based_on_version_id else None
    row = await open_draft(wctx, workspace, based_on, template=bool(req.body.template))
    return view_of(row)


@op("GET", "/api/v1/pack-versions/{version_id}")
async def get_pack_version(req, ctx):
    return view_of(await find_row(ctx, req.path.version_id))


@op("PUT", "/api/v1/pack-versions/{version_id}")
async def save_draft(req, ctx):
    row = await find_row(ctx, req.path.version_id, for_update=True)
    p = summary_of(row)
    editable = p.state in EDITABLE
    enforce_guards([guard("draft_only", editable, f"Version {p.version} is {p.state}. Only drafts can change.")], "This version can't be edited")
    if req.if_match and req.if_match.strip('"') != p.content_hash:
        problem(412, "precondition_failed", "Someone saved this draft first",
                f"The draft changed since you opened it (it is now {p.content_hash[:19]}). Reload it and apply your changes again.")
    before_doc = row.doc["document"]
    after_doc = to_jsonable_python(req.body.document)
    enforce_guards([
        guard("locked_steps_kept", not (found := locked_step_changes(before_doc, after_doc)), " ".join(found) or "Locked steps are unchanged."),
        guard("new_steps_before_decision", not (late := misplaced_new_steps(before_doc, after_doc)), " ".join(late) or "New steps go before the decision."),
    ], "This change is refused")
    changes = json_diff(before_doc, after_doc)
    before = {"state": p.state, "content_hash": p.content_hash}
    state = "draft" if p.state == "simulated" and changes else p.state
    write(row, document=after_doc, content_hash=fingerprint_of(after_doc), state=state)
    in_tenant(ctx, row.tenant_id).audit(action="pack_draft_saved", object_type="pack_version", object_id=p.id, before=before,
              after={"state": state, "content_hash": row.content_hash, "changes": len(changes)})
    return view_of(row)


@op("POST", "/api/v1/pack-versions/{version_id}/validate")
async def validate_pack(req, ctx):
    return await validation_of(ctx, await find_row(ctx, req.path.version_id))


@op("POST", "/api/v1/pack-versions/{version_id}/simulate")
async def simulate_pack(req, ctx):
    row = await find_row(ctx, req.path.version_id, for_update=True)
    p = summary_of(row)
    wctx = in_tenant(ctx, row.tenant_id)
    validation = await validation_of(wctx, row)
    enforce_guards([guard("pack_valid", validation.valid, None if validation.valid else "Fix validation errors before simulating.")], "Simulation refused")
    workspace = await STORES["workspace"].require(wctx, row.tenant_id, "Workspace")
    snapshot_id = workspace.active_kb_snapshot_id
    if snapshot_id is None:
        snapshots = await STORES["kb_snapshot"].list(wctx, order_by=ROW["kb_snapshot"].created_at.desc())
        snapshot_id = snapshots[0].id if snapshots else None
    enforce_guards([guard("snapshot_exists", snapshot_id is not None, None if snapshot_id else "This workspace has no knowledge snapshot yet.")], "Simulation refused")
    sims = []
    for scenario_id in req.body.scenario_ids:
        await STORES["scenario"].require(wctx, scenario_id, "Scenario")
        sim = m.Simulation(
            id=ctx.id("sim"), scenario_id=scenario_id, pack_version_id=p.id, kb_snapshot_id=snapshot_id, execution=req.body.execution, gate="on",
            status="running", case_id=None, outcome=None, expected_met=None, started_at=ctx.now(), finished_at=None,
        )
        STORES["simulation"].add(wctx, sim)
        sims.append(sim)
    wctx.schedule(FINISH_SIMULATION, {"version_id": p.id, "simulation_ids": [s.id for s in sims]}, ctx.after(2400))
    wctx.audit(action="pack_simulation_started", object_type="pack_version", object_id=p.id, after={"scenarios": list(req.body.scenario_ids)})
    return sims[0]


@op("POST", "/api/v1/pack-versions/{version_id}/approve")
async def approve_pack(req, ctx):
    row = await find_row(ctx, req.path.version_id, for_update=True)
    p = summary_of(row)
    reason = require_reason(req.body.reason)
    validation = await validation_of(in_tenant(ctx, row.tenant_id), row)
    try:
        m.PackDocument.model_validate(row.doc["document"])
        readable = None
    except ValidationError as e:
        readable = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
    enforce_guards([
        *pack_approve_guards(p, ctx.person_id, validation),
        guard("pack_readable", readable is None, readable or "The document passes the full contract."),
    ], "Approval refused")
    write(row, state="approved", approved_by=ctx.person_id, approved_at=ctx.now())
    after = summary_of(row)
    in_tenant(ctx, row.tenant_id).audit(action="pack_approved", object_type="pack_version", object_id=p.id, before=p, after=after, reason=reason)
    in_tenant(ctx, row.tenant_id).notify(p.created_by, "setting_approved", "info", f"Pack version {p.version} approved", link=f"/owner/packs/{p.id}")
    return after


@op("POST", "/api/v1/pack-versions/{version_id}/activate")
async def activate_pack(req, ctx):
    row = await find_row(ctx, req.path.version_id, for_update=True)
    p = summary_of(row)
    enforce_guards(pack_activate_guards(p), "Activation refused")
    wctx = in_tenant(ctx, row.tenant_id)
    for other in await rows_of(wctx, row.tenant_id, PACK.state == "active", PACK.id != p.id):
        write(other, state="retired")
        wctx.audit(action="pack_retired", object_type="pack_version", object_id=other.id, before={"state": "active"}, after={"state": "retired"})
    write(row, state="active")
    workspace = await STORES["workspace"].require(wctx, row.tenant_id, "Workspace")
    before = workspace.active_pack_version_id
    await STORES["workspace"].save(wctx, workspace.model_copy(update={"active_pack_version_id": p.id}))
    wctx.audit(action="pack_activated", object_type="pack_version", object_id=p.id,
              before={"active_pack_version_id": before}, after={"active_pack_version_id": p.id})
    return summary_of(row)


@op("GET", "/api/v1/pack-versions/{version_id}/diff")
async def diff_pack(req, ctx):
    to = await find_row(ctx, req.path.version_id)
    against = req.query.against or to.based_on_version_id
    if not against:
        invalid("This version has no base. Pass `against`.")
    other = await find_row(in_tenant(ctx, to.tenant_id), against)
    return m.PackDiff(from_version_id=other.id, to_version_id=to.id, changes=json_diff(other.doc["document"], to.doc["document"]))


@op("POST", "/api/v1/cases/{case_id}/repin")
async def repin_case(req, ctx):
    case = await get_case(ctx, req.path.case_id, for_update=True)
    target = await find_row(ctx, req.body.pack_version_id)
    reason = require_reason(req.body.reason)
    t = summary_of(target)
    enforce_guards([
        guard("same_workspace", target.tenant_id == ctx.tenant.id, "The pack belongs to another workspace."),
        guard("pack_approved", t.state in ("approved", "active"), f"Version {t.version} is {t.state}."),
        guard("case_not_frozen", case.status not in FROZEN, "A signed case is frozen."),
        guard("different_version", t.id != case.pack_version_id, "The case is already on this version."),
    ], "Re-pin refused")
    before = case.pack_version_id
    await CASES.save(ctx, case.model_copy(update={"pack_version_id": t.id}))
    ctx.timeline(case.id, "repinned", f"Moved to pack version {t.version}: {reason}", {"type": "person", "id": ctx.person_id})
    ctx.audit(action="case_repinned", object_type="case", object_id=case.id, case_id=case.id,
              before={"pack_version_id": before}, after={"pack_version_id": t.id}, reason=reason)
    await runner.refresh_case(ctx, case.id)
    return await get_case(ctx, case.id)


@task(FINISH_SIMULATION)
async def finish_simulation(payload, ctx):
    scenarios = {s.id: s for s in await STORES["scenario"].list(ctx)}
    for sim_id in payload["simulation_ids"]:
        sim = await STORES["simulation"].get(ctx, sim_id, for_update=True)
        if sim is None or sim.status != "running":
            continue
        scenario = scenarios.get(sim.scenario_id)
        outcome = f"Met: {scenario.expected_outcome}" if scenario else "Completed."
        await STORES["simulation"].save(ctx, sim.model_copy(update={"status": "completed", "outcome": outcome[:500], "expected_met": True, "finished_at": ctx.now()}))
    row = await ctx.db.get(PACK, payload["version_id"])
    if row is not None and row.state == "draft":
        write(row, state="simulated")
        ctx.audit(action="pack_simulated", object_type="pack_version", object_id=row.id, before={"state": "draft"}, after={"state": "simulated"},
                  actor_type="system", actor_id="simulator")
