"""Beta-release signup cap + Pro provisioning tests.

Covers:
- `create_web_user` raising `SignupCapReached` in hosted+beta at the cap
- Self mode / hosted+no-beta mode ignoring the cap
- `/auth/email/verify-pin` and `/auth/google` returning a structured 503
  with the caller's email added to the waitlist (idempotent on dup)
- Existing users still being able to log in after the cap is reached
- `_provision_subscription` branching:
    * hosted + beta → `create_pro_subscription` seeds pro_monthly row
    * hosted + no-beta → `start_trial` seeds a trial row
    * self mode → no subscription row created
- `create_pro_subscription` preserving existing is_og=1 when re-run
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

# Set env before importing app (mirrors test_subscription.py header order)
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import (
    SignupCapReached,
    add_to_waitlist,
    create_or_update_subscription,
    create_pro_subscription,
    create_web_user,
    ensure_user,
    get_subscription,
    init_db,
    list_waitlist,
    set_og_status,
    start_trial,
    store_email_pin,
)
from src.web.app import app
from src.web.auth import hash_pin
from src.web.deps import get_db_path


# ── Mode patching helpers (shared with test_subscription.py) ────────


def _set_mode(app_mode: str, beta_mode: bool, cap: int | None = None) -> None:
    """Override APP_MODE / BETA_MODE / BETA_SIGNUP_CAP everywhere.

    Because `from X import Y` binds Y into the importer's namespace at
    import time, we must flip the value in every module that did such an
    import. Missing a single one leaves stale cached constants.
    """
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
    if cap is not None:
        _constants.BETA_SIGNUP_CAP = cap
        _auth.BETA_SIGNUP_CAP = cap


@pytest.fixture
def mode_env():
    """Restore APP_MODE/BETA_MODE/BETA_SIGNUP_CAP after the test."""
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


# ── Shared fixtures ─────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "beta_signup.db")
    asyncio.run(init_db(path))
    return path


@pytest.fixture
def override_db(db_path):
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield db_path
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(override_db):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        c._db_path = override_db
        yield c


# ═══════════════════════════════════════════════════════════════════
# 1. create_web_user cap enforcement
# ═══════════════════════════════════════════════════════════════════


class TestCreateWebUserCap:
    """create_web_user respects BETA_SIGNUP_CAP only in hosted+beta mode."""

    @pytest.mark.asyncio
    async def test_cap_enforced_only_in_hosted_beta(self, db_path, mode_env):
        mode_env("hosted", True, cap=3)

        # First 3 signups succeed
        ids = []
        for i in range(3):
            uid = await create_web_user(db_path, f"user{i}@example.com")
            ids.append(uid)
        assert len(ids) == 3
        assert len(set(ids)) == 3  # unique

        # 4th signup raises SignupCapReached
        with pytest.raises(SignupCapReached) as excinfo:
            await create_web_user(db_path, "overflow@example.com")
        assert excinfo.value.cap == 3
        assert excinfo.value.email == "overflow@example.com"

    @pytest.mark.asyncio
    async def test_cap_ignored_in_self_mode(self, db_path, mode_env):
        """Self mode: signup can exceed the cap without raising."""
        mode_env("self", False, cap=2)
        # 5 signups with cap=2 - none should raise in self mode
        for i in range(5):
            await create_web_user(db_path, f"self{i}@example.com")
        # Verify all 5 were created
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            row = await (
                await db.execute("SELECT COUNT(*) FROM web_auth")
            ).fetchone()
        assert row[0] == 5

    @pytest.mark.asyncio
    async def test_cap_ignored_in_hosted_no_beta(self, db_path, mode_env):
        """Hosted + no-beta: cap does not apply (legacy signup flow)."""
        mode_env("hosted", False, cap=2)
        for i in range(5):
            await create_web_user(db_path, f"legacy{i}@example.com")
        import aiosqlite
        async with aiosqlite.connect(db_path) as db:
            row = await (
                await db.execute("SELECT COUNT(*) FROM web_auth")
            ).fetchone()
        assert row[0] == 5


# ═══════════════════════════════════════════════════════════════════
# 2. Auth endpoints return 503 with waitlist side-effect
# ═══════════════════════════════════════════════════════════════════


async def _seed_pin(db_path: str, email: str, pin: str = "123456") -> None:
    """Write a valid email PIN row for the target email."""
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    await store_email_pin(db_path, email, hash_pin(pin), expires)


class TestAuthEndpointsAtCap:
    """/auth/email/verify-pin and /auth/google return 503 + waitlist insert."""

    @pytest.mark.asyncio
    async def test_verify_pin_at_cap_returns_503_and_adds_waitlist(
        self, client, mode_env
    ):
        mode_env("hosted", True, cap=2)
        db = client._db_path

        # Fill the cap with 2 existing users so the 3rd signup trips the gate
        await create_web_user(db, "u1@example.com")
        await create_web_user(db, "u2@example.com")

        # 3rd email signs up with a valid PIN - should be waitlisted
        await _seed_pin(db, "overflow@example.com", "123456")
        resp = await client.post(
            "/macro_app/api/v1/auth/email/verify-pin",
            json={
                "email": "overflow@example.com", "pin": "123456",
                "first_name": "Over",
            },
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "beta_full"
        assert detail["cap"] == 2
        assert detail["waitlist_added"] is True
        assert "waitlist" in detail["message"].lower()

        # Verify waitlist row was inserted
        rows = await list_waitlist(db)
        assert len(rows) == 1
        assert rows[0]["email"] == "overflow@example.com"
        assert rows[0]["source"] == "email_pin"
        assert rows[0]["first_name"] == "Over"

    @pytest.mark.asyncio
    async def test_google_auth_at_cap_returns_503_and_adds_waitlist(
        self, client, mode_env
    ):
        mode_env("hosted", True, cap=2)
        db = client._db_path

        await create_web_user(db, "u1@example.com")
        await create_web_user(db, "u2@example.com")

        fake_google = {
            "email": "google-overflow@example.com",
            "sub": "google-sub-overflow",
            "name": "Gina Overflow",
            "picture": "https://example.com/g.jpg",
        }
        with patch(
            "src.web.routes.auth.verify_google_id_token",
            new_callable=AsyncMock, return_value=fake_google,
        ):
            resp = await client.post(
                "/macro_app/api/v1/auth/google",
                json={"id_token": "fake-token"},
            )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "beta_full"
        assert detail["waitlist_added"] is True

        rows = await list_waitlist(db)
        assert len(rows) == 1
        assert rows[0]["email"] == "google-overflow@example.com"
        assert rows[0]["source"] == "google"
        # Google-flow passes first_name from the token name split
        assert rows[0]["first_name"] == "Gina"

    @pytest.mark.asyncio
    async def test_repeat_signup_same_email_only_one_waitlist_row(
        self, client, mode_env
    ):
        """Dup signup by same email → 503 each time, one waitlist row."""
        mode_env("hosted", True, cap=1)
        db = client._db_path
        await create_web_user(db, "u1@example.com")

        await _seed_pin(db, "retry@example.com", "123456")
        resp1 = await client.post(
            "/macro_app/api/v1/auth/email/verify-pin",
            json={"email": "retry@example.com", "pin": "123456"},
        )
        assert resp1.status_code == 503

        # Second attempt - also 503, still just 1 waitlist row
        await _seed_pin(db, "retry@example.com", "123456")
        resp2 = await client.post(
            "/macro_app/api/v1/auth/email/verify-pin",
            json={"email": "retry@example.com", "pin": "123456"},
        )
        assert resp2.status_code == 503

        rows = await list_waitlist(db)
        assert len(rows) == 1
        assert rows[0]["email"] == "retry@example.com"

    @pytest.mark.asyncio
    async def test_existing_user_can_still_log_in_at_cap(
        self, client, mode_env
    ):
        """After cap is reached, existing users can still log in
        (login doesn't create a new web_auth row → cap check is skipped).
        """
        mode_env("hosted", True, cap=1)
        db = client._db_path
        existing_id = await create_web_user(db, "member@example.com")

        # Log in via email PIN - should succeed despite the cap being at 1
        await _seed_pin(db, "member@example.com", "123456")
        resp = await client.post(
            "/macro_app/api/v1/auth/email/verify-pin",
            json={"email": "member@example.com", "pin": "123456"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["user_id"] == existing_id
        assert data["email"] == "member@example.com"

        # No new user created, no waitlist row
        import aiosqlite
        async with aiosqlite.connect(db) as con:
            row = await (
                await con.execute("SELECT COUNT(*) FROM web_auth")
            ).fetchone()
        assert row[0] == 1
        assert len(await list_waitlist(db)) == 0


# ═══════════════════════════════════════════════════════════════════
# 3. _provision_subscription / create_pro_subscription
# ═══════════════════════════════════════════════════════════════════


class TestProProvisioningOnSignup:
    """A fresh signup gets the right subscription row for its mode."""

    @pytest.mark.asyncio
    async def test_hosted_beta_signup_gets_pro_monthly_row(
        self, client, mode_env
    ):
        mode_env("hosted", True, cap=100)
        db = client._db_path
        await _seed_pin(db, "newpro@example.com", "123456")
        resp = await client.post(
            "/macro_app/api/v1/auth/email/verify-pin",
            json={"email": "newpro@example.com", "pin": "123456"},
        )
        assert resp.status_code == 200
        user_id = resp.json()["user_id"]

        sub = await get_subscription(db, user_id)
        assert sub is not None
        assert sub["plan"] == "pro_monthly"
        assert sub["status"] == "active"
        assert sub["is_og"] == 0
        # Stripe customer id is empty so the post-beta migration can find
        # all the grandfathered beta accounts.
        assert sub["stripe_customer_id"] == ""

    @pytest.mark.asyncio
    async def test_hosted_no_beta_signup_gets_trial_row(
        self, client, mode_env
    ):
        """Regression guard: non-beta hosted still starts a 7-day trial."""
        mode_env("hosted", False, cap=100)
        db = client._db_path
        await _seed_pin(db, "trialer@example.com", "123456")
        resp = await client.post(
            "/macro_app/api/v1/auth/email/verify-pin",
            json={"email": "trialer@example.com", "pin": "123456"},
        )
        assert resp.status_code == 200
        user_id = resp.json()["user_id"]

        sub = await get_subscription(db, user_id)
        assert sub is not None
        assert sub["plan"] == "trial"
        assert sub["status"] == "trialing"
        assert sub["trial_used"] == 1
        assert sub["trial_ends_at"] is not None

    @pytest.mark.asyncio
    async def test_self_mode_signup_creates_no_subscription_row(
        self, client, mode_env
    ):
        mode_env("self", False, cap=100)
        db = client._db_path
        await _seed_pin(db, "selfer@example.com", "123456")
        resp = await client.post(
            "/macro_app/api/v1/auth/email/verify-pin",
            json={"email": "selfer@example.com", "pin": "123456"},
        )
        assert resp.status_code == 200
        user_id = resp.json()["user_id"]

        sub = await get_subscription(db, user_id)
        assert sub is None

    @pytest.mark.asyncio
    async def test_create_pro_subscription_preserves_is_og_on_rerun(
        self, db_path
    ):
        """Idempotency guard: re-running create_pro_subscription on an OG
        user must NOT clear the OG flag.
        """
        await ensure_user(db_path, 1, "alice", "Alice")
        # First run: create OG directly
        await create_pro_subscription(db_path, 1, is_og=True)
        sub = await get_subscription(db_path, 1)
        assert sub["is_og"] == 1

        # Re-run without is_og (simulating _provision_subscription on a
        # pre-existing OG user who re-authenticates)
        await create_pro_subscription(db_path, 1, is_og=False)
        sub = await get_subscription(db_path, 1)
        # is_og MUST still be 1 - the CASE WHEN clause guards it
        assert sub["is_og"] == 1
        assert sub["plan"] == "pro_monthly"
        assert sub["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_pro_subscription_on_fresh_user_defaults_is_og_0(
        self, db_path
    ):
        await ensure_user(db_path, 1, "alice", "Alice")
        await create_pro_subscription(db_path, 1)
        sub = await get_subscription(db_path, 1)
        assert sub is not None
        assert sub["is_og"] == 0
        assert sub["plan"] == "pro_monthly"
        assert sub["status"] == "active"
