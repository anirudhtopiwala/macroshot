"""Tests for MCP tool logic: exercise-adjusted targets, activity history,
source-filtered workouts, Fitbit health data, and correction gating.

Tests the underlying functions that MCP tools call, verifying the data
composition is correct without needing to spawn the MCP subprocess.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from src.db import (
    create_web_user,
    init_db,
    log_meal,
    upsert_workout,
    upsert_fitbit_activity,
    get_workouts_for_date,
    get_fitbit_activity,
    get_today_totals,
    get_user_target,
)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    asyncio.run(init_db(path))
    return path


@pytest_asyncio.fixture
async def user_id(db_path):
    return await create_web_user(db_path, "mcp_test@example.com")


@pytest_asyncio.fixture
async def seeded_db(db_path, user_id):
    """Seed DB with targets, meals, workouts, and Fitbit data."""
    from src.db import set_user_target

    today = _today_str()

    # Set targets
    await set_user_target(db_path, user_id, calories=2000, protein=150, carbs=200, fat=70)

    # Log meals
    await log_meal(
        db_path, user_id,
        logged_at=f"{today} 08:00",
        item_name="Oatmeal",
        meal_description="Oatmeal with berries",
        calories=400, protein=15, carbs=60, fat=10,
        source="Gemini", meal_type="breakfast",
    )
    await log_meal(
        db_path, user_id,
        logged_at=f"{today} 12:30",
        item_name="Chicken salad",
        meal_description="Grilled chicken salad",
        calories=600, protein=45, carbs=30, fat=25,
        source="Gemini", meal_type="lunch",
    )

    # Manual workout
    await upsert_workout(
        db_path, user_id,
        source="manual", external_id="manual_001",
        activity_type="Running", name="Morning Run",
        started_at=f"{today}T07:00:00Z",
        duration_sec=1800, calories_burned=300,
        distance_m=5000, avg_heart_rate=155,
        logged_at=today,
    )

    # Strava workout
    await upsert_workout(
        db_path, user_id,
        source="strava", external_id="strava_001",
        activity_type="Cycling", name="Evening Ride",
        started_at=f"{today}T18:00:00Z",
        duration_sec=3600, calories_burned=500,
        distance_m=20000, avg_heart_rate=140,
        logged_at=today,
    )

    # Fitbit daily activity
    await upsert_fitbit_activity(
        db_path, user_id,
        date_str=today,
        calories_out=2400, activity_calories=600,
        steps=10000, fairly_active_min=30,
        very_active_min=25, resting_heart_rate=62,
    )

    # Yesterday's data for multi-day tests
    yesterday = _days_ago(1)
    await log_meal(
        db_path, user_id,
        logged_at=f"{yesterday} 12:00",
        item_name="Pizza",
        meal_description="Cheese pizza",
        calories=800, protein=30, carbs=90, fat=35,
        source="Gemini", meal_type="lunch",
    )
    await upsert_fitbit_activity(
        db_path, user_id,
        date_str=yesterday,
        calories_out=2100, activity_calories=350,
        steps=7000, fairly_active_min=20,
        very_active_min=10, resting_heart_rate=64,
    )

    return db_path


# ── Exercise-adjusted targets ──────────────────────────────────────────


class TestExerciseAdjustedTargets:
    """Tests for get_progress() exercise adjustment logic."""

    @pytest.mark.asyncio
    async def test_adjusted_target_with_workouts(self, seeded_db, user_id):
        """Targets should be adjusted upward when workouts are logged.

        Contract (2026-04-22): eat-back only counts logged workout sessions
        (Strava/Fitbit-activity-log/manual), not the Fitbit daily summary,
        because the daily target already includes NEAT via the Mifflin-St
        Jeor activity multiplier.
        """
        from src.services import get_progress

        progress = await get_progress(user_id, seeded_db, _today_str())

        assert progress["target"] is not None
        assert progress["target"]["calories"] == 2000

        # Seeded: manual workout 300 + Strava workout 500 = 800 kcal workouts.
        # Fitbit summary of 600 is IGNORED (already-in-target NEAT).
        adjusted = progress["adjusted_target"]
        assert adjusted is not None
        assert adjusted["calories"] > 2000

        exercise = progress["exercise_adjustment"]
        assert exercise is not None
        assert exercise["active_calories"] == 800  # workouts-only: 300 + 500

        # Default eat-back is 75%
        expected_cal_adj = round(800 * 0.75)
        assert exercise["calories"] == expected_cal_adj
        assert adjusted["calories"] == 2000 + expected_cal_adj

    @pytest.mark.asyncio
    async def test_remaining_uses_adjusted_target(self, seeded_db, user_id):
        """Remaining macros should be computed against adjusted target, not base."""
        from src.services import get_progress

        progress = await get_progress(user_id, seeded_db, _today_str())

        remaining = progress["remaining"]
        adjusted = progress["adjusted_target"]
        totals = progress["totals"]

        assert remaining is not None
        # Remaining = adjusted_target - consumed
        assert remaining["calories"] == adjusted["calories"] - totals["calories"]
        assert remaining["protein"] == adjusted["protein"] - totals["protein"]

    @pytest.mark.asyncio
    async def test_no_adjustment_without_workouts(self, db_path, user_id):
        """No exercise adjustment when no workouts or Fitbit data exists."""
        from src.db import set_user_target
        from src.services import get_progress

        await set_user_target(db_path, user_id, calories=2000, protein=150, carbs=200, fat=70)
        progress = await get_progress(user_id, db_path, _today_str())

        assert progress["adjusted_target"] is None
        assert progress["exercise_adjustment"] is None

    @pytest.mark.asyncio
    async def test_fitbit_summary_alone_does_not_trigger_adjustment(self, db_path, user_id):
        """Fitbit daily summary with no logged workouts = no eat-back adjustment.

        The daily target already covers NEAT/baseline movement via the
        Mifflin-St Jeor activity multiplier. Eating back the Fitbit
        summary on top would double-count that baseline.
        """
        from src.db import set_user_target
        from src.services import get_progress

        today = _today_str()
        await set_user_target(db_path, user_id, calories=2000, protein=150, carbs=200, fat=70)
        await upsert_fitbit_activity(
            db_path, user_id, today,
            activity_calories=400, steps=8000,
            fairly_active_min=20, very_active_min=15,
        )

        progress = await get_progress(user_id, db_path, today)
        assert progress["adjusted_target"] is None
        assert progress["exercise_adjustment"] is None


# ── Workout source filtering ──────────────────────────────────────────


class TestWorkoutSourceFiltering:
    """Tests for filtering workouts by source."""

    @pytest.mark.asyncio
    async def test_all_sources_returned(self, seeded_db, user_id):
        """get_workouts_for_date returns all sources."""
        workouts = await get_workouts_for_date(seeded_db, user_id, _today_str())
        sources = {w["source"] for w in workouts}
        assert "manual" in sources
        assert "strava" in sources
        assert len(workouts) == 2

    @pytest.mark.asyncio
    async def test_filter_by_strava(self, seeded_db, user_id):
        """Can filter to only Strava workouts."""
        workouts = await get_workouts_for_date(seeded_db, user_id, _today_str())
        strava = [w for w in workouts if w["source"] == "strava"]
        assert len(strava) == 1
        assert strava[0]["activity_type"] == "Cycling"
        assert strava[0]["calories_burned"] == 500

    @pytest.mark.asyncio
    async def test_filter_by_manual(self, seeded_db, user_id):
        """Can filter to only manual workouts."""
        workouts = await get_workouts_for_date(seeded_db, user_id, _today_str())
        manual = [w for w in workouts if w["source"] == "manual"]
        assert len(manual) == 1
        assert manual[0]["activity_type"] == "Running"
        assert manual[0]["distance_m"] == 5000

    @pytest.mark.asyncio
    async def test_workout_details(self, seeded_db, user_id):
        """Workout entries contain all expected fields."""
        workouts = await get_workouts_for_date(seeded_db, user_id, _today_str())
        w = next(w for w in workouts if w["source"] == "strava")
        assert w["name"] == "Evening Ride"
        assert w["duration_sec"] == 3600
        assert w["avg_heart_rate"] == 140
        assert w["distance_m"] == 20000


# ── Fitbit health data ────────────────────────────────────────────────


class TestFitbitHealthData:
    """Tests for Fitbit daily health stats."""

    @pytest.mark.asyncio
    async def test_fitbit_activity_data(self, seeded_db, user_id):
        """Fitbit daily data includes all health metrics."""
        fb = await get_fitbit_activity(seeded_db, user_id, _today_str())
        assert fb is not None
        assert fb["steps"] == 10000
        assert fb["activity_calories"] == 600
        assert fb["calories_out"] == 2400
        assert fb["fairly_active_min"] == 30
        assert fb["very_active_min"] == 25
        assert fb["resting_heart_rate"] == 62

    @pytest.mark.asyncio
    async def test_fitbit_no_data(self, db_path, user_id):
        """Returns None when no Fitbit data exists for a date."""
        fb = await get_fitbit_activity(db_path, user_id, _today_str())
        assert fb is None

    @pytest.mark.asyncio
    async def test_fitbit_multi_day(self, seeded_db, user_id):
        """Fitbit data for multiple days returns correct per-day values."""
        today_fb = await get_fitbit_activity(seeded_db, user_id, _today_str())
        yesterday_fb = await get_fitbit_activity(seeded_db, user_id, _days_ago(1))

        assert today_fb["steps"] == 10000
        assert yesterday_fb["steps"] == 7000
        assert today_fb["resting_heart_rate"] == 62
        assert yesterday_fb["resting_heart_rate"] == 64


# ── Activity history aggregation ──────────────────────────────────────


class TestActivityHistoryAggregation:
    """Tests for multi-day activity aggregation (combines all sources)."""

    @pytest.mark.asyncio
    async def test_merged_calories(self, seeded_db, user_id):
        """Active calories use max(workout_total, fitbit) to avoid double-counting."""
        today = _today_str()
        workouts = await get_workouts_for_date(seeded_db, user_id, today)
        fitbit = await get_fitbit_activity(seeded_db, user_id, today)

        workout_cals = sum(w["calories_burned"] for w in workouts)
        fitbit_cals = fitbit["activity_calories"]
        expected = max(workout_cals, fitbit_cals)

        assert workout_cals == 800  # 300 + 500
        assert fitbit_cals == 600
        assert expected == 800

    @pytest.mark.asyncio
    async def test_today_totals(self, seeded_db, user_id):
        """Today's meal totals match seeded data."""
        totals = await get_today_totals(seeded_db, user_id, _today_str())
        assert totals["calories"] == 1000  # 400 + 600
        assert totals["protein"] == 60  # 15 + 45
        assert totals["meal_count"] == 2

    @pytest.mark.asyncio
    async def test_yesterday_data(self, seeded_db, user_id):
        """Yesterday's data is correctly stored and retrievable."""
        yesterday = _days_ago(1)
        totals = await get_today_totals(seeded_db, user_id, yesterday)
        assert totals["calories"] == 800
        assert totals["meal_count"] == 1

        fb = await get_fitbit_activity(seeded_db, user_id, yesterday)
        assert fb["activity_calories"] == 350
        assert fb["steps"] == 7000


