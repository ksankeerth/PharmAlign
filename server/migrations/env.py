"""Runs migrations against PHARMALIGN_DATABASE_URL, or the URL set on the Alembic config."""
import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db import Base
from app import tables  # noqa: F401  registers the tables on Base.metadata

target_metadata = Base.metadata
url = context.config.get_main_option("sqlalchemy.url") or Settings().database_url


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _online() -> None:
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_online())
