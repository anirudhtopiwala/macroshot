"""Tests for chat_sessions CRUD functions in src/db.py."""

import asyncio
import json

import aiosqlite
import pytest
import pytest_asyncio

from src.db import (
    create_chat_session,
    delete_chat_session,
    ensure_user,
    get_chat_session,
    get_meals_in_range,
    init_db,
    list_chat_sessions,
    log_meal,
    update_chat_session,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    """Fresh DB for each test."""
    path = str(tmp_path / "test.db")
    await init_db(path)
    return path


async def _add_user(db, user_id=1, username="alice", first_name="Alice"):
    await ensure_user(db, user_id, username, first_name)


# ---------------------------------------------------------------------------
# create / get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_get_chat_session(db):
    """Create a chat session and retrieve it by id."""
    await _add_user(db)
    await create_chat_session(db, "sess-1", 1, title="First chat", conversation='[{"role":"user","text":"hi"}]')

    session = await get_chat_session(db, "sess-1", 1)
    assert session is not None
    assert session["id"] == "sess-1"
    assert session["user_id"] == 1
    assert session["title"] == "First chat"
    conv = json.loads(session["conversation"])
    assert len(conv) == 1
    assert conv[0]["role"] == "user"
    assert session["created_at"] is not None
    assert session["updated_at"] is not None


@pytest.mark.asyncio
async def test_get_chat_session_nonexistent(db):
    """Getting a session that doesn't exist returns None."""
    await _add_user(db)
    result = await get_chat_session(db, "does-not-exist", 1)
    assert result is None


@pytest.mark.asyncio
async def test_get_chat_session_wrong_user(db):
    """A session created by user 1 is not visible to user 2."""
    await _add_user(db, 1, "alice", "Alice")
    await _add_user(db, 2, "bob", "Bob")
    await create_chat_session(db, "sess-private", 1, title="Private")

    result = await get_chat_session(db, "sess-private", 2)
    assert result is None


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_chat_session_title(db):
    """Update the title of a chat session."""
    await _add_user(db)
    await create_chat_session(db, "sess-upd", 1, title="Old")

    ok = await update_chat_session(db, "sess-upd", 1, title="New Title")
    assert ok is True

    session = await get_chat_session(db, "sess-upd", 1)
    assert session["title"] == "New Title"


@pytest.mark.asyncio
async def test_update_chat_session_conversation(db):
    """Update the conversation JSON of a chat session."""
    await _add_user(db)
    await create_chat_session(db, "sess-conv", 1)

    new_conv = json.dumps([{"role": "user", "text": "hello"}, {"role": "model", "text": "hi there"}])
    ok = await update_chat_session(db, "sess-conv", 1, conversation=new_conv)
    assert ok is True

    session = await get_chat_session(db, "sess-conv", 1)
    conv = json.loads(session["conversation"])
    assert len(conv) == 2
    assert conv[1]["text"] == "hi there"


@pytest.mark.asyncio
async def test_update_chat_session_user_id_filter(db):
    """Updating a session as the wrong user returns False."""
    await _add_user(db, 1, "alice", "Alice")
    await _add_user(db, 2, "bob", "Bob")
    await create_chat_session(db, "sess-owner", 1, title="Alice's Chat")

    ok = await update_chat_session(db, "sess-owner", 2, title="Hijacked")
    assert ok is False

    # Verify title unchanged
    session = await get_chat_session(db, "sess-owner", 1)
    assert session["title"] == "Alice's Chat"


@pytest.mark.asyncio
async def test_update_chat_session_optimistic_lock(db):
    """Optimistic locking: update succeeds with correct timestamp, fails with stale one."""
    await _add_user(db)
    await create_chat_session(db, "sess-lock", 1, title="V1")

    session = await get_chat_session(db, "sess-lock", 1)
    original_updated_at = session["updated_at"]

    # Manually set updated_at to an older timestamp so the next update produces
    # a genuinely different value (timestamps are second-resolution).
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "UPDATE chat_sessions SET updated_at = '2020-01-01 00:00:00' WHERE id = 'sess-lock'"
        )
        await conn.commit()

    # Re-read to get the backdated timestamp
    session = await get_chat_session(db, "sess-lock", 1)
    old_ts = session["updated_at"]
    assert old_ts == "2020-01-01 00:00:00"

    # Update with correct expected_updated_at should succeed
    ok = await update_chat_session(
        db, "sess-lock", 1,
        title="V2",
        expected_updated_at=old_ts,
    )
    assert ok is True

    # The updated_at should now be different from old_ts
    session = await get_chat_session(db, "sess-lock", 1)
    assert session["updated_at"] != old_ts

    # Second update with the STALE timestamp should fail
    ok2 = await update_chat_session(
        db, "sess-lock", 1,
        title="V3",
        expected_updated_at=old_ts,
    )
    assert ok2 is False

    # Verify title stayed at V2
    session = await get_chat_session(db, "sess-lock", 1)
    assert session["title"] == "V2"


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_chat_sessions_ordering_and_limit(db):
    """Sessions are returned ordered by updated_at DESC, respecting limit."""
    await _add_user(db)

    # Create 5 sessions with distinct, ascending updated_at timestamps
    for i in range(5):
        await create_chat_session(db, f"sess-{i}", 1, title=f"Chat {i}")
        # Backdate each session so they have distinct timestamps
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (f"2026-03-01 00:00:0{i}", f"sess-{i}"),
            )
            await conn.commit()

    # Update sess-2 to a much later timestamp so it becomes the most recent
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "UPDATE chat_sessions SET updated_at = '2026-03-20 00:00:00', title = 'Chat 2 Updated' WHERE id = 'sess-2'"
        )
        await conn.commit()

    sessions = await list_chat_sessions(db, 1, limit=3)
    assert len(sessions) == 3
    # sess-2 was updated most recently, so it should be first
    assert sessions[0]["id"] == "sess-2"
    assert sessions[0]["title"] == "Chat 2 Updated"


