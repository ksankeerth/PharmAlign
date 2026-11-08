"""Engine, sessions and the declarative base."""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def pool_arguments(pool_max: Optional[int]) -> dict:
    """With a limit, at most that many connections stay open and none are added on top. Without one, SQLAlchemy's defaults (5, and 10 more)."""
    return {} if pool_max is None else {"pool_size": pool_max, "max_overflow": 0}


# asyncpg prepares every statement, and from its sixth use Postgres may keep one generic plan. For the search query that plan is
# many times slower (about 5 s against 1 s on a small instance), so every statement is planned for its own values.
CONNECT_ARGUMENTS = {"server_settings": {"plan_cache_mode": "force_custom_plan"}}


def make_engine(url: str, pool_max: Optional[int] = None) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, connect_args=CONNECT_ARGUMENTS, **pool_arguments(pool_max))


def make_sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
