"""Async Postgres connection pool for the worker process."""

from __future__ import annotations

from psycopg_pool import AsyncConnectionPool

from personal_agent.settings import Settings

# Bounds a readiness probe so a stalled database cannot hang it.
READINESS_TIMEOUT_SECONDS = 2.0


def create_pool(settings: Settings) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        str(settings.database_url),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        open=False,
    )


async def database_ready(pool: AsyncConnectionPool) -> bool:
    try:
        async with pool.connection(timeout=READINESS_TIMEOUT_SECONDS) as connection:
            await connection.execute("SELECT 1")
    except Exception:
        return False
    return True
