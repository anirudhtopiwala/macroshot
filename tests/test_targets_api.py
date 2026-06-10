"""Tests for the AI target suggestion API endpoints."""

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import (
    create_meal_session,
    create_web_user,
    get_meal_session,
    init_db,
    get_user_target,
    get_user_profile,
)
from src.web.app import app
from src.web.deps import get_db_path, get_current_user

API = "/macro_app/api/v1"


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    asyncio.run(init_db(path))
    return path


@pytest.fixture
def override_db(db_path):
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield db_path
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_client(override_db):
    user_id = await create_web_user(override_db, "target_test@example.com")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "target_test@example.com",
            "username": "target_test",
            "first_name": "Test",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }

    app.dependency_overrides[get_current_user] = mock_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Requested-With": "MacroApp"}) as c:
        c._user_id = user_id  # type: ignore[attr-defined]
        c._db_path = override_db  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.pop(get_current_user, None)


GEMINI_RESPONSE_PARSED = {
    "calories": 2100.0,
    "protein": 160.0,
    "carbs": 230.0,
    "fat": 65.0,
    "explanation": "Based on your profile, these targets support muscle gain.",
    "profile": {"age": 28, "sex": "male", "weight_kg": 82.0, "height_cm": 180.0},
}

GEMINI_RAW_TEXT = json.dumps(GEMINI_RESPONSE_PARSED)


