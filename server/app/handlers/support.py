"""What the case-flow handlers share: the stores, and the loading of a case for the guards."""
from dataclasses import replace
from typing import Mapping, Optional

from pharmalign_contracts.server import models as m
from sqlalchemy import or_, select, update

from ..context import Ctx, Tenant
from ..domain.fingerprints import CaseFacts
from ..problems import not_found
from ..stores import STORES
from ..tables import ROW, PersonRow

CASES = STORES["case"]
CHECKS = STORES["check_result"]
TRACES = STORES["check_trace"]
PACKS = STORES["pack_version"]
THREADS = STORES["review_thread"]
COMMENTS = STORES["review_comment"]
QUESTIONS = STORES["question"]
RECOMMENDATIONS = STORES["recommendation_version"]
PROMOTIONS = STORES["promotion"]
CONFLICTS = STORES["conflict"]
MATCHES = STORES["watch_match"]
SOURCES = STORES["watch_source"]
RELIANCE = STORES["reliance_basis"]
DECISIONS = STORES["decision"]
PROPOSALS = STORES["proposal"]
SIGNERS = STORES["decision_signer"]
FROZEN = frozenset({"promoted", "signed", "recording", "closed"})  # the queue lists these last


async def get_case(ctx: Ctx, case_id: str, for_update: bool = False) -> m.CaseDetail:
    return await CASES.require(ctx, case_id, "Case", for_update=for_update)


async def name_of(ctx: Ctx, person_id: Optional[str]) -> str:
    person = await ctx.db.get(PersonRow, person_id) if person_id else None
    return person.display_name if person else (person_id or "Someone")


async def people_in_view(ctx: Ctx, view: str) -> list[str]:
    stmt = select(PersonRow.id).where(PersonRow.tenant_id == ctx.tenant.id, PersonRow.view == view).order_by(PersonRow.seq)
    return list((await ctx.db.scalars(stmt)).all())


async def facts_of(ctx: Ctx, case: m.CaseDetail) -> CaseFacts:
    """Everything the guards read about one case. Replaced check results are included: a person who acted on one still took part."""
    row = ROW["case"]
    assessors = (await ctx.db.scalar(select(row.assessors).where(row.id == case.id, row.tenant_id == ctx.tenant.id))) or []
    promotions = await PROMOTIONS.list(ctx, ROW["promotion"].case_id == case.id, order_by=ROW["promotion"].at.desc())
    conflicts = ROW["conflict"]
    pack = await PACKS.require(ctx, case.pack_version_id, "Pack version")
    return CaseFacts(
        case_id=case.id,
        case=case,
        assessors=list(assessors),
        promotion=promotions[0] if promotions else None,
        checks=await CHECKS.list(ctx, ROW["check_result"].case_id == case.id),
        recommendations=await RECOMMENDATIONS.list(ctx, ROW["recommendation_version"].case_id == case.id),
        threads=await THREADS.list(ctx, ROW["review_thread"].case_id == case.id),
        questions=await QUESTIONS.list(ctx, ROW["question"].case_id == case.id),
        matches=await MATCHES.list(ctx, ROW["watch_match"].target_type == "case", ROW["watch_match"].target_id == case.id),
        conflicts=await CONFLICTS.list(ctx, or_(conflicts.case_id == case.id, conflicts.party_id == case.applicant_party_id)),
        pack_checks=pack.document.checks,
    )


async def add_assessor(ctx: Ctx, case_id: str, person_id: str) -> None:
    """Recorded for separation of duties: this person took part in assessing the case."""
    row = ROW["case"]
    current = (await ctx.db.scalar(select(row.assessors).where(row.id == case_id, row.tenant_id == ctx.tenant.id))) or []
    if person_id not in current:
        await ctx.db.execute(update(row).where(row.id == case_id, row.tenant_id == ctx.tenant.id).values(assessors=[*current, person_id]))


def as_result(d: m.CheckDetail) -> m.CheckResult:
    return m.CheckResult(**{name: getattr(d, name) for name in m.CheckResult.model_fields})


def as_summary(d: m.CaseDetail) -> m.CaseSummary:
    return m.CaseSummary(**{name: getattr(d, name) for name in m.CaseSummary.model_fields})


def with_limit(query: Optional[Mapping], default: int) -> dict:
    """Lists the mock pages at 200 unless asked otherwise."""
    query = dict(query or {})
    if query.get("limit") is None:
        query["limit"] = default
    return query


def in_tenant(ctx: Ctx, tenant_id: str) -> Ctx:
    """The same request, working in another workspace. For the Platform Admin, who sets up every country.
    Its audit entries go into the same request, so they commit together."""
    return ctx if tenant_id == ctx.tenant.id else replace(ctx, tenant=Tenant(tenant_id, ctx.tenant.mode))


async def reach(ctx: Ctx, workspace_id: str) -> Ctx:
    """The caller's own workspace, or any workspace for a Platform Admin. Anyone else gets a 404."""
    if workspace_id == ctx.tenant.id:
        return ctx
    if ctx.view == "platform_admin" and await ctx.db.scalar(select(ROW["workspace"].id).where(ROW["workspace"].id == workspace_id)):
        return in_tenant(ctx, workspace_id)
    not_found("Workspace", workspace_id)
