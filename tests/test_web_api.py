"""Tests for the FastAPI web API endpoints."""

import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

# Set env before importing app
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import (
    create_meal_session,
    create_web_user,
    ensure_user,
    init_db,
    log_meal,
    set_user_target,
    store_email_pin,
)
from src.web.app import app
from src.web.auth import create_jwt, hash_pin
from src.web.deps import get_db_path, get_current_user, get_subscription_info, SubscriptionInfo


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary SQLite database."""
    path = str(tmp_path / "test.db")
    asyncio.run(init_db(path))
    return path


@pytest.fixture
def override_db(db_path):
    """Override the DB path dependency for FastAPI."""
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield db_path
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(override_db):
    """Create an async HTTP test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        yield c


@pytest_asyncio.fixture
async def auth_client(override_db):
    """Create an authenticated test client with a web user."""
    user_id = await create_web_user(override_db, "testuser@example.com")

    # Override the auth dependency to return our test user directly
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
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        c._user_id = user_id
        yield c

    # Clear the override (override_db fixture will clear all later too)
    app.dependency_overrides.pop(get_current_user, None)


# --- CSRF Middleware ---

@pytest.mark.asyncio
async def test_csrf_blocks_post_without_header(override_db):
    """POST without X-Requested-With header is blocked with 403."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as bare_client:
        resp = await bare_client.post("/macro_app/api/v1/auth/logout")
        assert resp.status_code == 403
        assert "X-Requested-With" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_csrf_allows_get_without_header(override_db):
    """GET requests don't need the CSRF header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as bare_client:
        resp = await bare_client.get("/macro_app/api/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_csrf_exempt_paths(override_db):
    """Webhook paths and /auth/google work without CSRF header.

    A15: /auth/email/send-pin and /auth/email/verify-pin are NO longer
    exempt - the SPA always sends X-Requested-With: MacroApp.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as bare_client:
        # Subscription webhook is still exempt (Stripe doesn't send our header).
        resp = await bare_client.post(
            "/macro_app/api/v1/subscription/webhook",
            json={},
        )
        # Should not be 403 from CSRF; will be 503/400 from missing config.
        assert resp.status_code != 403

        # send-pin must now require the X-Requested-With header.
        resp_pin = await bare_client.post(
            "/macro_app/api/v1/auth/email/send-pin",
            json={"email": "test@example.com"},
        )
        assert resp_pin.status_code == 403
        # When the header is set, the request gets through CSRF (status will
        # be 200/429 depending on rate-limit state, but not 403).
        resp_pin_ok = await bare_client.post(
            "/macro_app/api/v1/auth/email/send-pin",
            json={"email": "test2@example.com"},
            headers={"X-Requested-With": "MacroApp"},
        )
        assert resp_pin_ok.status_code != 403


# --- Health ---

@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/macro_app/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_503_when_db_unreachable(monkeypatch, client):
    """Health returns 503 if the DB ping fails (load balancer signal)."""
    from src import db_pool

    def _boom(*_a, **_kw):
        raise RuntimeError("db unreachable")

    monkeypatch.setattr(db_pool, "get_db", _boom)
    resp = await client.get("/macro_app/api/health")
    assert resp.status_code == 503
    assert resp.json()["status"] == "unhealthy"


# --- Auth ---

@pytest.mark.asyncio
async def test_auth_unauthenticated(client):
    resp = await client.get("/macro_app/api/v1/dashboard/today")
    assert resp.status_code == 401


def test_hash_pin_is_salted_and_verifiable():
    """hash_pin must produce a different value each call (random salt) but
    verify_pin_hash must still accept the raw pin for any of those outputs."""
    from src.web.auth import hash_pin, verify_pin_hash
    h1 = hash_pin("123456")
    h2 = hash_pin("123456")
    assert h1 != h2, "salt should make each hash unique"
    assert verify_pin_hash("123456", h1)
    assert verify_pin_hash("123456", h2)
    assert not verify_pin_hash("999999", h1)
    # Self-contained format: iterations$salt$hash
    assert h1.count("$") == 2


def test_verify_pin_hash_accepts_legacy_sha256():
    """In-flight PINs hashed with the old plain SHA-256 must still verify
    until they expire (≤10 minutes after the deploy)."""
    import hashlib
    from src.web.auth import verify_pin_hash
    legacy = hashlib.sha256(b"123456").hexdigest()
    assert verify_pin_hash("123456", legacy)
    assert not verify_pin_hash("000000", legacy)


@pytest.mark.asyncio
async def test_send_pin(client, override_db):
    resp = await client.post(
        "/macro_app/api/v1/auth/email/send-pin",
        json={"email": "user@example.com"},
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "PIN sent"


@pytest.mark.asyncio
async def test_verify_pin(client, override_db):
    email = "verify@example.com"
    pin = "654321"
    pin_hashed = hash_pin(pin)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    await store_email_pin(override_db, email, pin_hashed, expires)

    resp = await client.post(
        "/macro_app/api/v1/auth/email/verify-pin",
        json={"email": email, "pin": pin},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == email
    assert "user_id" in data


@pytest.mark.asyncio
async def test_verify_pin_wrong(client, override_db):
    email = "wrong@example.com"
    pin_hashed = hash_pin("111111")
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
    await store_email_pin(override_db, email, pin_hashed, expires)

    resp = await client.post(
        "/macro_app/api/v1/auth/email/verify-pin",
        json={"email": email, "pin": "999999"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_verify_pin_expired(client, override_db):
    email = "expired@example.com"
    pin_hashed = hash_pin("123456")
    expires = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    await store_email_pin(override_db, email, pin_hashed, expires)

    resp = await client.post(
        "/macro_app/api/v1/auth/email/verify-pin",
        json={"email": email, "pin": "123456"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_auth_me(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == "testuser@example.com"
    assert "user_id" in data


@pytest.mark.asyncio
async def test_auth_logout(auth_client):
    resp = await auth_client.post("/macro_app/api/v1/auth/logout")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_refresh(auth_client):
    resp = await auth_client.post("/macro_app/api/v1/auth/refresh")
    assert resp.status_code == 200


# --- Dashboard ---

@pytest.mark.asyncio
async def test_dashboard_today_empty(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/dashboard/today")
    assert resp.status_code == 200
    data = resp.json()
    assert "totals" in data
    assert data["totals"]["calories"] == 0.0


@pytest.mark.asyncio
async def test_dashboard_today_with_meal(auth_client, override_db):
    user_id = auth_client._user_id
    # Use user's timezone for the logged_at timestamp so it matches "today"
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)
    await log_meal(
        override_db, user_id, now.strftime("%Y-%m-%d %H:%M"),
        "Test Chicken", "Grilled chicken breast",
        350.0, 40.0, 5.0, 15.0, "Gemini", "lunch", "[]",
    )
    resp = await auth_client.get("/macro_app/api/v1/dashboard/today")
    assert resp.status_code == 200
    data = resp.json()
    assert data["totals"]["calories"] == 350.0
    assert data["totals"]["protein"] == 40.0


@pytest.mark.asyncio
async def test_dashboard_totals(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/dashboard/totals?period=week")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_remaining(auth_client, override_db):
    user_id = auth_client._user_id
    await set_user_target(override_db, user_id, 2000, 150, 200, 70)
    resp = await auth_client.get("/macro_app/api/v1/dashboard/remaining")
    assert resp.status_code == 200
    data = resp.json()
    assert data["remaining"]["calories"] == 2000.0


@pytest.mark.asyncio
async def test_dashboard_trend(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/dashboard/trend")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["days"]) == 7


@pytest.mark.asyncio
async def test_dashboard_stats(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/dashboard/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_meals" in data


# --- Meals ---

@pytest.mark.asyncio
async def test_meals_list_empty(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/meals")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_meals_list_with_data(auth_client, override_db):
    user_id = auth_client._user_id
    now = datetime.now(timezone.utc)
    await log_meal(
        override_db, user_id, now.strftime("%Y-%m-%d %H:%M"),
        "Pizza", "Margherita pizza", 800.0, 30.0, 90.0, 35.0, "Gemini", "dinner", "[]",
    )
    resp = await auth_client.get("/macro_app/api/v1/meals")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["item_name"] == "Pizza"


@pytest.mark.asyncio
async def test_meals_get_by_id(auth_client, override_db):
    user_id = auth_client._user_id
    now = datetime.now(timezone.utc)
    meal_id = await log_meal(
        override_db, user_id, now.strftime("%Y-%m-%d %H:%M"),
        "Salad", "Caesar salad", 200.0, 10.0, 15.0, 12.0, "Gemini", "lunch", "[]",
    )
    resp = await auth_client.get(f"/macro_app/api/v1/meals/{meal_id}")
    assert resp.status_code == 200
    assert resp.json()["item_name"] == "Salad"


@pytest.mark.asyncio
async def test_meals_get_not_found(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/meals/99999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_meals_update(auth_client, override_db):
    user_id = auth_client._user_id
    now = datetime.now(timezone.utc)
    meal_id = await log_meal(
        override_db, user_id, now.strftime("%Y-%m-%d %H:%M"),
        "Pasta", "Spaghetti", 600.0, 20.0, 80.0, 20.0, "Gemini", "dinner", "[]",
    )
    resp = await auth_client.put(
        f"/macro_app/api/v1/meals/{meal_id}",
        json={"calories": 700, "protein": 25},
    )
    assert resp.status_code == 200
    assert resp.json()["calories"] == 700.0
    assert resp.json()["protein"] == 25.0


@pytest.mark.asyncio
async def test_meals_delete(auth_client, override_db):
    user_id = auth_client._user_id
    now = datetime.now(timezone.utc)
    meal_id = await log_meal(
        override_db, user_id, now.strftime("%Y-%m-%d %H:%M"),
        "Delete Me", "", 100.0, 5.0, 10.0, 3.0, "Gemini", "snack", "[]",
    )
    resp = await auth_client.delete(f"/macro_app/api/v1/meals/{meal_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify it's gone
    resp2 = await auth_client.get(f"/macro_app/api/v1/meals/{meal_id}")
    assert resp2.status_code == 404


@pytest.mark.asyncio
async def test_meals_filter_by_type(auth_client, override_db):
    user_id = auth_client._user_id
    now = datetime.now(timezone.utc)
    await log_meal(override_db, user_id, now.strftime("%Y-%m-%d %H:%M"),
                   "Eggs", "", 200.0, 14.0, 2.0, 15.0, "Gemini", "breakfast", "[]")
    await log_meal(override_db, user_id, now.strftime("%Y-%m-%d %H:%M"),
                   "Steak", "", 500.0, 50.0, 0.0, 30.0, "Gemini", "dinner", "[]")

    resp = await auth_client.get("/macro_app/api/v1/meals?type=breakfast")
    data = resp.json()
    assert len(data) == 1
    assert data[0]["item_name"] == "Eggs"


# --- Meal Sessions ---

@pytest.mark.asyncio
async def test_session_cancel(auth_client, override_db):
    user_id = auth_client._user_id
    await create_meal_session(override_db, "cancel-test", user_id)
    resp = await auth_client.post("/macro_app/api/v1/meals/sessions/cancel-test/cancel")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_session_cancel_not_found(auth_client):
    resp = await auth_client.post("/macro_app/api/v1/meals/sessions/nonexistent/cancel")
    assert resp.status_code == 404


# --- Settings ---

@pytest.mark.asyncio
async def test_targets_get_default(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/settings/targets")
    assert resp.status_code == 200
    data = resp.json()
    assert data["set_by"] == "default"


@pytest.mark.asyncio
async def test_targets_set_and_get(auth_client):
    resp = await auth_client.put(
        "/macro_app/api/v1/settings/targets",
        json={"calories": 2500, "protein": 200, "carbs": 250, "fat": 80},
    )
    assert resp.status_code == 200

    resp2 = await auth_client.get("/macro_app/api/v1/settings/targets")
    data = resp2.json()
    assert data["calories"] == 2500.0
    assert data["set_by"] == "manual"


@pytest.mark.asyncio
async def test_prefs_get(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/settings/prefs")
    assert resp.status_code == 200
    data = resp.json()
    assert "timezone" in data


@pytest.mark.asyncio
async def test_prefs_update(auth_client):
    resp = await auth_client.put(
        "/macro_app/api/v1/settings/prefs",
        json={"timezone": "America/New_York"},
    )
    assert resp.status_code == 200
    assert resp.json()["timezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_profile_get(auth_client):
    resp = await auth_client.get("/macro_app/api/v1/settings/profile")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_profile_update(auth_client):
    resp = await auth_client.put(
        "/macro_app/api/v1/settings/profile",
        json={"age": 30, "weight_kg": 75.0, "height_cm": 180.0, "sex": "male"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["age"] == 30
    assert data["weight_kg"] == 75.0


# --- Rate Limiting ---

@pytest.mark.asyncio
async def test_pin_rate_limit(client, override_db):
    """A11: per-email cap tightened to 1/hour as a mailbomb-relay countermeasure."""
    email = "ratelimit@example.com"
    # First PIN succeeds.
    resp = await client.post(
        "/macro_app/api/v1/auth/email/send-pin",
        json={"email": email},
    )
    assert resp.status_code == 200

    # Second within the same hour is rate-limited (1/hour cap).
    resp = await client.post(
        "/macro_app/api/v1/auth/email/send-pin",
        json={"email": email},
    )
    assert resp.status_code == 429


# --- Analyze endpoint (text only, no image) ---

@pytest.mark.asyncio
async def test_analyze_no_input(auth_client):
    """Analyze with no input should return 400."""
    resp = await auth_client.post(
        "/macro_app/api/v1/meals/analyze",
        data={"text": "", "meal_type": "lunch"},
    )
    assert resp.status_code == 400


# --- Relog ---

@pytest.mark.asyncio
async def test_relog_meal(auth_client, override_db):
    """Relog creates a new meal with current timestamp."""
    uid = auth_client._user_id
    meal_id = await log_meal(override_db, uid, "2026-03-10 08:00", "Oatmeal", "", 300, 10, 50, 5, "Gemini")

    resp = await auth_client.post(
        f"/macro_app/api/v1/meals/{meal_id}/relog",
        json={"logged_at": "2026-03-18 19:30"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    new_id = data["meal_id"]
    assert new_id != meal_id

    # Verify the new meal has the client-provided timestamp
    from src.db import get_meal_by_id
    new_meal = await get_meal_by_id(override_db, uid, new_id)
    assert new_meal["logged_at"] == "2026-03-18 19:30"
    assert new_meal["item_name"] == "Oatmeal"
    assert new_meal["calories"] == 300


@pytest.mark.asyncio
async def test_relog_meal_not_found(auth_client):
    """Relog a non-existent meal returns 404."""
    resp = await auth_client.post("/macro_app/api/v1/meals/99999/relog", json={})
    assert resp.status_code == 404


# --- Push Subscriptions ---

@pytest.mark.asyncio
async def test_push_subscribe_and_status(auth_client):
    """Subscribe to push and verify status."""
    resp = await auth_client.post(
        "/macro_app/api/v1/settings/push/subscribe",
        json={
            "endpoint": "https://push.example.com/sub/123",
            "keys": {"p256dh": "test-p256dh-key", "auth": "test-auth-key"},
        },
    )
    assert resp.status_code == 200

    resp = await auth_client.get("/macro_app/api/v1/settings/push/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["subscribed"] is True
    assert data["count"] == 1


@pytest.mark.asyncio
async def test_push_unsubscribe(auth_client):
    """Unsubscribe removes the push subscription."""
    endpoint = "https://push.example.com/sub/456"
    await auth_client.post(
        "/macro_app/api/v1/settings/push/subscribe",
        json={"endpoint": endpoint, "keys": {"p256dh": "k1", "auth": "k2"}},
    )

    resp = await auth_client.post(
        "/macro_app/api/v1/settings/push/unsubscribe",
        json={"endpoint": endpoint},
    )
    assert resp.status_code == 200

    status = await auth_client.get("/macro_app/api/v1/settings/push/status")
    assert status.json()["subscribed"] is False


# --- Trend with today param ---

@pytest.mark.asyncio
async def test_trend_with_today_param(auth_client, override_db):
    """Trend endpoint respects the today= param for timezone correctness."""
    uid = auth_client._user_id
    await log_meal(override_db, uid, "2026-03-18 12:00", "Lunch", "", 500, 30, 60, 15, "Gemini")

    resp = await auth_client.get("/macro_app/api/v1/dashboard/trend?today=2026-03-18&days=7")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["days"]) == 7
    # The last day should be 2026-03-18
    assert data["days"][-1]["date"] == "2026-03-18"
    # And it should have our meal
    assert data["days"][-1]["calories"] == 500


@pytest.mark.asyncio
async def test_trend_default_7_days(auth_client):
    """Trend without params returns 7 days."""
    resp = await auth_client.get("/macro_app/api/v1/dashboard/trend")
    assert resp.status_code == 200
    assert len(resp.json()["days"]) == 7


@pytest.mark.asyncio
async def test_config_has_vapid_key(client):
    """Config endpoint includes vapid_public_key."""
    resp = await client.get("/macro_app/api/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "vapid_public_key" in data


# --- Alias Quick-Log ---

async def _create_alias(db_path, user_id, name="Chicken Rice", calories=500, protein=40, carbs=60, fat=12):
    """Helper to create a test alias."""
    from src.db import save_alias
    await save_alias(
        db_path, user_id, name,
        item_name=name, meal_description=f"A serving of {name}",
        calories=calories, protein=protein, carbs=carbs, fat=fat,
        items_json='[{"name":"' + name + '","calories":' + str(calories) + ',"protein":' + str(protein) + ',"carbs":' + str(carbs) + ',"fat":' + str(fat) + '}]',
    )


@pytest.mark.asyncio
async def test_alias_log_no_backdate(auth_client, override_db):
    """Quick-log an alias without backdate uses current time."""
    uid = auth_client._user_id
    await _create_alias(override_db, uid)

    resp = await auth_client.post("/macro_app/api/v1/aliases/chicken%20rice/log")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["meal_id"] > 0
    assert data["meal_type"] in ("breakfast", "lunch", "snack", "dinner")

    from src.db import get_meal_by_id
    meal = await get_meal_by_id(override_db, uid, data["meal_id"])
    assert meal["item_name"] == "Chicken Rice"
    assert meal["calories"] == 500
    assert meal["protein"] == 40


@pytest.mark.asyncio
async def test_alias_log_with_backdate(auth_client, override_db):
    """Quick-log an alias with logged_at stores the correct timestamp."""
    uid = auth_client._user_id
    await _create_alias(override_db, uid)

    resp = await auth_client.post(
        "/macro_app/api/v1/aliases/chicken%20rice/log",
        json={"logged_at": "2026-03-15 09:30"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["meal_type"] == "breakfast"  # hour 9 → breakfast

    from src.db import get_meal_by_id
    meal = await get_meal_by_id(override_db, uid, data["meal_id"])
    assert meal["logged_at"] == "2026-03-15 09:30"
    assert meal["item_name"] == "Chicken Rice"
    assert meal["calories"] == 500


@pytest.mark.asyncio
async def test_alias_log_backdate_dinner(auth_client, override_db):
    """Backdating to evening hour classifies as dinner."""
    uid = auth_client._user_id
    await _create_alias(override_db, uid)

    resp = await auth_client.post(
        "/macro_app/api/v1/aliases/chicken%20rice/log",
        json={"logged_at": "2026-03-15 21:00"},
    )
    assert resp.status_code == 200
    assert resp.json()["meal_type"] == "dinner"  # hour 21 → dinner


@pytest.mark.asyncio
async def test_alias_log_not_found(auth_client):
    """Quick-log a non-existent alias returns 404."""
    resp = await auth_client.post("/macro_app/api/v1/aliases/nonexistent/log")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_alias_log_returns_progress(auth_client, override_db):
    """Quick-log returns progress for the logged date."""
    uid = auth_client._user_id
    await _create_alias(override_db, uid)

    resp = await auth_client.post(
        "/macro_app/api/v1/aliases/chicken%20rice/log",
        json={"logged_at": "2026-03-15 12:00"},
    )
    data = resp.json()
    assert "progress" in data
    assert data["ok"] is True
    assert data["meal_id"] > 0


# --- Accept with Backdate ---

@pytest.mark.asyncio
async def test_accept_session_with_backdate(auth_client, override_db):
    """Accept a meal session with logged_at override stores correct timestamp."""
    import json as _json
    uid = auth_client._user_id
    sid = "test-accept-backdate"
    nutrition = _json.dumps({
        "item_name": "Pasta Carbonara",
        "meal_description": "Creamy pasta",
        "calories": 700, "protein": 30, "carbs": 80, "fat": 25,
        "items": [{"name": "Pasta Carbonara", "calories": 700, "protein": 30, "carbs": 80, "fat": 25, "weight_g": 300}],
    })
    await create_meal_session(override_db, sid, uid, nutrition=nutrition, meal_type="lunch")

    resp = await auth_client.post(
        f"/macro_app/api/v1/meals/sessions/{sid}/accept",
        json={"logged_at": "2026-03-20 13:30"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("error") is None

    from src.db import get_meals_for_day
    meals = await get_meals_for_day(override_db, uid, "2026-03-20")
    assert len(meals) == 1
    assert meals[0]["logged_at"] == "2026-03-20 13:30"
    assert meals[0]["item_name"] == "Pasta Carbonara"
    assert meals[0]["calories"] == 700


# --- Weight API ---

@pytest.mark.asyncio
async def test_weight_log_and_history(auth_client, override_db):
    """Log weight and retrieve history."""
    r = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 75.5})
    assert r.status_code == 200
    data = r.json()
    assert data["weight_kg"] == 75.5
    assert "id" in data
    assert "logged_at" in data
    assert "new_badges" in data

    # History should contain the entry
    r2 = await auth_client.get("/macro_app/api/v1/weight?limit=10")
    assert r2.status_code == 200
    hist = r2.json()
    assert hist["latest"] == 75.5
    assert len(hist["entries"]) == 1

    # users.weight_kg should be updated
    from src.db import get_user_profile
    profile = await get_user_profile(override_db, auth_client._user_id)
    assert profile["weight_kg"] == 75.5


@pytest.mark.asyncio
async def test_weight_delete_updates_profile(auth_client, override_db):
    """Deleting the latest weight entry updates users.weight_kg to the next latest."""
    # Log two entries
    r1 = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 80.0, "logged_at": "2026-03-20 10:00:00"})
    r2 = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 78.0, "logged_at": "2026-03-21 10:00:00"})
    assert r1.status_code == 200
    assert r2.status_code == 200

    from src.db import get_user_profile
    profile = await get_user_profile(override_db, auth_client._user_id)
    assert profile["weight_kg"] == 78.0  # latest

    # Delete the latest entry
    entry_id = r2.json()["id"]
    r3 = await auth_client.delete(f"/macro_app/api/v1/weight/{entry_id}")
    assert r3.status_code == 200

    # Profile should now reflect the remaining entry
    profile = await get_user_profile(override_db, auth_client._user_id)
    assert profile["weight_kg"] == 80.0


@pytest.mark.asyncio
async def test_weight_delete_all_sets_null(auth_client, override_db):
    """Deleting all weight entries sets users.weight_kg to NULL."""
    r1 = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 75.0})
    assert r1.status_code == 200

    entry_id = r1.json()["id"]
    r2 = await auth_client.delete(f"/macro_app/api/v1/weight/{entry_id}")
    assert r2.status_code == 200

    from src.db import get_user_profile
    profile = await get_user_profile(override_db, auth_client._user_id)
    assert profile["weight_kg"] is None


@pytest.mark.asyncio
async def test_weight_backdate_preserves_current(auth_client, override_db):
    """Backdating a weight entry does not overwrite a newer users.weight_kg."""
    # Log today's weight first
    await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 75.0, "logged_at": "2026-03-25 10:00:00"})
    # Now backdate an entry for earlier
    await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 80.0, "logged_at": "2026-03-20 10:00:00"})

    from src.db import get_user_profile
    profile = await get_user_profile(override_db, auth_client._user_id)
    # Should still be 75.0 (the most recent chronologically)
    assert profile["weight_kg"] == 75.0


@pytest.mark.asyncio
async def test_weight_validation_rejects_invalid(auth_client):
    """Weight validation rejects zero, negative, and absurdly large values."""
    r1 = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 0})
    assert r1.status_code == 422

    r2 = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": -5.0})
    assert r2.status_code == 422

    r3 = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 999.0})
    assert r3.status_code == 422


@pytest.mark.asyncio
async def test_weight_delete_not_found(auth_client):
    """Deleting a non-existent weight entry returns 404."""
    r = await auth_client.delete("/macro_app/api/v1/weight/99999")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_weight_history_ordering(auth_client):
    """Weight history returns entries newest-first."""
    await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 70.0, "logged_at": "2026-03-20 10:00:00"})
    await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 72.0, "logged_at": "2026-03-22 10:00:00"})
    await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 71.0, "logged_at": "2026-03-24 10:00:00"})

    r = await auth_client.get("/macro_app/api/v1/weight?limit=10")
    entries = r.json()["entries"]
    assert len(entries) == 3
    assert entries[0]["weight_kg"] == 71.0  # newest
    assert entries[2]["weight_kg"] == 70.0  # oldest
    assert r.json()["latest"] == 71.0
    assert r.json()["start"] == 70.0


@pytest.mark.asyncio
async def test_weight_multiple_same_day(auth_client, override_db):
    """Multiple entries on the same day: profile tracks the chronologically latest."""
    await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 75.0, "logged_at": "2026-03-25 08:00:00"})
    await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 74.5, "logged_at": "2026-03-25 20:00:00"})

    from src.db import get_user_profile
    profile = await get_user_profile(override_db, auth_client._user_id)
    assert profile["weight_kg"] == 74.5  # evening is later

    r = await auth_client.get("/macro_app/api/v1/weight?limit=10")
    assert r.json()["latest"] == 74.5


@pytest.mark.asyncio
async def test_weight_limit_parameter(auth_client):
    """GET /weight respects the limit query parameter."""
    for i in range(5):
        await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 70 + i, "logged_at": f"2026-03-{20+i:02d} 10:00:00"})

    r = await auth_client.get("/macro_app/api/v1/weight?limit=3")
    assert len(r.json()["entries"]) == 3
    # Should be the 3 most recent
    assert r.json()["entries"][0]["weight_kg"] == 74.0


@pytest.mark.asyncio
async def test_weight_empty_history(auth_client):
    """GET /weight with no entries returns correct empty shape."""
    r = await auth_client.get("/macro_app/api/v1/weight")
    assert r.status_code == 200
    data = r.json()
    assert data["entries"] == []
    assert data["latest"] is None
    assert data["start"] is None
    assert data["last_logged_at"] is None


@pytest.mark.asyncio
async def test_weight_badge_earned_after_threshold(auth_client):
    """Scale Warrior badge earned at bronze tier (3 entries)."""
    for i in range(3):
        r = await auth_client.post("/macro_app/api/v1/weight", json={"weight_kg": 70 + i * 0.1, "logged_at": f"2026-03-{20+i:02d} 10:00:00"})

    # The 3rd entry should trigger the badge
    data = r.json()
    badges = data.get("new_badges", [])
    scale_badge = [b for b in badges if b["badge_id"] == "weight_scale_warrior"]
    assert len(scale_badge) == 1
    assert scale_badge[0]["tier"] == 0  # bronze
    assert scale_badge[0]["is_new"] is True


@pytest.mark.asyncio
async def test_weight_cross_user_delete_denied(override_db):
    """User B cannot delete User A's weight entry."""
    from src.db import create_web_user, log_weight

    user_a = await create_web_user(override_db, "a@example.com")
    user_b = await create_web_user(override_db, "b@example.com")

    entry_id = await log_weight(override_db, user_a, 75.0, "2026-03-25 10:00:00")

    # Override auth to user B
    async def mock_b():
        return {"user_id": user_b, "email": "b@example.com", "username": "b@example.com",
                "first_name": "B", "google_sub": None, "created_at": "2026-01-01 00:00:00"}

    app.dependency_overrides[get_current_user] = mock_b

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           headers={"X-Requested-With": "MacroApp"}) as client_b:
        r = await client_b.delete(f"/macro_app/api/v1/weight/{entry_id}")
        assert r.status_code == 404  # not found for user B