# ── Suggest ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_success(mock_gemini, auth_client):
    """Happy path: Gemini returns valid targets."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "age": 28, "sex": "male", "weight_kg": 82, "height_cm": 180,
        "goal": "gain_weight", "activity_level": "active", "workouts_per_week": 5,
    })
    assert resp.status_code == 200
    data = resp.json()

    assert data["session_id"]
    assert data["error"] is None
    assert data["targets"]["calories"] == 2100
    assert data["targets"]["protein"] == 160
    assert data["explanation"] == "Based on your profile, these targets support muscle gain."

    # Verify session was created in DB
    session = await get_meal_session(auth_client._db_path, data["session_id"])
    assert session is not None
    assert session["meal_type"] == "target_setting"
    assert session["status"] == "pending"


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_gemini_failure_returns_error_not_500(mock_gemini, auth_client):
    """Gemini exception returns 200 with error field, not a 500.

    SECURITY: the error message returned to the client must be a generic
    user-facing string - never the underlying exception text, which can
    leak Gemini URLs, internal stack frames, or config state.
    """
    mock_gemini.side_effect = RuntimeError("API quota exceeded")

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "goal": "maintain", "activity_level": "sedentary",
    })
    assert resp.status_code == 200
    data = resp.json()

    # Generic message - must NOT contain the raw exception text.
    assert data["error"] is not None
    assert "API quota exceeded" not in data["error"]
    assert "temporarily unavailable" in data["error"].lower()
    assert data["targets"] is None
    # Session should still exist in DB (phantom session bug was fixed)
    session = await get_meal_session(auth_client._db_path, data["session_id"])
    assert session is not None
    assert session["meal_type"] == "target_setting"


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_no_parsed_targets(mock_gemini, auth_client):
    """Gemini returns text but no parseable targets (asks clarifying question)."""
    mock_gemini.return_value = ("I need more info. What's your current weight?", None, True)

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "goal": "lose_weight", "activity_level": "lightly_active",
    })
    assert resp.status_code == 200
    data = resp.json()

    assert data["targets"] is None
    assert "more info" in data["reply_text"]
    assert data["explanation"] == ""
    assert data["error"] is None


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_saves_conversation_with_system_prompt(mock_gemini, auth_client):
    """The stored conversation includes the system prompt, not just bare context."""
    # gemini_suggest_targets mutates conversation in-place
    def side_effect(user_context, conversation, **kwargs):
        conversation.append({"role": "user", "text": "SYSTEM_PROMPT + " + user_context})
        conversation.append({"role": "model", "text": GEMINI_RAW_TEXT})
        return (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    mock_gemini.side_effect = side_effect

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "age": 30, "goal": "maintain", "activity_level": "active",
    })
    data = resp.json()

    session = await get_meal_session(auth_client._db_path, data["session_id"])
    conv = json.loads(session["conversation"])
    assert len(conv) == 2
    assert "SYSTEM_PROMPT" in conv[0]["text"]


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_saves_profile_from_gemini(mock_gemini, auth_client):
    """Profile data extracted by Gemini is saved to the user profile."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    await auth_client.post(f"{API}/settings/targets/suggest", json={
        "goal": "maintain", "activity_level": "active",
    })

    profile = await get_user_profile(auth_client._db_path, auth_client._user_id)
    assert profile["age"] == 28
    assert profile["sex"] == "male"


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_minimal_request(mock_gemini, auth_client):
    """Sending only defaults (no optional fields) doesn't crash."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["targets"] is not None

    # Verify context string has no leading space or empty "I'm a ."
    call_args = mock_gemini.call_args
    context = call_args.kwargs.get("user_context") or call_args[0][0]
    assert not context.startswith(" ")
    assert "I'm a ." not in context


# ── Refine ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_refine_success(mock_gemini, auth_client):
    """Refine appends to conversation and returns updated targets."""
    # Initial suggest
    def suggest_side_effect(user_context, conversation, **kw):
        conversation.append({"role": "user", "text": "initial prompt + " + user_context})
        conversation.append({"role": "model", "text": GEMINI_RAW_TEXT})
        return (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    mock_gemini.side_effect = suggest_side_effect
    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "goal": "gain_weight", "activity_level": "active",
    })
    session_id = resp.json()["session_id"]

    # Refine
    refined = {**GEMINI_RESPONSE_PARSED, "protein": 200.0, "explanation": "More protein as requested."}

    def refine_side_effect(user_context, conversation, **kw):
        conversation.append({"role": "user", "text": user_context})
        conversation.append({"role": "model", "text": json.dumps(refined)})
        return (json.dumps(refined), refined, True)

    mock_gemini.side_effect = refine_side_effect
    resp = await auth_client.post(f"{API}/settings/targets/suggest/{session_id}/refine", json={
        "text": "I want more protein",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["targets"]["protein"] == 200
    assert data["explanation"] == "More protein as requested."

    # Verify conversation has all 4 turns
    session = await get_meal_session(auth_client._db_path, session_id)
    conv = json.loads(session["conversation"])
    assert len(conv) == 4
    assert conv[2]["role"] == "user"
    assert conv[3]["role"] == "model"


@pytest.mark.asyncio
async def test_refine_session_not_found(auth_client):
    resp = await auth_client.post(f"{API}/settings/targets/suggest/fake-id/refine", json={
        "text": "hello",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_refine_wrong_session_type(auth_client):
    """Refining a meal analysis session (not target_setting) returns 400."""
    session_id = "meal-session-123"
    await create_meal_session(auth_client._db_path, session_id, auth_client._user_id, meal_type="")

    resp = await auth_client.post(f"{API}/settings/targets/suggest/{session_id}/refine", json={
        "text": "hello",
    })
    assert resp.status_code == 400
    assert "Not a target-setting session" in resp.json()["detail"]


# ── Accept ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_accept_saves_targets(mock_gemini, auth_client):
    """Accept saves the provided targets to user_targets."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "goal": "maintain", "activity_level": "active",
    })
    session_id = resp.json()["session_id"]

    # Accept with user-edited values (different from AI suggestion)
    resp = await auth_client.post(f"{API}/settings/targets/suggest/{session_id}/accept", json={
        "calories": 1800, "protein": 140, "carbs": 200, "fat": 60,
    })
    assert resp.status_code == 200
    assert resp.json()["calories"] == 1800

    # Verify DB
    target = await get_user_target(auth_client._db_path, auth_client._user_id)
    assert target["calories"] == 1800
    assert target["protein"] == 140
    assert target["set_by"] == "gemini"

    # Verify session marked accepted
    session = await get_meal_session(auth_client._db_path, session_id)
    assert session["status"] == "accepted"


