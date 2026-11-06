"""The chain test keeps its own database and server, so the shared per-test cleaning does not apply."""
import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def clean():
    yield