# --- Export ---

@pytest.mark.asyncio
async def test_export_csv(auth_client, override_db):
    """CSV export returns a CSV file with meal data."""
    await log_meal(override_db, auth_client._user_id, "2026-03-20 12:00",
                   "Lunch", "", 500, 40, 50, 20, "Gemini")
    r = await auth_client.get("/macro_app/api/v1/settings/export")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "macroshot_export_" in r.headers["content-disposition"]
    body = r.text
    assert "--- MEALS ---" in body
    assert "Lunch" in body


@pytest.mark.asyncio
async def test_export_pdf_requires_pro(auth_client, override_db):
    """PDF export returns 403 for free-tier users."""
    # Override subscription to free
    async def mock_free_sub(user=None, db_path=None):
        return SubscriptionInfo(
            is_premium=False, plan="free", status="active",
            is_og=False, app_mode="hosted", beta_mode=False,
            image_queries_used=0, image_queries_limit=5,
            text_meals_used=0, text_meals_limit=5,
            chats_used=0, chats_limit=1,
            meal_edits_limit=2,
            trial_available=True, trial_ends_at=None,
        )

    app.dependency_overrides[get_subscription_info] = mock_free_sub
    try:
        r = await auth_client.get("/macro_app/api/v1/settings/export?format=pdf")
        assert r.status_code == 403
        assert "Pro" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_subscription_info, None)


