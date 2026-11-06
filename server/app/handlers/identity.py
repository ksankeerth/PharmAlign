"""Identity: who you are, demo people, View as."""
from dataclasses import replace

from pharmalign_contracts.server import models as m
from pharmalign_contracts.server.models import DemoPerson, Me
from sqlalchemy import or_, select

from ..identity import WORKSPACE_COOKIE, Actor, load_actor
from ..ops import op
from ..paging import paginate_select
from ..problems import invalid, not_found, problem
from ..stores import STORES
from ..tables import ROW, PersonRow, RoleGrantRow
from .support import in_tenant, reach

DELEGATIONS = STORES["delegation"]
CONFLICTS = STORES["conflict"]
WORKSPACES = STORES["workspace"]

# What a person's title is when the caller doesn't give one.
VIEW_TITLES = {
    "case_officer": "Case Officer", "decision_maker": "Decision Maker", "process_owner": "Process Owner",
    "platform_admin": "Platform Admin", "auditor": "Auditor",
}


async def me_of(actor: Actor, ctx) -> Me:
    """Who the actor is, in the workspace the actor's tenant names. For a Platform Admin that is the one they switched to."""
    workspace = await WORKSPACES.get(in_tenant(ctx, actor.tenant_id), actor.tenant_id)
    summary = m.WorkspaceSummary(
        id=workspace.id, name=workspace.name, country_code=workspace.country_code, authority_name=workspace.authority_name, mode=workspace.mode,
    ) if workspace else None
    return Me(
        person_id=actor.id, display_name=actor.display_name, is_demo=actor.is_demo, view=actor.view,
        roles=list(actor.roles), workspace_id=actor.tenant_id, workspace=summary, mode=ctx.tenant.mode,
    )


@op("GET", "/api/v1/me")
async def get_me(req, ctx):
    return await me_of(ctx.person, ctx)


@op("GET", "/api/v1/demo-people")
async def list_demo_people(req, ctx):
    # The workspace's own demo people, and every Platform Admin, so one can switch back from a country they set up.
    stmt = select(PersonRow).where(PersonRow.is_demo, or_(PersonRow.tenant_id == ctx.tenant.id, PersonRow.view == "platform_admin")).order_by(PersonRow.seq)
    return await paginate_select(ctx.db, stmt, req.query, map=lambda p: DemoPerson(person_id=p.id, display_name=p.display_name, view=p.view))


@op("POST", "/api/v1/session/view-as")
async def view_as(req, ctx):
    person = await ctx.db.get(PersonRow, req.body.person_id)
    if person is None or not person.is_demo or (person.tenant_id != ctx.tenant.id and person.view != "platform_admin"):
        not_found("Demo person", req.body.person_id)
    ctx.audit(action="view_as", object_type="session", object_id="session", before={"person_id": ctx.person_id}, after={"person_id": person.id})
    ctx.cookies.update(ctx.rt.identity.session_cookies(person.id))
    ctx.cookies[WORKSPACE_COOKIE] = None  # the new person starts in their own workspace
    return await me_of(await load_actor(ctx.db, person.id, ctx.now()), ctx)


@op("POST", "/api/v1/session/workspace")
async def set_session_workspace(req, ctx):
    # The route is for the Platform Admin view, and reach() gives an unknown workspace a 404.
    wctx = await reach(ctx, req.body.workspace_id)
    # In the chain of the workspace entered, so its auditors see who came in and from where.
    wctx.audit(action="workspace_switched", object_type="session", object_id="session", before={"workspace_id": ctx.tenant.id}, after={"workspace_id": wctx.tenant.id})
    ctx.cookies[WORKSPACE_COOKIE] = wctx.tenant.id
    return await me_of(replace(ctx.person, tenant_id=wctx.tenant.id), ctx)


def grant_of(g: RoleGrantRow) -> m.RoleGrant:
    return m.RoleGrant(id=g.id, person_id=g.person_id, role=g.role, discipline=g.discipline, scope=g.scope, valid_from=g.valid_from, valid_to=g.valid_to)


async def people_of(ctx, rows: list[PersonRow]) -> list[m.Person]:
    grants = (await ctx.db.scalars(select(RoleGrantRow).where(RoleGrantRow.person_id.in_([r.id for r in rows])).order_by(RoleGrantRow.id))).all()
    return [m.Person(id=r.id, display_name=r.display_name, is_demo=r.is_demo, grants=[grant_of(g) for g in grants if g.person_id == r.id]) for r in rows]


