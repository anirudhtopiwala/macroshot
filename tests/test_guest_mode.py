"""Tests for guest mode: /guest/analyze + /meals/import-guest."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import (
    create_web_user,
    get_guest_imported_at,
    get_meals_paginated,
    init_db,
    mark_guest_imported,
)
from src.models import FoodItem, NutritionResult
from src.web.app import app
from src.web.budget_gate import BudgetExceededError
from src.web.deps import get_current_user, get_db_path

API = "/macro_app/api/v1"


# ── Shared fixtures ─────────────────────────────────────────────────


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "guest.db")
    asyncio.run(init_db(path))
    return path


@pytest.fixture
def override_db(db_path):
    app.dependency_overrides[get_db_path] = lambda: db_path
    yield db_path
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def guest_client(override_db):
    """Unauthenticated client - guest endpoint requires no cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        c._db_path = override_db  # type: ignore[attr-defined]
        yield c


@pytest_asyncio.fixture
async def auth_client(override_db):
    user_id = await create_web_user(override_db, "guest_test@example.com")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "guest_test@example.com",
            "username": "guest_test",
            "first_name": "Guest",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }

    app.dependency_overrides[get_current_user] = mock_user
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        c._user_id = user_id  # type: ignore[attr-defined]
        c._db_path = override_db  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.pop(get_current_user, None)


def _nutrition_fixture() -> NutritionResult:
    return NutritionResult(
        item_name="Scrambled eggs",
        meal_description="2 eggs scrambled with butter",
        items=[
            FoodItem(
                name="Egg",
                description="2 large",
                calories=140.0,
                protein=12.0,
                carbs=1.0,
                fat=10.0,
                weight_g=100.0,
                source="gemini",
            )
        ],
        calories=180.0,
        protein=12.0,
        carbs=1.0,
        fat=14.0,
        source="Gemini",
    )


# ═══════════════════════════════════════════════════════════════════
# 1. /guest/analyze
# ═══════════════════════════════════════════════════════════════════


