"""Tests for the GET /api/v1/dashboard/activity-trend endpoint."""

import asyncio
import os
from datetime import datetime, timedelta

import aiosqlite
import pytest
import pytest_asyncio

# Set env before importing app
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import create_web_user, ensure_user, init_db, log_meal
from src.db_pool import get_db
from src.web.app import app
from src.web.deps import (
    SubscriptionInfo,
    get_current_user,
    get_db_path,
    get_subscription_info,
)

ACTIVITY_API = "/macro_app/api/v1/dashboard/activity-trend"


# ===========================================================================
# Fixtures
# ===========================================================================

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
async def auth_client(override_db):
    """Authenticated test client - premium by default (self-host mode, no STRIPE_SECRET_KEY)."""
    user_id = await create_web_user(override_db, "activitytest@example.com")
    await ensure_user(override_db, user_id, "activitytest", "Activity")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "activitytest@example.com",
            "username": "activitytest@example.com",
            "first_name": "activitytest",
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
        c._db_path = override_db
        yield c

    app.dependency_overrides.pop(get_current_user, None)


@pytest_asyncio.fixture
async def free_client(override_db):
    """Authenticated test client with free-tier subscription (not premium)."""
    user_id = await create_web_user(override_db, "freeuser@example.com")
    await ensure_user(override_db, user_id, "freeuser", "Free")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "freeuser@example.com",
            "username": "freeuser@example.com",
            "first_name": "freeuser",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }

    def mock_sub():
        return SubscriptionInfo(
            plan="free",
            status="active",
            is_premium=False,
            is_og=False,
            app_mode="hosted",
            beta_mode=False,
            image_queries_used=0,
            image_queries_limit=5,
            text_meals_used=0,
            text_meals_limit=5,
            chats_used=0,
            chats_limit=1,
            meal_edits_limit=2,
            trial_available=True,
            trial_ends_at=None,
        )

    app.dependency_overrides[get_current_user] = mock_user
    app.dependency_overrides[get_subscription_info] = mock_sub

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        c._user_id = user_id
        c._db_path = override_db
        yield c

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_subscription_info, None)


@pytest_asyncio.fixture
async def pro_client(override_db):
    """Authenticated test client with explicit pro subscription override."""
    user_id = await create_web_user(override_db, "prouser@example.com")
    await ensure_user(override_db, user_id, "prouser", "Pro")

    async def mock_user():
        return {
            "user_id": user_id,
            "email": "prouser@example.com",
            "username": "prouser@example.com",
            "first_name": "prouser",
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
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Requested-With": "MacroApp"},
    ) as c:
        c._user_id = user_id
        c._db_path = override_db
        yield c

    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_subscription_info, None)


# ===========================================================================
# Helpers
# ===========================================================================

