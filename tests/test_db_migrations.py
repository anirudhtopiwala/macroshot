"""Regression tests for the versioned-migration upgrade path in src/db.py.

These tests construct an "old" SQLite DB that lacks recent columns and
schema_version, then call init_db() and assert the migrations upgrade it
without crashing. They guard against the kind of regression where a DDL
fragment in `_DDL` references a column that's only added by a later
versioned migration — that pattern works on a fresh install (the `_DDL`
runs once with all columns present) but crashes on an existing prod DB
because `_DDL` runs BEFORE the versioned migrations.
"""

import aiosqlite
import pytest

from src import db as db_mod


# Minimal v1-shape DDL: just enough tables for init_db() / the
# email-normalization migration to operate on. We deliberately omit the
# email_canonical column from web_auth so that running v3 + v12 on this
# DB exercises the column-add + index-create path.
_V1_DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    registered_at TEXT
);

CREATE TABLE IF NOT EXISTS web_auth (
    user_id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    google_sub TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id INTEGER PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    breakfast_hour INTEGER NOT NULL DEFAULT 8,
    lunch_hour INTEGER NOT NULL DEFAULT 11,
    snack_hour INTEGER NOT NULL DEFAULT 15,
    dinner_hour INTEGER NOT NULL DEFAULT 19,
    streak_alert_hour INTEGER NOT NULL DEFAULT 22,
    reminders_on INTEGER NOT NULL DEFAULT 1,
    meals_public INTEGER NOT NULL DEFAULT 0,
    units_system TEXT NOT NULL DEFAULT 'metric',
    gamification TEXT NOT NULL DEFAULT 'full',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
"""


async def _build_v1_db(path: str) -> None:
    """Construct a pre-v3 shape DB with one row in web_auth (no canonical)."""
    async with aiosqlite.connect(path) as conn:
        await conn.executescript(_V1_DDL)
        await conn.execute(
            "INSERT INTO web_auth (user_id, email, google_sub, created_at) "
            "VALUES (?, ?, ?, ?)",
            (1, "Alice@Example.com", None, "2026-01-01 00:00:00"),
        )
        await conn.execute(
            "INSERT INTO users (user_id, username, first_name, registered_at) "
            "VALUES (?, ?, ?, ?)",
            (1, "alice", "Alice", "2026-01-01 00:00:00"),
        )
        await conn.execute(
            "INSERT INTO user_prefs (user_id, updated_at) VALUES (?, ?)",
            (1, "2026-01-01 00:00:00"),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_init_db_upgrades_old_shape_to_current(tmp_path):
    """init_db on a v1-shape DB should add email_canonical, backfill it,
    create the UNIQUE index (without crashing), and add the new prefs
    columns. This is the regression test for the 'CREATE UNIQUE INDEX
    in _DDL' bug — that line referenced email_canonical before v3
    added the column on existing DBs."""
    path = str(tmp_path / "old.db")
    await _build_v1_db(path)

    # If the regression is reintroduced, init_db raises
    # OperationalError("no such column: email_canonical") here.
    await db_mod.init_db(path)

    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row

        # email_canonical column was added (v3) and backfilled.
        cols = [r[1] for r in await (await conn.execute("PRAGMA table_info(web_auth)")).fetchall()]
        assert "email_canonical" in cols

        # Email was lowercased and email_canonical filled.
        row = await (await conn.execute("SELECT email, email_canonical FROM web_auth WHERE user_id = 1")).fetchone()
        assert row["email"] == "alice@example.com"
        assert row["email_canonical"] is not None
        assert row["email_canonical"].endswith("@example.com")

        # The UNIQUE index from v12 exists.
        idx = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_web_auth_email_canonical'"
        )).fetchone()
        assert idx is not None

        # The new prefs columns from v13/v14 were added.
        prefs_cols = [r[1] for r in await (await conn.execute("PRAGMA table_info(user_prefs)")).fetchall()]
        assert "ai_web_search_enabled" in prefs_cols
        assert "notif_show_macros" in prefs_cols

        # schema_version reflects the latest applied migration.
        ver = await (await conn.execute("SELECT version FROM schema_version")).fetchone()
        assert ver is not None
        assert ver[0] >= 14


@pytest.mark.asyncio
async def test_init_db_idempotent_on_fresh_install(tmp_path):
    """A fresh init_db run should also leave the new columns + index in
    place (the _DDL has the columns, and the v12 index migration is a
    CREATE...IF NOT EXISTS so it's idempotent)."""
    path = str(tmp_path / "fresh.db")
    await db_mod.init_db(path)
    # Re-run — should be a no-op.
    await db_mod.init_db(path)

    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        prefs_cols = [r[1] for r in await (await conn.execute("PRAGMA table_info(user_prefs)")).fetchall()]
        assert "ai_web_search_enabled" in prefs_cols
        assert "notif_show_macros" in prefs_cols
        idx = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_web_auth_email_canonical'"
        )).fetchone()
        assert idx is not None
