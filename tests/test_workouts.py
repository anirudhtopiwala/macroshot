"""Tests for the workout API endpoints."""

import asyncio
import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio

# Set env before importing app
os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import create_web_user, init_db, upsert_workout, upsert_fitbit_activity
from src.web.app import app
from src.web.deps import get_db_path, get_current_user


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
async def auth_client(override_db):
    """Create an authenticated test client with a web user."""
    user_id = await create_web_user(override_db, "testuser@example.com")

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
        c._db_path = override_db
        yield c

    app.dependency_overrides.pop(get_current_user, None)


# Helper to get today's date string in the test timezone
def _today_str() -> str:
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    return datetime.now(tz).strftime("%Y-%m-%d")


# --- GET /workouts ---


@pytest.mark.asyncio
async def test_get_workouts_empty(auth_client):
    """GET /workouts returns empty data when no workouts exist."""
    resp = await auth_client.get("/macro_app/api/v1/workouts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["workouts"] == []
    assert data["fitbit_summary"] is None
    assert data["totals"]["active_calories"] == 0
    assert data["totals"]["workout_count"] == 0
    assert data["totals"]["total_duration_sec"] == 0
    assert data["totals"]["steps"] == 0
    assert data["totals"]["active_minutes"] == 0


@pytest.mark.asyncio
async def test_get_workouts_with_workout_logs(auth_client):
    """GET /workouts returns workout data after inserting workout_logs."""
    db_path = auth_client._db_path
    user_id = auth_client._user_id
    today = _today_str()

    await upsert_workout(
        db_path=db_path,
        user_id=user_id,
        source="manual",
        external_id="manual_test001",
        activity_type="Running",
        name="Morning Run",
        started_at="2026-01-01T08:00:00Z",
        duration_sec=1800,
        calories_burned=300,
        distance_m=5000,
        avg_heart_rate=145,
        logged_at=today,
    )

    resp = await auth_client.get("/macro_app/api/v1/workouts", params={"date": today})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["workouts"]) == 1

    w = data["workouts"][0]
    assert w["source"] == "manual"
    assert w["activity_type"] == "Running"
    assert w["name"] == "Morning Run"
    assert w["duration_sec"] == 1800
    assert w["calories_burned"] == 300
    assert w["distance_m"] == 5000
    assert w["avg_heart_rate"] == 145

    assert data["totals"]["active_calories"] == 300
    assert data["totals"]["workout_count"] == 1
    assert data["totals"]["total_duration_sec"] == 1800


@pytest.mark.asyncio
async def test_get_workouts_with_fitbit_summary(auth_client):
    """GET /workouts returns fitbit_summary when fitbit_activity exists."""
    db_path = auth_client._db_path
    user_id = auth_client._user_id
    today = _today_str()

    await upsert_fitbit_activity(
        db_path=db_path,
        user_id=user_id,
        date_str=today,
        calories_out=2100,
        activity_calories=500,
        steps=8500,
        fairly_active_min=25,
        very_active_min=15,
        resting_heart_rate=62,
    )

    resp = await auth_client.get("/macro_app/api/v1/workouts", params={"date": today})
    assert resp.status_code == 200
    data = resp.json()

    fb = data["fitbit_summary"]
    assert fb is not None
    assert fb["calories_out"] == 2100
    assert fb["activity_calories"] == 500
    assert fb["steps"] == 8500
    assert fb["fairly_active_min"] == 25
    assert fb["very_active_min"] == 15
    assert fb["resting_heart_rate"] == 62
    assert fb["fetched_at"] is not None

    # Contract (2026-04-22): totals.active_calories reflects logged
    # workout sessions only - the Fitbit daily summary is NOT summed in,
    # because the daily target already covers NEAT via the Mifflin-St
    # Jeor activity multiplier. Summary data is still exposed via
    # fitbit_summary.* for informational display.
    assert data["totals"]["active_calories"] == 0  # no workouts logged
    assert data["totals"]["steps"] == 8500
    assert data["totals"]["active_minutes"] == 40  # 25 + 15


