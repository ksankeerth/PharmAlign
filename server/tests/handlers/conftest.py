"""The handler tests run on the full demo seed, loaded once per module. A module's tests use different cases, so none depends on another."""
import json
import re

import pytest_asyncio
from sqlalchemy import func, select

from app.db import make_sessions
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files, truncate_all
from app.tables import AuditEventRow, ROW

from ..conftest import START, TENANT, as_person, client_for


@pytest_asyncio.fixture(scope="module", autouse=True)
async def seeded(engine):
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)


@pytest_asyncio.fixture
async def http(make_app):
    app = make_app()
    async with client_for(app) as client:
        client.app = app
        yield client


def headers(person: str, **extra) -> dict:
    return {**as_person(person), **extra}


def failed(response) -> set[str]:
    assert response.status_code == 409, response.text
    return {g["guard"] for g in response.json()["failed"]}


def parse_sse(text: str) -> list[dict]:
    return [json.loads(m) for m in re.findall(r"^data: (.*)$", text, re.M)]


async def audit_rows(engine, **where) -> int:
    async with make_sessions(engine)() as db:
        stmt = select(func.count()).select_from(AuditEventRow)
        for k, v in where.items():
            stmt = stmt.where(getattr(AuditEventRow, k) == v)
        return await db.scalar(stmt)


async def count_rows(engine, table: str, **where) -> int:
    async with make_sessions(engine)() as db:
        stmt = select(func.count()).select_from(ROW[table])
        for k, v in where.items():
            stmt = stmt.where(getattr(ROW[table], k) == v)
        return await db.scalar(stmt)
