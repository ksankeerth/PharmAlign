"""Idempotency-Key: the same key returns the first response and changes nothing."""
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .problems import invalid, problem
from .tables import IdempotencyKeyRow as Row

MAX_KEY = 128


@dataclass(frozen=True)
class Claim:
    tenant_id: str
    actor_id: str
    operation: str
    key: str


@dataclass(frozen=True)
class Stored:
    status: int
    body: Optional[str]


def request_hash(url_path: str, query: str, body: bytes) -> str:
    return hashlib.sha256(b"\0".join((url_path.encode(), query.encode(), body))).hexdigest()


async def begin(db: AsyncSession, claim: Claim, digest: str, now: datetime) -> Optional[Stored]:
    """Claims the key in this transaction. Returns the first response if the key was already used.

    A second request with the same key waits here until the first commits or rolls back,
    so only one of them runs. A failed first attempt leaves nothing, and can be retried.
    """
    if len(claim.key) > MAX_KEY:
        invalid(f"Idempotency-Key is longer than {MAX_KEY} characters.")
    inserted = await db.execute(
        insert(Row)
        .values(tenant_id=claim.tenant_id, actor_id=claim.actor_id, operation=claim.operation, key=claim.key, request_hash=digest, created_at=now)
        .on_conflict_do_nothing()
    )
    if inserted.rowcount == 1:
        return None
    row = await db.scalar(select(Row).where(*_match(claim)))
    if row.request_hash != digest:
        problem(422, "idempotency_key_reused", "This key was used for a different request", "Use a new Idempotency-Key for a new request.")
    return Stored(row.status, row.body)


async def finish(db: AsyncSession, claim: Claim, stored: Stored) -> None:
    await db.execute(update(Row).where(*_match(claim)).values(status=stored.status, body=stored.body))


def _match(claim: Claim) -> tuple:
    return (Row.tenant_id == claim.tenant_id, Row.actor_id == claim.actor_id, Row.operation == claim.operation, Row.key == claim.key)
