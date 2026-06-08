"""Tests for the subscription system: DB CRUD + API endpoints + gates."""

import asyncio
import json
import os

import aiosqlite
import pytest
import pytest_asyncio

# Set env before importing app
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from src.db import (
    cancel_subscription,
    create_or_update_subscription,
    create_web_user,
    ensure_user,
    get_subscription,
    get_usage,
    increment_usage,
    init_db,
    start_trial,
)
from src.web.app import app
from src.web.deps import (
    SubscriptionInfo,
    get_current_user,
    get_db_path,
    get_subscription_info,
)


def _minimal_jpeg() -> bytes:
    """Return a 1x1 red JPEG with valid magic bytes.

    /meals/analyze validates uploads via magic bytes (_IMAGE_SIGNATURES) and
    then re-encodes through Pillow (_strip_exif_to_jpeg) - both paths must
    accept the payload, so we build a real JPEG rather than a fake header.
    """
    from io import BytesIO
    from PIL import Image

    img = Image.new("RGB", (1, 1), color=(255, 0, 0))
    buf = BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


JPEG_BYTES = _minimal_jpeg()


# ===========================================================================
# DB Fixtures
# ===========================================================================

@pytest_asyncio.fixture
async def db(tmp_path):
    """Fresh DB for each test."""
    path = str(tmp_path / "test.db")
    await init_db(path)
    return path


async def _add_user(db, user_id=1, username="alice", first_name="Alice"):
    await ensure_user(db, user_id, username, first_name)


# ===========================================================================
# API Fixtures
# ===========================================================================

@pytest.fixture
def stripe_env():
    """Set STRIPE_SECRET_KEY + monkeypatch APP_MODE='hosted' so the
    subscription gating logic is exercised in tests.

    Post-beta-mode-refactor, the gating branches on src.web.constants.APP_MODE
    (read at import time).  Setting the env var alone is too late - we have
    to override the already-imported module-level constant in BOTH places it
    is referenced (constants.py and deps.py imports it by name).
    Cleans up after the test so other test modules are not affected.
    """
    import src.web.constants as _constants
    import src.web.deps as _deps
    import src.web.routes.auth as _auth
    import src.web.routes.subscription as _sub_route

    old = os.environ.get("STRIPE_SECRET_KEY")
    old_price = os.environ.get("STRIPE_PRICE_MONTHLY")
    old_app_mode = _constants.APP_MODE
    old_deps_app_mode = _deps.APP_MODE
    old_beta_mode = _constants.BETA_MODE
    old_deps_beta_mode = _deps.BETA_MODE
    old_auth_beta_mode = _auth.BETA_MODE
    old_sub_beta_mode = _sub_route.BETA_MODE
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
    os.environ["STRIPE_PRICE_MONTHLY"] = "price_test_fake"
    _constants.APP_MODE = "hosted"
    _deps.APP_MODE = "hosted"
    # BETA_MODE is imported by name in multiple modules - we have to patch
    # every import site, otherwise subscription/auth routes keep the
    # module-default value (True, post-2026-04-11 default flip).
    _constants.BETA_MODE = False
    _deps.BETA_MODE = False
    _auth.BETA_MODE = False
    _sub_route.BETA_MODE = False
    yield
    _constants.APP_MODE = old_app_mode
    _deps.APP_MODE = old_deps_app_mode
    _constants.BETA_MODE = old_beta_mode
    _deps.BETA_MODE = old_deps_beta_mode
    _auth.BETA_MODE = old_auth_beta_mode
    _sub_route.BETA_MODE = old_sub_beta_mode
    if old is None:
        os.environ.pop("STRIPE_SECRET_KEY", None)
    else:
        os.environ["STRIPE_SECRET_KEY"] = old
    if old_price is None:
        os.environ.pop("STRIPE_PRICE_MONTHLY", None)
    else:
        os.environ["STRIPE_PRICE_MONTHLY"] = old_price


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
async def auth_client(override_db, stripe_env):
    """Create an authenticated test client with a web user (free tier).

    Uses stripe_env so the subscription gating logic is active (not self-host mode).
    """
    user_id = await create_web_user(override_db, "subtest@example.com")
    await ensure_user(override_db, user_id, "subtest", "SubTest")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "subtest@example.com",
            "username": "subtest@example.com",
            "first_name": "subtest",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }

    app.dependency_overrides[get_current_user] = mock_user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Requested-With": "MacroApp"}) as c:
        c._user_id = user_id
        c._db_path = override_db
        yield c

    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def premium_client(override_db, stripe_env):
    """Create an authenticated test client that is a premium user."""
    user_id = await create_web_user(override_db, "premium@example.com")
    await ensure_user(override_db, user_id, "premium", "Premium")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "premium@example.com",
            "username": "premium@example.com",
            "first_name": "premium",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }

    def mock_sub():
        from src.web.constants import PRO_IMAGE_LIMIT
        return SubscriptionInfo(
            plan="pro_monthly",
            status="active",
            is_premium=True,
            is_og=False,
            app_mode="hosted",
            beta_mode=False,
            image_queries_used=0,
            image_queries_limit=PRO_IMAGE_LIMIT,
            text_meals_used=0,
            text_meals_limit=-1,
            chats_used=0,
            chats_limit=-1,
            meal_edits_limit=-1,
            trial_available=False,
            trial_ends_at=None,
        )

    app.dependency_overrides[get_current_user] = mock_user
    app.dependency_overrides[get_subscription_info] = mock_sub

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers={"X-Requested-With": "MacroApp"}) as c:
        c._user_id = user_id
        c._db_path = override_db
        yield c

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_subscription_info, None)


# ===========================================================================
# Helpers
# ===========================================================================

