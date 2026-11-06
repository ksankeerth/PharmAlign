"""One small corner of the demo seed: CASE-1042 back at intake, with its pinned pack and snapshot. Fast enough to load per test."""
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.context import Tenant
from app.jobs import Worker
from app.pipeline import runner
from app.pipeline.replay import ReplayAgentClient, load_recorded_dossier
from app.seed import read
from app.stores import STORES
from app.tables import ROW

from ..conftest import TENANT, system_ctx

CASE = "CASE-1042"
LAW_DOCS = {"E-S25", "E-S06", "A-01", "B-S09", "F-01"}
ALL_CHECKS = {"SAFE-01", "DOOR-01", "DOOR-02", "QUAL-03", "ROUTE-01", "WATCH-01", "SAME-01", "QUAL-07", "LBL-02", "LEGAL-01", "PV-01"}


def _writer(db: AsyncSession, tenant=TENANT):
    return type("Writer", (), {"db": db, "tenant": Tenant(tenant, "simulation")})()


async def load_case(db: AsyncSession, now: datetime) -> None:
    def keep(name, test):
        return [r for r in read(name, now) if test(r)]

    in_case = lambda r: r.get("doc", {}).get("case_id") == CASE or r.get("case_id") == CASE  # noqa: E731
    version_ids = {f"{d}-v1" for d in LAW_DOCS}
    wanted = [
        ("document", lambda r: r["doc"]["id"] in LAW_DOCS),
        ("document_version", lambda r: r["doc"]["id"] in version_ids),
        ("chunk", lambda r: r["doc"]["document_version_id"] in version_ids),
        ("kb_snapshot", lambda r: r["doc"]["id"] == "snap-ae-14"),
        ("kb_snapshot_item", lambda r: r.get("snapshot_id") == "snap-ae-14" and r["doc"]["document_version_id"] in version_ids),
        ("pack_version", lambda r: r["doc"]["id"] == "pv-ae-3"),
        ("party", lambda r: r["doc"]["id"] == "pt-example"),
        ("medicine", lambda r: r["doc"]["id"] == "med-amoxiclav"),
    ]
    for name, test in wanted:
        for row in keep(name, test):
            extras = {k: v for k, v in row.items() if k not in ("tenant_id", "doc", "id")}
            STORES[name].add(_writer(db, row.get("tenant_id") or TENANT), row["doc"], id=row.get("id"), **extras)
        await db.flush()
    case = next(r for r in read("case", now) if r["doc"]["id"] == CASE)
    doc = {**case["doc"], "status": "intake", "current_pipeline_run_id": None, "current_recommendation_version": None, "blocking_checks_open": 0}
    STORES["case"].add(_writer(db), doc, assessors=case["assessors"])
    await db.flush()
    for name in ("dossier_document", "dossier_page", "dossier_chunk"):
        for row in keep(name, in_case):
            extras = {k: v for k, v in row.items() if k not in ("tenant_id", "doc", "id")}
            if name == "dossier_chunk":
                db.add(ROW[name](**row))
            else:
                STORES[name].add(_writer(db), row["doc"], id=row.get("id"), **extras)
        await db.flush()
    for row in keep("reliance_basis", lambda r: r["doc"]["target_id"] == CASE):
        STORES["reliance_basis"].add(_writer(db), row["doc"])
    await db.flush()
    await load_recorded_dossier(db, TENANT)


async def start(rt) -> str:
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        run = await runner.start_pipeline(ctx, CASE)
        await ctx.flush()
    return run.id


async def run_jobs(rt) -> int:
    return await Worker(rt).run_due()


async def drive(rt, clock, rounds: int = 30, seconds: float = 6) -> None:
    """Runs due jobs and lets time pass, until nothing is left to do."""
    for _ in range(rounds):
        await run_jobs(rt)
        clock.advance(seconds=seconds)
    await run_jobs(rt)


async def checks_by_id(rt) -> dict:
    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        return {c.check_id: c for c in await runner._current_checks(ctx, CASE)}


async def statuses(rt) -> dict:
    return {k: c.status for k, c in (await checks_by_id(rt)).items()}


async def the_case(rt):
    async with rt.sessions() as db:
        return await STORES["case"].require(system_ctx(rt, db), CASE)


async def all_runs(rt) -> list:
    async with rt.sessions() as db:
        return await STORES["agent_run"].list(system_ctx(rt, db), ROW["agent_run"].case_id == CASE)


async def all_work(rt) -> list:
    async with rt.sessions() as db:
        return await runner._work_of(system_ctx(rt, db), CASE)


def run_of(check_id: str, generation: int = 1, attempt: int = 1) -> str:
    return f"case-1042-s2-{check_id.lower()}-g{generation}-a{attempt}"


def replay_client(clock) -> ReplayAgentClient:
    return ReplayAgentClient.from_files(clock=clock)
