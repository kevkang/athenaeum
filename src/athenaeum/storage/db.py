from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from athenaeum.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _ensure_engine() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the session factory; initialises the engine if needed.

    Use this in contexts where the FastAPI Depends() generator cannot be used
    (e.g. WebSocket handlers, background tasks that need their own session).
    """
    return _ensure_engine()


async def get_session() -> AsyncIterator[AsyncSession]:
    maker = _ensure_engine()
    async with maker() as session:
        yield session
