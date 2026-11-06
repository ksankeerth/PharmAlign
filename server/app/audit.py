"""The audit log. write is its only writer. Rows chain by hash, one chain per tenant."""
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from pharmalign_contracts.server.models import AuditEvent, AuditVerification
from pydantic_core import to_jsonable_python
from sqlalchemy import func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import AuditEventRow, AuditHeadRow

HASHED = ("id", "tenant_id", "actor_type", "actor_id", "account_id", "role_used", "action", "object_type", "object_id", "case_id", "before", "after", "reason")


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    actor_type: str
    actor_id: str
    action: str
    object_type: str
    object_id: str
    account_id: Optional[str] = None
    role_used: Optional[str] = None
    case_id: Optional[str] = None
    before: Any = None
    after: Any = None
    reason: Optional[str] = None


def row_hash(prev_hash: Optional[str], row: dict) -> str:
    fields = {name: row[name] for name in HASHED}
    fields["at"] = row["at"].astimezone(timezone.utc).isoformat()
    fields["prev_hash"] = prev_hash
    text = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


async def write(db: AsyncSession, tenant_id: str, entries: Iterable[AuditEntry]) -> list[str]:
    """Appends to the tenant's chain, inside the caller's transaction. Returns the new row ids.

    Holds a per-tenant lock until commit, so call it last. Ctx does.
    """
    entries = list(entries)
    if not entries:
        return []
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(tenant_id, 0))))
    prev = await db.scalar(
        select(AuditEventRow.row_hash).where(AuditEventRow.tenant_id == tenant_id).order_by(AuditEventRow.seq.desc()).limit(1)
    )
    ids = []
    last_seq = None
    for entry in entries:
        row = {name: getattr(entry, name) for name in entry.__dataclass_fields__}
        row.update(id=f"ae-{uuid.uuid4().hex[:16]}", tenant_id=tenant_id, before=to_jsonable_python(entry.before), after=to_jsonable_python(entry.after))
        row_digest = row_hash(prev, row)
        AuditEvent.model_validate({**{k: v for k, v in row.items() if k != "tenant_id"}, "row_hash": row_digest})
        last_seq = await db.scalar(insert(AuditEventRow).values(**row, prev_hash=prev, row_hash=row_digest).returning(AuditEventRow.seq))
        ids.append(row["id"])
        prev = row_digest
    head = {"seq": last_seq, "row_id": ids[-1], "row_hash": prev}
    await db.execute(pg_insert(AuditHeadRow).values(tenant_id=tenant_id, **head).on_conflict_do_update(index_elements=["tenant_id"], set_=head))
    return ids


async def verify(db: AsyncSession, tenant_id: str) -> AuditVerification:
    """Walks the chain, recomputes every hash and checks it ends at the stored head.
    The rows and the head are read in one read-only snapshot on a connection of their own, so a write that commits in between can't look like a cut."""
    async with AsyncSession(db.bind, expire_on_commit=False) as snapshot:
        await snapshot.connection(execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True})
        return await _walk(snapshot, tenant_id)


async def _walk(db: AsyncSession, tenant_id: str) -> AuditVerification:
    rows = await db.stream_scalars(select(AuditEventRow).where(AuditEventRow.tenant_id == tenant_id).order_by(AuditEventRow.seq))
    prev = None
    checked = 0
    last = None
    async for row in rows:
        fields = {name: getattr(row, name) for name in (*HASHED, "at")}
        if row.prev_hash != prev or row.row_hash != row_hash(prev, fields):
            return AuditVerification(ok=False, rows_checked=checked, first_bad_row=row.id)
        prev = row.row_hash
        last = row.seq
        checked += 1
    head = await db.get(AuditHeadRow, tenant_id)
    if head and (head.seq != last or head.row_hash != prev):
        # Rows were cut from the end. The row named is the one the log should end with.
        return AuditVerification(ok=False, rows_checked=checked, first_bad_row=head.row_id)
    return AuditVerification(ok=True, rows_checked=checked)