@pytest.mark.asyncio
async def test_export_pdf_pro_user(auth_client, override_db):
    """PDF export succeeds for Pro users and returns a valid PDF."""
    await log_meal(override_db, auth_client._user_id, "2026-03-20 12:00",
                   "Lunch", "", 500, 40, 50, 20, "Gemini")
    # In self-host mode (no STRIPE_SECRET_KEY), user is premium by default
    r = await auth_client.get("/macro_app/api/v1/settings/export?format=pdf")
    assert r.status_code == 200
    assert "application/pdf" in r.headers["content-type"]
    assert r.content[:5] == b"%PDF-"


# --- Barcode corrections (read path, overlay, reset endpoint) ---

def _off_product(name="Peanut Butter", brand="365",
                 cal=6080.0, p=7.0, c=7.0, f=18.0,
                 serving_g=32.0, label="2 Tbsp (32 g)"):
    """Build a dict in the shape lookup_barcode returns (mocked OFF response)."""
    return {
        "barcode": "099482450267",
        "product_name": name,
        "brand": brand,
        "calories": cal,
        "protein": p,
        "carbs": c,
        "fat": f,
        "serving_size_g": serving_g,
        "serving_label": label,
        "cal_per_100g": round(cal * 100 / serving_g, 1) if serving_g else None,
        "protein_per_100g": round(p * 100 / serving_g, 1) if serving_g else None,
        "carbs_per_100g": round(c * 100 / serving_g, 1) if serving_g else None,
        "fat_per_100g": round(f * 100 / serving_g, 1) if serving_g else None,
        "source": "openfoodfacts",
        "image_url": "",
        "cached": False,
        "cached_days_ago": None,
    }


