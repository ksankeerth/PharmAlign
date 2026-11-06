"""Each audit test starts from a fresh full seed, since some of them tamper with the log."""
import pytest
import pytest_asyncio

from app.db import make_sessions
from app.pipeline.replay import load_recorded_dossier
from app.seed import load_files, truncate_all

from ..conftest import START, TENANT
from ..handlers.conftest import http  # noqa: F401  the shared client fixture

pytestmark = pytest.mark.seeded


@pytest_asyncio.fixture(autouse=True)
async def seeded(engine):
    async with make_sessions(engine)() as db, db.begin():
        await truncate_all(db)
        await load_files(db, START)
        await load_recorded_dossier(db, TENANT)