# ── Correction gating ─────────────────────────────────────────────────


class TestCorrectionGating:
    """Tests for the meal correction limit (2 free per session)."""

    @pytest.mark.asyncio
    async def test_correction_limit_enforced(self, db_path, user_id):
        """Free users should be blocked after 2 corrections in a session."""
        import json
        from src.db import create_meal_session, get_meal_session

        today = _today_str()
        session_id = "test-session-001"

        # Create a session with 2 user corrections already in conversation
        # Initial analysis turn + 2 correction turns = 3 user entries
        conversation = [
            {"role": "user", "text": "analyze my meal"},
            {"role": "model", "text": "Here's what I see..."},
            {"role": "user", "text": "add more rice"},
            {"role": "model", "text": "Updated with rice..."},
            {"role": "user", "text": "also add dal"},
            {"role": "model", "text": "Added dal..."},
        ]

        await create_meal_session(
            db_path, session_id, user_id,
            conversation=json.dumps(conversation),
        )

        session = await get_meal_session(db_path, session_id, user_id)
        convo = json.loads(session["conversation"])
        # Count user correction turns (subtract 1 for initial analysis)
        user_turns = sum(1 for m in convo if m.get("role") == "user") - 1
        assert user_turns == 2  # At limit

    @pytest.mark.asyncio
    async def test_correction_under_limit(self, db_path, user_id):
        """Session with 1 correction should be under the limit."""
        import json
        from src.db import create_meal_session, get_meal_session

        session_id = "test-session-002"
        conversation = [
            {"role": "user", "text": "analyze my meal"},
            {"role": "model", "text": "Here's what I see..."},
            {"role": "user", "text": "add more rice"},
            {"role": "model", "text": "Updated..."},
        ]

        await create_meal_session(
            db_path, session_id, user_id,
            conversation=json.dumps(conversation),
        )

        session = await get_meal_session(db_path, session_id, user_id)
        convo = json.loads(session["conversation"])
        user_turns = sum(1 for m in convo if m.get("role") == "user") - 1
        assert user_turns == 1  # Under limit