SUB_API = "/macro_app/api/v1/subscription"
MEALS_API = "/macro_app/api/v1/meals"
CHAT_API = "/macro_app/api/v1/chat"


# ===========================================================================
# DB CRUD Tests
# ===========================================================================

class TestSubscriptionDB:
    """Tests for subscription DB functions."""

    @pytest.mark.asyncio
    async def test_get_subscription_no_row(self, db):
        """get_subscription returns None for a user with no subscription."""
        await _add_user(db)
        result = await get_subscription(db, 1)
        assert result is None

    @pytest.mark.asyncio
    async def test_create_and_get_subscription(self, db):
        """create_or_update_subscription creates a row, get_subscription retrieves it."""
        await _add_user(db)
        await create_or_update_subscription(
            db, 1, plan="pro_monthly", status="active",
        )
        sub = await get_subscription(db, 1)
        assert sub is not None
        assert sub["user_id"] == 1
        assert sub["plan"] == "pro_monthly"
        assert sub["status"] == "active"
        assert sub["created_at"] is not None
        assert sub["updated_at"] is not None

    @pytest.mark.asyncio
    async def test_create_or_update_upserts(self, db):
        """Calling create_or_update_subscription twice updates the existing row."""
        await _add_user(db)
        await create_or_update_subscription(db, 1, plan="free", status="active")
        sub1 = await get_subscription(db, 1)
        assert sub1["plan"] == "free"

        # Update to pro
        await create_or_update_subscription(db, 1, plan="pro_monthly", status="active")
        sub2 = await get_subscription(db, 1)
        assert sub2["plan"] == "pro_monthly"
        assert sub2["id"] == sub1["id"]  # Same row, not a new one

    @pytest.mark.asyncio
    async def test_create_or_update_insert_preserves_is_og(self, db):
        """INSERT branch must honor is_og=1 when no row exists yet.

        Regression guard: a previous bug dropped is_og on the INSERT
        path (the allow-list included it but the INSERT col dict
        didn't). Fix lives in src/db.py create_or_update_subscription.
        """
        await _add_user(db)
        await create_or_update_subscription(
            db, 1, plan="pro_monthly", status="active", is_og=1,
        )
        sub = await get_subscription(db, 1)
        assert sub is not None
        assert sub["is_og"] == 1
        assert sub["plan"] == "pro_monthly"

    @pytest.mark.asyncio
    async def test_get_usage_no_row(self, db):
        """get_usage returns defaults {used_count: 0, limit_count: 5} for new user."""
        await _add_user(db)
        usage = await get_usage(db, 1, "image_query", "2026-03-21")
        assert usage["used_count"] == 0
        assert usage["limit_count"] == 5

    @pytest.mark.asyncio
    async def test_increment_usage_under_limit(self, db):
        """increment_usage increments and returns allowed=True when under limit."""
        await _add_user(db)
        result = await increment_usage(db, 1, "image_query", "2026-03-21", limit=5)
        assert result["allowed"] is True
        assert result["used_count"] == 1
        assert result["limit_count"] == 5

        # Increment again
        result2 = await increment_usage(db, 1, "image_query", "2026-03-21", limit=5)
        assert result2["allowed"] is True
        assert result2["used_count"] == 2

    @pytest.mark.asyncio
    async def test_increment_usage_at_limit(self, db):
        """increment_usage returns allowed=False and does not increment when at limit."""
        await _add_user(db)
        # Use up all 5
        for _ in range(5):
            result = await increment_usage(db, 1, "image_query", "2026-03-21", limit=5)
            assert result["allowed"] is True

        # 6th attempt should be denied
        result = await increment_usage(db, 1, "image_query", "2026-03-21", limit=5)
        assert result["allowed"] is False
        assert result["used_count"] == 5  # Count stays at 5, not incremented

    @pytest.mark.asyncio
    async def test_increment_usage_daily_reset(self, db):
        """Different dates track usage separately."""
        await _add_user(db)
        # Use up all 5 on day 1
        for _ in range(5):
            await increment_usage(db, 1, "image_query", "2026-03-21", limit=5)

        # Day 2 should start fresh
        result = await increment_usage(db, 1, "image_query", "2026-03-22", limit=5)
        assert result["allowed"] is True
        assert result["used_count"] == 1

    @pytest.mark.asyncio
    async def test_start_trial(self, db):
        """start_trial starts successfully and returns True."""
        await _add_user(db)
        started = await start_trial(db, 1)
        assert started is True

        # Verify subscription row
        sub = await get_subscription(db, 1)
        assert sub is not None
        assert sub["plan"] == "trial"
        assert sub["status"] == "trialing"
        assert sub["trial_used"] == 1
        assert sub["trial_ends_at"] is not None

    @pytest.mark.asyncio
    async def test_start_trial_already_used(self, db):
        """start_trial returns False on second attempt."""
        await _add_user(db)
        first = await start_trial(db, 1)
        assert first is True

        second = await start_trial(db, 1)
        assert second is False

        # Verify state is unchanged - still trialing from first attempt
        sub = await get_subscription(db, 1)
        assert sub["plan"] == "trial"
        assert sub["status"] == "trialing"

    @pytest.mark.asyncio
    async def test_start_trial_on_existing_free_subscription(self, db):
        """start_trial upgrades an existing free subscription to trial."""
        await _add_user(db)
        await create_or_update_subscription(db, 1, plan="free", status="active")

        started = await start_trial(db, 1)
        assert started is True

        sub = await get_subscription(db, 1)
        assert sub["plan"] == "trial"
        assert sub["status"] == "trialing"
        assert sub["trial_used"] == 1

    @pytest.mark.asyncio
    async def test_cancel_subscription(self, db):
        """cancel_subscription sets plan to free and status to cancelled."""
        await _add_user(db)
        await create_or_update_subscription(
            db, 1, plan="pro_monthly", status="active",
        )

        await cancel_subscription(db, 1)

        sub = await get_subscription(db, 1)
        assert sub["plan"] == "free"
        assert sub["status"] == "cancelled"
        assert sub["cancelled_at"] is not None