@pytest.mark.asyncio
async def test_list_chat_sessions_user_isolation(db):
    """Each user only sees their own sessions."""
    await _add_user(db, 1, "alice", "Alice")
    await _add_user(db, 2, "bob", "Bob")

    await create_chat_session(db, "alice-sess", 1, title="Alice Chat")
    await create_chat_session(db, "bob-sess", 2, title="Bob Chat")

    alice_sessions = await list_chat_sessions(db, 1)
    bob_sessions = await list_chat_sessions(db, 2)

    assert len(alice_sessions) == 1
    assert alice_sessions[0]["id"] == "alice-sess"

    assert len(bob_sessions) == 1
    assert bob_sessions[0]["id"] == "bob-sess"


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_chat_session(db):
    """Deleting a session removes it and returns True."""
    await _add_user(db)
    await create_chat_session(db, "sess-del", 1, title="To Delete")

    ok = await delete_chat_session(db, "sess-del", 1)
    assert ok is True

    # Verify it's gone
    session = await get_chat_session(db, "sess-del", 1)
    assert session is None


@pytest.mark.asyncio
async def test_delete_chat_session_wrong_user(db):
    """Deleting a session as the wrong user returns False."""
    await _add_user(db, 1, "alice", "Alice")
    await _add_user(db, 2, "bob", "Bob")
    await create_chat_session(db, "sess-nodelete", 1, title="Protected")

    ok = await delete_chat_session(db, "sess-nodelete", 2)
    assert ok is False

    # Verify it still exists for the owner
    session = await get_chat_session(db, "sess-nodelete", 1)
    assert session is not None


# ---------------------------------------------------------------------------
# get_meals_in_range
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_meals_in_range(db):
    """Meals within the date range are returned; those outside are excluded."""
    await _add_user(db)

    # Log meals on different days
    await log_meal(db, 1, "2026-03-10 08:00", "Oatmeal", "", 300, 10, 50, 5, "Gemini", "breakfast", "[]")
    await log_meal(db, 1, "2026-03-11 12:00", "Salad", "", 200, 8, 20, 10, "Gemini", "lunch", "[]")
    await log_meal(db, 1, "2026-03-12 19:00", "Pasta", "", 600, 20, 80, 20, "Gemini", "dinner", "[]")
    await log_meal(db, 1, "2026-03-15 12:00", "Sandwich", "", 400, 25, 40, 15, "Gemini", "lunch", "[]")

    # Range that includes only 2 of the 4 meals
    meals = await get_meals_in_range(db, 1, "2026-03-11", "2026-03-12")
    assert len(meals) == 2
    names = {m["item_name"] for m in meals}
    assert names == {"Salad", "Pasta"}

    # Verify ordering is by id DESC (Pasta should come first since it has a higher id)
    assert meals[0]["item_name"] == "Pasta"
    assert meals[1]["item_name"] == "Salad"

    # Empty range
    meals_empty = await get_meals_in_range(db, 1, "2026-04-01", "2026-04-30")
    assert meals_empty == []
