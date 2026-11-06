"""Background jobs in the database. A worker leases one, runs it, and commits its work with its completion."""
import asyncio
import logging
import socket
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Awaitable, Callable, Optional

from sqlalchemy import and_, or_, select, update

from .context import Ctx, Runtime, Tenant
from .delivery import notifications_tick
from .identity import Actor
from .tables import JobRow

log = logging.getLogger(__name__)

# Bound with @task, as the mock's `tasks` maps are. A task takes (payload, ctx).
TASKS: dict[str, Callable[[dict, Ctx], Awaitable[None]]] = {}
# Scheduled work named by TickRequest. Each takes the runtime and returns how much it did.
TICK_TASKS: dict[str, Callable[[Runtime], Awaitable[int]]] = {}
MAX_BACKOFF_SECONDS = 300
MAX_ERROR = 500


def task(kind: str):
    def bind(fn):
        if kind in TASKS:
            raise ValueError(f"job {kind} already has a task")
        TASKS[kind] = fn
        return fn

    return bind


@dataclass(frozen=True)
class Lease:
    job_id: str
    token: str
    kind: str
    payload: dict
    tenant_id: str
    attempts: int
    max_attempts: int


class LeaseLost(Exception):
    """Another worker took the job after this lease expired. This worker's work is rolled back."""


class Worker:
    def __init__(self, rt: Runtime, owner: Optional[str] = None, lease_seconds: Optional[float] = None):
        self.rt = rt
        self.owner = owner or rt.settings.worker_id or socket.gethostname()
        self.lease = timedelta(seconds=lease_seconds or rt.settings.job_lease_seconds)

    async def claim(self) -> Optional[Lease]:
        """Leases the next due job: queued, or running with an expired lease. Other workers skip it."""
        now = self.rt.clock.now()
        async with self.rt.sessions() as db, db.begin():
            while True:
                job = await db.scalar(
                    select(JobRow)
                    .where(or_(
                        and_(JobRow.status == "queued", JobRow.run_at <= now),
                        and_(JobRow.status == "running", JobRow.lease_expires_at <= now),
                    ))
                    .order_by(JobRow.run_at, JobRow.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                if job is None:
                    return None
                if job.attempts >= job.max_attempts:
                    # It keeps dying with its lease held, so it never reached the retry path.
                    job.status, job.finished_at, job.last_error = "failed", now, "out of attempts"
                    job.lease_owner = job.lease_token = job.lease_expires_at = None
                    await db.flush()
                    continue
                job.status = "running"
                job.attempts += 1
                job.lease_owner = self.owner
                job.lease_token = uuid.uuid4().hex
                job.lease_expires_at = now + self.lease
                return Lease(job.id, job.lease_token, job.kind, job.payload, job.tenant_id, job.attempts, job.max_attempts)

    async def run(self, lease: Lease) -> bool:
        """Runs a leased job in one transaction with its completion. Returns whether it finished."""
        fn = self.rt.tasks.get(lease.kind)
        if fn is None:
            await self._give_up(lease, f"no task is bound to job {lease.kind}")
            return False
        beat = asyncio.create_task(self._heartbeat(lease))
        try:
            async with self.rt.sessions() as db, db.begin():
                actor = Actor(type="system", id="worker", tenant_id=lease.tenant_id)
                ctx = Ctx(self.rt, db, actor, Tenant(lease.tenant_id, self.rt.settings.env))
                await fn(lease.payload, ctx)
                await db.flush()
                done = await db.execute(
                    update(JobRow).where(*self._held(lease)).values(
                        status="done", finished_at=self.rt.clock.now(), lease_owner=None, lease_token=None, lease_expires_at=None, last_error=None,
                    )
                )
                if done.rowcount != 1:
                    raise LeaseLost(lease.job_id)
                await ctx.flush()
            return True
        except LeaseLost:
            log.warning("job %s: lease lost, its work was rolled back", lease.job_id)
            return False
        except Exception as e:
            log.exception("job %s (%s) failed", lease.job_id, lease.kind)
            await self._retry(lease, e)
            return False
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)

    async def run_due(self, limit: int = 100) -> int:
        """Runs due jobs until none are left, or `limit` have been tried. Returns how many finished."""
        finished = 0
        for _ in range(limit):
            lease = await self.claim()
            if lease is None:
                break
            finished += await self.run(lease)
        return finished

    async def reattach(self) -> int:
        """After a restart, picks jobs this worker was running back up without waiting for their leases."""
        async with self.rt.sessions() as db, db.begin():
            result = await db.execute(
                update(JobRow)
                .where(JobRow.status == "running", JobRow.lease_owner == self.owner)
                .values(status="queued", run_at=self.rt.clock.now(), lease_owner=None, lease_token=None, lease_expires_at=None)
            )
        return result.rowcount

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                count = await self.reattach()
                log.info("worker %s started, re-attached %d jobs", self.owner, count)
                break
            except Exception:
                log.exception("worker could not start, trying again")
                await self._wait(stop)
        while not stop.is_set():
            try:
                if await self.run_due() + await notifications_tick(self.rt):
                    continue
            except Exception:
                log.exception("worker loop failed, trying again")
            await self._wait(stop)

    async def _wait(self, stop: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop.wait(), self.rt.settings.worker_poll_seconds)
        except TimeoutError:
            pass

    def _held(self, lease: Lease) -> tuple:
        return (JobRow.id == lease.job_id, JobRow.lease_token == lease.token, JobRow.status == "running")

    async def _heartbeat(self, lease: Lease) -> None:
        """Keeps the lease alive while a slow task runs."""
        while True:
            await asyncio.sleep(self.lease.total_seconds() / 3)
            try:
                async with self.rt.sessions() as db, db.begin():
                    await db.execute(update(JobRow).where(*self._held(lease)).values(lease_expires_at=self.rt.clock.now() + self.lease))
            except Exception:
                log.exception("job %s: could not renew its lease", lease.job_id)

    async def _retry(self, lease: Lease, error: Exception) -> None:
        message = f"{type(error).__name__}: {error}"[:MAX_ERROR]
        if lease.attempts >= lease.max_attempts:
            await self._give_up(lease, message)
            return
        delay = min(MAX_BACKOFF_SECONDS, 5 * 2 ** (lease.attempts - 1))
        async with self.rt.sessions() as db, db.begin():
            await db.execute(update(JobRow).where(*self._held(lease)).values(
                status="queued", run_at=self.rt.clock.now() + timedelta(seconds=delay), last_error=message,
                lease_owner=None, lease_token=None, lease_expires_at=None,
            ))

    async def _give_up(self, lease: Lease, message: str) -> None:
        async with self.rt.sessions() as db, db.begin():
            await db.execute(update(JobRow).where(*self._held(lease)).values(
                status="failed", finished_at=self.rt.clock.now(), last_error=message,
                lease_owner=None, lease_token=None, lease_expires_at=None,
            ))


async def run_tick(rt: Runtime, tasks: list[str]) -> dict[str, int]:
    """One scheduler tick. No tasks named means everything that is due."""
    done: dict[str, int] = {}
    for name in tasks or TICK_TASKS:
        fn = TICK_TASKS.get(name)
        done[name] = await fn(rt) if fn else 0
    if not tasks:
        done["jobs"] = await Worker(rt, owner=f"tick-{uuid.uuid4().hex[:8]}").run_due()
    rt.last_tick_at = rt.clock.now()
    if "escalations" in done:
        rt.last_escalations_at = rt.last_tick_at
    return done


TICK_TASKS["escalations"] = notifications_tick