@pytest.mark.asyncio
async def test_barcode_scan_returns_raw_off_when_no_correction(auth_client, override_db):
    """No saved correction → response returns the OFF values and
    correction_applied=false."""
    from unittest.mock import patch, AsyncMock

    with patch("src.web.routes.barcode.lookup_barcode",
               new=AsyncMock(return_value=_off_product())):
        r = await auth_client.post(
            "/macro_app/api/v1/meals/barcode",
            json={"barcode": "099482450267", "servings": 1, "meal_type": ""},
        )
    assert r.status_code == 200
    body = r.json()
    # Scans are free - session should be created and nutrition returned
    assert body["session_id"]
    assert body["nutrition"] is not None
    assert body["nutrition"]["calories"] == 6080  # OFF's broken value
    assert body["correction_applied"] is False
    assert body["corrected_at"] is None


@pytest.mark.asyncio
async def test_barcode_scan_overlays_saved_correction(auth_client, override_db):
    """Saved correction → response uses corrected macros and
    correction_applied=true."""
    from unittest.mock import patch, AsyncMock
    from src.db import save_barcode_correction

    # Pre-save a correction for this user
    await save_barcode_correction(
        db_path=override_db,
        user_id=auth_client._user_id,
        barcode="099482450267",
        product_name="Peanut Butter",
        brand="365",
        calories=190.0,  # correct value
        protein=7.0,
        carbs=7.0,
        fat=18.0,
        serving_size_g=32.0,
        serving_label="2 Tbsp (32 g)",
    )

    # OFF still returns the bad data
    with patch("src.web.routes.barcode.lookup_barcode",
               new=AsyncMock(return_value=_off_product())):
        r = await auth_client.post(
            "/macro_app/api/v1/meals/barcode",
            json={"barcode": "099482450267", "servings": 1, "meal_type": ""},
        )
    assert r.status_code == 200
    body = r.json()
    # Response should use the CORRECTION's values, not OFF's 6080
    assert body["nutrition"]["calories"] == 190
    assert body["correction_applied"] is True
    assert body["corrected_at"] is not None