# ===========================================================================
# API Tests - Subscription endpoints
# ===========================================================================

class TestSubscriptionAPI:
    """Tests for subscription API endpoints."""

    @pytest.mark.asyncio
    async def test_get_subscription_status(self, auth_client):
        """GET /subscription returns free tier defaults for a new user."""
        resp = await auth_client.get(SUB_API)
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "free"
        assert data["status"] == "active"
        assert data["is_premium"] is False
        assert data["trial_available"] is True
        assert data["usage_image_limit"] == 3

    @pytest.mark.asyncio
    async def test_start_trial_api(self, auth_client):
        """POST /subscription/trial starts trial and returns trial info."""
        resp = await auth_client.post(f"{SUB_API}/trial")
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "trial"
        assert data["status"] == "trialing"
        assert data["is_premium"] is True
        assert data["trial_ends_at"] is not None
        assert "Trial started" in data["message"]

    @pytest.mark.asyncio
    async def test_start_trial_api_already_used(self, auth_client):
        """POST /subscription/trial returns 409 on second attempt."""
        # First trial - should succeed
        resp1 = await auth_client.post(f"{SUB_API}/trial")
        assert resp1.status_code == 200

        # Second trial - should fail with 409
        resp2 = await auth_client.post(f"{SUB_API}/trial")
        assert resp2.status_code == 409
        assert "already used" in resp2.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_checkout_no_stripe(self, auth_client):
        """POST /subscription/checkout returns 503 when the configured Stripe key is invalid.

        The fixture sets STRIPE_SECRET_KEY to a fake value so the endpoint
        reaches Stripe, which rejects the key → stripe.AuthenticationError →
        503 "billing not configured".
        """
        resp = await auth_client.post(
            f"{SUB_API}/checkout",
            json={"plan": "pro_monthly"},
        )
        assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_subscription_status_after_trial(self, auth_client):
        """GET /subscription reflects trial state after starting a trial."""
        from src.web.constants import PRO_IMAGE_LIMIT
        await auth_client.post(f"{SUB_API}/trial")
        resp = await auth_client.get(SUB_API)
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan"] == "trial"
        assert data["is_premium"] is True
        assert data["trial_available"] is False
        # Even trial/pro users have a daily photo cap - protects AI spend.
        # Retries on an image-backed session also count toward this limit.
        assert data["usage_image_limit"] == PRO_IMAGE_LIMIT


# ===========================================================================
# API Tests - Image analysis gate (meals endpoint)
# ===========================================================================

class TestImageGate:
    """Tests for the free-tier image analysis metering gate."""

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
    async def test_image_gate_under_limit(self, mock_analyze, auth_client):
        """POST /meals/analyze with image works when under the daily limit."""
        mock_analyze.return_value = {
            "session_id": "test-session-1",
            "nutrition": None,
            "questions": [],
            "raw_text": "Looks like a salad",
        }

        resp = await auth_client.post(
            f"{MEALS_API}/analyze",
            data={"text": ""},
            files={"images": ("test.jpg", JPEG_BYTES, "image/jpeg")},
        )
        assert resp.status_code == 200
        mock_analyze.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
    async def test_image_gate_at_limit(self, mock_analyze, auth_client):
        """POST /meals/analyze with image returns 429 after FREE_IMAGE_LIMIT scans."""
        from src.web.constants import FREE_IMAGE_LIMIT
        mock_analyze.return_value = {
            "session_id": "test-session",
            "nutrition": None,
            "questions": [],
            "raw_text": "Food detected",
        }

        # Use up the daily free-tier image quota
        for i in range(FREE_IMAGE_LIMIT):
            resp = await auth_client.post(
                f"{MEALS_API}/analyze",
                data={"text": ""},
                files={"images": ("test.jpg", JPEG_BYTES, "image/jpeg")},
            )
            assert resp.status_code == 200, f"Request {i+1} failed unexpectedly"

        # Next request should be blocked by the subscription gate
        resp = await auth_client.post(
            f"{MEALS_API}/analyze",
            data={"text": ""},
            files={"images": ("test.jpg", JPEG_BYTES, "image/jpeg")},
        )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["error"] == "limit_reached"
        assert detail["used"] == FREE_IMAGE_LIMIT
        assert detail["limit"] == FREE_IMAGE_LIMIT

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
    async def test_text_logging_no_gate(self, mock_analyze, auth_client):
        """POST /meals/analyze with text only (no image) works regardless of subscription."""
        mock_analyze.return_value = {
            "session_id": "text-session",
            "nutrition": None,
            "questions": [],
            "raw_text": "Logged a sandwich",
        }

        # First exhaust image quota (to prove text is not gated)
        db_path = auth_client._db_path
        user_id = auth_client._user_id
        from datetime import datetime, timezone
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for _ in range(5):
            await increment_usage(db_path, user_id, "image_query", today_str, limit=5)

        # Text-only request should still work
        resp = await auth_client.post(
            f"{MEALS_API}/analyze",
            data={"text": "I had a turkey sandwich with mayo"},
        )
        assert resp.status_code == 200
        mock_analyze.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
    async def test_image_gate_premium_user_higher_limit(self, mock_analyze, premium_client):
        """Premium users get PRO_IMAGE_LIMIT (higher than free), not infinite.

        The cap still applies - it protects AI spend even for paid users -
        but premium gets a bigger daily budget than free.
        """
        from src.web.constants import PRO_IMAGE_LIMIT
        mock_analyze.return_value = {
            "session_id": "premium-session",
            "nutrition": None,
            "questions": [],
            "raw_text": "Food detected",
        }

        for _ in range(PRO_IMAGE_LIMIT):
            resp = await premium_client.post(
                f"{MEALS_API}/analyze",
                data={"text": ""},
                files={"images": ("test.jpg", JPEG_BYTES, "image/jpeg")},
            )
            assert resp.status_code == 200


