"""Lists use `cursor` and `limit`. The cursor is an opaque offset, as in the mock."""
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from .problems import invalid

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _window(query: Optional[Mapping[str, Any]]) -> tuple[int, int]:
    query = query or {}
    cursor = query.get("cursor")
    start = 0
    if cursor:
        if not str(cursor).isdecimal():
            invalid("That cursor did not come from next_cursor.")
        start = int(cursor)
    limit = query.get("limit")
    return start, min(MAX_LIMIT, max(1, DEFAULT_LIMIT if limit is None else int(limit)))


def paginate(items: list, query: Optional[Mapping[str, Any]]) -> dict:
    """Pages a list already in memory."""
    start, limit = _window(query)
    end = start + limit
    return {"items": items[start:end], "next_cursor": str(end) if end < len(items) else None}


async def paginate_select(db: AsyncSession, stmt: Select, query: Optional[Mapping[str, Any]], map: Callable = lambda row: row) -> dict:
    """Pages a select of one entity or column. The select needs a stable order_by."""
    start, limit = _window(query)
    rows = (await db.scalars(stmt.offset(start).limit(limit + 1))).all()
    more = len(rows) > limit
    return {"items": [map(r) for r in rows[:limit]], "next_cursor": str(start + limit) if more else None}
