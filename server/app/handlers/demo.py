"""Demo data: what is loaded, and a reset to the seed."""
import asyncio
import logging

from pharmalign_contracts.server import models as m
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from ..context import Runtime
from ..identity import SESSION_COOKIE, WORKSPACE_COOKIE
from ..ops import op
from ..pipeline import runner as pipeline_runner
from ..problems import problem
from ..seed import load_seed, set_resetting
from ..tables import ROW, DemoStateRow, PersonRow

log = logging.getLogger(__name__)

DEADLOCK = "40P01"
LOAD_ATTEMPTS = 3


async def status_of(db) -> m.DemoStatus:
    state = await db.get(DemoStateRow, 1)
    row = ROW["workspace"]
    workspaces = [m.Workspace.model_validate(r.doc) for r in await db.scalars(select(row).order_by(row.created_at, row.id))]
    return m.DemoStatus(
        loaded_at=state.loaded_at if state else None,
        with_library=state.with_library if state else False,
        resetting=state.resetting if state else False,
        workspaces=[m.WorkspaceSummary(id=w.id, name=w.name, country_code=w.country_code, authority_name=w.authority_name, mode=w.mode) for w in workspaces],
        people=await db.scalar(select(func.count()).select_from(PersonRow).where(PersonRow.is_demo)),
        cases=await db.scalar(select(func.count()).select_from(ROW["case"])),
    )


@op("GET", "/api/v1/demo")
async def get_demo(req, ctx):
    return await status_of(ctx.db)


@op("POST", "/api/v1/demo/reset")
async def reset_demo(req, ctx):
    # The route is simulation_only, so authz has already refused this anywhere else.
    claimed = await ctx.db.execute(
        insert(DemoStateRow).values(id=1, with_library=False, resetting=True)
        .on_conflict_do_update(index_elements=["id"], set_={"resetting": True}, where=DemoStateRow.resetting.is_(False))
        .returning(DemoStateRow.id)
    )
    if claimed.first() is None:
        problem(409, "reset_running", "A reset is already running", "Wait for the running reset to finish.")
    # Not a row in the job table: the seed empties that table, and the job would lose its lease.
    task = asyncio.create_task(run_reset(ctx.rt, req.body.with_library))
    ctx.rt.background.add(task)
    task.add_done_callback(ctx.rt.background.discard)
    # Everything is replaced, so the caller starts again as the default person in their own workspace.
    ctx.cookies.update({SESSION_COOKIE: None, WORKSPACE_COOKIE: None})
    return await status_of(ctx.db)


def _deadlock(error: DBAPIError) -> bool:
    cause = error.orig
    while cause is not None:
        if getattr(cause, "sqlstate", None) == DEADLOCK:
            return True
        cause = cause.__cause__
    return False


async def _request_over(rt: Runtime) -> bool:
    """Waits for the request that started the reset to commit, and says whether it set the flag. The request still holds
    demo_state, and the lock conflicts with its write, so this neither polls nor races it."""
    async with rt.sessions() as db, db.begin():
        await db.execute(text("LOCK TABLE demo_state IN SHARE MODE"))
        state = await db.get(DemoStateRow, 1)
        return bool(state and state.resetting)


async def run_reset(rt: Runtime, with_library: bool) -> None:
    """The reset itself, outside the request: the seed empties the tables the request's transaction has open, and with the
    library it takes minutes. The flag is cleared however it ends, and loaded_at changes only if the load succeeds."""
    try:
        if not await _request_over(rt):
            return
        for attempt in range(1, LOAD_ATTEMPTS + 1):
            try:
                await load_seed(rt.engine, rt.settings, with_library=with_library, reset=True)
                break
            except DBAPIError as e:
                # Emptying the tables can meet a request that holds some of them in the other order.
                if attempt == LOAD_ATTEMPTS or not _deadlock(e):
                    raise
                log.warning("demo reset: deadlock, trying again")
        forget_runs = getattr(rt.agents, "forget_runs", None)
        if forget_runs:
            forget_runs()
        if rt.settings.worker_enabled:
            await pipeline_runner.pick_up_unfinished(rt)
    except Exception:
        log.exception("demo reset failed")
    finally:
        try:
            await set_resetting(rt.sessions, False)
        except Exception:
            log.exception("demo reset: could not clear the flag")