class TestGuestAnalyze:
    @pytest.mark.asyncio
    @patch("src.web.routes.guest.gemini_analyze_meal", new_callable=AsyncMock)
    async def test_text_only_happy_path(self, mock_gemini, guest_client):
        mock_gemini.return_value = ("raw model output", _nutrition_fixture())
        resp = await guest_client.post(f"{API}/guest/analyze", data={"text": "scrambled eggs"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["error"] is None
        assert body["nutrition"]["item_name"] == "Scrambled eggs"
        assert body["nutrition"]["calories"] == 180.0
        assert len(body["nutrition"]["items"]) == 1

    @pytest.mark.asyncio
    @patch("src.web.routes.guest.gemini_analyze_meal", new_callable=AsyncMock)
    async def test_no_dry_input_rejected(self, mock_gemini, guest_client):
        resp = await guest_client.post(f"{API}/guest/analyze", data={"text": ""})
        assert resp.status_code == 400
        mock_gemini.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.web.routes.guest.gemini_analyze_meal", new_callable=AsyncMock)
    async def test_budget_gate_503(self, mock_gemini, guest_client):
        mock_gemini.side_effect = BudgetExceededError("over cap")
        resp = await guest_client.post(f"{API}/guest/analyze", data={"text": "anything"})
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["reason"] == "budget_exceeded"

    @pytest.mark.asyncio
    @patch("src.web.routes.guest.gemini_analyze_meal", new_callable=AsyncMock)
    async def test_no_db_row_created(self, mock_gemini, guest_client):
        """Guest analyze must not write anything to meal_sessions, users, or
        usage_tracking - the whole point of the path is zero-state."""
        import aiosqlite
        mock_gemini.return_value = ("raw", _nutrition_fixture())
        async with aiosqlite.connect(guest_client._db_path) as db:
            before_sessions = (await (await db.execute("SELECT COUNT(*) FROM meal_sessions")).fetchone())[0]
            before_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]

        resp = await guest_client.post(f"{API}/guest/analyze", data={"text": "oats"})
        assert resp.status_code == 200

        async with aiosqlite.connect(guest_client._db_path) as db:
            after_sessions = (await (await db.execute("SELECT COUNT(*) FROM meal_sessions")).fetchone())[0]
            after_users = (await (await db.execute("SELECT COUNT(*) FROM users")).fetchone())[0]
        assert before_sessions == after_sessions
        assert before_users == after_users

    @pytest.mark.asyncio
    @patch("src.web.routes.guest.gemini_analyze_meal", new_callable=AsyncMock)
    async def test_unparseable_returns_error_string(self, mock_gemini, guest_client):
        mock_gemini.return_value = ("not a meal", None)
        resp = await guest_client.post(f"{API}/guest/analyze", data={"text": "??"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["nutrition"] is None
        assert body["error"]
        assert "not a meal" in body["error"]


# ═══════════════════════════════════════════════════════════════════
# 2. /meals/import-guest
# ═══════════════════════════════════════════════════════════════════


_SAMPLE_NUTRITION = {
    "item_name": "Apple",
    "meal_description": "1 medium apple",
    "items": [
        {
            "name": "Apple",
            "description": "Medium",
            "brand": None,
            "has_label": False,
            "calories": 95.0,
            "protein": 0.5,
            "carbs": 25.0,
            "fat": 0.3,
            "weight_g": 180.0,
            "source": "gemini",
        }
    ],
    "calories": 95.0,
    "protein": 0.5,
    "carbs": 25.0,
    "fat": 0.3,
    "source": "Gemini",
}


def _meal_payload(idx: int = 0) -> dict:
    return {
        "nutrition": _SAMPLE_NUTRITION,
        "logged_at": f"2026-06-{1 + idx:02d} 09:00",
        "meal_type": "breakfast",
        "user_input": f"guest meal {idx}",
    }


class TestImportGuest:
    @pytest.mark.asyncio
    async def test_requires_auth(self, override_db):
        """No cookie → 401 (CurrentUser dep). Confirms attackers can't replay
        someone else's guest meals into a victim's account."""
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
            headers={"X-Requested-With": "MacroApp"},
        ) as c:
            resp = await c.post(f"{API}/meals/import-guest", json={"meals": [_meal_payload()]})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_happy_path_inserts_meals(self, auth_client):
        payload = {"meals": [_meal_payload(0), _meal_payload(1), _meal_payload(2)]}
        resp = await auth_client.post(f"{API}/meals/import-guest", json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["imported"] == 3
        assert body["already_imported"] is False

        meals = await get_meals_paginated(auth_client._db_path, auth_client._user_id, limit=10, offset=0)
        assert len(meals) == 3
        assert {m["item_name"] for m in meals} == {"Apple"}

        # Confirm meal_logs.source was tagged so we can audit/import-trace later.
        import aiosqlite
        async with aiosqlite.connect(auth_client._db_path) as db:
            sources = await (await db.execute(
                "SELECT DISTINCT source FROM meal_logs WHERE user_id = ?",
                (auth_client._user_id,),
            )).fetchall()
        assert {r[0] for r in sources} == {"guest_import"}

        ts = await get_guest_imported_at(auth_client._db_path, auth_client._user_id)
        assert ts is not None

    @pytest.mark.asyncio
    async def test_second_call_is_noop(self, auth_client):
        first = await auth_client.post(f"{API}/meals/import-guest", json={"meals": [_meal_payload()]})
        assert first.status_code == 200
        assert first.json()["imported"] == 1

        second = await auth_client.post(f"{API}/meals/import-guest", json={"meals": [_meal_payload(5)]})
        assert second.status_code == 200
        body = second.json()
        assert body["imported"] == 0
        assert body["already_imported"] is True

        meals = await get_meals_paginated(auth_client._db_path, auth_client._user_id, limit=20, offset=0)
        assert len(meals) == 1

    @pytest.mark.asyncio
    async def test_empty_payload_noop(self, auth_client):
        resp = await auth_client.post(f"{API}/meals/import-guest", json={"meals": []})
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 0
        assert body["already_imported"] is False
        # Empty payload must NOT consume the once-per-user gate.
        ts = await get_guest_imported_at(auth_client._db_path, auth_client._user_id)
        assert ts is None

    @pytest.mark.asyncio
    async def test_thirty_one_meals_rejected(self, auth_client):
        """Pydantic max_length=30 on meals[] guards the per-call cap."""
        payload = {"meals": [_meal_payload(i) for i in range(31)]}
        resp = await auth_client.post(f"{API}/meals/import-guest", json=payload)
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_status_endpoint_reflects_import(self, auth_client):
        before = await auth_client.get(f"{API}/meals/import-guest/status")
        assert before.status_code == 200
        assert before.json()["already_imported"] is False

        await auth_client.post(f"{API}/meals/import-guest", json={"meals": [_meal_payload()]})

        after = await auth_client.get(f"{API}/meals/import-guest/status")
        assert after.json()["already_imported"] is True
        assert after.json()["imported_at"]


# ═══════════════════════════════════════════════════════════════════
# 3. /weight/import-guest
# ═══════════════════════════════════════════════════════════════════


class TestImportGuestWeights:
    @pytest.mark.asyncio
    async def test_happy_path_inserts_entries(self, auth_client):
        payload = {"entries": [
            {"weight_kg": 78.5, "logged_at": "2026-06-01 08:00"},
            {"weight_kg": 78.2, "logged_at": "2026-06-03 08:00"},
            {"weight_kg": 77.9, "logged_at": "2026-06-05 08:00"},
        ]}
        resp = await auth_client.post(f"{API}/weight/import-guest", json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["imported"] == 3
        assert body["already_imported"] is False

        # Confirm entries actually landed in weight_logs.
        import aiosqlite
        async with aiosqlite.connect(auth_client._db_path) as db:
            rows = await (await db.execute(
                "SELECT weight_kg FROM weight_logs WHERE user_id = ? ORDER BY logged_at",
                (auth_client._user_id,),
            )).fetchall()
        assert [r[0] for r in rows] == [78.5, 78.2, 77.9]

    @pytest.mark.asyncio
    async def test_second_call_is_noop(self, auth_client):
        first = await auth_client.post(
            f"{API}/weight/import-guest",
            json={"entries": [{"weight_kg": 80.0, "logged_at": "2026-06-01 08:00"}]},
        )
        assert first.status_code == 200
        assert first.json()["imported"] == 1

        second = await auth_client.post(
            f"{API}/weight/import-guest",
            json={"entries": [{"weight_kg": 82.0, "logged_at": "2026-06-02 08:00"}]},
        )
        assert second.status_code == 200
        body = second.json()
        assert body["imported"] == 0
        assert body["already_imported"] is True

    @pytest.mark.asyncio
    async def test_requires_auth(self, override_db):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test",
            headers={"X-Requested-With": "MacroApp"},
        ) as c:
            resp = await c.post(
                f"{API}/weight/import-guest",
                json={"entries": [{"weight_kg": 80.0, "logged_at": "2026-06-01 08:00"}]},
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_ninety_one_entries_rejected(self, auth_client):
        payload = {"entries": [
            {"weight_kg": 80.0 + i * 0.01, "logged_at": f"2026-06-{(i % 28) + 1:02d} 08:00"}
            for i in range(91)
        ]}
        resp = await auth_client.post(f"{API}/weight/import-guest", json=payload)
        assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════
# 4. /guest/barcode
# ═══════════════════════════════════════════════════════════════════


class TestGuestBarcode:
    @pytest.mark.asyncio
    @patch("src.web.routes.guest.lookup_barcode", new_callable=AsyncMock)
    async def test_happy_path(self, mock_lookup, guest_client):
        mock_lookup.return_value = {
            "product_name": "Chobani Greek Yogurt",
            "brand": "Chobani",
            "calories": 100.0,
            "protein": 18.0,
            "carbs": 6.0,
            "fat": 0.0,
            "serving_size_g": 170.0,
            "serving_size_unit": "g",
            "serving_label": "1 container (170g)",
            "image_url": "https://example.com/y.jpg",
        }
        resp = await guest_client.post(f"{API}/guest/barcode", json={"barcode": "0894700010014"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["nutrition"]["calories"] == 100.0
        assert body["serving_size_g"] == 170.0
        assert body["image_url"] == "https://example.com/y.jpg"

    @pytest.mark.asyncio
    async def test_invalid_format(self, guest_client):
        resp = await guest_client.post(f"{API}/guest/barcode", json={"barcode": "abc"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    @patch("src.web.routes.guest.lookup_barcode", new_callable=AsyncMock)
    async def test_product_not_found(self, mock_lookup, guest_client):
        mock_lookup.return_value = None
        resp = await guest_client.post(f"{API}/guest/barcode", json={"barcode": "1234567890123"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["nutrition"] is None
        assert "not found" in body["error"].lower()


# ═══════════════════════════════════════════════════════════════════
# 5. mark_guest_imported race semantics
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mark_guest_imported_single_winner(db_path):
    """Concurrent calls: exactly one returns True."""
    user_id = await create_web_user(db_path, "race@example.com")

    # Sequential calls - second sees the flag and returns False.
    first = await mark_guest_imported(db_path, user_id)
    second = await mark_guest_imported(db_path, user_id)
    assert first is True
    assert second is False
