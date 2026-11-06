"""The regulator records API (contracts/pharmalign_contracts/regulator.py), served for simulation.

It deduplicates by idempotency key and misbehaves on request, so retries can be tested. State lives in its own tables.
"""
import asyncio
import hashlib
import json
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pharmalign_contracts.common import Health, Problem
from pharmalign_contracts.regulator import FailureMode, ProposalCreate, ProposalStatus, RecordEvent, RecordLicence, ReviewMetrics
from pharmalign_contracts.server.models import Page
from pydantic import AwareDatetime
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from ..context import Runtime
from ..problems import MEDIA_TYPE
from .tables import MockEventRow, MockLicenceRow, MockProposalRow, MockSettingRow

ONE_SHOT = ("applied_but_timed_out", "reject_next", "timeout_before_applying")
LICENCE_STATUS = {"grant": "licensed", "suspend": "suspended", "revoke": "revoked"}
EVENT_OF = {"applied": "proposal_applied", "rejected": "proposal_rejected"}


class ModeSet(FailureMode):
    set_at: AwareDatetime


class Down(Exception):
    pass


def _problem(status: int, code: str, title: str, detail: Optional[str] = None) -> JSONResponse:
    body = Problem(title=title, status=status, code=code, detail=detail)
    return JSONResponse(body.model_dump(mode="json"), status_code=status, media_type=MEDIA_TYPE)


def _status(row: MockProposalRow) -> ProposalStatus:
    return ProposalStatus(
        id=row.id, idempotency_key=row.idempotency_key, state=row.state, gov_ref=row.gov_ref,
        resulting_version=row.resulting_version, reason=row.reason, updated_at=row.updated_at,
    )


