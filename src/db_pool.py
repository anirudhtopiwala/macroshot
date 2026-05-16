"""Async SQLite connection pool.

Manages a fixed set of reusable aiosqlite connections with PRAGMAs applied
once at creation time. This avoids the overhead of opening/closing a new
connection (and re-running PRAGMAs) for every DB call.

Usage:
    # At startup:
    await init_pool("/path/to/db.sqlite", size=4)

    # In DB functions (via get_db):
    async with get_db(db_path) as db:
        row = await (await db.execute("SELECT ...")).fetchone()

    # At shutdown:
    await close_pool()

If the pool is not initialized (tests, MCP subprocess, etc.), get_db()
falls back to a direct aiosqlite.connect() with PRAGMAs applied per-connection.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import aiosqlite

logger = logging.getLogger("macro_app")

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA cache_size = 5000",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA mmap_size = 10000000",
    "PRAGMA busy_timeout = 5000",
)


class ConnectionPool:
    """Fixed-size pool of aiosqlite connections."""

    def __init__(self, db_path: str, size: int = 4):
        self.db_path = db_path
        self._size = size
        self._queue: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=size)
        self._connections: list[aiosqlite.Connection] = []

    async def init(self) -> None:
        """Create connections, apply PRAGMAs, and add them to the pool."""
        for _ in range(self._size):
            conn = await aiosqlite.connect(self.db_path)
            conn.row_factory = aiosqlite.Row
            for pragma in _PRAGMAS:
                await conn.execute(pragma)
            self._connections.append(conn)
            await self._queue.put(conn)
        logger.info("Connection pool initialized: %d connections to %s", self._size, self.db_path)

    @asynccontextmanager
    async def acquire(self):
        """Check out a connection from the pool.

        The connection is returned to the pool when the context manager exits.
        Any uncommitted transaction is rolled back to prevent state leakage.
        """
        conn = await self._queue.get()
        try:
            yield conn
        finally:
            # Ensure clean state before returning to pool
            if conn.in_transaction:
                await conn.rollback()
            await self._queue.put(conn)

    async def close(self) -> None:
        """Close all connections in the pool."""
        for conn in self._connections:
            try:
                await conn.close()
            except Exception:
                pass
        self._connections.clear()
        logger.info("Connection pool closed")


# ── Module-level singleton ──────────────────────────────────────────

_pool: ConnectionPool | None = None


async def init_pool(db_path: str, size: int = 4) -> ConnectionPool:
    """Create and initialize the global connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
    pool = ConnectionPool(db_path, size)
    await pool.init()
    _pool = pool
    return pool


async def close_pool() -> None:
    """Close the global connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_db(db_path: str):
    """Get a database connection.

    Uses the pool if initialized and db_path matches, otherwise falls back
    to a direct connection with PRAGMAs applied (for tests, MCP server, etc.).
    """
    if _pool is not None and _pool.db_path == db_path:
        async with _pool.acquire() as conn:
            yield conn
    else:
        # Fallback: direct connection with PRAGMAs
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            for pragma in _PRAGMAS:
                await conn.execute(pragma)
            yield conn