@pytest.mark.asyncio
async def test_accept_session_not_found(auth_client):
    resp = await auth_client.post(f"{API}/settings/targets/suggest/fake-id/accept", json={
        "calories": 2000, "protein": 150, "carbs": 200, "fat": 70,
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_accept_wrong_session_type(auth_client):
    """Accepting a meal analysis session returns 400."""
    session_id = "meal-session-456"
    await create_meal_session(auth_client._db_path, session_id, auth_client._user_id, meal_type="")

    resp = await auth_client.post(f"{API}/settings/targets/suggest/{session_id}/accept", json={
        "calories": 2000, "protein": 150, "carbs": 200, "fat": 70,
    })
    assert resp.status_code == 400


# ── Cross-User Isolation ────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_cross_user_cannot_access_session(mock_gemini, override_db):
    """User B cannot refine or accept User A's session."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    # Create User A
    user_a_id = await create_web_user(override_db, "userA@example.com")

    async def mock_user_a():
        return {"user_id": user_a_id, "email": "userA@example.com", "username": "a",
                "first_name": "A", "google_sub": None, "created_at": "2026-01-01"}

    app.dependency_overrides[get_current_user] = mock_user_a

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Requested-With": "MacroApp"}) as client_a:
        resp = await client_a.post(f"{API}/settings/targets/suggest", json={
            "goal": "maintain", "activity_level": "active",
        })
        session_id = resp.json()["session_id"]

    # Switch to User B
    user_b_id = await create_web_user(override_db, "userB@example.com")

    async def mock_user_b():
        return {"user_id": user_b_id, "email": "userB@example.com", "username": "b",
                "first_name": "B", "google_sub": None, "created_at": "2026-01-01"}

    app.dependency_overrides[get_current_user] = mock_user_b

    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Requested-With": "MacroApp"}) as client_b:
        # User B tries to accept User A's session
        resp = await client_b.post(f"{API}/settings/targets/suggest/{session_id}/accept", json={
            "calories": 9999, "protein": 999, "carbs": 999, "fat": 999,
        })
        assert resp.status_code == 404

        # User B tries to refine User A's session
        resp = await client_b.post(f"{API}/settings/targets/suggest/{session_id}/refine", json={
            "text": "give me everything",
        })
        assert resp.status_code == 404

    app.dependency_overrides.pop(get_current_user, None)


# ── Build Context ────────────────────────────────────────────────────


def test_build_context_full_profile():
    from src.web.routes.targets import _build_context
    from src.web.schemas import TargetSuggestRequest

    req = TargetSuggestRequest(
        age=28, sex="male", weight_kg=82, height_cm=180,
        goal="lose_weight", activity_level="very_active", workouts_per_week=6,
    )
    ctx = _build_context(req)
    assert "28-year-old" in ctx
    assert "male" in ctx
    assert "82.0kg" in ctx
    assert "180cm" in ctx
    assert "lose weight" in ctx
    assert "very active" in ctx
    assert "6 sessions per week" in ctx
    assert not ctx.startswith(" ")


def test_build_context_empty():
    from src.web.routes.targets import _build_context
    from src.web.schemas import TargetSuggestRequest

    req = TargetSuggestRequest()
    ctx = _build_context(req)
    assert not ctx.startswith(" ")
    assert "I'm a" not in ctx
    assert "maintain weight" in ctx


def test_build_context_unknown_goal():
    from src.web.routes.targets import _build_context
    from src.web.schemas import TargetSuggestRequest

    req = TargetSuggestRequest(goal="custom_goal")
    ctx = _build_context(req)
    assert "custom_goal" in ctx


# ── seed_message wiring (refine-flow lazy-suggest) ────────────────────


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_seed_message_threaded_into_context(mock_gemini, auth_client):
    """seed_message is appended to user_context so the initial Gemini call
    already factors in the user's first refine message."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "age": 30, "sex": "male", "weight_kg": 75, "height_cm": 178,
        "goal": "maintain", "activity_level": "active",
        "seed_message": "I'm training for a marathon, bump my carbs",
    })
    assert resp.status_code == 200

    # First positional or kw arg passed to gemini_suggest_targets is user_context.
    call = mock_gemini.call_args
    user_context = call.kwargs.get("user_context") if call.kwargs else call.args[0]
    assert "Training for a marathon" in user_context or "training for a marathon" in user_context
    assert "User's first message:" in user_context


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_without_seed_message_unchanged(mock_gemini, auth_client):
    """Omitting seed_message preserves the legacy context shape (no
    'User's first message:' marker) so the onboarding flow is untouched."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "age": 30, "sex": "male", "weight_kg": 75, "height_cm": 178,
        "goal": "maintain", "activity_level": "active",
    })
    assert resp.status_code == 200
    call = mock_gemini.call_args
    user_context = call.kwargs.get("user_context") if call.kwargs else call.args[0]
    assert "User's first message:" not in user_context


@pytest.mark.asyncio
@patch("src.web.routes.targets.gemini_suggest_targets", new_callable=AsyncMock)
async def test_suggest_seed_message_whitespace_only_ignored(mock_gemini, auth_client):
    """Whitespace-only seed_message is treated as not provided (no marker)."""
    mock_gemini.return_value = (GEMINI_RAW_TEXT, GEMINI_RESPONSE_PARSED, True)

    resp = await auth_client.post(f"{API}/settings/targets/suggest", json={
        "age": 30, "sex": "male", "weight_kg": 75, "height_cm": 178,
        "goal": "maintain", "activity_level": "active",
        "seed_message": "   ",
    })
    assert resp.status_code == 200
    call = mock_gemini.call_args
    user_context = call.kwargs.get("user_context") if call.kwargs else call.args[0]
    assert "User's first message:" not in user_context
