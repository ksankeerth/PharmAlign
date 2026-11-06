"""Versions and chunks are fixed once written, and a changed file makes a new version."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import make_sessions

from .conftest import ADMIN, law


async def test_re_ingest_keeps_old_chunks_and_old_anchors_resolve(lib, engine):
    doc = await lib.register("Medicines law")
    v1 = (await lib.upload(doc, law("storage")))["id"]
    await lib.process(v1)
    before = await lib.chunks(v1)
    assert before and all(c["anchor"].startswith(f"{doc}:v1:c") for c in before)

    v2 = (await lib.upload(doc, law("storage", suffix="Amended in 2026.")))["id"]
    assert v2 == f"{doc}-v2"
    await lib.process(v2)

    after = await lib.chunks(v1)
    assert after == before  # not one chunk of the old version changed
    new = await lib.chunks(v2)
    assert {c["anchor"] for c in new}.isdisjoint(c["anchor"] for c in before)
    async with make_sessions(engine)() as db:
        for c in before:  # each old anchor still resolves, to the text it always had
            assert await db.scalar(text("SELECT text FROM chunk WHERE id = :a"), {"a": c["anchor"]}) == c["text"]
        with pytest.raises(DBAPIError, match="fixed once written"):
            await db.execute(text("DELETE FROM chunk WHERE id = :a"), {"a": before[0]["anchor"]})


async def test_an_identical_file_makes_no_new_version(lib, engine):
    doc = await lib.register("Medicines law")
    data = law("storage")
    first = await lib.upload(doc, data, label="first")
    again = await lib.http.post(f"/api/v1/documents/{doc}/versions", files={"file": ("copy.txt", data)}, data={"version_label": "second"}, headers=lib.as_(ADMIN))
    assert again.status_code == 409 and again.json()["code"] == "duplicate_version"
    detail = (await lib.http.get(f"/api/v1/documents/{doc}", headers=lib.as_(ADMIN))).json()
    assert [v["id"] for v in detail["versions"]] == [first["id"]]


async def test_the_same_file_under_another_document_is_flagged_not_merged(lib):
    one, two = await lib.register("Law one"), await lib.register("Law two")
    a = await lib.upload(one, law("storage"))
    b = await lib.upload(two, law("storage"))
    assert b["id"] != a["id"] and b["duplicate_of"] == a["id"]


async def test_a_near_duplicate_is_flagged_not_merged(lib):
    one, two = await lib.register("Original law"), await lib.register("Copy with a small change")
    first = (await lib.upload(one, law("pharmacy records", articles=6)))["id"]
    await lib.ingest(first, "trial")
    second = (await lib.upload(two, law("pharmacy records", articles=6).replace(b"five years", b"six years", 1)))["id"]
    await lib.ingest(second, "trial")
    check = next(c for c in (await lib.quality(second))["checks"] if c["key"] == "duplicate_text")
    assert check["status"] == "attention" and first in check["detail"] and "not merged" in check["detail"]
    version = (await lib.http.get(f"/api/v1/document-versions/{second}", headers=lib.as_(ADMIN))).json()
    assert version["duplicate_of"] == first
    assert (await lib.http.get(f"/api/v1/document-versions/{first}", headers=lib.as_(ADMIN))).json()["duplicate_of"] is None