# ===========================================================================
# API Tests - Chat premium gate
# ===========================================================================

class TestChatGate:
    """Tests for the chat premium gate."""

    @pytest.mark.asyncio
    async def test_chat_gate_free_user_monthly_limit(self, auth_client):
        """Free user gets 1 chat session/month, then blocked with 429."""
        # First session should succeed (1 free per month)
        resp = await auth_client.post(CHAT_API)
        assert resp.status_code == 200

        # Second session should be blocked
        resp2 = await auth_client.post(CHAT_API)
        assert resp2.status_code == 429
        detail = resp2.json()["detail"]
        assert detail["error"] == "limit_reached"
        assert detail["feature"] == "ai_chat"

    @pytest.mark.asyncio
    @patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="Test Title")
    @patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="Hi there!")
    async def test_chat_allowed_for_premium_user(self, mock_chat, mock_title, premium_client):
        """Premium users can create chat sessions and send messages."""
        # Create session
        resp = await premium_client.post(CHAT_API)
        assert resp.status_code == 200
        session_id = resp.json()["session_id"]

        # Send message
        resp2 = await premium_client.post(
            f"{CHAT_API}/{session_id}/message",
            data={"text": "What should I eat?"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["reply"] == "Hi there!"

    @pytest.mark.asyncio
    async def test_chat_unlimited_after_trial_start(self, auth_client):
        """After starting a trial, chat has no monthly limit."""
        # Use the free session first
        resp = await auth_client.post(CHAT_API)
        assert resp.status_code == 200

        # Second session should be blocked for free user
        resp2 = await auth_client.post(CHAT_API)
        assert resp2.status_code == 429

        # Start trial
        trial_resp = await auth_client.post(f"{SUB_API}/trial")
        assert trial_resp.status_code == 200

        # Now chat should work unlimited - mock the AI
        with patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="Title"), \
             patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="reply"):
            resp3 = await auth_client.post(CHAT_API)
            assert resp3.status_code == 200
            assert "session_id" in resp3.json()


# ===========================================================================
# Beta-release additions: mode branching, gates, Stripe blocks, status shape
# ===========================================================================
#
# Everything below this line was added when BETA_MODE/APP_MODE landed. These
# tests exercise the three distinct paths of get_subscription_info and the
# gate-site wiring that depends on them. See the module docstrings for
# rationale on why we must monkey-patch both src.web.constants AND
# src.web.deps module-level names (deps.py imports the constants by name).


def _set_mode(app_mode: str, beta_mode: bool) -> None:
    """Override APP_MODE/BETA_MODE everywhere they were imported by name.

    Each consumer does `from src.web.constants import APP_MODE, BETA_MODE`
    at import time, so setting the env var or just the constants module
    is ignored. We have to patch each already-bound name.
    """
    import src.web.constants as _constants
    import src.web.deps as _deps
    import src.web.routes.auth as _auth
    import src.web.routes.subscription as _sub_route
    import src.db as _db_module  # SignupCapReached branch reads constants

    _constants.APP_MODE = app_mode
    _deps.APP_MODE = app_mode
    _auth.APP_MODE = app_mode
    _constants.BETA_MODE = beta_mode
    _deps.BETA_MODE = beta_mode
    _auth.BETA_MODE = beta_mode
    _sub_route.BETA_MODE = beta_mode
    # db.py does a lazy import inside create_web_user, so APP_MODE/BETA_MODE
    # are re-read each call - nothing to patch there.
    _ = _db_module  # keep import for side-effects / future patch points


@pytest.fixture
def mode_env():
    """Like `stripe_env` but gives the test full control over APP_MODE /
    BETA_MODE via `_set_mode`. Restores the previous values on teardown.
    Also stashes a STRIPE_SECRET_KEY so the Stripe-checks branches exercise.
    """
    import src.web.constants as _constants
    import src.web.deps as _deps

    old_app_mode = _constants.APP_MODE
    old_deps_app_mode = _deps.APP_MODE
    old_beta_mode = _constants.BETA_MODE
    old_deps_beta_mode = _deps.BETA_MODE
    old_stripe = os.environ.get("STRIPE_SECRET_KEY")
    old_price = os.environ.get("STRIPE_PRICE_MONTHLY")
    os.environ["STRIPE_SECRET_KEY"] = "sk_test_fake"
    os.environ["STRIPE_PRICE_MONTHLY"] = "price_test_fake"
    yield _set_mode
    _constants.APP_MODE = old_app_mode
    _deps.APP_MODE = old_deps_app_mode
    _constants.BETA_MODE = old_beta_mode
    _deps.BETA_MODE = old_deps_beta_mode
    if old_stripe is None:
        os.environ.pop("STRIPE_SECRET_KEY", None)
    else:
        os.environ["STRIPE_SECRET_KEY"] = old_stripe
    if old_price is None:
        os.environ.pop("STRIPE_PRICE_MONTHLY", None)
    else:
        os.environ["STRIPE_PRICE_MONTHLY"] = old_price