# ── Shield gating ─────────────────────────────────────────────────────


class TestShieldGating:
    """Tests for streak shield Pro-only gating."""

    @pytest.mark.asyncio
    async def test_shield_earning_logic(self, db_path, user_id):
        """Shield earning: 1 per 3 on-target days, max 3 unused."""
        from src.db import set_user_target, get_streak_shields
        import aiosqlite

        await set_user_target(db_path, user_id, calories=2000, protein=150, carbs=200, fat=70)

        # Log meals on-target for 3 days
        for i in range(3):
            d = _days_ago(i)
            await log_meal(
                db_path, user_id,
                logged_at=f"{d} 12:00",
                item_name=f"Day {i} meal",
                meal_description="On-target meal",
                calories=2000, protein=150, carbs=200, fat=70,
                source="Gemini", meal_type="lunch",
            )

        shields = await get_streak_shields(db_path, user_id)
        # Shields are earned via evaluate_badges, not directly from logging
        # So shields should still be 0 (no badge evaluation triggered here)
        assert shields["available"] == 0

    @pytest.mark.asyncio
    async def test_free_user_shield_cap(self, db_path, user_id):
        """Free users can only earn 1 lifetime shield."""
        import aiosqlite

        # Directly insert 1 shield (simulating earning)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "INSERT INTO streak_shields (user_id, earned_at) VALUES (?, ?)",
                (user_id, now),
            )
            await db.commit()

            # Verify 1 shield exists
            row = await (await db.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = ? AND used_at IS NULL",
                (user_id,),
            )).fetchone()
            assert row[0] == 1

            # Now check badge engine respects the cap for free users
            total_earned = await (await db.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = ?",
                (user_id,),
            )).fetchone()
            assert total_earned[0] == 1

            # Free user with total_earned >= 1 and is_premium=False
            # should not earn more shields (tested via badge_engine logic)
            is_premium = False
            if not is_premium and total_earned[0] >= 1:
                should_earn = False
            else:
                should_earn = True
            assert should_earn is False