def create_mock_app(rt: Runtime) -> FastAPI:
    """Refuses to exist outside simulation."""
    if rt.settings.env != "simulation":
        raise RuntimeError("The mock regulator runs only in simulation.")
    app = FastAPI(title="PharmAlign Mock Regulator", docs_url=None, redoc_url=None, openapi_url=None)
    token = rt.settings.connector_token

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, e: HTTPException):
        return _problem(e.status_code, "unauthorised" if e.status_code == 401 else "not_found", "Refused" if e.status_code == 401 else "Not found")

    @app.exception_handler(Down)
    async def down(request: Request, e: Down):
        return _problem(500, "internal_error", "The simulated regulator is failing")

    async def authed(request: Request) -> None:
        if token and request.headers.get("authorization") != f"Bearer {token}":
            raise HTTPException(401)

    async def mode_now() -> str:
        async with rt.sessions() as db:
            row = await db.get(MockSettingRow, "failure_mode")
        return row.value["mode"] if row else "normal"

    async def served(request: Request) -> None:
        """Authentication, then the faults that apply to every call."""
        await authed(request)
        mode = await mode_now()
        if mode == "unavailable":
            raise Down()
        if mode == "slow":
            await asyncio.sleep(rt.settings.mock_regulator_slow_seconds)

    def event(db, kind: str, object_id: str) -> None:
        db.add(MockEventRow(id=f"ev-{uuid.uuid4().hex[:12]}", at=rt.clock.now(), kind=kind, object_id=object_id))

    async def take(db, mode: str) -> bool:
        """Uses up a one-shot fault. True if it was set."""
        taken = await db.execute(
            update(MockSettingRow).where(MockSettingRow.key == "failure_mode", MockSettingRow.value["mode"].astext == mode)
            .values(value={"mode": "normal"}, set_at=rt.clock.now())
        )
        return taken.rowcount == 1

    # ---------------------------------------------------------------- proposals

    @app.post("/proposals", status_code=202, dependencies=[Depends(served)])
    async def propose(request: Request):
        key = request.headers.get("idempotency-key")
        if not key:
            return _problem(422, "invalid_request", "The request breaks the contract", "Idempotency-Key is required.")
        raw = await request.body()
        try:
            body = ProposalCreate.model_validate_json(raw)
        except ValueError as e:
            return _problem(422, "invalid_request", "The request breaks the contract", str(e)[:300])
        digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        hang = rt.settings.mock_regulator_hang_seconds
        async with rt.sessions() as db, db.begin():
            seen = await db.scalar(select(MockProposalRow.id).where(MockProposalRow.idempotency_key == key))
            silent = seen is None and await take(db, "timeout_before_applying")
        if silent:  # never applied, and the caller never hears
            await asyncio.sleep(hang)
            return _problem(504, "timed_out", "The simulated regulator gave up")
        async with rt.sessions() as db, db.begin():
            now = rt.clock.now()
            created = await db.scalar(
                insert(MockProposalRow).values(
                    id=f"mp-{uuid.uuid4().hex[:12]}", idempotency_key=key, request_hash=digest, body=body.model_dump(mode="json"),
                    state="received", created_at=now, updated_at=now,
                ).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(MockProposalRow.id)
            )
            row = await db.scalar(select(MockProposalRow).where(MockProposalRow.idempotency_key == key).with_for_update())
            if created is None:  # the same key again: the first result, and nothing new applied
                if row.request_hash != digest:
                    return _problem(409, "idempotency_conflict", "This key was used with a different body")
                return JSONResponse(_status(row).model_dump(mode="json"), status_code=202)
            reject = await take(db, "reject_next")
            late = await take(db, "applied_but_timed_out")
            await self_apply(db, row, body, reject)
            result = _status(row).model_dump(mode="json")
        if late:  # applied and saved, and the caller never hears
            await asyncio.sleep(hang)
        return JSONResponse(result, status_code=202)

    async def self_apply(db, row: MockProposalRow, body: ProposalCreate, reject: bool) -> None:
        now = rt.clock.now()
        licence = await db.get(MockLicenceRow, body.licence_id) if body.licence_id else None
        reason = None
        if reject:
            reason = "Rejected by the simulated regulator."
        elif body.based_on_version and licence and licence.doc["version"] != body.based_on_version:
            reason = "The licence changed since this was based on it."
        if reason:
            row.state, row.reason, row.updated_at = "rejected", reason, now
            event(db, EVENT_OF["rejected"], row.id)
            return
        row.gov_ref = f"GOV-MA-2026-{9000 + row.seq:05d}"
        row.resulting_version = f"{row.gov_ref}-v1"
        row.state, row.updated_at = "applied", now
        event(db, EVENT_OF["applied"], row.id)
        if body.action in LICENCE_STATUS:
            changed = licence.doc if licence else None
            payload = body.payload
            doc = RecordLicence(
                id=changed["id"] if changed else f"LIC-{9000 + row.seq}", version=row.resulting_version, status=LICENCE_STATUS[body.action],
                medicine_name=changed["medicine_name"] if changed else payload.get("medicine_name", "Unnamed medicine"),
                substances=changed["substances"] if changed else payload.get("substances", []),
                holder=changed["holder"] if changed else payload.get("holder", "Unnamed holder"), updated_at=now,
            ).model_dump(mode="json")
            if licence:
                licence.doc, licence.updated_at = doc, now
            else:
                db.add(MockLicenceRow(id=doc["id"], doc=doc, updated_at=now))
            event(db, "licence_changed", doc["id"])

    @app.get("/proposals", dependencies=[Depends(served)])
    async def find_proposal(idempotency_key: Optional[str] = None):
        if not idempotency_key:
            return _problem(422, "invalid_request", "The request breaks the contract", "idempotency_key is required.")
        async with rt.sessions() as db:
            row = await db.scalar(select(MockProposalRow).where(MockProposalRow.idempotency_key == idempotency_key))
        return Page[ProposalStatus](items=[_status(row)] if row else [], next_cursor=None).model_dump(mode="json")

    @app.get("/proposals/{proposal_id}", dependencies=[Depends(served)])
    async def get_proposal(proposal_id: str):
        async with rt.sessions() as db:
            row = await db.get(MockProposalRow, proposal_id)
        if row is None:
            return _problem(404, "not_found", "Not found", f"Proposal {proposal_id} does not exist.")
        return _status(row).model_dump(mode="json")

    # ---------------------------------------------------------------- records

    @app.get("/licences", dependencies=[Depends(served)])
    async def list_licences(changed_since: Optional[AwareDatetime] = None, substance: Optional[str] = None, limit: int = 100):
        stmt = select(MockLicenceRow).order_by(MockLicenceRow.id).limit(max(1, min(limit, 200)))
        if changed_since:
            stmt = stmt.where(MockLicenceRow.updated_at > changed_since)
        async with rt.sessions() as db:
            rows = (await db.scalars(stmt)).all()
        items = [RecordLicence.model_validate(r.doc) for r in rows if not substance or substance.lower() in [s.lower() for s in r.doc["substances"]]]
        return Page[RecordLicence](items=items, next_cursor=None).model_dump(mode="json")

    @app.get("/licences/{licence_id}", dependencies=[Depends(served)])
    async def get_licence(licence_id: str):
        async with rt.sessions() as db:
            row = await db.get(MockLicenceRow, licence_id)
        if row is None:
            return _problem(404, "not_found", "Not found", f"Licence {licence_id} does not exist.")
        return row.doc

    @app.get("/submissions", dependencies=[Depends(served)])
    async def list_submissions():
        return {"items": [], "next_cursor": None}  # the mock holds no submissions

    @app.get("/submissions/{submission_id}", dependencies=[Depends(served)])
    @app.get("/submissions/{submission_id}/documents/{document_id}", dependencies=[Depends(served)])
    async def no_submission(submission_id: str, document_id: Optional[str] = None):
        return _problem(404, "not_found", "Not found", f"Submission {submission_id} does not exist.")

    @app.get("/events", dependencies=[Depends(served)])
    async def events(since: AwareDatetime, limit: int = 100):
        async with rt.sessions() as db:
            rows = (await db.scalars(select(MockEventRow).where(MockEventRow.at > since).order_by(MockEventRow.seq).limit(max(1, min(limit, 200))))).all()
        items = [RecordEvent(id=r.id, at=r.at, kind=r.kind, object_id=r.object_id) for r in rows]
        return Page[RecordEvent](items=items, next_cursor=None).model_dump(mode="json")

    @app.get("/metrics/review", dependencies=[Depends(served)])
    async def metrics(request: Request):
        q = request.query_params
        try:
            period_from, period_to = q["from"], q["to"]
            async with rt.sessions() as db:
                applied = await db.scalar(select(func.count()).select_from(MockProposalRow).where(MockProposalRow.state == "applied"))
            return ReviewMetrics(period_from=period_from, period_to=period_to, submissions_received=0, decisions_recorded=applied).model_dump(mode="json")
        except (KeyError, ValueError):
            return _problem(422, "invalid_request", "The request breaks the contract", "from and to are required dates.")

    # ---------------------------------------------------------------- test controls and health

    @app.post("/test-controls/failure-mode", dependencies=[Depends(authed)])
    async def set_failure_mode(body: FailureMode):
        now = rt.clock.now()
        async with rt.sessions() as db, db.begin():
            await db.execute(
                insert(MockSettingRow).values(key="failure_mode", value={"mode": body.mode}, set_at=now)
                .on_conflict_do_update(index_elements=["key"], set_={"value": {"mode": body.mode}, "set_at": now})
            )
        return ModeSet(mode=body.mode, set_at=now).model_dump(mode="json")

    @app.get("/health")
    async def health():
        return Health(status="ok").model_dump()

    return app