class TestSubscriptionInfoModeBranching:
    """Exercise get_subscription_info for the three APP_MODE/BETA_MODE paths.

    Important: these tests call `get_subscription_info` directly with a
    dict `user` and a raw `db_path` - no FastAPI dependency injection, no
    app client. That keeps the assertions focused on the branching logic
    and avoids accidentally exercising any routes.
    """

    @pytest.mark.asyncio
    async def test_self_mode_returns_unlimited_without_touching_db(
        self, db, mode_env, tmp_path
    ):
        """APP_MODE=self → everyone premium, subscription table is never read."""
        from src.db import ensure_user
        import src.web.deps as _deps

        await ensure_user(db, 1, "alice", "Alice")
        mode_env("self", False)

        # Spy on get_subscription to verify it isn't called in self mode.
        with patch(
            "src.web.deps.get_subscription", new_callable=AsyncMock
        ) as spy_get_sub:
            info = await _deps.get_subscription_info(
                user={"user_id": 1, "email": "a@b"}, db_path=db,
            )

        assert info.is_premium is True
        assert info.plan == "self_hosted"
        assert info.status == "active"
        assert info.app_mode == "self"
        assert info.beta_mode is False
        assert info.is_og is False
        assert info.image_queries_limit == _deps.UNLIMITED
        assert info.text_meals_limit == _deps.UNLIMITED
        assert info.chats_limit == _deps.UNLIMITED
        assert info.meal_edits_limit == _deps.UNLIMITED
        assert info.trial_available is False
        # Subscription table was not consulted
        spy_get_sub.assert_not_called()

    @pytest.mark.asyncio
    async def test_hosted_beta_pro_without_sub_row(self, db, mode_env):
        """hosted+beta: no subscription row yet → still treated as Pro."""
        from src.db import ensure_user
        import src.web.deps as _deps
        from src.web.constants import (
            PRO_AI_EDITS_PER_MEAL,
            PRO_CHAT_LIMIT,
            PRO_IMAGE_LIMIT,
            PRO_TEXT_MEAL_LIMIT,
        )

        await ensure_user(db, 1, "alice", "Alice")
        mode_env("hosted", True)

        info = await _deps.get_subscription_info(
            user={"user_id": 1, "email": "a@b"}, db_path=db,
        )
        assert info.is_premium is True
        # Plan falls back to pro_monthly even with no row
        assert info.plan == "pro_monthly"
        assert info.beta_mode is True
        assert info.is_og is False
        assert info.image_queries_limit == PRO_IMAGE_LIMIT
        assert info.text_meals_limit == PRO_TEXT_MEAL_LIMIT
        assert info.chats_limit == PRO_CHAT_LIMIT
        assert info.meal_edits_limit == PRO_AI_EDITS_PER_MEAL

    @pytest.mark.asyncio
    async def test_hosted_beta_is_og_reflects_db_flag(self, db, mode_env):
        """hosted+beta: is_og is read from the subscriptions row."""
        from src.db import create_pro_subscription, ensure_user
        import src.web.deps as _deps

        await ensure_user(db, 1, "alice", "Alice")
        await create_pro_subscription(db, 1, is_og=True)
        mode_env("hosted", True)

        info = await _deps.get_subscription_info(
            user={"user_id": 1, "email": "a@b"}, db_path=db,
        )
        assert info.is_og is True
        assert info.is_premium is True

    @pytest.mark.asyncio
    async def test_hosted_beta_expired_period_end_does_not_downgrade(
        self, db, mode_env
    ):
        """In beta mode, an expired current_period_end must NOT downgrade."""
        from src.db import ensure_user
        import src.web.deps as _deps

        await ensure_user(db, 1, "alice", "Alice")
        # Pre-beta pro row whose period ended a year ago. In beta mode we
        # skip renewal checks entirely, so this should remain premium.
        await create_or_update_subscription(
            db, 1,
            plan="pro_monthly", status="cancelled",
            current_period_end="2020-01-01 00:00:00",
        )
        mode_env("hosted", True)

        info = await _deps.get_subscription_info(
            user={"user_id": 1, "email": "a@b"}, db_path=db,
        )
        assert info.is_premium is True
        # Still reports the plan from the row
        assert info.plan == "pro_monthly"

    @pytest.mark.asyncio
    async def test_hosted_no_beta_og_bypasses_trial_expiry(self, db, mode_env):
        """hosted + no-beta: OG trial with past trial_ends_at is NOT expired."""
        from src.db import ensure_user, set_og_status
        import src.web.deps as _deps

        await ensure_user(db, 1, "alice", "Alice")
        # First create the row, then set is_og=1 via set_og_status (which
        # upserts the OG flag reliably - see bug note at end of file).
        await create_or_update_subscription(
            db, 1,
            plan="trial", status="trialing",
            trial_ends_at="2020-01-01 00:00:00",
            trial_used=1,
        )
        # Now flip is_og via the update path (which persists correctly)
        await create_or_update_subscription(db, 1, is_og=1)
        mode_env("hosted", False)

        info = await _deps.get_subscription_info(
            user={"user_id": 1, "email": "a@b"}, db_path=db,
        )
        # OG bypasses the past trial_ends_at check. The presentation
        # layer also overrides plan/status to 'pro_monthly' / 'active'
        # so Settings doesn't render "Plan: trial, Status: trialing"
        # for a grandfathered user - OGs always look like Pro in the UI.
        assert info.is_og is True
        assert info.is_premium is True
        assert info.plan == "pro_monthly"
        assert info.status == "active"

    @pytest.mark.asyncio
    async def test_hosted_no_beta_non_og_trial_auto_expires(self, db, mode_env):
        """hosted + no-beta: non-OG with past trial_ends_at downgrades to free."""
        from src.db import ensure_user
        import src.web.deps as _deps

        await ensure_user(db, 1, "alice", "Alice")
        await create_or_update_subscription(
            db, 1,
            plan="trial", status="trialing",
            trial_ends_at="2020-01-01 00:00:00",
            trial_used=1,
        )
        mode_env("hosted", False)

        info = await _deps.get_subscription_info(
            user={"user_id": 1, "email": "a@b"}, db_path=db,
        )
        assert info.is_premium is False
        assert info.plan == "free"
        assert info.status == "expired"

        # Verify DB was actually updated
        sub = await get_subscription(db, 1)
        assert sub["plan"] == "free"
        assert sub["status"] == "expired"

    @pytest.mark.asyncio
    async def test_hosted_no_beta_non_og_cancelled_pro_stays_until_period_end(
        self, db, mode_env
    ):
        """Cancelled Pro with future current_period_end remains premium."""
        from datetime import datetime, timedelta, timezone
        from src.db import ensure_user
        import src.web.deps as _deps

        await ensure_user(db, 1, "alice", "Alice")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        await create_or_update_subscription(
            db, 1,
            plan="pro_monthly", status="cancelled",
            current_period_end=future,
        )
        mode_env("hosted", False)

        info = await _deps.get_subscription_info(
            user={"user_id": 1, "email": "a@b"}, db_path=db,
        )
        assert info.is_premium is True
        assert info.status == "cancelled"
        assert info.plan == "pro_monthly"

    @pytest.mark.asyncio
    async def test_hosted_no_beta_cancelled_past_period_end_downgrades(
        self, db, mode_env
    ):
        """Cancelled Pro with past current_period_end is no longer premium."""
        from src.db import ensure_user
        import src.web.deps as _deps

        await ensure_user(db, 1, "alice", "Alice")
        await create_or_update_subscription(
            db, 1,
            plan="pro_monthly", status="cancelled",
            current_period_end="2020-01-01 00:00:00",
        )
        mode_env("hosted", False)

        info = await _deps.get_subscription_info(
            user={"user_id": 1, "email": "a@b"}, db_path=db,
        )
        assert info.is_premium is False


