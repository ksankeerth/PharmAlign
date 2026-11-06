"""Loads the demo data from demo/seed/*.json.

    PHARMALIGN_ENV=simulation uv run python -m app.seed --reset [--with-library]

Every doc is validated against its contract model. A row that doesn't fit stops the load, naming the file and row.
--with-library then ingests the real text of the core documents (app/seed_library.py).
The demo reset (POST /api/v1/demo/reset) runs the same load_seed.
Times in the files are relative: {"$t": seconds} from the load time, and {"$d": days} for a date.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from pharmalign_contracts.server import models as m
from pydantic import ValidationError
from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from . import audit
from .config import Settings
from .context import NOTIFICATIONS, TIMELINE, Tenant
from .db import Base, make_engine, make_sessions
from .stores import STORES
from .tables import ROW, DemoStateRow, PersonRow, RoleGrantRow

SEED_DIR = Path(os.environ.get("PHARMALIGN_SEED_DIR") or Path(__file__).resolve().parents[2] / "demo" / "seed")

# Parents before children.
ORDER = [
    "workspace", "setup_stage", "document", "document_version", "chunk", "tenant_document_adoption", "kb_snapshot", "kb_snapshot_item",
    "document_slot", "ingest_job", "document_review", "retrieval_test_run", "pack_version", "readiness_run", "sign_off",
    "party", "medicine", "licence", "delegation", "case", "conflict", "reliance_basis", "case_sequence", "dossier_document",
    "dossier_page", "dossier_chunk", "pipeline_run", "check_result", "check_trace", "agent_run", "waiver", "confirmation", "fact",
    "recommendation_version", "spot_check", "review_thread", "review_comment", "question", "assistant_session", "promotion",
    "decision", "decision_signer", "proposal", "connection", "watch_source", "foreign_notice", "foreign_notice_revision",
    "watch_match", "watch_incident", "notification_delivery", "gap_signal", "gap", "gap_analysis", "scenario", "simulation",
    "simulation_comparison", "agent_health", "usage_report",
]
# Seed files that land in a table with another name.
EVENT_FILES = {"timeline_event": (TIMELINE, m.TimelineEvent, "case_id"), "notification_event": (NOTIFICATIONS, m.Notification, "recipient")}
# Tables with an immutability trigger on TRUNCATE or a user trigger that a reset has to step around.
GUARDED = ("audit_event", "chunk", "kb_snapshot_item")


class SeedError(Exception):
    pass


def resolve(value: Any, now: datetime) -> Any:
    """Turns {"$t": s} into a time and {"$d": n} into a date, relative to now."""
    if isinstance(value, dict):
        if set(value) == {"$t"}:
            return (now + timedelta(seconds=value["$t"])).astimezone(timezone.utc).isoformat()
        if set(value) == {"$d"}:
            return (now.astimezone(timezone.utc).date() + timedelta(days=value["$d"])).isoformat()
        return {k: resolve(v, now) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, now) for v in value]
    return value


def read(name: str, now: datetime) -> list[dict]:
    path = SEED_DIR / f"{name}.json"
    if not path.exists():
        raise SeedError(f"missing seed file {path}")
    return resolve(json.loads(path.read_text()), now)


def manifest() -> dict:
    return json.loads((SEED_DIR / "manifest.json").read_text())


def _ctx(db: AsyncSession, tenant_id: Optional[str]):
    return SimpleNamespace(db=db, tenant=Tenant(tenant_id or "", "simulation"))


async def seed_demo_people(db: AsyncSession, tenant_id: str, now: datetime) -> None:
    """The demo people and their grants. Safe to run twice."""
    for i, row in enumerate(read("person", now)):
        try:
            person = m.Person.model_validate(row["doc"])
        except ValidationError as e:
            raise SeedError(f"person.json row {i}: {e}") from e
        await db.execute(insert(PersonRow).values(
            id=person.id, tenant_id=tenant_id, display_name=person.display_name, view=row["view"], title=row.get("title"),
            is_demo=person.is_demo, created_at=now,
        ).on_conflict_do_nothing())
        for g in person.grants:
            await db.execute(insert(RoleGrantRow).values(**g.model_dump()).on_conflict_do_nothing())


async def seed_reference_pages(db: AsyncSession, now: datetime) -> int:
    """A first page for each reference document a reliance record names, so the approval can be read. Synthetic."""
    added = 0
    for row in read("reliance_basis", now):
        d = row["doc"]
        for ref in d["document_refs"]:
            text = f"Marketing authorisation {d['approval_ref']}, granted by {d['regulator']} on {d['approved_on']}. This is a synthetic summary of reference document {ref}."
            db.add(ROW["reference_page"](id=f"{ref}:v1:p1", tenant_id=row["tenant_id"], basis_id=d["id"], document_id=ref, version="v1", page=1, text=text))
            added += 1
    await db.flush()
    return added


async def truncate_all(db: AsyncSession, keep: tuple[str, ...] = ()) -> None:
    """Empties every table, including the ones that refuse deletes, except those in `keep`. For a reset or a test, never a running system."""
    for table in GUARDED:
        await db.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER USER"))
    names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name not in keep)
    await db.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    for table in GUARDED:
        await db.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER USER"))


UNVALIDATED: list[str] = []


def _draft(db: AsyncSession, store, row: dict) -> bool:
    """A draft pack may hold a document with errors, because the setup tracker shows them. The rest of the row must still fit."""
    try:
        head = m.PackVersionSummary.model_validate({k: v for k, v in row["doc"].items() if k != "document"})
    except ValidationError:
        return False
    db.add(ROW["pack_version"](id=head.id, tenant_id=row["tenant_id"], doc=row["doc"], **store.columns(head)))
    UNVALIDATED.append(f"pack_version {head.id}: draft document has errors (kept as is)")
    return True


async def load_files(db: AsyncSession, now: datetime) -> dict[str, int]:
    """Loads every file. Returns the rows written per file."""
    counts: dict[str, int] = {}
    await seed_demo_people(db, "ws-ae", now)
    counts["person"] = len(read("person", now))
    await db.flush()

    async def load(name: str, store) -> None:
        rows = read(name, now)
        for i, row in enumerate(rows):
            extras = {k: v for k, v in row.items() if k not in ("tenant_id", "doc", "id")}
            try:
                if store is None:  # a plain row with no doc
                    db.add(ROW[name](tenant_id=row["tenant_id"], **{k: v for k, v in row.items() if k != "tenant_id"}))
                else:
                    store.add(_ctx(db, row.get("tenant_id")), row["doc"], id=row.get("id"), **extras)
            except ValidationError as e:
                if name == "pack_version" and row["doc"].get("state") == "draft" and _draft(db, store, row):
                    continue
                raise SeedError(f"{name}.json row {i} ({row.get('id') or row['doc'].get('id')}): {e}") from e
        await db.flush()
        counts[name] = len(rows)

    for name in ORDER:
        await load(name, STORES.get(name))
    for name, (store, model, _) in EVENT_FILES.items():
        # Events take their doc as the model, so they validate the same way.
        rows = read(name, now)
        for i, row in enumerate(rows):
            extras = {k: v for k, v in row.items() if k not in ("tenant_id", "doc", "id")}
            try:
                store.add(_ctx(db, row["tenant_id"]), model.model_validate(row["doc"]), **extras)
            except ValidationError as e:
                raise SeedError(f"{name}.json row {i}: {e}") from e
        await db.flush()
        counts[name] = len(rows)

    counts["reference_page"] = await seed_reference_pages(db, now)

    rows = read("audit_event", now)
    by_tenant: dict[str, list] = {}
    for i, row in enumerate(rows):
        d = row["doc"]
        try:
            m.AuditEvent.model_validate({**d, "row_hash": "sha256:0000"})
        except ValidationError as e:
            raise SeedError(f"audit_event.json row {i}: {e}") from e
        fields = {k: d[k] for k in audit.AuditEntry.__dataclass_fields__}
        fields["at"] = datetime.fromisoformat(fields["at"])
        by_tenant.setdefault(row["tenant_id"], []).append(audit.AuditEntry(**fields))
    for tenant_id, entries in by_tenant.items():
        await audit.write(db, tenant_id, entries)
    counts["audit_event"] = len(rows)
    return counts


async def write_demo_state(db: AsyncSession, now: datetime, with_library: bool) -> None:
    """Records the load. A reset that is running keeps its `resetting` flag until the reset itself clears it."""
    values = {"loaded_at": now, "with_library": with_library}
    await db.execute(insert(DemoStateRow).values(id=1, resetting=False, **values).on_conflict_do_update(index_elements=["id"], set_=values))


async def set_resetting(sessions, resetting: bool) -> None:
    """Sets the flag in its own transaction, so a reset's progress is visible to other requests."""
    async with sessions() as db, db.begin():
        await db.execute(update(DemoStateRow).values(resetting=resetting))


