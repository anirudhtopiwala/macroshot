"""Tests for the chat API endpoints."""

import asyncio
import json
import os

import pytest
import pytest_asyncio

# Set env before importing app
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from src.db import (
    create_chat_session,
    create_web_user,
    ensure_user,
    get_chat_session,
    init_db,
    update_chat_session,
)
from src.web.app import app
from src.web.deps import get_db_path, get_current_user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Create a temporary SQLite database."""
    path = str(tmp_path / "test.db")
    asyncio.get_event_loop().run_until_complete(init_db(path))
    return path


@pytest.fixture
def override_db(db_path):
    """Override the DB path dependency for FastAPI."""
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield db_path
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(override_db):
    """Create an unauthenticated async HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Requested-With": "MacroApp"}) as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(override_db):
    """Create an authenticated test client with a web user."""
    user_id = await create_web_user(override_db, "testuser@example.com")
    await ensure_user(override_db, user_id, "testuser", "Test")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "testuser@example.com",
            "username": "testuser@example.com",
            "first_name": "testuser",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }

    app.dependency_overrides[get_current_user] = mock_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Requested-With": "MacroApp"}) as c:
        c._user_id = user_id
        yield c

    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API = "/macro_app/api/v1/chat"


async def _create_session(client) -> str:
    """Create a chat session and return its session_id."""
    resp = await client.post(API)
    assert resp.status_code == 200
    return resp.json()["session_id"]


# ---------------------------------------------------------------------------
# Create session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_chat_session(auth_client):
    """POST /chat creates a new session and returns a session_id."""
    resp = await auth_client.post(API)
    assert resp.status_code == 200
    data = resp.json()
    assert "session_id" in data
    assert len(data["session_id"]) > 0


@pytest.mark.asyncio
async def test_create_chat_unauthenticated(client):
    """POST /chat without auth returns 401."""
    resp = await client.post(API)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Send message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="Test Title")
@patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="Test AI reply")
async def test_send_message(mock_chat, mock_title, auth_client):
    """POST /chat/{id}/message returns the AI reply."""
    session_id = await _create_session(auth_client)

    resp = await auth_client.post(
        f"{API}/{session_id}/message",
        data={"text": "How much protein should I eat?"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reply"] == "Test AI reply"
    mock_chat.assert_called_once()


@pytest.mark.asyncio
@patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="Auto Title")
@patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="Sure thing")
async def test_send_message_generates_title(mock_chat, mock_title, auth_client):
    """First message auto-generates a title for the session.

    Title generation runs as a fire-and-forget background task so the reply
    returns immediately. The response carries an empty title; the persisted
    title is picked up by the next sessions/history fetch. We assert by
    polling the session row until the background task lands the title.
    """
    session_id = await _create_session(auth_client)

    resp = await auth_client.post(
        f"{API}/{session_id}/message",
        data={"text": "What should I eat for dinner?"},
    )
    assert resp.status_code == 200
    # Immediate response carries no title — title gen runs in the background.
    assert resp.json()["title"] == ""

    # Poll the session's history endpoint up to ~1s for the title to land.
    # The background task is dispatched via asyncio.create_task; small sleeps
    # let the loop run it.
    title = ""
    for _ in range(20):
        await asyncio.sleep(0.05)
        history = await auth_client.get(f"{API}/{session_id}")
        if history.status_code == 200 and history.json().get("title"):
            title = history.json()["title"]
            break
    assert title == "Auto Title"
    mock_title.assert_called_once()


@pytest.mark.asyncio
@patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="reply")
async def test_send_message_not_found(mock_chat, auth_client):
    """Sending a message to a non-existent session returns 404."""
    resp = await auth_client.post(
        f"{API}/nonexistent-session-id/message",
        data={"text": "hello"},
    )
    assert resp.status_code == 404
    mock_chat.assert_not_called()


@pytest.mark.asyncio
@patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="Title")
@patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="reply")
async def test_send_message_at_limit(mock_chat, mock_title, auth_client, override_db):
    """After MAX_USER_TURNS messages, the endpoint returns a limit message without calling the AI."""
    session_id = await _create_session(auth_client)

    # Send 10 messages to reach the limit
    for i in range(10):
        resp = await auth_client.post(
            f"{API}/{session_id}/message",
            data={"text": f"Message {i + 1}"},
        )
        assert resp.status_code == 200

    # Reset call counts after the 10 setup messages
    mock_chat.reset_mock()
    mock_title.reset_mock()

    # 11th message should hit the limit
    resp = await auth_client.post(
        f"{API}/{session_id}/message",
        data={"text": "One more"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "limit" in data["reply"].lower()
    mock_chat.assert_not_called()


# ---------------------------------------------------------------------------
# List sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="Test reply")
@patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="Test Title")
async def test_get_sessions(mock_title, mock_chat, auth_client):
    """GET /chat/sessions returns sessions with titles (empty/untitled are filtered)."""
    # Create a session and send a message (so it gets a title)
    session_id = await _create_session(auth_client)
    await auth_client.post(f"{API}/{session_id}/message", data={"text": "hello"})

    # Create another session with a message
    session_id2 = await _create_session(auth_client)
    await auth_client.post(f"{API}/{session_id2}/message", data={"text": "hi"})

    # Create an empty session (no message, no title - should be filtered out)
    await _create_session(auth_client)

    resp = await auth_client.get(f"{API}/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2  # Only the 2 with titles, not the empty one
    for s in data:
        assert "id" in s
        assert s["title"]  # Must have a title
        assert "created_at" in s


# ---------------------------------------------------------------------------
# Get history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_history(auth_client):
    """GET /chat/{id} returns the session history."""
    session_id = await _create_session(auth_client)

    resp = await auth_client.get(f"{API}/{session_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id
    assert "messages" in data
    assert "at_limit" in data
    assert data["at_limit"] is False


@pytest.mark.asyncio
async def test_get_history_hides_seed_messages(auth_client):
    """The first 2 seed messages (context + greeting) are hidden from the history."""
    session_id = await _create_session(auth_client)

    # A fresh session has 2 seed messages but the history should be empty
    resp = await auth_client.get(f"{API}/{session_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []


# ---------------------------------------------------------------------------
# Delete session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_session(auth_client):
    """DELETE /chat/{id} removes the session."""
    session_id = await _create_session(auth_client)

    resp = await auth_client.delete(f"{API}/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify it's gone
    resp2 = await auth_client.get(f"{API}/{session_id}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_not_found(auth_client):
    """DELETE /chat/{id} for a non-existent session returns 404."""
    resp = await auth_client.delete(f"{API}/nonexistent-session-id")
    assert resp.status_code == 404
