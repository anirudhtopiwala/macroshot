"""Tests for src/db_pool.py - connection pool."""

import asyncio

import pytest
import pytest_asyncio

import aiosqlite

from src.db_pool import ConnectionPool, get_db, init_pool, close_pool, _pool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def pool(tmp_path):
    """Create a fresh pool for each test, tear down after."""
    db_path = str(tmp_path / "test_pool.db")
    # Create schema so queries work
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        await db.commit()
    p = ConnectionPool(db_path, size=3)
    await p.init()
    yield p
    await p.close()


@pytest_asyncio.fixture
async def db_path(tmp_path):
    """Create a test DB path with schema, no pool."""
    path = str(tmp_path / "test_no_pool.db")
    async with aiosqlite.connect(path) as db:
        await db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# ConnectionPool tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pool_init_creates_connections(pool):
    """Pool should create the requested number of connections."""
    assert len(pool._connections) == 3
    assert pool._queue.qsize() == 3


@pytest.mark.asyncio
async def test_pool_acquire_returns_connection(pool):
    """Acquiring from pool should return a usable connection."""
    async with pool.acquire() as conn:
        row = await (await conn.execute("SELECT 1 AS x")).fetchone()
        assert row["x"] == 1
    # Connection returned to pool
    assert pool._queue.qsize() == 3


@pytest.mark.asyncio
async def test_pool_acquire_reduces_available(pool):
    """While a connection is checked out, pool size decreases."""
    async with pool.acquire() as _conn:
        assert pool._queue.qsize() == 2


@pytest.mark.asyncio
async def test_pool_pragmas_applied(pool):
    """PRAGMAs should be applied on pool connections."""
    async with pool.acquire() as conn:
        # WAL mode
        row = await (await conn.execute("PRAGMA journal_mode")).fetchone()
        assert row[0] == "wal"
        # synchronous = NORMAL (1)
        row = await (await conn.execute("PRAGMA synchronous")).fetchone()
        assert row[0] == 1
        # foreign_keys = ON
        row = await (await conn.execute("PRAGMA foreign_keys")).fetchone()
        assert row[0] == 1
        # cache_size = 5000 (negative value in some SQLite versions)
        row = await (await conn.execute("PRAGMA cache_size")).fetchone()
        assert abs(row[0]) >= 5000 or row[0] == -5000
        # temp_store = MEMORY (2)
        row = await (await conn.execute("PRAGMA temp_store")).fetchone()
        assert row[0] == 2


@pytest.mark.asyncio
async def test_pool_row_factory_set(pool):
    """Pool connections should have row_factory = aiosqlite.Row."""
    async with pool.acquire() as conn:
        assert conn.row_factory == aiosqlite.Row


@pytest.mark.asyncio
async def test_pool_rollback_on_uncommitted(pool):
    """Uncommitted transactions should be rolled back when connection is returned."""
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO test (val) VALUES (?)", ("rollback_me",))
        # Don't commit - should be rolled back on release

    # Verify the row was NOT persisted
    async with pool.acquire() as conn:
        row = await (await conn.execute("SELECT COUNT(*) FROM test")).fetchone()
        assert row[0] == 0


@pytest.mark.asyncio
async def test_pool_committed_data_persists(pool):
    """Committed data should persist across acquire calls."""
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO test (val) VALUES (?)", ("keep_me",))
        await conn.commit()

    async with pool.acquire() as conn:
        row = await (await conn.execute("SELECT val FROM test")).fetchone()
        assert row["val"] == "keep_me"


@pytest.mark.asyncio
async def test_pool_concurrent_access(pool):
    """Multiple concurrent checkouts should work (up to pool size)."""
    results = []

    async def worker(i: int):
        async with pool.acquire() as conn:
            await conn.execute("INSERT INTO test (val) VALUES (?)", (f"worker_{i}",))
            await conn.commit()
            results.append(i)

    # Run 3 workers concurrently (pool size = 3, so all get a connection)
    await asyncio.gather(worker(0), worker(1), worker(2))
    assert sorted(results) == [0, 1, 2]

    # Verify all rows inserted
    async with pool.acquire() as conn:
        row = await (await conn.execute("SELECT COUNT(*) FROM test")).fetchone()
        assert row[0] == 3


@pytest.mark.asyncio
async def test_pool_blocks_when_exhausted(pool):
    """When all connections are checked out, acquire should block until one is returned."""
    event = asyncio.Event()
    acquired_count = 0

    async def hold_connection():
        nonlocal acquired_count
        async with pool.acquire():
            acquired_count += 1
            await event.wait()  # Hold until released

    async def wait_for_connection():
        nonlocal acquired_count
        async with pool.acquire():
            acquired_count += 1

    # Hold all 3 connections
    tasks = [asyncio.create_task(hold_connection()) for _ in range(3)]
    await asyncio.sleep(0.05)  # Let them acquire
    assert acquired_count == 3

    # 4th connection should block
    waiter = asyncio.create_task(wait_for_connection())
    await asyncio.sleep(0.05)
    assert acquired_count == 3  # Still waiting

    # Release all held connections
    event.set()
    await asyncio.gather(*tasks)
    await waiter
    assert acquired_count == 4  # Waiter got through


@pytest.mark.asyncio
async def test_pool_close(pool):
    """Closing the pool should close all connections."""
    await pool.close()
    assert len(pool._connections) == 0


# ---------------------------------------------------------------------------
# get_db tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_db_fallback_without_pool(db_path):
    """When no pool is initialized, get_db should fall back to direct connection."""
    # Make sure global pool is not set for this path
    async with get_db(db_path) as conn:
        row = await (await conn.execute("SELECT 1 AS x")).fetchone()
        assert row["x"] == 1


@pytest.mark.asyncio
async def test_get_db_fallback_applies_pragmas(db_path):
    """Fallback connections should also have PRAGMAs applied."""
    async with get_db(db_path) as conn:
        row = await (await conn.execute("PRAGMA synchronous")).fetchone()
        assert row[0] == 1  # NORMAL
        row = await (await conn.execute("PRAGMA foreign_keys")).fetchone()
        assert row[0] == 1


@pytest.mark.asyncio
async def test_get_db_fallback_has_row_factory(db_path):
    """Fallback connections should have row_factory set."""
    async with get_db(db_path) as conn:
        assert conn.row_factory == aiosqlite.Row


# ---------------------------------------------------------------------------
# init_pool / close_pool module-level tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_pool_and_close(tmp_path):
    """init_pool should create a global pool, close_pool should tear it down."""
    db_path = str(tmp_path / "test_global.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        await db.commit()

    pool = await init_pool(db_path, size=2)
    try:
        assert pool is not None
        assert pool._queue.qsize() == 2

        # get_db should use the pool
        async with get_db(db_path) as conn:
            row = await (await conn.execute("SELECT 1")).fetchone()
            assert row[0] == 1
    finally:
        await close_pool()


@pytest.mark.asyncio
async def test_init_pool_replaces_existing(tmp_path):
    """Calling init_pool twice should close the old pool."""
    db_path = str(tmp_path / "test_replace.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        await db.commit()

    pool1 = await init_pool(db_path, size=2)
    pool2 = await init_pool(db_path, size=3)
    try:
        # Old pool should be closed
        assert len(pool1._connections) == 0
        assert pool2._queue.qsize() == 3
    finally:
        await close_pool()