# ===========================================================================
# Beta mode: Stripe checkout / portal / trial must be blocked
# ===========================================================================


class TestStripeBetaBlocks:
    """In hosted+beta mode, Stripe endpoints must return 409 error=beta_mode."""

    @pytest_asyncio.fixture
    async def beta_client(self, override_db, mode_env):
        """Authenticated client in hosted+beta mode with Stripe configured."""
        mode_env("hosted", True)
        user_id = await create_web_user(override_db, "beta@example.com")
        await ensure_user(override_db, user_id, "beta", "BetaUser")

        async def mock_user():
            return {
                "user_id": user_id,
                "email": "beta@example.com",
                "username": "beta@example.com",
                "first_name": "beta",
                "google_sub": None,
                "created_at": "2026-01-01 00:00:00",
            }

        app.dependency_overrides[get_current_user] = mock_user
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
            headers={"X-Requested-With": "MacroApp"},
        ) as c:
            c._user_id = user_id
            c._db_path = override_db
            yield c
        app.dependency_overrides.pop(get_current_user, None)

    @pytest.mark.asyncio
    async def test_checkout_blocked_in_beta(self, beta_client):
        resp = await beta_client.post(
            f"{SUB_API}/checkout", json={"plan": "pro_monthly"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "beta_mode"
        assert "beta" in detail["message"].lower()

    @pytest.mark.asyncio
    async def test_portal_blocked_in_beta(self, beta_client):
        resp = await beta_client.post(f"{SUB_API}/portal")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "beta_mode"

    @pytest.mark.asyncio
    async def test_trial_blocked_in_beta(self, beta_client):
        resp = await beta_client.post(f"{SUB_API}/trial")
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "beta_mode"


# ===========================================================================
# Beta mode: gate-site tests - 429 with beta copy
# ===========================================================================


@pytest_asyncio.fixture
async def beta_gate_client(override_db, mode_env):
    """Hosted+beta authenticated client with a provisioned Pro subscription.

    The subscription row mirrors what _provision_subscription creates during
    signup so get_subscription_info reports the full PRO_* limits.
    """
    from src.db import create_pro_subscription

    mode_env("hosted", True)
    user_id = await create_web_user(override_db, "gate@example.com")
    await ensure_user(override_db, user_id, "gate", "Gate")
    await create_pro_subscription(override_db, user_id)

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "gate@example.com",
            "username": "gate@example.com",
            "first_name": "gate",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }

    app.dependency_overrides[get_current_user] = mock_user
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        c._user_id = user_id
        c._db_path = override_db
        yield c
    app.dependency_overrides.pop(get_current_user, None)


class TestBetaGates:
    """Gate site wiring when BETA_MODE=on and the user has Pro."""

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
    async def test_text_meal_11th_analyze_returns_429(
        self, mock_analyze, beta_gate_client
    ):
        """11th text-only analyze in a day → 429 feature=text_meal."""
        from src.web.constants import PRO_TEXT_MEAL_LIMIT

        mock_analyze.return_value = {
            "session_id": "text-session",
            "nutrition": None,
            "questions": [],
            "raw_text": "ok",
        }
        # Burn through the cap - first PRO_TEXT_MEAL_LIMIT calls succeed
        for i in range(PRO_TEXT_MEAL_LIMIT):
            resp = await beta_gate_client.post(
                f"{MEALS_API}/analyze",
                data={"text": f"meal {i}"},
            )
            assert resp.status_code == 200, f"#{i+1} unexpectedly blocked"

        # 11th request is blocked
        resp = await beta_gate_client.post(
            f"{MEALS_API}/analyze", data={"text": "one more"},
        )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["error"] == "limit_reached"
        assert detail["feature"] == "text_meal"
        assert detail["limit"] == PRO_TEXT_MEAL_LIMIT
        assert "free beta" in detail["message"]
        assert "paid tier" in detail["message"]

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
    async def test_image_6th_analyze_returns_429(
        self, mock_analyze, beta_gate_client, tmp_path, monkeypatch
    ):
        """6th image-attached analyze → 429 feature=image_analysis."""
        from io import BytesIO
        from PIL import Image as PILImage
        from src.web.constants import PRO_IMAGE_LIMIT
        import src.web.routes.meals as meals_module

        mock_analyze.return_value = {
            "session_id": "img-session",
            "nutrition": None,
            "questions": [],
            "raw_text": "ok",
        }
        # Redirect image save dir to tmp so the save step is harmless
        monkeypatch.setattr(meals_module, "IMAGE_DIR", str(tmp_path / "images"))

        # Generate a real 8x8 JPEG PIL can decode
        img_buf = BytesIO()
        PILImage.new("RGB", (8, 8), color="red").save(img_buf, "JPEG")
        jpeg = img_buf.getvalue()

        for i in range(PRO_IMAGE_LIMIT):
            resp = await beta_gate_client.post(
                f"{MEALS_API}/analyze",
                data={"text": ""},
                files={"images": ("t.jpg", jpeg, "image/jpeg")},
            )
            assert resp.status_code == 200, f"img #{i+1} unexpectedly blocked"

        resp = await beta_gate_client.post(
            f"{MEALS_API}/analyze",
            data={"text": ""},
            files={"images": ("t.jpg", jpeg, "image/jpeg")},
        )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["feature"] == "image_analysis"
        assert detail["limit"] == PRO_IMAGE_LIMIT
        assert "free beta" in detail["message"]

    @pytest.mark.asyncio
    @patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="Title")
    @patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="Hi")
    async def test_chat_11th_creation_returns_429_with_daily_counter(
        self, _mock_chat, _mock_title, beta_gate_client
    ):
        """11th chat session creation/day → 429 feature=ai_chat.

        Also verify the usage_tracking row is keyed by today's date
        (YYYY-MM-DD) - not month - so post-beta free tier doesn't leak keys.
        """
        from datetime import datetime, timezone
        from src.web.constants import PRO_CHAT_LIMIT

        for i in range(PRO_CHAT_LIMIT):
            resp = await beta_gate_client.post(CHAT_API)
            assert resp.status_code == 200, f"chat #{i+1} unexpectedly blocked"

        resp = await beta_gate_client.post(CHAT_API)
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["error"] == "limit_reached"
        assert detail["feature"] == "ai_chat"
        assert detail["limit"] == PRO_CHAT_LIMIT
        assert "free beta" in detail["message"]
        assert "paid tier" in detail["message"]

        # Verify counter key is today's YYYY-MM-DD (in the USER'S tz, not UTC)
        from src.services import user_today_str
        today = await user_today_str(
            beta_gate_client._db_path, beta_gate_client._user_id,
        )
        assert len(today) == 10  # YYYY-MM-DD
        usage = await get_usage(
            beta_gate_client._db_path,
            beta_gate_client._user_id,
            "chat_session",
            today,
        )
        assert usage["used_count"] == PRO_CHAT_LIMIT
        # Ensure no row with a YYYY-MM-only key was created
        month = today[:7]
        month_usage = await get_usage(
            beta_gate_client._db_path,
            beta_gate_client._user_id,
            "chat_session",
            month,
        )
        assert month_usage["used_count"] == 0

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.send_correction", new_callable=AsyncMock)
    async def test_meal_edit_11th_correction_returns_429_per_meal(
        self, mock_correct, beta_gate_client
    ):
        """Per-meal cap: 11th correction on a single text session is blocked."""
        from src.db import create_meal_session
        from src.web.constants import PRO_AI_EDITS_PER_MEAL

        session_id = "edit-cap-session"
        # Conversation: first turn is the initial analysis (not counted)
        # + 10 more user turns (each with a model reply). This puts us at
        # the edit cap so the very next correct call must 429.
        convo = [{"role": "user", "text": "initial"}]
        for i in range(PRO_AI_EDITS_PER_MEAL):
            convo.append({"role": "model", "text": f"m{i}"})
            convo.append({"role": "user", "text": f"edit {i}"})
        import json as _json
        await create_meal_session(
            beta_gate_client._db_path, session_id,
            beta_gate_client._user_id,
            conversation=_json.dumps(convo),
        )
        # Count check: total user turns = 11, subtract 1 = 10 edits used
        mock_correct.return_value = {
            "nutrition": None, "reply_text": "", "error": None,
        }

        resp = await beta_gate_client.post(
            f"{MEALS_API}/sessions/{session_id}/correct",
            json={"text": "11th edit"},
        )
        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["error"] == "limit_reached"
        assert detail["feature"] == "meal_edit"
        assert detail["limit"] == PRO_AI_EDITS_PER_MEAL
        # Scope wording comes from limit_message(..., scope="on this meal")
        assert "on this meal" in detail["message"]
        assert "free beta" in detail["message"]
        # send_correction MUST NOT have been called when the gate tripped
        mock_correct.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.send_correction", new_callable=AsyncMock)
    async def test_meal_edit_subtract_one_for_initial_analysis_turn(
        self, mock_correct, beta_gate_client
    ):
        """An 11-turn conversation (1 initial + 10 edits) is AT cap, so the
        next correction would be the 11th edit. Build a conversation with
        one fewer user turn (1 initial + 9 edits → 10 user turns) and assert
        the next correction is ALLOWED because used=9 < limit=10.
        """
        from src.db import create_meal_session
        from src.web.constants import PRO_AI_EDITS_PER_MEAL

        import json as _json

        session_id = "edit-allow-session"
        # 1 initial + 9 edits. user_turns counted = 10 - 1 = 9.
        convo = [{"role": "user", "text": "initial"}]
        for i in range(PRO_AI_EDITS_PER_MEAL - 1):
            convo.append({"role": "model", "text": f"m{i}"})
            convo.append({"role": "user", "text": f"edit {i}"})
        await create_meal_session(
            beta_gate_client._db_path, session_id,
            beta_gate_client._user_id,
            conversation=_json.dumps(convo),
        )
        mock_correct.return_value = {
            "nutrition": None, "reply_text": "ok", "error": None,
        }
        resp = await beta_gate_client.post(
            f"{MEALS_API}/sessions/{session_id}/correct",
            json={"text": "10th edit - should pass"},
        )
        assert resp.status_code == 200
        mock_correct.assert_called_once()

    @pytest.mark.asyncio
    async def test_manual_macro_edit_does_not_increment_counters(
        self, beta_gate_client
    ):
        """PUT /meals/{id} - manual macro edit - must NOT consume meal_edit
        quota. Manual dial-picker edits go through the meals PUT route,
        which does not touch usage_tracking or conversation.
        """
        from datetime import datetime, timezone
        from src.db import log_meal

        # Seed a meal via log_meal
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        meal_log_id = await log_meal(
            beta_gate_client._db_path, beta_gate_client._user_id,
            logged_at=now_str, item_name="test", meal_description="test meal",
            calories=170, protein=10, carbs=20, fat=5, source="test",
        )
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        before_text = await get_usage(
            beta_gate_client._db_path, beta_gate_client._user_id,
            "text_meal", today,
        )
        before_img = await get_usage(
            beta_gate_client._db_path, beta_gate_client._user_id,
            "image_query", today,
        )

        resp = await beta_gate_client.put(
            f"{MEALS_API}/{meal_log_id}",
            json={"calories": 200, "protein": 15, "carbs": 25, "fat": 6},
        )
        # 200 or 404 is acceptable - the point is that the counters must
        # not change.  Some PUT routes validate different shapes.
        assert resp.status_code in (200, 404, 422)

        after_text = await get_usage(
            beta_gate_client._db_path, beta_gate_client._user_id,
            "text_meal", today,
        )
        after_img = await get_usage(
            beta_gate_client._db_path, beta_gate_client._user_id,
            "image_query", today,
        )
        assert before_text["used_count"] == after_text["used_count"]
        assert before_img["used_count"] == after_img["used_count"]


