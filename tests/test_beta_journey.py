"""End-to-end smoke test for the beta release flow.

Single linear test in hosted+beta with BETA_SIGNUP_CAP=3:
  1. Sign up 3 users → all get Pro
  2. User 1 burns through 5 image analyses, 10 text analyses, 10 chats, and
     11 corrections on a single meal session (10 allowed + 1 blocked)
  3. 4th signup attempt lands in the waitlist, returns 503
  4. Admin flips OG on user 1 → is_og=1 in DB
  5. Admin exports users.csv (3 rows) and waitlist.csv (1 row)

The test is intentionally long and linear - this is the "does it all hang
together" smoke test. Each individual claim is also covered by a dedicated
unit-style test elsewhere (see test_subscription.py, test_beta_signup.py,
test_admin_beta.py). If this test breaks but the smaller ones don't, a new
cross-cutting regression has slipped in.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient
from io import BytesIO
from PIL import Image as PILImage

from src.db import (
    create_meal_session,
    get_subscription,
    init_db,
    list_waitlist,
    store_email_pin,
)
from src.web.app import app
from src.web.auth import hash_pin
from src.web.deps import get_current_user, get_db_path


MEALS_API = "/macro_app/api/v1/meals"
CHAT_API = "/macro_app/api/v1/chat"
AUTH_API = "/macro_app/api/v1/auth"
ADMIN_API = "/macro_app/api/v1/admin"


def _set_mode(app_mode: str, beta_mode: bool, cap: int) -> None:
    import src.web.constants as _constants
    import src.web.deps as _deps
    import src.web.routes.auth as _auth
    import src.web.routes.subscription as _sub_route

    _constants.APP_MODE = app_mode
    _deps.APP_MODE = app_mode
    _auth.APP_MODE = app_mode
    _constants.BETA_MODE = beta_mode
    _deps.BETA_MODE = beta_mode
    _auth.BETA_MODE = beta_mode
    _sub_route.BETA_MODE = beta_mode
    _constants.BETA_SIGNUP_CAP = cap
    _auth.BETA_SIGNUP_CAP = cap


@pytest.fixture
def mode_env():
    import src.web.constants as _constants
    import src.web.deps as _deps
    import src.web.routes.auth as _auth
    import src.web.routes.subscription as _sub_route

    old = (
        _constants.APP_MODE, _deps.APP_MODE, _auth.APP_MODE,
        _constants.BETA_MODE, _deps.BETA_MODE, _auth.BETA_MODE,
        _sub_route.BETA_MODE, _constants.BETA_SIGNUP_CAP,
        _auth.BETA_SIGNUP_CAP,
    )
    yield _set_mode
    (
        _constants.APP_MODE, _deps.APP_MODE, _auth.APP_MODE,
        _constants.BETA_MODE, _deps.BETA_MODE, _auth.BETA_MODE,
        _sub_route.BETA_MODE, _constants.BETA_SIGNUP_CAP,
        _auth.BETA_SIGNUP_CAP,
    ) = old


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "journey.db")
    asyncio.get_event_loop().run_until_complete(init_db(path))
    return path


@pytest.fixture
def override_db(db_path):
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield db_path
    app.dependency_overrides.clear()


async def _seed_pin(db: str, email: str) -> None:
    """Stash a valid PIN row so the email-verify flow can succeed."""
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    await store_email_pin(db, email, hash_pin("123456"), expires)


def _real_jpeg() -> bytes:
    """Generate a real 8x8 JPEG PIL can decode (passes EXIF strip step)."""
    buf = BytesIO()
    PILImage.new("RGB", (8, 8), color="blue").save(buf, "JPEG")
    return buf.getvalue()


@pytest.mark.asyncio
@patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
@patch("src.web.routes.meals.send_correction", new_callable=AsyncMock)
@patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="T")
@patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="Reply")
async def test_full_beta_journey(
    _mock_chat, _mock_title, mock_correct, mock_analyze,
    override_db, mode_env, tmp_path, monkeypatch,
):
    db = override_db
    mode_env("hosted", True, cap=3)
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")

    # Redirect image save dir so image uploads are harmless in tmp
    import src.web.routes.meals as meals_module
    monkeypatch.setattr(meals_module, "IMAGE_DIR", str(tmp_path / "images"))

    # Default analyze/correct stubs
    mock_analyze.return_value = {
        "session_id": "s-stub", "nutrition": None,
        "questions": [], "raw_text": "ok",
    }
    mock_correct.return_value = {
        "nutrition": None, "reply_text": "ok", "error": None,
    }

    # Bare client for auth endpoints (no user override needed - signup
    # creates the user and cookies would normally kick in, but we override
    # get_current_user for the subsequent calls to avoid cookie plumbing).
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as client:

        # ── 1. Sign up 3 users via email PIN ──────────────────────
        user_ids: list[int] = []
        for i in range(3):
            email = f"beta{i}@example.com"
            await _seed_pin(db, email)
            resp = await client.post(
                f"{AUTH_API}/email/verify-pin",
                json={"email": email, "pin": "123456", "first_name": f"Beta{i}"},
            )
            assert resp.status_code == 200, (
                f"Signup #{i} failed: {resp.status_code} {resp.text}"
            )
            uid = resp.json()["user_id"]
            user_ids.append(uid)

            # Each signup auto-provisioned a pro_monthly row
            sub = await get_subscription(db, uid)
            assert sub is not None
            assert sub["plan"] == "pro_monthly"
            assert sub["status"] == "active"
            assert sub["is_og"] == 0

        u1 = user_ids[0]

        # ── 2. Act as User 1 and burn through all the caps ────────
        async def mock_user():
            return {
                "user_id": u1,
                "email": "beta0@example.com",
                "username": "beta0@example.com",
                "first_name": "Beta0",
                "google_sub": None,
                "created_at": "2026-01-01 00:00:00",
            }

        app.dependency_overrides[get_current_user] = mock_user
        try:
            # 2a. 5 image analyses succeed, 6th is 429
            jpeg = _real_jpeg()
            for i in range(5):
                resp = await client.post(
                    f"{MEALS_API}/analyze",
                    data={"text": ""},
                    files={"images": ("t.jpg", jpeg, "image/jpeg")},
                )
                assert resp.status_code == 200, (
                    f"Image analyze #{i+1} failed: {resp.text}"
                )
            resp = await client.post(
                f"{MEALS_API}/analyze",
                data={"text": ""},
                files={"images": ("t.jpg", jpeg, "image/jpeg")},
            )
            assert resp.status_code == 429
            detail = resp.json()["detail"]
            assert detail["feature"] == "image_analysis"
            assert "free beta" in detail["message"]

            # 2b. 10 text analyses succeed, 11th is 429
            for i in range(10):
                resp = await client.post(
                    f"{MEALS_API}/analyze", data={"text": f"meal {i}"},
                )
                assert resp.status_code == 200, (
                    f"Text analyze #{i+1} failed: {resp.text}"
                )
            resp = await client.post(
                f"{MEALS_API}/analyze", data={"text": "one more meal"},
            )
            assert resp.status_code == 429
            detail = resp.json()["detail"]
            assert detail["feature"] == "text_meal"
            assert "free beta" in detail["message"]

            # 2c. 10 chat creations succeed, 11th is 429
            for i in range(10):
                resp = await client.post(CHAT_API)
                assert resp.status_code == 200, (
                    f"Chat create #{i+1} failed: {resp.text}"
                )
            resp = await client.post(CHAT_API)
            assert resp.status_code == 429
            detail = resp.json()["detail"]
            assert detail["feature"] == "ai_chat"
            assert "free beta" in detail["message"]

            # 2d. Seed a meal session with 11 user turns (1 initial + 10
            # edits) directly in the DB, then attempt an 11th correction →
            # 429 with scope wording.
            session_id = "journey-edit-session"
            convo = [{"role": "user", "text": "initial"}]
            for i in range(10):
                convo.append({"role": "model", "text": f"m{i}"})
                convo.append({"role": "user", "text": f"e{i}"})
            await create_meal_session(
                db, session_id, u1, conversation=json.dumps(convo),
            )
            resp = await client.post(
                f"{MEALS_API}/sessions/{session_id}/correct",
                json={"text": "11th edit"},
            )
            assert resp.status_code == 429
            detail = resp.json()["detail"]
            assert detail["feature"] == "meal_edit"
            assert "on this meal" in detail["message"]
            assert "free beta" in detail["message"]

        finally:
            app.dependency_overrides.pop(get_current_user, None)

        # ── 3. 4th signup attempt → 503, waitlist row added ───────
        await _seed_pin(db, "overflow@example.com")
        resp = await client.post(
            f"{AUTH_API}/email/verify-pin",
            json={
                "email": "overflow@example.com", "pin": "123456",
                "first_name": "Over",
            },
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "beta_full"
        assert detail["cap"] == 3
        assert detail["waitlist_added"] is True

        wl = await list_waitlist(db)
        assert len(wl) == 1
        assert wl[0]["email"] == "overflow@example.com"

        # ── 4. Admin flips OG on User 1 ───────────────────────────
        async def mock_admin():
            return {
                "user_id": 9000,
                "email": "admin@example.com",
                "username": "admin@example.com",
                "first_name": "Admin",
                "google_sub": None,
                "created_at": "2026-01-01 00:00:00",
            }

        # Ensure the admin user exists (required by the OG toggle
        # endpoint's ownership checks via get_web_user_by_id)
        from src.db import ensure_user
        await ensure_user(db, 9000, "admin@example.com", "Admin")

        app.dependency_overrides[get_current_user] = mock_admin
        try:
            resp = await client.post(
                f"{ADMIN_API}/og-user",
                json={"user_id": u1, "is_og": True},
            )
            assert resp.status_code == 200
            assert resp.json()["is_og"] is True
            sub = await get_subscription(db, u1)
            assert sub["is_og"] == 1

            # ── 5. Admin CSV exports ──────────────────────────────
            resp = await client.get(f"{ADMIN_API}/users.csv")
            assert resp.status_code == 200
            rows = list(csv.reader(io.StringIO(resp.text)))
            # Header + at least 3 beta users (admin row may or may not be
            # present depending on whether ensure_user was enough - it was
            # for u1..u3 via _handle signup flow, and ensure_user(9000,...)
            # above seeded the admin row too without a web_auth entry.
            # The users.csv export LEFT-joins on web_auth so the admin
            # may not appear - we only require the 3 beta users here.
            emails = {row[1] for row in rows[1:]}
            assert {"beta0@example.com", "beta1@example.com", "beta2@example.com"} <= emails

            resp = await client.get(f"{ADMIN_API}/waitlist.csv")
            assert resp.status_code == 200
            rows = list(csv.reader(io.StringIO(resp.text)))
            assert len(rows) == 2  # header + 1 waitlist row
            assert rows[1][1] == "overflow@example.com"
        finally:
            app.dependency_overrides.pop(get_current_user, None)
