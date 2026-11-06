"""Nobody accepts a document version they added."""
import pytest

from .conftest import failed, headers

pytestmark = pytest.mark.seeded
VERSION = "G-S05-v1"  # added by p-priya


async def test_the_uploader_cannot_accept_their_own_document(http):
    refused = await http.post(f"/api/v1/document-versions/{VERSION}/review", json={"decision": "approved"}, headers=headers("p-priya"))
    assert failed(refused) == {"not_own_upload"}
    assert "Another person must review it" in refused.text


async def test_a_second_person_can_accept_it(http, engine):
    accepted = await http.post(f"/api/v1/document-versions/{VERSION}/review", json={"decision": "approved"}, headers=headers("p-sam"))
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["reviewed_by"] == "p-sam"
    from sqlalchemy import select
    from app.db import make_sessions
    from app.tables import ROW
    async with make_sessions(engine)() as db:
        assert (await db.scalar(select(ROW["document_version"].review_status).where(ROW["document_version"].id == VERSION))) == "approved"


async def test_a_failed_quality_gate_cannot_be_accepted(http):
    refused = await http.post("/api/v1/document-versions/B-S17-v1/review", json={"decision": "approved"}, headers=headers("p-sam"))
    assert "quality_gate_passed" in failed(refused)
