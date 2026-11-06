"""Workspaces: one per country. A Platform Admin sets each up; everyone else sees only their own."""
from pharmalign_contracts.server import models as m
from sqlalchemy import select

from ..domain.scenario_template import PLATFORM_SCENARIOS, ScenarioDefinition, scenario_key, scenario_of
from ..domain.setup import SETUP_STAGES
from ..domain.slot_template import PLATFORM_SLOTS, SlotDefinition, empty_slot
from ..ops import op
from ..paging import paginate_select
from ..problems import not_found, problem
from ..stores import STORES
from ..tables import ROW
from .packs import open_draft
from .support import in_tenant, reach

WORKSPACES = STORES["workspace"]
PACK = ROW["pack_version"]


@op("POST", "/api/v1/workspaces")
async def create_workspace(req, ctx):
    b = req.body
    if b.template == "region":
        problem(422, "unknown_region", "No regional template", "No regional templates exist yet. Use the platform template or copy a workspace.")
    source = await ctx.db.get(ROW["workspace"], b.copy_from_workspace_id) if b.copy_from_workspace_id else None
    if b.copy_from_workspace_id and source is None:
        not_found("Workspace", b.copy_from_workspace_id)
    wid = ctx.id("ws")
    wctx = in_tenant(ctx, wid)
    w = m.Workspace(
        id=wid, name=b.name, country_code=b.country_code, authority_name=b.authority_name, languages=b.languages, time_zone=b.time_zone,
        day_counting=b.day_counting, data_location=b.data_location, mode="draft", active_pack_version_id=None, active_kb_snapshot_id=None,
        created_at=ctx.now(),
    )
    WORKSPACES.add(wctx, w)
    for i, (stage, done_when) in enumerate(SETUP_STAGES):
        STORES["setup_stage"].add(wctx, m.SetupStageStatus(
            stage=stage, status="done" if i == 0 else "not_started", done_when=done_when, blockers=[],
            completed_by=ctx.person_id if i == 0 else None, completed_at=ctx.now() if i == 0 else None,
        ))
    wctx.audit(action="workspace_created", object_type="workspace", object_id=wid, after=w)
    definitions = await slot_definitions(ctx, source.id) if b.template == "copy" else PLATFORM_SLOTS
    for i, definition in enumerate(definitions, 1):
        # The index keeps the list in template order, since slots are listed by id.
        STORES["document_slot"].add(wctx, empty_slot(ctx.id(f"sl-{i:02d}"), definition))
    scenarios = await scenario_definitions(ctx, source.id) if b.template == "copy" else PLATFORM_SCENARIOS
    for definition in scenarios:
        # Ids are unique across workspaces, so each keeps its key as a readable prefix.
        STORES["scenario"].add(wctx, scenario_of(ctx.id(definition.key), definition))
    if source is not None and source.doc.get("active_pack_version_id"):
        # The copied draft starts from the source's active version. It is a new draft in this workspace, to review before it is approved.
        await open_draft(wctx, w, await ctx.db.get(PACK, source.doc["active_pack_version_id"]))
    return w


async def slot_definitions(ctx, source_id: str) -> list[SlotDefinition] | tuple[SlotDefinition, ...]:
    """The source workspace's slots, as definitions only. A source with none gives the platform's list."""
    slots = await STORES["document_slot"].list(in_tenant(ctx, source_id))
    return [SlotDefinition(s.key, s.label, s.importance) for s in slots] or PLATFORM_SLOTS


async def scenario_definitions(ctx, source_id: str) -> list[ScenarioDefinition] | tuple[ScenarioDefinition, ...]:
    """The source workspace's scenarios, as definitions only. A source with none gives the platform's list."""
    scenarios = await STORES["scenario"].list(in_tenant(ctx, source_id))
    return [ScenarioDefinition(scenario_key(s.id), s.label, s.kind, s.description, s.expected_outcome) for s in scenarios] or PLATFORM_SCENARIOS


@op("GET", "/api/v1/workspaces")
async def list_workspaces(req, ctx):
    row = ROW["workspace"]
    return await paginate_select(ctx.db, select(row).order_by(row.created_at, row.id), req.query, map=lambda r: m.Workspace.model_validate(r.doc))


@op("GET", "/api/v1/workspaces/{workspace_id}")
async def get_workspace(req, ctx):
    wctx = await reach(ctx, req.path.workspace_id)
    return await WORKSPACES.require(wctx, wctx.tenant.id, "Workspace")
