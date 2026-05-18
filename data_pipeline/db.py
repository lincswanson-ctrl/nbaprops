"""Database connection helpers for PostgreSQL."""

from __future__ import annotations

import atexit
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from data_pipeline.config import get_settings

_SYNC_POOL: ConnectionPool | None = None
_ASYNC_POOL: AsyncConnectionPool | None = None


def _conninfo() -> str:
    """Pick the best available DB URL for pooled connections."""

    settings = get_settings()
    # Prefer transaction-safe URL when provided (e.g., Supabase pooler).
    return settings.database_url_transaction or settings.database_url


def _close_sync_pool_at_exit() -> None:
    """Release pool workers before interpreter shutdown (avoids destructor warnings)."""

    global _SYNC_POOL
    if _SYNC_POOL is not None:
        try:
            _SYNC_POOL.close()
        except Exception:
            pass
        _SYNC_POOL = None


atexit.register(_close_sync_pool_at_exit)


def _sync_pool() -> ConnectionPool:
    """Return the process-wide synchronous connection pool."""

    global _SYNC_POOL
    if _SYNC_POOL is None:
        _SYNC_POOL = ConnectionPool(
            conninfo=_conninfo(),
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )
    return _SYNC_POOL


def _async_pool() -> AsyncConnectionPool:
    """Return the process-wide asynchronous connection pool."""

    global _ASYNC_POOL
    if _ASYNC_POOL is None:
        _ASYNC_POOL = AsyncConnectionPool(
            conninfo=_conninfo(),
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=False,
        )
    return _ASYNC_POOL


@contextmanager
def get_connection() -> Iterator[psycopg.Connection]:
    """Yield a pooled sync connection with transactional semantics."""

    with _sync_pool().connection() as connection:
        with connection.transaction():
            yield connection


@asynccontextmanager
async def get_async_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """Yield a pooled async connection with transactional semantics."""

    pool = _async_pool()
    await pool.open(wait=True)
    async with pool.connection() as connection:
        async with connection.transaction():
            yield connection


@contextmanager
def get_server_cursor(
    connection: psycopg.Connection,
    *,
    name: str,
) -> Iterator[psycopg.ServerCursor]:
    """Yield a server-side cursor for long-running reads."""

    with connection.cursor(name=name) as cursor:
        yield cursor
