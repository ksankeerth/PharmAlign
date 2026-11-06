"""Tables with typed columns plus a JSONB doc, validated by a contract model on the way in and out."""
from typing import Any, Callable, Generic, Mapping, Optional, TypeVar

from pydantic import BaseModel
from sqlalchemy import select

from .paging import paginate_select
from .problems import not_found

M = TypeVar("M", bound=BaseModel)


class DocStore(Generic[M]):
    """One contract model stored in one table, always scoped to ctx.tenant.

    `columns` copies fields from the doc into typed columns, so they can't disagree with it.
    `fixed` sets and filters columns, so several models can share a table.
    `key` builds the row id for a model with no `id` of its own. `scoped=False` is for tables shared by every tenant.
    """

    def __init__(
        self, row: type, model: type[M], columns: Optional[Callable[[M], Mapping[str, Any]]] = None, fixed: Optional[Mapping[str, Any]] = None,
        key: Optional[Callable[[Any, M], str]] = None, scoped: bool = True,
    ):
        self.row = row
        self.model = model
        self.columns = columns or (lambda doc: {})
        self.fixed = dict(fixed or {})
        self.key = key or (lambda ctx, doc: doc.id)
        self.scoped = scoped

    def _select(self, ctx):
        stmt = select(self.row)
        if self.scoped:
            stmt = stmt.where(self.row.tenant_id == ctx.tenant.id)
        for name, value in self.fixed.items():
            stmt = stmt.where(getattr(self.row, name) == value)
        return stmt

    def _load(self, row) -> M:
        return self.model.model_validate(row.doc)

    def add(self, ctx, doc: M, id: Optional[str] = None, **extra) -> M:
        """Inserts a new row. Written at the next flush, inside the request's transaction."""
        doc = self.model.model_validate(doc)
        tenant = {"tenant_id": ctx.tenant.id} if self.scoped else {}
        ctx.db.add(self.row(
            id=id or self.key(ctx, doc), doc=doc.model_dump(mode="json"), **tenant,
            **self.fixed, **self.columns(doc), **extra,
        ))
        return doc

    async def get(self, ctx, id: str, for_update: bool = False) -> Optional[M]:
        row = await self._row(ctx, id, for_update)
        return self._load(row) if row else None

    async def require(self, ctx, id: str, what: Optional[str] = None, for_update: bool = False) -> M:
        """Like get, or a 404. Use for_update when a guard depends on what you read."""
        row = await self._row(ctx, id, for_update)
        if row is None:
            not_found(what or self.model.__name__, id)
        return self._load(row)

    async def save(self, ctx, doc: M) -> M:
        """Writes back a changed doc and its typed columns."""
        doc = self.model.model_validate(doc.model_dump())
        row = await self._row(ctx, self.key(ctx, doc), False)
        if row is None:
            not_found(self.model.__name__, self.key(ctx, doc))
        row.doc = doc.model_dump(mode="json")
        for name, value in self.columns(doc).items():
            setattr(row, name, value)
        return doc

    async def list(self, ctx, *where, order_by=None) -> list[M]:
        stmt = self._select(ctx).where(*where).order_by(*self._order(order_by))
        return [self._load(r) for r in (await ctx.db.scalars(stmt)).all()]

    async def page(self, ctx, query, *where, order_by=None) -> dict:
        stmt = self._select(ctx).where(*where).order_by(*self._order(order_by))
        return await paginate_select(ctx.db, stmt, query, map=self._load)

    def _order(self, order_by) -> tuple:
        if order_by is None:
            return (self.row.id,)
        return order_by if isinstance(order_by, tuple) else (order_by,)

    async def _row(self, ctx, id: str, for_update: bool):
        stmt = self._select(ctx).where(self.row.id == id)
        if for_update:
            stmt = stmt.with_for_update()
        return (await ctx.db.scalars(stmt)).first()