def _today_str():
    """Return today's date as YYYY-MM-DD in America/Los_Angeles (matches DB default timezone pref)."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    return datetime.now(tz).strftime("%Y-%m-%d")


async def _insert_workout(db_path, user_id, date_str, calories_burned, duration_sec=1800,
                          source="manual", started_at=None, external_id=None):
    """Insert a workout_logs row for a given date."""
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO workout_logs (user_id, source, external_id, activity_type, name, "
            "started_at, duration_sec, calories_burned, distance_m, avg_heart_rate, logged_at, raw_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, source, external_id or f"{source}-{date_str}", "running", "Morning Run",
             started_at or f"{date_str}T08:00:00", duration_sec, calories_burned, 5000, 140,
             date_str, "{}"),
        )
        await db.commit()


async def _insert_fitbit_activity(db_path, user_id, date_str, activity_calories=0, steps=0,
                                   fairly_active_min=0, very_active_min=0):
    """Insert a fitbit_activity row for a given date."""
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO fitbit_activity (user_id, date, calories_out, activity_calories, steps, "
            "fairly_active_min, very_active_min, resting_heart_rate, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, date_str, activity_calories + 500, activity_calories, steps,
             fairly_active_min, very_active_min, 65, f"{date_str}T12:00:00"),
        )
        await db.commit()


# ===========================================================================
# Tests
# ===========================================================================

class TestActivityTrendBasic:
    """Test basic response structure with no data."""

    @pytest.mark.asyncio
    async def test_empty_response_structure(self, auth_client):
        """With no data, endpoint returns 7 days of zeroed-out entries."""
        resp = await auth_client.get(ACTIVITY_API)
        assert resp.status_code == 200
        data = resp.json()
        assert "days" in data
        assert len(data["days"]) == 7

        # Each day should have the expected keys with zero values
        for day in data["days"]:
            assert "date" in day
            assert "burned_calories" in day
            assert "intake_calories" in day
            assert "steps" in day
            assert "active_minutes" in day
            assert "workout_count" in day
            assert "duration_min" in day
            assert day["burned_calories"] == 0
            assert day["intake_calories"] == 0
            assert day["steps"] == 0
            assert day["active_minutes"] == 0
            assert day["workout_count"] == 0
            assert day["duration_min"] == 0

    @pytest.mark.asyncio
    async def test_empty_no_truncated_field(self, auth_client):
        """Premium users requesting default 7 days should not get a truncated field."""
        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_dates_are_in_order(self, auth_client):
        """Days should be returned in chronological order (oldest first)."""
        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()
        dates = [d["date"] for d in data["days"]]
        assert dates == sorted(dates)

    @pytest.mark.asyncio
    async def test_last_date_is_today(self, auth_client):
        """The last entry should be today's date."""
        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()
        today = _today_str()
        assert data["days"][-1]["date"] == today


class TestActivityTrendWorkouts:
    """Test response with workout_logs data."""

    @pytest.mark.asyncio
    async def test_workout_calories_and_count(self, auth_client):
        """Workout data should populate burned_calories, workout_count, and duration_min."""
        user_id = auth_client._user_id
        db_path = auth_client._db_path
        today = _today_str()

        await _insert_workout(db_path, user_id, today, calories_burned=350, duration_sec=2400)

        resp = await auth_client.get(ACTIVITY_API)
        assert resp.status_code == 200
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        assert today_entry["burned_calories"] == 350
        assert today_entry["workout_count"] == 1
        assert today_entry["duration_min"] == 40  # 2400 / 60

    @pytest.mark.asyncio
    async def test_multiple_workouts_same_day(self, auth_client):
        """Multiple workouts on the same day should be summed."""
        user_id = auth_client._user_id
        db_path = auth_client._db_path
        today = _today_str()

        await _insert_workout(db_path, user_id, today, calories_burned=200, duration_sec=1800, source="strava")
        await _insert_workout(db_path, user_id, today, calories_burned=150, duration_sec=1200, source="manual")

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        assert today_entry["burned_calories"] == 350  # 200 + 150
        assert today_entry["workout_count"] == 2
        assert today_entry["duration_min"] == 50  # (1800 + 1200) / 60


class TestActivityTrendFitbit:
    """Test response with fitbit_activity data.

    Contract (2026-04-22 change): `burned_calories` reflects LOGGED WORKOUTS
    only - Strava + Fitbit individual activity logs + manual entries. The
    Fitbit/Oura daily summary's above-BMR number is intentionally excluded
    because the daily calorie target already accounts for NEAT via the
    Mifflin-St Jeor activity multiplier. Summing it on top would
    double-count baseline activity in the eat-back adjustment.
    """

    @pytest.mark.asyncio
    async def test_fitbit_summary_alone_does_not_populate_burned_calories(self, auth_client):
        """Fitbit daily summary (no individual workouts) = 0 burned_calories.

        Steps and active_minutes still populate from the daily summary so
        users who only wear a tracker still see their movement data.
        """
        user_id = auth_client._user_id
        db_path = auth_client._db_path
        today = _today_str()

        await _insert_fitbit_activity(
            db_path, user_id, today,
            activity_calories=500, steps=12000,
            fairly_active_min=30, very_active_min=20,
        )

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        assert today_entry["burned_calories"] == 0  # No workouts logged
        assert today_entry["steps"] == 12000
        assert today_entry["active_minutes"] == 50
        assert today_entry["workout_count"] == 0
        assert today_entry["duration_min"] == 0