@pytest.mark.asyncio
async def test_barcode_correction_is_per_user(auth_client, override_db):
    """User A's correction must not affect user B's scan."""
    from unittest.mock import patch, AsyncMock
    from src.db import create_web_user, save_barcode_correction

    user_b_id = await create_web_user(override_db, "userb@example.com")
    await save_barcode_correction(
        db_path=override_db,
        user_id=user_b_id,
        barcode="099482450267",
        product_name="Peanut Butter",
        brand="365",
        calories=190.0, protein=7.0, carbs=7.0, fat=18.0,
        serving_size_g=32.0, serving_label="2 Tbsp (32 g)",
    )

    # Scan as user A (from auth_client fixture) - should NOT see B's correction
    with patch("src.web.routes.barcode.lookup_barcode",
               new=AsyncMock(return_value=_off_product())):
        r = await auth_client.post(
            "/macro_app/api/v1/meals/barcode",
            json={"barcode": "099482450267", "servings": 1, "meal_type": ""},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["nutrition"]["calories"] == 6080  # A sees raw OFF, not B's correction
    assert body["correction_applied"] is False


@pytest.mark.asyncio
async def test_barcode_scan_stashes_raw_off_as_original_nutrition(auth_client, override_db):
    """The session's original_nutrition must be the RAW OFF per-serving
    (not the overlaid correction), so a second-pass correction still
    passes the threshold check in accept_meal."""
    from unittest.mock import patch, AsyncMock
    from src.db import save_barcode_correction, get_meal_session

    await save_barcode_correction(
        db_path=override_db,
        user_id=auth_client._user_id,
        barcode="099482450267",
        product_name="Peanut Butter",
        brand="365",
        calories=190.0, protein=7.0, carbs=7.0, fat=18.0,
        serving_size_g=32.0, serving_label="2 Tbsp (32 g)",
    )

    with patch("src.web.routes.barcode.lookup_barcode",
               new=AsyncMock(return_value=_off_product())):
        r = await auth_client.post(
            "/macro_app/api/v1/meals/barcode",
            json={"barcode": "099482450267", "servings": 1, "meal_type": ""},
        )
    assert r.status_code == 200
    session_id = r.json()["session_id"]

    session = await get_meal_session(override_db, session_id, auth_client._user_id)
    assert session is not None
    assert session["barcode"] == "099482450267"
    stashed = json.loads(session["original_nutrition"])
    # Should be RAW OFF (6080), not the correction (190). This is the
    # bug-fix assertion: without the pre-overlay stash, this would be 190.
    assert stashed["calories"] == 6080.0


@pytest.mark.asyncio
async def test_reset_barcode_correction_endpoint(auth_client, override_db):
    """DELETE /meals/barcode/{barcode}/correction removes the user's row."""
    from src.db import save_barcode_correction, get_barcode_correction

    await save_barcode_correction(
        db_path=override_db,
        user_id=auth_client._user_id,
        barcode="099482450267",
        product_name="X", brand="",
        calories=100, protein=10, carbs=10, fat=2,
        serving_size_g=50, serving_label="",
    )
    # Row exists
    assert await get_barcode_correction(override_db, auth_client._user_id, "099482450267") is not None

    r = await auth_client.delete("/macro_app/api/v1/meals/barcode/099482450267/correction")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "removed": True}

    # Second delete: still 200 but removed=False
    r2 = await auth_client.delete("/macro_app/api/v1/meals/barcode/099482450267/correction")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True, "removed": False}

    # Row is gone
    assert await get_barcode_correction(override_db, auth_client._user_id, "099482450267") is None