async def person_in(ctx, person_id: str) -> PersonRow:
    person = await ctx.db.get(PersonRow, person_id)
    if person is None or person.tenant_id != ctx.tenant.id:
        not_found("Person", person_id)
    return person


@op("GET", "/api/v1/people")
async def list_people(req, ctx):
    page = await paginate_select(ctx.db, select(PersonRow).where(PersonRow.tenant_id == ctx.tenant.id).order_by(PersonRow.seq), req.query)
    return {"items": await people_of(ctx, page["items"]), "next_cursor": page["next_cursor"]}


@op("POST", "/api/v1/people")
async def add_person(req, ctx):
    b = req.body
    row = PersonRow(
        id=ctx.id("p"), tenant_id=ctx.tenant.id, display_name=b.display_name, view=b.view, title=b.title or VIEW_TITLES[b.view],
        email=b.email, is_demo=True, created_at=ctx.now(),
    )
    ctx.db.add(row)
    person = m.Person(id=row.id, display_name=row.display_name, is_demo=True, grants=[])
    ctx.audit(action="person_created", object_type="person", object_id=row.id, after=person)
    return person


@op("GET", "/api/v1/people/{person_id}")
async def get_person(req, ctx):
    return (await people_of(ctx, [await person_in(ctx, req.path.person_id)]))[0]


@op("POST", "/api/v1/role-grants")
async def grant_role(req, ctx):
    b = req.body
    person = await person_in(ctx, b.person_id)
    held = (await ctx.db.scalars(select(RoleGrantRow).where(RoleGrantRow.person_id == person.id, RoleGrantRow.role == b.role))).all()
    if any(g.discipline == b.discipline and g.valid_to is None for g in held):
        problem(409, "already_granted", "Already granted", f"{person.display_name} already holds {b.role}.")
    grant = RoleGrantRow(id=ctx.id("g"), person_id=person.id, role=b.role, discipline=b.discipline, scope=b.scope, valid_from=b.valid_from, valid_to=b.valid_to)
    ctx.db.add(grant)
    made = grant_of(grant)
    ctx.audit(action="role_granted", object_type="role_grant", object_id=grant.id, after=made)
    return made


@op("DELETE", "/api/v1/role-grants/{grant_id}")
async def remove_grant(req, ctx):
    grant = await ctx.db.get(RoleGrantRow, req.path.grant_id)
    if grant is None or (await person_in(ctx, grant.person_id)) is None:
        not_found("Role grant", req.path.grant_id)
    if grant.person_id == ctx.person_id and grant.role == "platform_admin":
        problem(409, "guard_failed", "Refused", "You can't remove your own Platform Admin grant.")
    removed = grant_of(grant)
    await ctx.db.delete(grant)
    ctx.audit(action="role_revoked", object_type="role_grant", object_id=grant.id, before=removed)


@op("GET", "/api/v1/delegations")
async def list_delegations(req, ctx):
    return await DELEGATIONS.page(ctx, req.query)


@op("POST", "/api/v1/delegations")
async def create_delegation(req, ctx):
    b = req.body
    if b.valid_to < b.valid_from:
        invalid("valid_to must not be before valid_from.")
    if b.from_person_id == b.to_person_id:
        invalid("A person can't delegate to themselves.")
    await person_in(ctx, b.from_person_id)
    await person_in(ctx, b.to_person_id)
    d = m.Delegation(**b.model_dump(), id=ctx.id("dl"), created_by=ctx.person_id, created_at=ctx.now())
    DELEGATIONS.add(ctx, d)
    ctx.audit(action="delegation_created", object_type="delegation", object_id=d.id, after=d, reason=d.reason)
    return d


@op("GET", "/api/v1/conflicts")
async def list_conflicts(req, ctx):
    return await CONFLICTS.page(ctx, req.query, order_by=ROW["conflict"].declared_at)


@op("POST", "/api/v1/conflicts")
async def declare_conflict(req, ctx):
    b = req.body
    if not b.party_id and not b.case_id:
        invalid("A conflict needs a party or a case.")
    await person_in(ctx, b.person_id)
    if b.party_id:
        await STORES["party"].require(ctx, b.party_id, "Party")
    if b.case_id:
        await STORES["case"].require(ctx, b.case_id, "Case")
    c = m.Conflict(**b.model_dump(), id=ctx.id("coi"), declared_at=ctx.now())
    CONFLICTS.add(ctx, c)
    ctx.audit(action="conflict_declared", object_type="conflict", object_id=c.id, after=c, reason=c.reason, case_id=c.case_id)
    return c