async def load_seed(engine: AsyncEngine, settings: Settings, *, with_library: bool, reset: bool = False, say: Callable[[str], None] = lambda _: None) -> dict[str, int]:
    """The whole load, for the command line and for the demo reset.

    The files, and in simulation the recorded dossier and the chunk embeddings, go in one transaction: it all lands or none of it does.
    A reset empties the tables in that transaction, except demo_state, so a running reset stays visible. The library is added after it commits.
    Returns the rows written per file.
    """
    UNVALIDATED.clear()
    now = datetime.now(timezone.utc)
    async with make_sessions(engine)() as db, db.begin():
        if reset:
            await truncate_all(db, keep=("demo_state",))
        counts = await load_files(db, now)
        if settings.env == "simulation":
            from .knowledge.embedding import get_embedder
            from .knowledge.indexing import embed_missing
            from .pipeline.replay import load_recorded_dossier

            counts["replay_dossier"] = await load_recorded_dossier(db, "ws-ae")
            # The demo chunks are searchable from the start.
            counts["chunk_embedding"] = await embed_missing(db, get_embedder(settings))
    say(f"{len(counts)} files, {sum(counts.values())} rows loaded.")
    for note in UNVALIDATED:
        say(f"  not fully validated: {note}")
    if with_library:
        if settings.env != "simulation":
            raise SeedError("The library seed needs PHARMALIGN_ENV=simulation.")
        from .seed_library import load_library

        await load_library(settings)
    async with make_sessions(engine)() as db, db.begin():
        await write_demo_state(db, now, with_library)
    return counts


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="app.seed", description="Load the demo data into an empty database.")
    parser.add_argument("--reset", action="store_true", help="empty every table first (not allowed when PHARMALIGN_ENV is live)")
    parser.add_argument("--with-library", action="store_true", help="then add the real text of the core documents found in knowledge/documents (simulation only)")
    args = parser.parse_args(argv)
    settings = Settings()
    if args.reset and settings.env == "live":
        print("Refusing to reset: set PHARMALIGN_ENV=simulation.", file=sys.stderr)
        return 2
    if args.with_library and settings.env != "simulation":
        print("The library seed needs PHARMALIGN_ENV=simulation.", file=sys.stderr)
        return 2
    engine = make_engine(settings.database_url)
    try:
        await load_seed(engine, settings, with_library=args.with_library, reset=args.reset, say=print)
    except SeedError as e:
        print(f"Seed failed: {e}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