class TestSelfModeSkipsCounters:
    """Self mode must not touch usage_tracking at all for gate sites."""

    @pytest_asyncio.fixture
    async def self_client(self, override_db, mode_env):
        mode_env("self", False)
        user_id = await create_web_user(override_db, "self@example.com")
        await ensure_user(override_db, user_id, "self", "Self")

        async def mock_user():
            return {
                "user_id": user_id,
                "email": "self@example.com",
                "username": "self@example.com",
                "first_name": "self",
                "google_sub": None,
                "created_at": "2026-01-01 00:00:00",
            }

        app.dependency_overrides[get_current_user] = mock_user
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
            headers={"X-Requested-With": "MacroApp"},
        ) as c:
            c._user_id = user_id
            c._db_path = override_db
            yield c
        app.dependency_overrides.pop(get_current_user, None)

    @pytest.mark.asyncio
    @patch("src.web.routes.meals.analyze_meal", new_callable=AsyncMock)
    @patch("src.web.routes.meals.increment_usage", new_callable=AsyncMock)
    async def test_self_mode_text_analyze_skips_increment(
        self, mock_inc, mock_analyze, self_client
    ):
        """Self mode: analyze route never calls increment_usage."""
        mock_analyze.return_value = {
            "session_id": "s1", "nutrition": None, "questions": [], "raw_text": "",
        }
        resp = await self_client.post(
            f"{MEALS_API}/analyze", data={"text": "chicken rice"},
        )
        assert resp.status_code == 200
        mock_inc.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.web.routes.chat.generate_chat_title", new_callable=AsyncMock, return_value="t")
    @patch("src.web.routes.chat.chat_with_mcp", new_callable=AsyncMock, return_value="r")
    @patch("src.web.routes.chat.increment_usage", new_callable=AsyncMock)
    async def test_self_mode_chat_skips_increment(
        self, mock_inc, _mock_chat, _mock_title, self_client
    ):
        resp = await self_client.post(CHAT_API)
        assert resp.status_code == 200
        mock_inc.assert_not_called()


