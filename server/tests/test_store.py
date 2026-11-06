"""Typed columns plus a validated doc, and pagination."""
import pytest
from pharmalign_contracts.server.models import TimelineEvent
from pydantic import ValidationError
from sqlalchemy import select

from app.context import NOTIFICATIONS, TIMELINE, Ctx, Tenant
from app.identity import Actor
from app.paging import paginate, paginate_select
from app.problems import ApiError
from app.tables import DomainEventRow

from .conftest import START, TENANT, system_ctx


def event(id="te-1", kind="made", summary="Made") -> TimelineEvent:
    return TimelineEvent(id=id, at=START, kind=kind, summary=summary, actor_type="system", actor_id="x")


@pytest.fixture
def rt(make_app):
    return make_app().state.rt


async def test_a_doc_round_trips_with_its_columns(rt):
    async with rt.sessions() as db, db.begin():
        TIMELINE.add(system_ctx(rt, db), event(), case_id="c-1")
    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        assert await TIMELINE.require(ctx, "te-1") == event()
        row = await db.get(DomainEventRow, "te-1")
    assert (row.type, row.case_id, row.at) == ("timeline", "c-1", START)


async def test_a_doc_is_validated_going_in(rt):
    async with rt.sessions() as db:
        with pytest.raises(ValidationError):
            TIMELINE.add(system_ctx(rt, db), {"id": "te-1", "kind": "Not A Slug"})


async def test_a_doc_is_validated_coming_out(rt):
    async with rt.sessions() as db, db.begin():
        db.add(DomainEventRow(id="te-bad", tenant_id=TENANT, type="timeline", at=START, doc={"id": "te-bad"}))
    async with rt.sessions() as db:
        with pytest.raises(ValidationError):
            await TIMELINE.get(system_ctx(rt, db), "te-bad")


async def test_save_updates_doc_and_columns(rt):
    async with rt.sessions() as db, db.begin():
        TIMELINE.add(system_ctx(rt, db), event(), case_id="c-1")
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        doc = await TIMELINE.require(ctx, "te-1", for_update=True)
        doc.summary = "Changed"
        doc.at = START.replace(year=2027)
        await TIMELINE.save(ctx, doc)
    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        assert (await TIMELINE.require(ctx, "te-1")).summary == "Changed"
        assert (await db.get(DomainEventRow, "te-1")).at.year == 2027


async def test_stores_are_scoped_to_the_tenant_and_type(rt):
    async with rt.sessions() as db, db.begin():
        TIMELINE.add(system_ctx(rt, db), event())
    async with rt.sessions() as db:
        other = Ctx(rt, db, Actor(type="system", id="x", tenant_id="ws-other"), Tenant("ws-other", "simulation"))
        assert await TIMELINE.get(other, "te-1") is None
        with pytest.raises(ApiError) as refused:
            await TIMELINE.require(other, "te-1", "Timeline event")
        assert refused.value.body.status == 404
        assert await NOTIFICATIONS.get(system_ctx(rt, db), "te-1") is None


async def test_list_and_page(rt):
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        for i in range(5):
            TIMELINE.add(ctx, event(id=f"te-{i}"), case_id="c-1" if i < 4 else "c-2")
    async with rt.sessions() as db:
        ctx = system_ctx(rt, db)
        assert len(await TIMELINE.list(ctx, DomainEventRow.case_id == "c-2")) == 1
        page = await TIMELINE.page(ctx, {"limit": 3}, DomainEventRow.case_id == "c-1", order_by=DomainEventRow.seq)
        assert [e.id for e in page["items"]] == ["te-0", "te-1", "te-2"]
        last = await TIMELINE.page(ctx, {"limit": 3, "cursor": page["next_cursor"]}, DomainEventRow.case_id == "c-1", order_by=DomainEventRow.seq)
        assert [e.id for e in last["items"]] == ["te-3"]
        assert last["next_cursor"] is None


def test_paginate_a_list():
    items = list(range(5))
    assert paginate(items, {"limit": 2}) == {"items": [0, 1], "next_cursor": "2"}
    assert paginate(items, {"limit": 2, "cursor": "4"}) == {"items": [4], "next_cursor": None}
    assert paginate(items, None)["next_cursor"] is None
    assert len(paginate(list(range(500)), {"limit": 9999})["items"]) == 200
    assert len(paginate(list(range(500)), {})["items"]) == 50
    assert len(paginate(items, {"limit": 0})["items"]) == 1


def test_a_made_up_cursor_is_refused():
    with pytest.raises(ApiError):
        paginate([], {"cursor": "-1"})


async def test_paginate_a_select(rt):
    async with rt.sessions() as db, db.begin():
        ctx = system_ctx(rt, db)
        for i in range(3):
            TIMELINE.add(ctx, event(id=f"te-{i}"))
    async with rt.sessions() as db:
        stmt = select(DomainEventRow.id).order_by(DomainEventRow.seq)
        page = await paginate_select(db, stmt, {"limit": 2})
    assert page == {"items": ["te-0", "te-1"], "next_cursor": "2"}