@pytest.mark.asyncio
async def test_reset_barcode_correction_rejects_invalid_barcode(auth_client, override_db):
    """DELETE with a non-digit or too-short barcode returns 400."""
    r = await auth_client.delete("/macro_app/api/v1/meals/barcode/not-a-barcode/correction")
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_reset_barcode_correction_is_per_user(override_db):
    """User B cannot delete user A's correction. Without per-user scoping
    this would leak into a cross-user IDOR."""
    from src.db import create_web_user, save_barcode_correction, get_barcode_correction

    user_a = await create_web_user(override_db, "a@example.com")
    user_b = await create_web_user(override_db, "b@example.com")

    await save_barcode_correction(
        db_path=override_db,
        user_id=user_a,
        barcode="099482450267",
        product_name="X", brand="",
        calories=100, protein=10, carbs=10, fat=2,
        serving_size_g=50, serving_label="",
    )

    async def mock_b():
        return {"user_id": user_b, "email": "b@example.com", "username": "b@example.com",
                "first_name": "B", "google_sub": None, "created_at": "2026-01-01 00:00:00"}
    app.dependency_overrides[get_current_user] = mock_b

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test",
                               headers={"X-Requested-With": "MacroApp"}) as client_b:
            r = await client_b.delete("/macro_app/api/v1/meals/barcode/099482450267/correction")
            # Endpoint returns 200 but removed=False - the DELETE scoped to
            # user B's row (which doesn't exist), not touching user A's.
            assert r.status_code == 200
            assert r.json()["removed"] is False

        # User A's correction must still exist
        row = await get_barcode_correction(override_db, user_a, "099482450267")
        assert row is not None
        assert row["calories"] == 100
    finally:
        app.dependency_overrides.pop(get_current_user, None)
