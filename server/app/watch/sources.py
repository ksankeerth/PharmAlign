"""Reading the sources, and saying so when one can't be read. Silence is never taken as safe.

A source is unhealthy when its fetch errors, when it yields nothing for longer than the silence limit, or when its layout
changed. An unhealthy source shows in the sources list, in coverage and on the signing screen, and a grant that relies on
it needs the signer's reason. In simulation a source reads the items staged for it; fetching a real feed or page is not built.
"""
import logging
from datetime import timedelta
from typing import Awaitable, Callable, Iterable, Optional

from pharmalign_contracts.server import models as m
from sqlalchemy import select

from ..context import Runtime
from ..domain.rules import UNREADABLE_SOURCE
from ..pipeline.runner import system_tx
from ..stores import STORES
from ..tables import ROW
from . import alerts

log = logging.getLogger(__name__)
SOURCES = STORES["watch_source"]
STAGED = ROW["watch_staged_item"]
SILENCE_DAYS = 14  # no item for this long and the source is called silent
Reader = Callable[[Runtime, str, m.WatchSource], Awaitable[list[tuple[str, m.StagedNotice]]]]


class SourceError(Exception):
    """The source could not be read."""


async def read_staged(rt: Runtime, tenant_id: str, source: m.WatchSource) -> list[tuple[str, m.StagedNotice]]:
    """What is staged for this source and not yet taken."""
    if source.kind not in ("staged",) and rt.settings.env != "simulation":
        raise SourceError(f"A {source.kind.replace('_', ' ')} source can't be fetched yet.")
    async with rt.sessions() as db, db.begin():
        rows = (await db.scalars(select(STAGED).where(STAGED.tenant_id == tenant_id, STAGED.source_id == source.id, STAGED.taken_at.is_(None)).order_by(STAGED.staged_at, STAGED.id))).all()
        return [(r.id, m.StagedNotice.model_validate(r.notice)) for r in rows]


class SourceFetcher:
    """The WatchFetcher for the watch. `readers` maps a source kind to a function that returns its staged items or raises SourceError."""

    def __init__(self, readers: Optional[dict[str, Reader]] = None, silence_days: int = SILENCE_DAYS):
        self.readers = {"staged": read_staged, "feed": read_staged, "web_page": read_staged, **(readers or {})}
        self.silence = timedelta(days=silence_days)

    async def fetch(self, rt: Runtime, tenant_id: str, source_ids: Iterable[str]) -> dict[str, tuple[int, int]]:
        """Reads each source now. Each gets its own transactions, so one failure leaves the others read. Returns (new notices, new revisions) per source."""
        return {source_id: await self._one(rt, tenant_id, source_id) for source_id in source_ids}

    async def _one(self, rt: Runtime, tenant_id: str, source_id: str) -> tuple[int, int]:
        async with system_tx(rt, tenant_id) as ctx:
            source = await SOURCES.get(ctx, source_id)
        if source is None or source.health == "layout_changed":
            return 0, 0  # a changed layout stays unhealthy until a person repairs the source
        if source.kind == "manual":
            await self._settle(rt, tenant_id, source_id, health="manual", fetched=0)
            return 0, 0
        try:
            items = await self.readers[source.kind](rt, tenant_id, source)
        except Exception as e:  # any error is a failed read, never a quiet one
            log.warning("source %s could not be read: %s", source_id, e)
            await self._settle(rt, tenant_id, source_id, health="failing", error=str(e) or type(e).__name__)
            return 0, 0
        from .notices import ingest

        new = revised = 0
        for item_id, staged in items:
            async with system_tx(rt, tenant_id) as ctx:
                taken = await ctx.db.get(STAGED, item_id, with_for_update=True)
                if taken is None or taken.taken_at is not None:
                    continue
                taken.taken_at = ctx.now()
                result = await ingest(ctx, staged, source_id)
                new += result.state == "new"
                revised += result.state == "revised"
        await self._settle(rt, tenant_id, source_id, fetched=new + revised)
        return new, revised

    async def _settle(self, rt: Runtime, tenant_id: str, source_id: str, health: Optional[str] = None, error: Optional[str] = None, fetched: int = 0) -> None:
        """Saves the outcome of a read, and tells the watch officers when a source turns unhealthy."""
        async with system_tx(rt, tenant_id) as ctx:
            source = await SOURCES.require(ctx, source_id, "Watch source", for_update=True)
            row = await ctx.db.get(ROW["watch_source"], source_id)
            now = ctx.now()
            if health != "failing" and fetched:
                row.last_item_at = now
            elif row.last_item_at is None:
                row.last_item_at = now  # the silence clock starts at the first read
            if health == "failing":
                state = "failing"
            elif health == "manual":
                state = "manual"
            elif not fetched and now - row.last_item_at > self.silence:
                state = "zero_yield"
            else:
                state = "healthy"
            update = {"health": state, "last_error": error[:200] if error else None}
            if state == "zero_yield":
                update["last_error"] = f"Nothing new for {(now - row.last_item_at).days} days."
            if state != "failing":
                update["last_success_at"] = now
                update["items_last_30_days"] = source.items_last_30_days + fetched
            await SOURCES.save(ctx, source.model_copy(update=update))
            ctx.audit("source_fetched", "watch_source", source_id, before={"health": source.health}, after={"health": state, "new_items": fetched}, actor_type="system", actor_id="watch")
            if state in UNREADABLE_SOURCE and source.health not in UNREADABLE_SOURCE:
                # Visible: a person is told once, when it turns.
                for person_id in await alerts.watch_officers(ctx):
                    ctx.notify(person_id, "source_unhealthy", "review", f"Watch source unhealthy: {source.regulator}",
                               f"{state.replace('_', ' ')}. Foreign status may be out of date, so a grant needs the signer's reason.", "/watch/sources")


async def due_sources(rt: Runtime) -> dict[str, list[str]]:
    """Per tenant, the sources whose time to be read has come, or that have never been read. Manual ones and changed layouts wait for a person."""
    row = ROW["watch_source"]
    now = rt.clock.now()
    async with rt.sessions() as db, db.begin():
        found = (await db.scalars(select(row).where(row.kind != "manual", row.health != "layout_changed"))).all()
    due: dict[str, list[str]] = {}
    for r in found:
        every = timedelta(minutes=r.doc.get("check_every_minutes") or 60)
        if r.last_success_at is None or now - r.last_success_at >= every or r.health in UNREADABLE_SOURCE:
            due.setdefault(r.tenant_id, []).append(r.id)
    return due


async def check_sources(rt: Runtime) -> int:
    """The scheduler's tick: reads what is due, which also finds a source that has gone quiet. Returns how many it read."""
    count = 0
    for tenant_id, ids in (await due_sources(rt)).items():
        await rt.fetcher.fetch(rt, tenant_id, ids)
        count += len(ids)
    return count


from ..jobs import TICK_TASKS  # noqa: E402

TICK_TASKS["watch"] = check_sources