class TestActivityTrendCombined:
    """Test response with both workout_logs and Fitbit daily summary data.

    burned_calories is workouts-only regardless of Fitbit summary value.
    """

    @pytest.mark.asyncio
    async def test_workouts_only_ignores_fitbit_summary_smaller(self, auth_client):
        """Workouts 600 + Fitbit summary 400 -> burned_calories = 600 (workouts)."""
        user_id = auth_client._user_id
        db_path = auth_client._db_path
        today = _today_str()

        await _insert_fitbit_activity(
            db_path, user_id, today,
            activity_calories=400, steps=8000,
            fairly_active_min=15, very_active_min=10,
        )
        await _insert_workout(db_path, user_id, today, calories_burned=600, duration_sec=3600)

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        assert today_entry["burned_calories"] == 600  # workouts only
        assert today_entry["steps"] == 8000
        assert today_entry["active_minutes"] == 25
        assert today_entry["workout_count"] == 1
        assert today_entry["duration_min"] == 60

    @pytest.mark.asyncio
    async def test_workouts_only_ignores_fitbit_summary_larger(self, auth_client):
        """Even when Fitbit summary > workouts, burned_calories = workouts only.

        This is the key anti-double-count invariant: Fitbit's
        activityCalories / (caloriesOut-BMR) includes NEAT, which the
        target already budgets for.
        """
        user_id = auth_client._user_id
        db_path = auth_client._db_path
        today = _today_str()

        await _insert_fitbit_activity(
            db_path, user_id, today,
            activity_calories=800, steps=15000,
            fairly_active_min=40, very_active_min=30,
        )
        await _insert_workout(db_path, user_id, today, calories_burned=300, duration_sec=1800)

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        assert today_entry["burned_calories"] == 300  # workouts only, not 800
        assert today_entry["workout_count"] == 1
        assert today_entry["duration_min"] == 30


class TestActivityTrendIntake:
    """Test response with meal_logs intake data."""

    @pytest.mark.asyncio
    async def test_meal_intake_calories(self, auth_client, override_db):
        """Meal logs should populate intake_calories for the correct day."""
        user_id = auth_client._user_id
        today = _today_str()

        await log_meal(
            override_db, user_id, f"{today} 12:00",
            "Chicken Salad", "Grilled chicken with greens",
            450.0, 35.0, 20.0, 15.0, "Gemini", "lunch", "[]",
        )
        await log_meal(
            override_db, user_id, f"{today} 19:00",
            "Pasta", "Spaghetti bolognese",
            700.0, 25.0, 85.0, 25.0, "Gemini", "dinner", "[]",
        )

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        assert today_entry["intake_calories"] == 1150  # 450 + 700

    @pytest.mark.asyncio
    async def test_intake_only_counts_within_range(self, auth_client, override_db):
        """Meals outside the requested date range should not appear."""
        user_id = auth_client._user_id
        today = _today_str()
        # Log a meal 30 days ago (outside the default 7-day window)
        old_date = (datetime.fromisoformat(today) - timedelta(days=30)).strftime("%Y-%m-%d")

        await log_meal(
            override_db, user_id, f"{old_date} 12:00",
            "Old Meal", "Something old",
            999.0, 50.0, 50.0, 50.0, "Gemini", "lunch", "[]",
        )

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        # None of the 7 days should have 999 calories
        for day in data["days"]:
            assert day["intake_calories"] != 999