@pytest.mark.asyncio
async def test_get_workouts_totals_workouts_only_ignores_fitbit_summary(auth_client):
    """totals.active_calories = sum of workouts; Fitbit summary ignored."""
    db_path = auth_client._db_path
    user_id = auth_client._user_id
    today = _today_str()

    await upsert_workout(
        db_path=db_path,
        user_id=user_id,
        source="strava",
        external_id="strava_abc123",
        activity_type="Cycling",
        name="Afternoon Ride",
        started_at="2026-01-01T14:00:00Z",
        duration_sec=3600,
        calories_burned=600,
        logged_at=today,
    )

    # Fitbit daily summary - should NOT influence the workout totals
    # (it still shows up separately in fitbit_summary).
    await upsert_fitbit_activity(
        db_path=db_path,
        user_id=user_id,
        date_str=today,
        calories_out=2000,
        activity_calories=400,
        steps=6000,
        fairly_active_min=20,
        very_active_min=10,
    )

    resp = await auth_client.get("/macro_app/api/v1/workouts", params={"date": today})
    assert resp.status_code == 200
    data = resp.json()

    assert data["totals"]["active_calories"] == 600  # workouts-only
    assert data["totals"]["workout_count"] == 1


@pytest.mark.asyncio
async def test_get_workouts_totals_ignore_larger_fitbit_summary(auth_client):
    """Even when the Fitbit daily summary exceeds workouts, workouts win.

    This is the anti-double-count invariant - the target already includes
    NEAT, and the summary on top would overpay the eat-back adjustment.
    """
    db_path = auth_client._db_path
    user_id = auth_client._user_id
    today = _today_str()

    await upsert_workout(
        db_path=db_path,
        user_id=user_id,
        source="manual",
        external_id="manual_low",
        activity_type="Walking",
        name="Short Walk",
        started_at="2026-01-01T10:00:00Z",
        duration_sec=900,
        calories_burned=200,
        logged_at=today,
    )

    await upsert_fitbit_activity(
        db_path=db_path,
        user_id=user_id,
        date_str=today,
        activity_calories=800,
        steps=12000,
        fairly_active_min=30,
        very_active_min=20,
    )

    resp = await auth_client.get("/macro_app/api/v1/workouts", params={"date": today})
    assert resp.status_code == 200
    data = resp.json()

    assert data["totals"]["active_calories"] == 200  # workouts-only, not 800


# --- GET /workouts/history ---


@pytest.mark.asyncio
async def test_get_workout_history_empty(auth_client):
    """GET /workouts/history returns empty when no workouts exist."""
    resp = await auth_client.get("/macro_app/api/v1/workouts/history")
    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == []
    assert data["totals"]["workout_count"] == 0
    assert data["totals"]["total_calories"] == 0
    assert data["totals"]["total_duration_sec"] == 0


@pytest.mark.asyncio
async def test_get_workout_history_grouped_by_date(auth_client):
    """GET /workouts/history returns data grouped by date."""
    db_path = auth_client._db_path
    user_id = auth_client._user_id
    today = _today_str()

    # Insert two workouts on the same date
    await upsert_workout(
        db_path=db_path,
        user_id=user_id,
        source="manual",
        external_id="manual_hist1",
        activity_type="Running",
        name="Morning Run",
        started_at="2026-01-01T08:00:00Z",
        duration_sec=1800,
        calories_burned=300,
        logged_at=today,
    )
    await upsert_workout(
        db_path=db_path,
        user_id=user_id,
        source="manual",
        external_id="manual_hist2",
        activity_type="Yoga",
        name="Evening Yoga",
        started_at="2026-01-01T18:00:00Z",
        duration_sec=3600,
        calories_burned=150,
        logged_at=today,
    )

    resp = await auth_client.get("/macro_app/api/v1/workouts/history", params={"days": 7})
    assert resp.status_code == 200
    data = resp.json()

    assert len(data["days"]) == 1
    day = data["days"][0]
    assert day["date"] == today
    assert len(day["workouts"]) == 2
    assert day["total_calories"] == 450
    assert day["total_duration_sec"] == 5400

    assert data["totals"]["workout_count"] == 2
    assert data["totals"]["total_calories"] == 450
    assert data["totals"]["total_duration_sec"] == 5400


# --- POST /workouts ---