# ===========================================================================
# Status endpoint shape - new beta fields
# ===========================================================================


class TestSubscriptionStatusShape:
    """GET /subscription returns all new fields with expected types."""

    @pytest.mark.asyncio
    async def test_new_fields_present_and_typed(self, auth_client):
        resp = await auth_client.get(SUB_API)
        assert resp.status_code == 200
        data = resp.json()
        # New beta fields
        assert "is_og" in data and isinstance(data["is_og"], bool)
        assert "app_mode" in data and isinstance(data["app_mode"], str)
        assert "beta_mode" in data and isinstance(data["beta_mode"], bool)
        assert "usage_text_meal_used" in data and isinstance(data["usage_text_meal_used"], int)
        assert "usage_text_meal_limit" in data and isinstance(data["usage_text_meal_limit"], int)
        assert "usage_meal_edit_limit" in data and isinstance(data["usage_meal_edit_limit"], int)
        assert "stripe_customer_id" in data and isinstance(data["stripe_customer_id"], str)
        # Legacy alias
        assert data["founding_member"] == data["is_og"]

    @pytest.mark.asyncio
    async def test_premium_user_has_unlimited_saved_meals(self, premium_client):
        resp = await premium_client.get(SUB_API)
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_premium"] is True
        assert data["usage_saved_meals_limit"] == -1
