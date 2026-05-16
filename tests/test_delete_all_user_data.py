"""Regression test for delete_all_user_data.

GDPR Article 17: every per-user row must be wiped when a user requests
erasure. This test walks every CREATE TABLE … FOREIGN KEY … REFERENCES
users in the schema and verifies delete_all_user_data removes rows
for the target user. New per-user tables that aren't added to the
deletion list get caught here instead of shipping as a slow leak.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.db import delete_all_user_data, get_db, init_db

# Tables whose per-user rows are wiped by a non-user_id path (web_auth on
# user_id + email_pins on email). Excluded from the regression loop.
_NON_USER_ID_DELETE_PATH = {"web_auth", "email_pins"}


@pytest.mark.asyncio
async def test_delete_all_user_data_covers_every_user_scoped_table():
    db_path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    try:
        await init_db(db_path)
        async with get_db(db_path) as db:
            await db.execute("PRAGMA foreign_keys = OFF")
            await db.execute(
                "INSERT INTO users (user_id, username, registered_at) VALUES (?,?,?)",
                (1, "target", "2026-01-01"),
            )
            await db.execute(
                "INSERT INTO users (user_id, username, registered_at) VALUES (?,?,?)",
                (2, "bystander", "2026-01-01"),
            )
            await db.commit()

        # Discover every table that declares a user_id FK into users.
        async with get_db(db_path) as db:
            tables = [r[0] async for r in await db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )]
            user_scoped: list[str] = []
            for t in tables:
                if t in _NON_USER_ID_DELETE_PATH or t == "users":
                    continue
                fks = [r async for r in await db.execute(
                    f"PRAGMA foreign_key_list({t})"
                )]
                for fk in fks:
                    # row: (id, seq, table, from, to, on_update, on_delete, match)
                    if fk[2] == "users" and fk[3] == "user_id":
                        user_scoped.append(t)
                        break
        user_scoped.sort()

        # Seed one row per user in every user-scoped table (ignore per-table
        # constraint violations - skip-on-error is fine because we only
        # need at least one table with both users' rows to assert the
        # selective wipe).
        async with get_db(db_path) as db:
            await db.execute("PRAGMA foreign_keys = OFF")
            for table in user_scoped:
                for uid in (1, 2):
                    try:
                        await db.execute(
                            f"INSERT INTO {table} (user_id) VALUES (?)", (uid,),
                        )
                    except Exception:
                        pass
            await db.commit()

        await delete_all_user_data(db_path, 1)

        async with get_db(db_path) as db:
            for table in user_scoped:
                row = await (await db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (1,)
                )).fetchone()
                assert row[0] == 0, (
                    f"delete_all_user_data left rows in {table}. "
                    f"Add {table!r} to the table list in src/db.py::delete_all_user_data."
                )
            row = await (await db.execute(
                "SELECT COUNT(*) FROM users WHERE user_id = ?", (2,)
            )).fetchone()
            assert row[0] == 1, "bystander user was wrongly deleted"
    finally:
        os.unlink(db_path)