@pytest.mark.asyncio
async def test_post_manual_workout(auth_client):
    """POST /workouts creates a manual workout with valid data."""
    today = _today_str()
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "activity_type": "Running",
            "name": "Morning Run",
            "duration_min": 30,
            "calories_burned": 350,
            "date": today,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert "id" in data

    # Verify it shows up in GET
    resp2 = await auth_client.get("/macro_app/api/v1/workouts", params={"date": today})
    assert resp2.status_code == 200
    workouts = resp2.json()["workouts"]
    assert len(workouts) == 1
    assert workouts[0]["activity_type"] == "Running"
    assert workouts[0]["name"] == "Morning Run"
    assert workouts[0]["duration_sec"] == 1800  # 30 * 60
    assert workouts[0]["calories_burned"] == 350
    assert workouts[0]["source"] == "manual"


@pytest.mark.asyncio
async def test_post_manual_workout_defaults(auth_client):
    """POST /workouts uses defaults for optional fields and workout appears in GET."""
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "activity_type": "Stretching",
            "duration_min": 15,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert "id" in data

    # Verify the workout appears in GET with correct defaults
    get_resp = await auth_client.get("/macro_app/api/v1/workouts")
    workouts = get_resp.json()["workouts"]
    created = [w for w in workouts if w["id"] == data["id"]]
    assert len(created) == 1
    w = created[0]
    assert w["name"] == "Stretching"  # defaults to activity_type
    assert w["calories_burned"] == 0  # default
    assert w["duration_sec"] == 900   # 15 min * 60
    assert w["source"] == "manual"


@pytest.mark.asyncio
async def test_post_manual_workout_missing_activity_type(auth_client):
    """POST /workouts rejects request with missing activity_type."""
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "duration_min": 30,
            "calories_burned": 200,
        },
    )
    assert resp.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_post_manual_workout_missing_duration(auth_client):
    """POST /workouts rejects request with missing duration_min."""
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "activity_type": "Running",
            "calories_burned": 200,
        },
    )
    assert resp.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_post_manual_workout_bad_duration_zero(auth_client):
    """POST /workouts rejects duration_min of 0 (must be >= 1)."""
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "activity_type": "Running",
            "duration_min": 0,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_manual_workout_bad_duration_too_high(auth_client):
    """POST /workouts rejects duration_min over 600."""
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "activity_type": "Running",
            "duration_min": 601,
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_manual_workout_empty_activity_type(auth_client):
    """POST /workouts rejects empty activity_type string."""
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "activity_type": "",
            "duration_min": 30,
        },
    )
    assert resp.status_code == 422


# --- DELETE /workouts/{id} ---


@pytest.mark.asyncio
async def test_delete_manual_workout(auth_client):
    """DELETE /workouts/{id} deletes a manual workout."""
    today = _today_str()
    # Create a manual workout
    resp = await auth_client.post(
        "/macro_app/api/v1/workouts",
        json={
            "activity_type": "Running",
            "name": "To Delete",
            "duration_min": 20,
            "calories_burned": 200,
            "date": today,
        },
    )
    assert resp.status_code == 200
    workout_id = resp.json()["id"]

    # Delete it
    resp2 = await auth_client.delete(f"/macro_app/api/v1/workouts/{workout_id}")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "deleted"

    # Verify it's gone
    resp3 = await auth_client.get("/macro_app/api/v1/workouts", params={"date": today})
    assert resp3.status_code == 200
    assert len(resp3.json()["workouts"]) == 0


@pytest.mark.asyncio
async def test_delete_non_manual_workout_rejected(auth_client):
    """DELETE /workouts/{id} rejects deletion of non-manual workouts."""
    db_path = auth_client._db_path
    user_id = auth_client._user_id
    today = _today_str()

    # Insert a Strava workout directly (non-manual source)
    row_id = await upsert_workout(
        db_path=db_path,
        user_id=user_id,
        source="strava",
        external_id="strava_nodelete",
        activity_type="Cycling",
        name="Strava Ride",
        started_at="2026-01-01T10:00:00Z",
        duration_sec=3600,
        calories_burned=500,
        logged_at=today,
    )

    resp = await auth_client.delete(f"/macro_app/api/v1/workouts/{row_id}")
    assert resp.status_code == 400
    assert "manual" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_delete_workout_not_found(auth_client):
    """DELETE /workouts/{id} returns 404 for unknown workout ID."""
    resp = await auth_client.delete("/macro_app/api/v1/workouts/99999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()