class TestActivityTrendFreeUserTruncation:
    """Test that free users are truncated to 7 days when requesting more."""

    @pytest.mark.asyncio
    async def test_free_user_truncated_to_7_days(self, free_client):
        """Free user requesting 30 days should get only 7 days with truncated flag."""
        resp = await free_client.get(f"{ACTIVITY_API}?days=30")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["days"]) == 7
        assert data["truncated"] is True
        assert "upgrade_message" in data

    @pytest.mark.asyncio
    async def test_free_user_7_days_not_truncated(self, free_client):
        """Free user requesting 7 days (default) should not get truncated flag."""
        resp = await free_client.get(ACTIVITY_API)
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["days"]) == 7
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_free_user_90_days_still_truncated(self, free_client):
        """Free user requesting 90 days should still be truncated to 7."""
        resp = await free_client.get(f"{ACTIVITY_API}?days=90")
        data = resp.json()

        assert len(data["days"]) == 7
        assert data["truncated"] is True


class TestActivityTrendProUser:
    """Test that pro users get the full requested range."""

    @pytest.mark.asyncio
    async def test_pro_user_gets_30_days(self, pro_client):
        """Pro user requesting 30 days should get 30 days with no truncation."""
        resp = await pro_client.get(f"{ACTIVITY_API}?days=30")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["days"]) == 30
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_pro_user_gets_90_days(self, pro_client):
        """Pro user requesting 90 days should get 90 days."""
        resp = await pro_client.get(f"{ACTIVITY_API}?days=90")
        data = resp.json()

        assert len(data["days"]) == 90
        assert "truncated" not in data

    @pytest.mark.asyncio
    async def test_pro_user_capped_at_90_days(self, pro_client):
        """Requesting more than 90 days should be capped at 90."""
        resp = await pro_client.get(f"{ACTIVITY_API}?days=180")
        data = resp.json()

        assert len(data["days"]) == 90

    @pytest.mark.asyncio
    async def test_pro_user_min_7_days(self, pro_client):
        """Requesting fewer than 7 days should still return 7."""
        resp = await pro_client.get(f"{ACTIVITY_API}?days=3")
        data = resp.json()

        assert len(data["days"]) == 7


class TestActivityTrendStravaFitbitDedup:
    """Strava + Fitbit often capture the same session (Strava's GPS'd run
    gets auto-detected by Fitbit as a Walk/Run). The activity-trend
    endpoint must dedup these by time overlap - Strava wins - or every
    shared session doubles the day's burned_calories.
    """

    @pytest.mark.asyncio
    async def test_overlapping_strava_and_fitbit_counted_once(self, auth_client):
        """Same time range + Strava + Fitbit -> counted once (Strava)."""
        user_id = auth_client._user_id
        db_path = auth_client._db_path
        today = _today_str()

        await _insert_workout(
            db_path, user_id, today,
            calories_burned=400, duration_sec=1800,
            source="strava", external_id="strava-123",
            started_at=f"{today}T09:00:00Z",
        )
        # Same activity picked up by Fitbit auto-detection, 2 minutes later.
        await _insert_workout(
            db_path, user_id, today,
            calories_burned=380, duration_sec=1800,
            source="fitbit", external_id="fitbit-456",
            started_at=f"{today}T09:02:00Z",
        )

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        # Only Strava's 400 should count - Fitbit gets deduped out.
        assert today_entry["burned_calories"] == 400
        assert today_entry["workout_count"] == 1

    @pytest.mark.asyncio
    async def test_non_overlapping_strava_and_fitbit_both_counted(self, auth_client):
        """Distinct sessions (morning Strava + evening Fitbit) both count."""
        user_id = auth_client._user_id
        db_path = auth_client._db_path
        today = _today_str()

        await _insert_workout(
            db_path, user_id, today,
            calories_burned=400, duration_sec=1800,
            source="strava", external_id="strava-morning",
            started_at=f"{today}T09:00:00Z",
        )
        await _insert_workout(
            db_path, user_id, today,
            calories_burned=200, duration_sec=1200,
            source="fitbit", external_id="fitbit-evening",
            started_at=f"{today}T19:00:00Z",
        )

        resp = await auth_client.get(ACTIVITY_API)
        data = resp.json()

        today_entry = next(d for d in data["days"] if d["date"] == today)
        assert today_entry["burned_calories"] == 600  # 400 + 200
        assert today_entry["workout_count"] == 2
