"""Reading the foreign watch sources, as signing a grant and the scheduler need it. The watch's own fetcher is `watch.sources.SourceFetcher`."""
from typing import Iterable, Optional, Protocol

from .context import Runtime
from .domain.rules import UNREADABLE_SOURCE
from .pipeline.runner import system_tx
from .stores import STORES

SOURCES = STORES["watch_source"]


class WatchFetcher(Protocol):
    async def fetch(self, rt: Runtime, tenant_id: str, source_ids: Iterable[str]) -> Optional[dict]:
        """Reads each source now and records the result. Each source gets its own transaction, so a failure leaves the others read.
        May return (new notices, new revisions) per source."""


class SimulatedFetcher:
    """Marks sources read. One that is already failing stays unread, as a real fetch of it would fail too.
    `unreadable` names more sources that fail, for tests."""

    def __init__(self, unreadable: Iterable[str] = ()):
        self.unreadable = set(unreadable)

    async def fetch(self, rt: Runtime, tenant_id: str, source_ids: Iterable[str]) -> None:
        for source_id in source_ids:
            async with system_tx(rt, tenant_id) as ctx:
                source = await SOURCES.get(ctx, source_id, for_update=True)
                if source is None or source_id in self.unreadable or source.health in UNREADABLE_SOURCE:
                    continue
                await SOURCES.save(ctx, source.model_copy(update={"last_success_at": ctx.now(), "health": "healthy", "last_error": None}))
                ctx.audit("source_fetched", "watch_source", source_id, after={"health": "healthy"})


def make_fetcher(settings) -> WatchFetcher:
    from .watch.sources import SourceFetcher

    return SourceFetcher(silence_days=settings.watch_silence_days)
