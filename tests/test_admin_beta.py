"""Beta-release admin endpoint tests.

Covers:
- `POST /admin/og-user` with admin flips is_og in DB, returns ok payload
- Non-admin callers get 404 (NOT 403) - intentional reconnaissance shield
- OG toggle on user with no subscription row creates the row
- `GET /admin/users.csv` returns valid CSV with the expected header
- `GET /admin/waitlist.csv` returns valid CSV with the expected header
- Non-admin gets 404 on both CSV endpoints
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio

os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import (
    add_to_waitlist,
    create_or_update_subscription,
    create_pro_subscription,
    create_web_user,
    ensure_user,
    get_subscription,
    init_db,
    set_og_status,
)
from src.web.app import app
from src.web.deps import get_current_user, get_db_path


ADMIN_URL = "/macro_app/api/v1/admin"


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "admin_beta.db")
    asyncio.get_event_loop().run_until_complete(init_db(path))
    return path


@pytest.fixture
def override_db(db_path):
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield db_path
    app.dependency_overrides.clear()


@pytest.fixture
def admin_env(monkeypatch):
    """Set ADMIN_EMAIL so admin@example.com is admin, others are not."""
    monkeypatch.setenv("ADMIN_EMAIL", "admin@example.com")
    yield


def _make_client_fixture(user_id: int, email: str):
    """Shared helper to construct an auth client for a given user_id."""

    @pytest_asyncio.fixture
    async def _client(override_db, admin_env):
        await ensure_user(override_db, user_id, email, email.split("@")[0])

        async def mock_user():
            return {
                "user_id": user_id,
                "email": email,
                "username": email,
                "first_name": email.split("@")[0],
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

    return _client


admin_client = _make_client_fixture(9000, "admin@example.com")
regular_client = _make_client_fixture(42, "regular@example.com")


# ═══════════════════════════════════════════════════════════════════
# /admin/og-user
# ═══════════════════════════════════════════════════════════════════


class TestOGUserToggle:

    @pytest.mark.asyncio
    async def test_admin_can_flip_og_flag(self, admin_client):
        db = admin_client._db_path
        # Create a target user with a pro_monthly row
        target_id = await create_web_user(db, "target@example.com")
        await ensure_user(db, target_id, "target", "Target")
        await create_pro_subscription(db, target_id, is_og=False)

        resp = await admin_client.post(
            f"{ADMIN_URL}/og-user",
            json={"user_id": target_id, "is_og": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["user_id"] == target_id
        assert data["email"] == "target@example.com"
        assert data["is_og"] is True

        # Verify DB persisted
        sub = await get_subscription(db, target_id)
        assert sub["is_og"] == 1

        # Flip it back off
        resp2 = await admin_client.post(
            f"{ADMIN_URL}/og-user",
            json={"user_id": target_id, "is_og": False},
        )
        assert resp2.status_code == 200
        assert resp2.json()["is_og"] is False
        sub2 = await get_subscription(db, target_id)
        assert sub2["is_og"] == 0

    @pytest.mark.asyncio
    async def test_non_admin_gets_404_not_403(self, regular_client):
        db = regular_client._db_path
        target_id = await create_web_user(db, "victim@example.com")
        await ensure_user(db, target_id, "victim", "Victim")

        resp = await regular_client.post(
            f"{ADMIN_URL}/og-user",
            json={"user_id": target_id, "is_og": True},
        )
        # Non-admin must get 404 (reconnaissance shield), NOT 403
        assert resp.status_code == 404

        # DB should be untouched - no row created
        assert await get_subscription(db, target_id) is None

    @pytest.mark.asyncio
    async def test_og_toggle_creates_row_when_missing(self, admin_client):
        """User exists but has no subscription row - flipping OG creates it."""
        db = admin_client._db_path
        target_id = await create_web_user(db, "new@example.com")
        await ensure_user(db, target_id, "new", "New")

        assert await get_subscription(db, target_id) is None

        resp = await admin_client.post(
            f"{ADMIN_URL}/og-user",
            json={"user_id": target_id, "is_og": True},
        )
        assert resp.status_code == 200

        sub = await get_subscription(db, target_id)
        assert sub is not None
        assert sub["is_og"] == 1
        assert sub["plan"] == "pro_monthly"
        assert sub["status"] == "active"

    @pytest.mark.asyncio
    async def test_og_toggle_on_nonexistent_user_returns_404(self, admin_client):
        resp = await admin_client.post(
            f"{ADMIN_URL}/og-user",
            json={"user_id": 123456789, "is_og": True},
        )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# /admin/users.csv
# ═══════════════════════════════════════════════════════════════════


EXPECTED_USERS_HEADER = [
    "user_id", "email", "first_name", "last_name", "signed_up_at",
    "plan", "status", "is_og", "has_stripe_customer",
    "current_period_end", "newsletter_opt_in",
]


class TestUsersCsvExport:

    @pytest.mark.asyncio
    async def test_admin_gets_valid_csv_with_expected_header(self, admin_client):
        db = admin_client._db_path
        u1 = await create_web_user(db, "alpha@example.com", first_name="Alpha")
        await ensure_user(db, u1, "alpha", "Alpha")
        await create_pro_subscription(db, u1, is_og=True)

        u2 = await create_web_user(db, "bravo@example.com", first_name="Bravo")
        await ensure_user(db, u2, "bravo", "Bravo")
        # Bravo has no subscription row (LEFT JOIN covers this case)

        resp = await admin_client.get(f"{ADMIN_URL}/users.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        content = resp.text

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        assert rows[0] == EXPECTED_USERS_HEADER

        # At least alpha, bravo, and the admin's own row (from _make_client_fixture)
        emails = [row[1] for row in rows[1:]]
        assert "alpha@example.com" in emails
        assert "bravo@example.com" in emails

        # Verify Alpha's OG flag round-trips as "1"
        alpha_row = [row for row in rows[1:] if row[1] == "alpha@example.com"][0]
        assert alpha_row[7] == "1"  # is_og column

    @pytest.mark.asyncio
    async def test_non_admin_gets_404(self, regular_client):
        resp = await regular_client.get(f"{ADMIN_URL}/users.csv")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════
# /admin/waitlist.csv
# ═══════════════════════════════════════════════════════════════════


EXPECTED_WAITLIST_HEADER = [
    "id", "email", "source", "first_name", "referrer",
    "created_at", "invited_at", "notes",
]


class TestWaitlistCsvExport:

    @pytest.mark.asyncio
    async def test_admin_gets_valid_csv(self, admin_client):
        db = admin_client._db_path
        await add_to_waitlist(db, "wait1@example.com", source="google", first_name="One")
        await add_to_waitlist(db, "wait2@example.com", source="email_pin", first_name="Two")

        resp = await admin_client.get(f"{ADMIN_URL}/waitlist.csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")

        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        assert rows[0] == EXPECTED_WAITLIST_HEADER

        emails = [r[1] for r in rows[1:]]
        assert emails == ["wait1@example.com", "wait2@example.com"]  # ASC by created_at

        # Row shape sanity
        for row in rows[1:]:
            assert len(row) == len(EXPECTED_WAITLIST_HEADER)

    @pytest.mark.asyncio
    async def test_non_admin_gets_404(self, regular_client):
        resp = await regular_client.get(f"{ADMIN_URL}/waitlist.csv")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_empty_waitlist_returns_header_only(self, admin_client):
        resp = await admin_client.get(f"{ADMIN_URL}/waitlist.csv")
        assert resp.status_code == 200
        reader = csv.reader(io.StringIO(resp.text))
        rows = list(reader)
        assert rows[0] == EXPECTED_WAITLIST_HEADER
        assert len(rows) == 1  # header only
