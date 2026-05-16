"""Tests for src/db.py - SQLite async layer."""

import asyncio
import os
import tempfile

import pytest
import pytest_asyncio

import aiosqlite

from src.db import (
    _calculate_streak,
    delete_meal,
    ensure_user,
    get_community_stats,
    get_daily_totals_7days,
    get_gemini_stats,
    get_last_meal,
    get_last_meal_by_type,
    get_meal_by_id,
    get_meals_for_day,
    get_period_totals,
    get_recent_meals,
    get_today_totals,
    get_user_prefs,
    get_user_stats,
    get_weekly_avg,
    has_meal_in_window,
    init_db,
    log_fatsecret_comparison,
    log_gemini_call,
    log_meal,
    set_user_prefs,
    set_user_target,
    get_user_target,
    update_meal,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    """Fresh DB for each test."""
    path = str(tmp_path / "test.db")
    await init_db(path)
    return path


async def _add_user(db, user_id=1, username="alice", first_name="Alice"):
    await ensure_user(db, user_id, username, first_name)


async def _add_meal(db, user_id=1, logged_at="2026-03-01 12:00",
                    item_name="Oatmeal", meal_description="",
                    calories=400, protein=15, carbs=60, fat=10, source="Gemini"):
    await log_meal(db, user_id, logged_at, item_name, meal_description,
                   calories, protein, carbs, fat, source)


# ---------------------------------------------------------------------------
# _calculate_streak  (pure, no DB needed)
# ---------------------------------------------------------------------------

class TestCalculateStreak:
    """Pure sync tests - no asyncio mark needed."""

    def test_empty(self):
        assert _calculate_streak([], "2026-03-01") == 0

    def test_only_today(self):
        assert _calculate_streak(["2026-03-01"], "2026-03-01") == 1

    def test_only_yesterday(self):
        # No log today but yesterday present → streak = 1 (user hasn't eaten yet)
        assert _calculate_streak(["2026-02-28"], "2026-03-01") == 1

    def test_today_and_yesterday(self):
        assert _calculate_streak(["2026-02-28", "2026-03-01"], "2026-03-01") == 2

    def test_consecutive_three_days(self):
        dates = ["2026-02-27", "2026-02-28", "2026-03-01"]
        assert _calculate_streak(dates, "2026-03-01") == 3

    def test_gap_breaks_streak(self):
        # Missing 2026-02-28, so streak from yesterday is 0; today=1
        dates = ["2026-02-27", "2026-03-01"]
        assert _calculate_streak(dates, "2026-03-01") == 1

    def test_old_dates_only(self):
        # Last log was two days ago - no anchor → 0
        dates = ["2026-02-27"]
        assert _calculate_streak(dates, "2026-03-01") == 0

    def test_long_streak_no_log_today(self):
        dates = ["2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27", "2026-02-28"]
        # anchor = yesterday (2026-02-28), walk back 5 days
        assert _calculate_streak(dates, "2026-03-01") == 5


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

async def test_init_db_idempotent(db):
    """Calling init_db twice on the same file should not raise."""
    await init_db(db)


# ---------------------------------------------------------------------------
# ensure_user
# ---------------------------------------------------------------------------

async def test_ensure_user_creates_row(db):
    await ensure_user(db, 42, "bob", "Bob")
    stats = await get_community_stats(db)
    assert stats["total_users"] == 1


async def test_ensure_user_idempotent(db):
    """Calling ensure_user twice should not duplicate the user."""
    await ensure_user(db, 42, "bob", "Bob")
    await ensure_user(db, 42, "bob_new", "Bob New")
    stats = await get_community_stats(db)
    assert stats["total_users"] == 1


async def test_ensure_user_updates_mutable_fields(db):
    """Second call should update username/first_name."""
    await ensure_user(db, 42, "old_name", "Old")
    await ensure_user(db, 42, "new_name", "New")
    # Verify via get_user_stats (row still exists, no error)
    u = await get_user_stats(db, 42)
    assert u["total_meals"] == 0


# ---------------------------------------------------------------------------
# log_meal
# ---------------------------------------------------------------------------

async def test_log_meal_appears_in_stats(db):
    await _add_user(db)
    await _add_meal(db)
    stats = await get_community_stats(db)
    assert stats["total_meals"] == 1


async def test_log_meal_multiple_rows(db):
    await _add_user(db)
    await _add_meal(db, logged_at="2026-03-01 08:00", item_name="Eggs")
    await _add_meal(db, logged_at="2026-03-01 12:00", item_name="Salad")
    await _add_meal(db, logged_at="2026-03-01 19:00", item_name="Pasta")
    stats = await get_community_stats(db)
    assert stats["total_meals"] == 3


# ---------------------------------------------------------------------------
# get_community_stats
# ---------------------------------------------------------------------------

async def test_community_stats_empty(db):
    stats = await get_community_stats(db)
    assert stats["total_meals"] == 0
    assert stats["total_users"] == 0
    assert stats["active_today"] == 0
    assert stats["active_this_week"] == 0
    assert stats["top_foods"] == []
    assert stats["avg_calories"] == 0.0


async def test_community_stats_active_today(db):
    await _add_user(db, user_id=1)
    await _add_user(db, user_id=2, username="carol", first_name="Carol")
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, user_id=1, logged_at=f"{today} 08:00")
    await _add_meal(db, user_id=2, logged_at=f"{today} 12:00")
    stats = await get_community_stats(db)
    assert stats["active_today"] == 2


async def test_community_stats_top_foods(db):
    await _add_user(db)
    await _add_meal(db, item_name="Oatmeal")
    await _add_meal(db, item_name="Oatmeal")
    await _add_meal(db, item_name="Oatmeal")
    await _add_meal(db, item_name="Eggs")
    await _add_meal(db, item_name="Eggs")
    stats = await get_community_stats(db)
    assert stats["top_foods"][0] == ("Oatmeal", 3)
    assert stats["top_foods"][1] == ("Eggs", 2)


async def test_community_stats_avg_calories(db):
    await _add_user(db)
    await _add_meal(db, calories=300)
    await _add_meal(db, calories=500)
    stats = await get_community_stats(db)
    assert stats["avg_calories"] == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# get_user_stats
# ---------------------------------------------------------------------------

async def test_user_stats_no_meals(db):
    await _add_user(db)
    u = await get_user_stats(db, 1)
    assert u["total_meals"] == 0


async def test_user_stats_total_meals(db):
    await _add_user(db)
    await _add_meal(db)
    await _add_meal(db)
    u = await get_user_stats(db, 1)
    assert u["total_meals"] == 2


async def test_user_stats_weekly_avg_divides_by_active_days(db):
    """Weekly avg divides total by number of days that have meals (not a fixed 7)."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    # Only one meal this week - avg should be the meal's values (1 active day)
    await _add_meal(db, logged_at=f"{today} 12:00", calories=700, protein=28, carbs=70, fat=14)
    u = await get_user_stats(db, 1)
    assert u["avg_daily_calories_week"] == pytest.approx(700.0)
    assert u["avg_protein_week"] == pytest.approx(28.0)
    assert u["avg_carbs_week"] == pytest.approx(70.0)
    assert u["avg_fat_week"] == pytest.approx(14.0)


async def test_user_stats_most_logged_meal(db):
    await _add_user(db)
    await _add_meal(db, item_name="Oatmeal")
    await _add_meal(db, item_name="Oatmeal")
    await _add_meal(db, item_name="Eggs")
    u = await get_user_stats(db, 1)
    assert u["most_logged_meal"] == ("Oatmeal", 2)


async def test_user_stats_macro_split_sums_to_100(db):
    await _add_user(db)
    await _add_meal(db, protein=25, carbs=50, fat=10)
    u = await get_user_stats(db, 1)
    macro = u["macro_split"]
    total = macro["pct_protein"] + macro["pct_carbs"] + macro["pct_fat"]
    assert total == pytest.approx(100.0, abs=0.1)


async def test_user_stats_macro_split_zero_macros(db):
    """If all macros are 0, split should be 0/0/0 without division error."""
    await _add_user(db)
    await _add_meal(db, protein=0, carbs=0, fat=0)
    u = await get_user_stats(db, 1)
    macro = u["macro_split"]
    assert macro["pct_protein"] == 0.0
    assert macro["pct_carbs"] == 0.0
    assert macro["pct_fat"] == 0.0


async def test_user_stats_macro_split_values(db):
    """Pure protein: 100% P, 0% C, 0% F."""
    await _add_user(db)
    await _add_meal(db, protein=100, carbs=0, fat=0)
    u = await get_user_stats(db, 1)
    macro = u["macro_split"]
    assert macro["pct_protein"] == pytest.approx(100.0)
    assert macro["pct_carbs"] == pytest.approx(0.0)
    assert macro["pct_fat"] == pytest.approx(0.0)


async def test_user_stats_streak(db):
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today()
    for i in range(3):
        d = (today - timedelta(days=i)).isoformat()
        await _add_meal(db, logged_at=f"{d} 12:00")
    u = await get_user_stats(db, 1)
    assert u["streak_days"] == 3


async def test_user_stats_only_own_meals(db):
    """User stats must not include another user's meals."""
    await _add_user(db, user_id=1)
    await _add_user(db, user_id=2, username="dave", first_name="Dave")
    await _add_meal(db, user_id=1, calories=500)
    await _add_meal(db, user_id=2, calories=900)
    u = await get_user_stats(db, 1)
    assert u["total_meals"] == 1


# ---------------------------------------------------------------------------
# set_user_target / get_user_target
# ---------------------------------------------------------------------------

async def test_set_and_get_user_target(db):
    await _add_user(db)
    await set_user_target(db, 1, calories=2200.0, protein=160.0, carbs=220.0, fat=75.0, set_by="manual")
    t = await get_user_target(db, 1)
    assert t is not None
    assert t["calories"] == pytest.approx(2200.0)
    assert t["protein"] == pytest.approx(160.0)
    assert t["carbs"] == pytest.approx(220.0)
    assert t["fat"] == pytest.approx(75.0)
    assert t["set_by"] == "manual"


async def test_set_user_target_upsert(db):
    """Second call should replace the first."""
    await _add_user(db)
    await set_user_target(db, 1, calories=2000.0, protein=150.0, carbs=200.0, fat=70.0)
    await set_user_target(db, 1, calories=1800.0, protein=130.0, carbs=180.0, fat=60.0, set_by="gemini")
    t = await get_user_target(db, 1)
    assert t["calories"] == pytest.approx(1800.0)
    assert t["set_by"] == "gemini"


async def test_get_user_target_returns_none_when_unset(db):
    await _add_user(db)
    t = await get_user_target(db, 1)
    assert t is None


# ---------------------------------------------------------------------------
# get_today_totals
# ---------------------------------------------------------------------------

async def test_get_today_totals_empty(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    totals = await get_today_totals(db, 1, today)
    assert totals["calories"] == 0.0
    assert totals["protein"] == 0.0
    assert totals["carbs"] == 0.0
    assert totals["fat"] == 0.0
    assert totals["meal_count"] == 0


async def test_get_today_totals_sums_only_today(db):
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    # Two meals today, one yesterday
    await _add_meal(db, logged_at=f"{today} 08:00", calories=300, protein=20, carbs=40, fat=10)
    await _add_meal(db, logged_at=f"{today} 13:00", calories=500, protein=35, carbs=55, fat=15)
    await _add_meal(db, logged_at=f"{yesterday} 12:00", calories=700, protein=50, carbs=80, fat=20)
    totals = await get_today_totals(db, 1, today)
    assert totals["calories"] == pytest.approx(800.0)
    assert totals["protein"] == pytest.approx(55.0)
    assert totals["carbs"] == pytest.approx(95.0)
    assert totals["fat"] == pytest.approx(25.0)
    assert totals["meal_count"] == 2


# ---------------------------------------------------------------------------
# get_weekly_avg
# ---------------------------------------------------------------------------

async def test_get_weekly_avg_returns_none_when_no_meals(db):
    await _add_user(db)
    avg = await get_weekly_avg(db, 1)
    assert avg is None


async def test_get_weekly_avg_divides_by_active_days(db):
    """Weekly avg divides total by number of days that have meals."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    # One meal with known values - avg should be full values (1 active day)
    await _add_meal(db, logged_at=f"{today} 12:00",
                    calories=700, protein=70, carbs=140, fat=35)
    avg = await get_weekly_avg(db, 1)
    assert avg is not None
    assert avg["calories"] == pytest.approx(700.0)
    assert avg["protein"] == pytest.approx(70.0)
    assert avg["carbs"] == pytest.approx(140.0)
    assert avg["fat"] == pytest.approx(35.0)


# ---------------------------------------------------------------------------
# get_last_meal / delete_meal
# ---------------------------------------------------------------------------

async def test_get_last_meal_returns_none_when_empty(db):
    await _add_user(db)
    assert await get_last_meal(db, 1) is None


async def test_get_last_meal_returns_most_recent(db):
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await _add_meal(db, logged_at=f"{yesterday} 12:00", item_name="Old Meal", calories=400)
    await _add_meal(db, logged_at=f"{today} 08:00", item_name="New Meal", calories=300)
    meal = await get_last_meal(db, 1)
    assert meal is not None
    assert meal["item_name"] == "New Meal"
    assert meal["calories"] == pytest.approx(300.0)


async def test_get_last_meal_isolated_by_user(db):
    await _add_user(db, user_id=1)
    await _add_user(db, user_id=2, username="dave", first_name="Dave")
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, user_id=2, logged_at=f"{today} 12:00", item_name="Dave's Meal")
    # User 1 has no meals
    assert await get_last_meal(db, 1) is None


async def test_delete_meal_removes_row(db):
    await _add_user(db)
    await _add_meal(db)
    meal = await get_last_meal(db, 1)
    assert meal is not None
    await delete_meal(db, meal["id"])
    assert await get_last_meal(db, 1) is None


async def test_delete_meal_only_deletes_target(db):
    """Deleting one meal should leave the other intact."""
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await _add_meal(db, logged_at=f"{yesterday} 12:00", item_name="Older")
    await _add_meal(db, logged_at=f"{today} 12:00", item_name="Newer")
    newer = await get_last_meal(db, 1)
    assert newer["item_name"] == "Newer"
    await delete_meal(db, newer["id"])
    remaining = await get_last_meal(db, 1)
    assert remaining is not None
    assert remaining["item_name"] == "Older"


# ---------------------------------------------------------------------------
# get_daily_totals_7days
# ---------------------------------------------------------------------------

async def test_get_daily_totals_7days_returns_7_entries(db):
    await _add_user(db)
    rows = await get_daily_totals_7days(db, 1)
    assert len(rows) == 7


async def test_get_daily_totals_7days_fills_zeros_for_empty_days(db):
    await _add_user(db)
    rows = await get_daily_totals_7days(db, 1)
    for r in rows:
        assert r["calories"] == 0.0
        assert r["meal_count"] == 0


async def test_get_daily_totals_7days_today_has_values(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00", calories=500, protein=30, carbs=50, fat=20)
    rows = await get_daily_totals_7days(db, 1)
    today_row = rows[-1]  # ascending order, today is last
    assert today_row["date"] == today
    assert today_row["calories"] == pytest.approx(500.0)
    assert today_row["meal_count"] == 1


async def test_get_daily_totals_7days_aggregates_multiple_meals(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 08:00", calories=300)
    await _add_meal(db, logged_at=f"{today} 13:00", calories=400)
    rows = await get_daily_totals_7days(db, 1)
    today_row = rows[-1]
    assert today_row["calories"] == pytest.approx(700.0)
    assert today_row["meal_count"] == 2


# ---------------------------------------------------------------------------
# get_user_prefs / set_user_prefs
# ---------------------------------------------------------------------------

async def test_get_user_prefs_defaults_when_unset(db):
    await _add_user(db)
    prefs = await get_user_prefs(db, 1)
    assert prefs["timezone"] == "America/Los_Angeles"
    assert prefs["reminders_on"] == 1
    assert prefs["breakfast_hour"] == 8
    assert prefs["dinner_hour"] == 19


async def test_set_user_prefs_updates_fields(db):
    await _add_user(db)
    await set_user_prefs(db, 1, timezone="America/New_York", reminders_on=0)
    prefs = await get_user_prefs(db, 1)
    assert prefs["timezone"] == "America/New_York"
    assert prefs["reminders_on"] == 0
    # Untouched fields stay at defaults
    assert prefs["breakfast_hour"] == 8


async def test_set_user_prefs_idempotent(db):
    """Calling set_user_prefs twice should not fail and should preserve last value."""
    await _add_user(db)
    await set_user_prefs(db, 1, breakfast_hour=7)
    await set_user_prefs(db, 1, breakfast_hour=9)
    prefs = await get_user_prefs(db, 1)
    assert prefs["breakfast_hour"] == 9


async def test_set_user_prefs_ignores_unknown_keys(db):
    """Unknown kwargs must be silently ignored (not cause a SQL error)."""
    await _add_user(db)
    await set_user_prefs(db, 1, totally_fake_column=99)
    prefs = await get_user_prefs(db, 1)
    assert prefs["breakfast_hour"] == 8  # unchanged


# ---------------------------------------------------------------------------
# log_gemini_call / get_gemini_stats
# ---------------------------------------------------------------------------

async def test_log_gemini_call_and_stats(db):
    from datetime import date
    today = date.today().isoformat()
    await log_gemini_call(db, call_type="analyze_meal", user_id=1,
                          input_tokens=100, output_tokens=50,
                          has_image=True, web_searches=1)
    stats = await get_gemini_stats(db)
    assert stats["today"]["calls"] == 1
    assert stats["today"]["input_tokens"] == 100
    assert stats["today"]["output_tokens"] == 50
    assert stats["today"]["image_calls"] == 1
    assert stats["today"]["web_searches"] == 1
    assert stats["all_time"]["calls"] == 1


async def test_gemini_stats_empty(db):
    stats = await get_gemini_stats(db)
    assert stats["today"]["calls"] == 0
    assert stats["all_time"]["calls"] == 0


async def test_gemini_stats_multiple_calls_accumulate(db):
    for _ in range(3):
        await log_gemini_call(db, call_type="chat", user_id=1,
                              input_tokens=50, output_tokens=20)
    stats = await get_gemini_stats(db)
    assert stats["today"]["calls"] == 3
    assert stats["today"]["input_tokens"] == 150
    assert stats["today"]["output_tokens"] == 60
    assert stats["today"]["image_calls"] == 0


# ---------------------------------------------------------------------------
# get_meals_for_day
# ---------------------------------------------------------------------------

async def test_get_meals_for_day_empty(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    meals = await get_meals_for_day(db, 1, today)
    assert meals == []


async def test_get_meals_for_day_returns_todays_meals(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 08:00", item_name="Eggs", calories=200)
    await _add_meal(db, logged_at=f"{today} 13:00", item_name="Salad", calories=300)
    meals = await get_meals_for_day(db, 1, today)
    assert len(meals) == 2
    # DESC order: newest first
    assert meals[0]["item_name"] == "Salad"
    assert meals[1]["item_name"] == "Eggs"


async def test_get_meals_for_day_excludes_other_days(db):
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00", item_name="Today Meal")
    await _add_meal(db, logged_at=f"{yesterday} 12:00", item_name="Yesterday Meal")
    meals = await get_meals_for_day(db, 1, today)
    assert len(meals) == 1
    assert meals[0]["item_name"] == "Today Meal"


async def test_get_meals_for_day_ordered_by_time(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    # Insert in order: Dinner (id=N), Breakfast (id=N+1), Lunch (id=N+2)
    await _add_meal(db, logged_at=f"{today} 21:00", item_name="Dinner")
    await _add_meal(db, logged_at=f"{today} 08:00", item_name="Breakfast")
    await _add_meal(db, logged_at=f"{today} 13:00", item_name="Lunch")
    meals = await get_meals_for_day(db, 1, today)
    # id DESC order: last inserted first
    assert [m["item_name"] for m in meals] == ["Lunch", "Breakfast", "Dinner"]


async def test_get_meals_for_day_includes_logged_at(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 08:30", item_name="Oatmeal")
    meals = await get_meals_for_day(db, 1, today)
    assert "logged_at" in meals[0]
    assert meals[0]["logged_at"] == f"{today} 08:30"


async def test_get_meals_for_day_isolated_by_user(db):
    await _add_user(db, user_id=1)
    await _add_user(db, user_id=2, username="bob", first_name="Bob")
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, user_id=1, logged_at=f"{today} 08:00", item_name="Alice Breakfast")
    await _add_meal(db, user_id=2, logged_at=f"{today} 08:00", item_name="Bob Breakfast")
    meals = await get_meals_for_day(db, 1, today)
    assert len(meals) == 1
    assert meals[0]["item_name"] == "Alice Breakfast"


async def test_get_meals_for_day_macros_correct(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00",
                    calories=500, protein=40, carbs=50, fat=15)
    meals = await get_meals_for_day(db, 1, today)
    m = meals[0]
    assert m["calories"] == pytest.approx(500.0)
    assert m["protein"] == pytest.approx(40.0)
    assert m["carbs"] == pytest.approx(50.0)
    assert m["fat"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# get_recent_meals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_recent_meals_empty(db):
    await _add_user(db)
    result = await get_recent_meals(db, 1)
    assert result == []


@pytest.mark.asyncio
async def test_get_recent_meals_returns_expected_fields(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00",
                    item_name="Pasta", meal_description="Marinara",
                    calories=600, protein=20, carbs=90, fat=12)
    meals = await get_recent_meals(db, 1)
    assert len(meals) == 1
    m = meals[0]
    assert m["item_name"] == "Pasta"
    assert m["meal_description"] == "Marinara"
    assert m["calories"] == pytest.approx(600.0)
    assert m["protein"] == pytest.approx(20.0)
    assert m["carbs"] == pytest.approx(90.0)
    assert m["fat"] == pytest.approx(12.0)
    assert "logged_at" in m


@pytest.mark.asyncio
async def test_get_recent_meals_ordered_newest_first(db):
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await _add_meal(db, logged_at=f"{yesterday} 12:00", item_name="Old Meal")
    await _add_meal(db, logged_at=f"{today} 08:00", item_name="New Meal")
    meals = await get_recent_meals(db, 1)
    assert meals[0]["item_name"] == "New Meal"
    assert meals[1]["item_name"] == "Old Meal"


@pytest.mark.asyncio
async def test_get_recent_meals_respects_limit(db):
    await _add_user(db)
    from datetime import date, timedelta
    for i in range(5):
        day = (date.today() - timedelta(days=i)).isoformat()
        await _add_meal(db, logged_at=f"{day} 12:00", item_name=f"Meal {i}")
    meals = await get_recent_meals(db, 1, limit=3)
    assert len(meals) == 3


@pytest.mark.asyncio
async def test_get_recent_meals_isolated_by_user(db):
    """Each user only sees their own meals."""
    await _add_user(db, user_id=1, username="alice", first_name="Alice")
    await _add_user(db, user_id=2, username="bob", first_name="Bob")
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, user_id=1, logged_at=f"{today} 08:00", item_name="Alice Oats")
    await _add_meal(db, user_id=2, logged_at=f"{today} 08:00", item_name="Bob Eggs")
    alice_meals = await get_recent_meals(db, 1)
    bob_meals = await get_recent_meals(db, 2)
    assert len(alice_meals) == 1
    assert alice_meals[0]["item_name"] == "Alice Oats"
    assert len(bob_meals) == 1
    assert bob_meals[0]["item_name"] == "Bob Eggs"


@pytest.mark.asyncio
async def test_get_recent_meals_default_limit_is_20(db):
    await _add_user(db)
    from datetime import date, timedelta
    for i in range(25):
        day = (date.today() - timedelta(days=i)).isoformat()
        await _add_meal(db, logged_at=f"{day} 12:00", item_name=f"Meal {i}")
    meals = await get_recent_meals(db, 1)
    assert len(meals) == 20


# ---------------------------------------------------------------------------
# meals_public user pref
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meals_public_defaults_to_0(db):
    await _add_user(db)
    prefs = await get_user_prefs(db, 1)
    assert prefs["meals_public"] == 0


@pytest.mark.asyncio
async def test_set_meals_public_to_1(db):
    await _add_user(db)
    await set_user_prefs(db, 1, meals_public=1)
    prefs = await get_user_prefs(db, 1)
    assert prefs["meals_public"] == 1


@pytest.mark.asyncio
async def test_toggle_meals_public_off(db):
    await _add_user(db)
    await set_user_prefs(db, 1, meals_public=1)
    await set_user_prefs(db, 1, meals_public=0)
    prefs = await get_user_prefs(db, 1)
    assert prefs["meals_public"] == 0


@pytest.mark.asyncio
async def test_meals_public_does_not_affect_other_prefs(db):
    """Toggling meals_public should not reset unrelated prefs."""
    await _add_user(db)
    await set_user_prefs(db, 1, timezone="America/New_York", meals_public=1)
    prefs = await get_user_prefs(db, 1)
    assert prefs["timezone"] == "America/New_York"
    assert prefs["meals_public"] == 1


@pytest.mark.asyncio
async def test_meals_public_default_row_created_by_upsert(db):
    """set_user_prefs on a new user creates a row with meals_public=0, then updates it."""
    await _add_user(db)
    # First call creates the row with defaults
    await set_user_prefs(db, 1, meals_public=1)
    prefs = await get_user_prefs(db, 1)
    assert prefs["meals_public"] == 1
    # Verify other defaults were set by the INSERT
    assert prefs["timezone"] == "America/Los_Angeles"


# ---------------------------------------------------------------------------
# meal_type stored and returned
# ---------------------------------------------------------------------------

async def test_log_meal_stores_meal_type(db):
    """meal_type passed to log_meal is returned by get_meals_for_day."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await log_meal(db, 1, f"{today} 12:00", "Salad", "", 300, 20, 40, 10, "Gemini", meal_type="lunch")
    meals = await get_meals_for_day(db, 1, today)
    assert meals[0]["meal_type"] == "lunch"


async def test_log_meal_meal_type_defaults_empty(db):
    """Omitting meal_type stores an empty string (backward-compatible)."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 08:00")
    meals = await get_meals_for_day(db, 1, today)
    assert meals[0]["meal_type"] == ""


async def test_log_meal_meal_type_all_categories(db):
    """Each meal type value round-trips correctly."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    for i, mt in enumerate(["breakfast", "lunch", "snack", "dinner"]):
        await log_meal(db, 1, f"{today} 0{i}:00", f"Meal {i}", "", 300, 20, 40, 10, "Gemini", meal_type=mt)
    meals = await get_meals_for_day(db, 1, today)
    stored = [m["meal_type"] for m in meals]
    # DESC order: newest first (dinner at 03:00, snack at 02:00, lunch at 01:00, breakfast at 00:00)
    assert stored == ["dinner", "snack", "lunch", "breakfast"]


# ---------------------------------------------------------------------------
# has_meal_in_window
# ---------------------------------------------------------------------------

async def test_has_meal_in_window_returns_true(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 13:00")  # hour=13, window 11–16
    assert await has_meal_in_window(db, 1, today, 11, 16) is True


async def test_has_meal_in_window_returns_false_no_meals(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    assert await has_meal_in_window(db, 1, today, 11, 16) is False


async def test_has_meal_in_window_returns_false_outside_window(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 08:00")  # hour=8, outside 11–16
    assert await has_meal_in_window(db, 1, today, 11, 16) is False


async def test_has_meal_in_window_boundary_start_inclusive(db):
    """Meal logged exactly at start_hour is inside the window."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 11:00")  # hour=11 == start
    assert await has_meal_in_window(db, 1, today, 11, 16) is True


async def test_has_meal_in_window_boundary_end_exclusive(db):
    """Meal logged at end_hour is outside the window (exclusive upper bound)."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 16:00")  # hour=16 == end (exclusive)
    assert await has_meal_in_window(db, 1, today, 11, 16) is False


async def test_has_meal_in_window_isolated_by_user(db):
    """A meal logged by another user must not affect the check."""
    await _add_user(db, user_id=1)
    await _add_user(db, user_id=2, username="bob", first_name="Bob")
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, user_id=2, logged_at=f"{today} 13:00")  # only user 2
    assert await has_meal_in_window(db, 1, today, 11, 16) is False


async def test_has_meal_in_window_wrong_date(db):
    """Meal from yesterday is not counted for today."""
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await _add_meal(db, logged_at=f"{yesterday} 13:00")
    assert await has_meal_in_window(db, 1, today, 11, 16) is False


# ---------------------------------------------------------------------------
# get_weekly_avg - two-day scenario
# ---------------------------------------------------------------------------

async def test_get_weekly_avg_two_active_days(db):
    """Two meals on two different days → avg divides by 2, not 7."""
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00", calories=600, protein=60, carbs=60, fat=20)
    await _add_meal(db, logged_at=f"{yesterday} 12:00", calories=400, protein=40, carbs=40, fat=10)
    avg = await get_weekly_avg(db, 1)
    assert avg is not None
    assert avg["calories"] == pytest.approx(500.0)   # (600+400)/2
    assert avg["protein"] == pytest.approx(50.0)
    assert avg["carbs"] == pytest.approx(50.0)
    assert avg["fat"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# items_json column (new edit flow)
# ---------------------------------------------------------------------------

import json as _json  # avoid shadowing built-in in test scope


async def test_log_meal_stores_items_json(db):
    """items_json passed to log_meal is returned by get_meals_for_day."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    items = [{"name": "Rice", "calories": 200, "protein": 4, "carbs": 44, "fat": 1}]
    await log_meal(db, 1, f"{today} 12:00", "Rice Bowl", "", 200, 4, 44, 1, "Gemini",
                   meal_type="lunch", items_json=_json.dumps(items))
    meals = await get_meals_for_day(db, 1, today)
    assert len(meals) == 1
    stored = _json.loads(meals[0]["items_json"])
    assert stored[0]["name"] == "Rice"
    assert stored[0]["calories"] == 200


async def test_log_meal_items_json_defaults_empty(db):
    """Omitting items_json stores an empty string."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 08:00")
    meals = await get_meals_for_day(db, 1, today)
    assert meals[0].get("items_json", "") == ""


async def test_get_meals_for_day_returns_id(db):
    """get_meals_for_day now returns the 'id' field."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00")
    meals = await get_meals_for_day(db, 1, today)
    assert "id" in meals[0]
    assert isinstance(meals[0]["id"], int)


async def test_get_last_meal_returns_full_row(db):
    """get_last_meal now includes meal_description, meal_type, items_json."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    items = [{"name": "Egg", "calories": 80, "protein": 6, "carbs": 1, "fat": 5}]
    await log_meal(db, 1, f"{today} 08:00", "Eggs", "scrambled",
                   80, 6, 1, 5, "Gemini", meal_type="breakfast",
                   items_json=_json.dumps(items))
    meal = await get_last_meal(db, 1)
    assert meal is not None
    assert meal["meal_description"] == "scrambled"
    assert meal["meal_type"] == "breakfast"
    stored = _json.loads(meal["items_json"])
    assert stored[0]["name"] == "Egg"


# ---------------------------------------------------------------------------
# update_meal
# ---------------------------------------------------------------------------

async def test_update_meal_changes_macros(db):
    """update_meal should change calories/protein/carbs/fat in the DB."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00", item_name="Oatmeal",
                    calories=400, protein=15, carbs=60, fat=10)
    meal = await get_last_meal(db, 1)
    assert meal is not None

    await update_meal(db, meal["id"], 1, "Oatmeal", "", 350, 12, 55, 8)

    updated = await get_last_meal(db, 1)
    assert updated["calories"] == pytest.approx(350.0)
    assert updated["protein"] == pytest.approx(12.0)
    assert updated["carbs"] == pytest.approx(55.0)
    assert updated["fat"] == pytest.approx(8.0)


async def test_update_meal_stores_items_json(db):
    """update_meal should persist new items_json."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00", item_name="Rice")
    meal = await get_last_meal(db, 1)
    items = [{"name": "Rice", "calories": 180, "protein": 4, "carbs": 40, "fat": 1}]
    await update_meal(db, meal["id"], 1, "Rice", "", 180, 4, 40, 1,
                      items_json=_json.dumps(items))
    updated = await get_last_meal(db, 1)
    stored = _json.loads(updated["items_json"])
    assert stored[0]["calories"] == 180


async def test_update_meal_changes_item_name(db):
    """update_meal can rename the meal."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00", item_name="Salad")
    meal = await get_last_meal(db, 1)
    await update_meal(db, meal["id"], 1, "Caesar Salad", "dressing added", 300, 10, 20, 15)
    updated = await get_last_meal(db, 1)
    assert updated["item_name"] == "Caesar Salad"
    assert updated["meal_description"] == "dressing added"


# ---------------------------------------------------------------------------
# get_meal_by_id
# ---------------------------------------------------------------------------

async def test_get_meal_by_id_returns_full_row(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, logged_at=f"{today} 12:00", item_name="Pasta",
                    calories=500, protein=20, carbs=75, fat=12)
    meal = await get_last_meal(db, 1)
    assert meal is not None

    full = await get_meal_by_id(db, 1, meal["id"])
    assert full is not None
    assert full["item_name"] == "Pasta"
    assert full["calories"] == pytest.approx(500.0)
    assert "items_json" in full


async def test_get_meal_by_id_returns_none_for_wrong_user(db):
    """A user cannot fetch another user's meal by ID."""
    await _add_user(db, user_id=1)
    await _add_user(db, user_id=2, username="bob", first_name="Bob")
    from datetime import date
    today = date.today().isoformat()
    await _add_meal(db, user_id=1, logged_at=f"{today} 12:00")
    meal = await get_last_meal(db, 1)
    assert meal is not None

    result = await get_meal_by_id(db, 2, meal["id"])  # user 2 queries user 1's meal
    assert result is None


async def test_get_meal_by_id_returns_none_when_not_found(db):
    await _add_user(db)
    result = await get_meal_by_id(db, 1, 9999)
    assert result is None


# ---------------------------------------------------------------------------
# get_last_meal_by_type
# ---------------------------------------------------------------------------

async def test_get_last_meal_by_type_returns_matching_meal(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await log_meal(db, 1, f"{today} 08:00", "Eggs", "", 300, 20, 5, 15,
                   "Gemini", meal_type="breakfast")
    await log_meal(db, 1, f"{today} 13:00", "Salad", "", 350, 25, 30, 10,
                   "Gemini", meal_type="lunch")

    result = await get_last_meal_by_type(db, 1, today, "lunch")
    assert result is not None
    assert result["item_name"] == "Salad"
    assert result["meal_type"] == "lunch"


async def test_get_last_meal_by_type_returns_none_when_no_match(db):
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await log_meal(db, 1, f"{today} 08:00", "Eggs", "", 300, 20, 5, 15,
                   "Gemini", meal_type="breakfast")
    result = await get_last_meal_by_type(db, 1, today, "dinner")
    assert result is None


async def test_get_last_meal_by_type_returns_most_recent(db):
    """When two meals of the same type exist, the most recent is returned."""
    await _add_user(db)
    from datetime import date
    today = date.today().isoformat()
    await log_meal(db, 1, f"{today} 07:00", "Early Breakfast", "", 200, 10, 30, 5,
                   "Gemini", meal_type="breakfast")
    await log_meal(db, 1, f"{today} 09:00", "Late Breakfast", "", 350, 15, 50, 8,
                   "Gemini", meal_type="breakfast")
    result = await get_last_meal_by_type(db, 1, today, "breakfast")
    assert result is not None
    assert result["item_name"] == "Late Breakfast"


async def test_get_last_meal_by_type_isolated_by_user(db):
    """Another user's meal of the same type is not returned."""
    await _add_user(db, user_id=1)
    await _add_user(db, user_id=2, username="bob", first_name="Bob")
    from datetime import date
    today = date.today().isoformat()
    await log_meal(db, 2, f"{today} 12:00", "Bob's Lunch", "", 400, 25, 50, 12,
                   "Gemini", meal_type="lunch")
    result = await get_last_meal_by_type(db, 1, today, "lunch")
    assert result is None


async def test_get_last_meal_by_type_only_today(db):
    """Meals from other days are excluded."""
    await _add_user(db)
    from datetime import date, timedelta
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await log_meal(db, 1, f"{yesterday} 12:00", "Yesterday Lunch", "", 400, 25, 50, 12,
                   "Gemini", meal_type="lunch")
    result = await get_last_meal_by_type(db, 1, today, "lunch")
    assert result is None


# ---------------------------------------------------------------------------
# log_meal - returns integer row id
# ---------------------------------------------------------------------------

async def test_log_meal_returns_int(db):
    await _add_user(db)
    row_id = await log_meal(db, 1, "2026-03-01 12:00", "Oatmeal", "",
                             400, 15, 60, 10, "Gemini")
    assert isinstance(row_id, int)
    assert row_id >= 1


async def test_log_meal_row_ids_increment(db):
    await _add_user(db)
    id1 = await log_meal(db, 1, "2026-03-01 08:00", "Eggs", "", 300, 20, 5, 15, "Gemini")
    id2 = await log_meal(db, 1, "2026-03-01 12:00", "Salad", "", 200, 5, 20, 8, "Gemini")
    assert id2 > id1


# ---------------------------------------------------------------------------
# log_fatsecret_comparison
# ---------------------------------------------------------------------------

def _make_food_item(name, calories=200, protein=20, carbs=10, fat=5,
                    weight_g=None, gemini_per_100g=None, fatsecret_per_100g=None):
    """Build a simple namespace that mimics FoodItem for log_fatsecret_comparison."""
    from types import SimpleNamespace
    return SimpleNamespace(
        name=name,
        calories=calories,
        protein=protein,
        carbs=carbs,
        fat=fat,
        weight_g=weight_g,
        gemini_per_100g=gemini_per_100g,
        fatsecret_per_100g=fatsecret_per_100g,
    )


async def test_log_fatsecret_comparison_inserts_rows(db):
    await _add_user(db)
    items = [
        _make_food_item(
            "Chicken", weight_g=150,
            gemini_per_100g={"calories": 166.0, "protein": 23.3, "carbs": 0.0, "fat": 5.3},
            fatsecret_per_100g={"calories": 165.0, "protein": 31.0, "carbs": 0.0, "fat": 3.6},
        ),
        _make_food_item(
            "Rice", weight_g=200,
            gemini_per_100g={"calories": 130.0, "protein": 2.7, "carbs": 28.2, "fat": 0.3},
        ),
    ]
    await log_fatsecret_comparison(db, user_id=1, meal_log_id=42, items=items, logged_at="2026-03-01 12:00")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("SELECT * FROM fatsecret_comparison WHERE user_id=1")
        rows = await cursor.fetchall()
    assert len(rows) == 2


async def test_log_fatsecret_comparison_skips_items_with_no_per_100g(db):
    await _add_user(db)
    items = [
        _make_food_item("NoData"),  # neither gemini_per_100g nor fatsecret_per_100g
        _make_food_item(
            "Chicken",
            gemini_per_100g={"calories": 166.0, "protein": 23.3, "carbs": 0.0, "fat": 5.3},
        ),
    ]
    await log_fatsecret_comparison(db, user_id=1, meal_log_id=None, items=items, logged_at="2026-03-01 12:00")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("SELECT item_name FROM fatsecret_comparison WHERE user_id=1")
        rows = await cursor.fetchall()
    item_names = [r[0] for r in rows]
    assert "Chicken" in item_names
    assert "NoData" not in item_names


async def test_log_fatsecret_comparison_stores_correct_values(db):
    await _add_user(db)
    g = {"calories": 166.0, "protein": 23.3, "carbs": 0.0, "fat": 5.3}
    f = {"calories": 165.0, "protein": 31.0, "carbs": 0.0, "fat": 3.6}
    items = [_make_food_item("Chicken", weight_g=150, gemini_per_100g=g, fatsecret_per_100g=f)]
    await log_fatsecret_comparison(db, user_id=1, meal_log_id=7, items=items, logged_at="2026-03-01 12:00")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute(
            "SELECT item_name, weight_g, meal_log_id, gemini_cal_100g, fatsecret_cal_100g "
            "FROM fatsecret_comparison WHERE user_id=1"
        )
        row = await cursor.fetchone()
    assert row[0] == "Chicken"
    assert row[1] == pytest.approx(150.0)
    assert row[2] == 7
    assert row[3] == pytest.approx(166.0)
    assert row[4] == pytest.approx(165.0)


async def test_log_fatsecret_comparison_no_rows_when_all_items_empty(db):
    await _add_user(db)
    items = [_make_food_item("A"), _make_food_item("B")]
    await log_fatsecret_comparison(db, user_id=1, meal_log_id=None, items=items, logged_at="2026-03-01 12:00")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("SELECT COUNT(*) FROM fatsecret_comparison")
        count = (await cursor.fetchone())[0]
    assert count == 0


async def test_log_fatsecret_comparison_null_meal_log_id_allowed(db):
    """meal_log_id can be None (nullable FK)."""
    await _add_user(db)
    g = {"calories": 200.0, "protein": 10.0, "carbs": 30.0, "fat": 5.0}
    items = [_make_food_item("Rice", gemini_per_100g=g)]
    await log_fatsecret_comparison(db, user_id=1, meal_log_id=None, items=items, logged_at="2026-03-01 12:00")
    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("SELECT meal_log_id FROM fatsecret_comparison")
        row = await cursor.fetchone()
    assert row[0] is None


# ---------------------------------------------------------------------------
# get_period_totals
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestGetPeriodTotals:
    """Tests for the SQLite-backed period totals query."""

    async def test_day_zero_meals(self, db):
        await _add_user(db)
        result = await get_period_totals(db, user_id=1, period="day", today_str="2026-03-01")
        assert result.meal_count == 0
        assert result.total_calories == pytest.approx(0.0)
        assert result.total_protein == pytest.approx(0.0)
        assert result.avg_calories_per_day is None

    async def test_day_sums_todays_meals(self, db):
        await _add_user(db)
        await log_meal(db, 1, "2026-03-01 08:00", "Oatmeal", "", 400, 15, 60, 10, "G")
        await log_meal(db, 1, "2026-03-01 13:00", "Chicken", "", 550, 45, 30, 22, "G")
        result = await get_period_totals(db, user_id=1, period="day", today_str="2026-03-01")
        assert result.meal_count == 2
        assert result.total_calories == pytest.approx(950.0)
        assert result.total_protein == pytest.approx(60.0)
        assert result.total_carbs == pytest.approx(90.0)
        assert result.total_fat == pytest.approx(32.0)
        assert result.period == "day"
        assert result.avg_calories_per_day is None  # day period has no avg

    async def test_day_excludes_other_days(self, db):
        await _add_user(db)
        await log_meal(db, 1, "2026-02-28 20:00", "Yesterday dinner", "", 700, 40, 50, 30, "G")
        await log_meal(db, 1, "2026-03-01 12:00", "Today lunch", "", 500, 30, 40, 20, "G")
        result = await get_period_totals(db, user_id=1, period="day", today_str="2026-03-01")
        assert result.meal_count == 1
        assert result.total_calories == pytest.approx(500.0)

    async def test_day_isolated_by_user(self, db):
        await _add_user(db, user_id=1)
        await ensure_user(db, 2, "bob", "Bob")
        await log_meal(db, 1, "2026-03-01 12:00", "User 1 meal", "", 600, 40, 50, 25, "G")
        await log_meal(db, 2, "2026-03-01 12:00", "User 2 meal", "", 800, 55, 70, 35, "G")
        r1 = await get_period_totals(db, user_id=1, period="day", today_str="2026-03-01")
        r2 = await get_period_totals(db, user_id=2, period="day", today_str="2026-03-01")
        assert r1.total_calories == pytest.approx(600.0)
        assert r2.total_calories == pytest.approx(800.0)

    async def test_week_sums_last_7_days(self, db):
        await _add_user(db)
        # 6 days ago, 3 days ago, and today
        await log_meal(db, 1, "2026-02-23 12:00", "Meal A", "", 500, 30, 50, 20, "G")
        await log_meal(db, 1, "2026-02-26 12:00", "Meal B", "", 600, 35, 55, 25, "G")
        await log_meal(db, 1, "2026-03-01 12:00", "Meal C", "", 700, 40, 60, 30, "G")
        result = await get_period_totals(db, user_id=1, period="week", today_str="2026-03-01")
        assert result.meal_count == 3
        assert result.total_calories == pytest.approx(1800.0)
        assert result.period == "week"

    async def test_week_excludes_8_days_ago(self, db):
        await _add_user(db)
        await log_meal(db, 1, "2026-02-21 12:00", "Old meal", "", 900, 50, 80, 40, "G")
        await log_meal(db, 1, "2026-03-01 12:00", "Today meal", "", 500, 30, 50, 20, "G")
        result = await get_period_totals(db, user_id=1, period="week", today_str="2026-03-01")
        assert result.meal_count == 1
        assert result.total_calories == pytest.approx(500.0)

    async def test_week_avg_calories_per_day(self, db):
        """avg_calories_per_day divides by number of distinct days with meals, not 7."""
        await _add_user(db)
        # Only 2 distinct days with meals this week
        await log_meal(db, 1, "2026-02-28 08:00", "Breakfast", "", 400, 20, 50, 15, "G")
        await log_meal(db, 1, "2026-02-28 13:00", "Lunch", "", 600, 35, 60, 25, "G")
        await log_meal(db, 1, "2026-03-01 19:00", "Dinner", "", 800, 45, 70, 35, "G")
        result = await get_period_totals(db, user_id=1, period="week", today_str="2026-03-01")
        # 2 distinct days → avg = (400+600+800) / 2 = 900
        assert result.avg_calories_per_day == pytest.approx(900.0)

    async def test_week_no_meals_avg_is_none(self, db):
        await _add_user(db)
        result = await get_period_totals(db, user_id=1, period="week", today_str="2026-03-01")
        assert result.meal_count == 0
        assert result.avg_calories_per_day is None

    async def test_month_spans_30_days(self, db):
        """Month cutoff is today - 29 days (inclusive), giving a 30-day window."""
        await _add_user(db)
        # Jan 31 = 29 days before March 1 → included (earliest in window)
        await log_meal(db, 1, "2026-01-31 12:00", "Edge meal", "", 500, 25, 50, 20, "G")
        await log_meal(db, 1, "2026-03-01 12:00", "Today", "", 600, 30, 55, 25, "G")
        result = await get_period_totals(db, user_id=1, period="month", today_str="2026-03-01")
        assert result.meal_count == 2
        assert result.total_calories == pytest.approx(1100.0)

    async def test_month_excludes_30_days_ago(self, db):
        """Jan 30 = 30 days before March 1 → falls outside the 30-day window."""
        await _add_user(db)
        await log_meal(db, 1, "2026-01-30 12:00", "Too old", "", 900, 50, 80, 40, "G")
        await log_meal(db, 1, "2026-03-01 12:00", "Today", "", 500, 30, 50, 20, "G")
        result = await get_period_totals(db, user_id=1, period="month", today_str="2026-03-01")
        assert result.meal_count == 1
        assert result.total_calories == pytest.approx(500.0)

    async def test_unknown_period_treated_as_day(self, db):
        """Any unrecognised period string falls back to 'day'."""
        await _add_user(db)
        await log_meal(db, 1, "2026-03-01 12:00", "Today", "", 400, 20, 40, 15, "G")
        result = await get_period_totals(db, user_id=1, period="quarterly", today_str="2026-03-01")
        assert result.period == "day"
        assert result.meal_count == 1

    async def test_returns_totalsresult_with_correct_fields(self, db):
        """Verify returned object has all TotalsResult fields."""
        from src.models import TotalsResult
        await _add_user(db)
        await log_meal(db, 1, "2026-03-01 12:00", "Salad", "", 200, 10, 20, 8, "G")
        result = await get_period_totals(db, user_id=1, period="day", today_str="2026-03-01")
        assert isinstance(result, TotalsResult)
        assert hasattr(result, "total_calories")
        assert hasattr(result, "total_protein")
        assert hasattr(result, "total_carbs")
        assert hasattr(result, "total_fat")
        assert hasattr(result, "meal_count")
        assert hasattr(result, "avg_calories_per_day")


# ===========================================================================
# Beta-release DB helpers: waitlist + list_all_users
# ===========================================================================


class TestWaitlistHelpers:
    """add_to_waitlist / list_waitlist / list_all_users for beta release."""

    @pytest.mark.asyncio
    async def test_add_to_waitlist_returns_true_for_new_row(self, db):
        from src.db import add_to_waitlist
        ok = await add_to_waitlist(
            db, "new@example.com", source="google", first_name="New",
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_add_to_waitlist_returns_false_on_duplicate(self, db):
        from src.db import add_to_waitlist
        ok1 = await add_to_waitlist(db, "dup@example.com")
        ok2 = await add_to_waitlist(db, "dup@example.com", source="email_pin")
        assert ok1 is True
        assert ok2 is False

    @pytest.mark.asyncio
    async def test_list_waitlist_orders_by_created_at_asc(self, db):
        """list_waitlist returns rows in creation order (oldest first)."""
        from src.db import add_to_waitlist, list_waitlist

        await add_to_waitlist(db, "first@example.com")
        # Small sleep isn't practical in tests, but SQLite timestamps have
        # second resolution - so ensure the rows are distinguishable by
        # inserting one at a known-later created_at. Easiest: just rely on
        # autoincrement id order as a proxy since the sort key is stable
        # when rows are inserted in the same second.
        await add_to_waitlist(db, "second@example.com")
        await add_to_waitlist(db, "third@example.com")

        rows = await list_waitlist(db)
        emails = [r["email"] for r in rows]
        assert emails == ["first@example.com", "second@example.com", "third@example.com"]

    @pytest.mark.asyncio
    async def test_list_waitlist_returns_all_fields(self, db):
        from src.db import add_to_waitlist, list_waitlist
        await add_to_waitlist(
            db, "full@example.com", source="google",
            first_name="Full", referrer="https://macro.example.com/",
        )
        rows = await list_waitlist(db)
        assert len(rows) == 1
        row = rows[0]
        for key in (
            "id", "email", "source", "first_name", "referrer",
            "created_at", "invited_at", "notes",
        ):
            assert key in row
        assert row["source"] == "google"
        assert row["first_name"] == "Full"
        assert row["referrer"] == "https://macro.example.com/"
        assert row["invited_at"] is None  # not yet invited


class TestListAllUsers:
    """list_all_users LEFT-joins so users without sub rows still appear."""

    @pytest.mark.asyncio
    async def test_user_without_subscription_row_still_appears(self, db):
        from src.db import create_web_user, list_all_users

        await create_web_user(db, "nosub@example.com", first_name="NoSub")
        rows = await list_all_users(db)
        emails = [r["email"] for r in rows]
        assert "nosub@example.com" in emails
        match = [r for r in rows if r["email"] == "nosub@example.com"][0]
        # Subscription columns come back as None (LEFT JOIN)
        assert match["plan"] is None
        assert match["status"] is None
        # newsletter_opt_in COALESCEs to 1 (default)
        assert match["newsletter_opt_in"] == 1

    @pytest.mark.asyncio
    async def test_user_with_subscription_row_populated(self, db):
        from src.db import (
            create_pro_subscription,
            create_web_user,
            list_all_users,
        )
        user_id = await create_web_user(db, "pro@example.com", first_name="Pro")
        await create_pro_subscription(db, user_id, is_og=True)
        rows = await list_all_users(db)
        match = [r for r in rows if r["email"] == "pro@example.com"][0]
        assert match["plan"] == "pro_monthly"
        assert match["status"] == "active"
        assert match["is_og"] == 1

    @pytest.mark.asyncio
    async def test_list_all_users_ordered_by_signup_time(self, db):
        from src.db import create_web_user, list_all_users
        await create_web_user(db, "a@example.com")
        await create_web_user(db, "b@example.com")
        await create_web_user(db, "c@example.com")
        rows = await list_all_users(db)
        emails = [r["email"] for r in rows]
        # ORDER BY wa.created_at ASC
        assert emails[:3] == ["a@example.com", "b@example.com", "c@example.com"]


# ===========================================================================
# Beta-release DB helpers: create_pro_subscription + set_og_status + count
# ===========================================================================


class TestBetaSubscriptionHelpers:

    @pytest.mark.asyncio
    async def test_count_web_users_empty(self, db):
        from src.db import count_web_users
        assert await count_web_users(db) == 0

    @pytest.mark.asyncio
    async def test_count_web_users_after_insert(self, db):
        from src.db import count_web_users, create_web_user
        await create_web_user(db, "a@example.com")
        await create_web_user(db, "b@example.com")
        assert await count_web_users(db) == 2

    @pytest.mark.asyncio
    async def test_set_og_status_creates_row_when_missing(self, db):
        from src.db import (
            create_web_user,
            get_subscription,
            set_og_status,
        )
        uid = await create_web_user(db, "og@example.com")
        ok = await set_og_status(db, uid, True)
        assert ok is True
        sub = await get_subscription(db, uid)
        assert sub is not None
        assert sub["is_og"] == 1
        assert sub["plan"] == "pro_monthly"

    @pytest.mark.asyncio
    async def test_set_og_status_flips_flag_on_existing_row(self, db):
        from src.db import (
            create_pro_subscription,
            create_web_user,
            get_subscription,
            set_og_status,
        )
        uid = await create_web_user(db, "og@example.com")
        await create_pro_subscription(db, uid, is_og=False)
        await set_og_status(db, uid, True)
        sub = await get_subscription(db, uid)
        assert sub["is_og"] == 1
        # Flip back to 0
        await set_og_status(db, uid, False)
        sub = await get_subscription(db, uid)
        assert sub["is_og"] == 0


# ---------------------------------------------------------------------------
# Schema migrations (prevents "forgot to bump schema_version" regressions)
# ---------------------------------------------------------------------------

class TestVersionedMigrations:
    """Guards against the class of bug where a new ALTER is added to code
    but never applied to existing DBs because schema_version gated it out.

    The 2026-04-22 incident: calories_bmr was appended to the frozen
    _MIGRATIONS list (which only runs at schema_version<1). Prod DB was
    already at v1, so the ALTER never ran; every Fitbit sync crashed.
    """

    @pytest.mark.asyncio
    async def test_upgrade_from_v1_applies_all_pending_migrations(self, tmp_path):
        """Simulate a prod DB stuck at schema_version=1 and confirm every
        _VERSIONED_MIGRATIONS entry is applied when init_db runs again."""
        from src.db import _VERSIONED_MIGRATIONS, _TARGET_SCHEMA_VERSION, init_db

        path = str(tmp_path / "legacy.db")
        # First init: fresh DB, ends at target version.
        await init_db(path)

        # Roll the version back to 1 to simulate a pre-existing prod DB
        # that predates any v2+ migration being authored.
        async with aiosqlite.connect(path) as db:
            await db.execute("UPDATE schema_version SET version = 1")
            await db.commit()

        # Also drop every v2+ column so the test proves the migration
        # runs, not that the column happened to already exist.
        # (SQLite can't DROP COLUMN pre-3.35 - use a best-effort recreate
        # for the specific columns we know about; future migrations may
        # need their own recreate logic here.)
        async with aiosqlite.connect(path) as db:
            # Rebuild fitbit_activity without calories_bmr to force the v2
            # ALTER to do real work.
            await db.executescript(
                """
                CREATE TABLE fitbit_activity_old AS SELECT
                    id, user_id, date, calories_out, activity_calories,
                    steps, fairly_active_min, very_active_min,
                    resting_heart_rate, fetched_at
                FROM fitbit_activity;
                DROP TABLE fitbit_activity;
                CREATE TABLE fitbit_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    calories_out REAL DEFAULT 0,
                    activity_calories REAL DEFAULT 0,
                    steps INTEGER DEFAULT 0,
                    fairly_active_min INTEGER DEFAULT 0,
                    very_active_min INTEGER DEFAULT 0,
                    resting_heart_rate INTEGER DEFAULT 0,
                    fetched_at TEXT NOT NULL,
                    UNIQUE(user_id, date)
                );
                INSERT INTO fitbit_activity SELECT * FROM fitbit_activity_old;
                DROP TABLE fitbit_activity_old;
                """
            )
            await db.commit()

            # Confirm setup: calories_bmr really is missing.
            cols = [r[1] for r in await (await db.execute("PRAGMA table_info(fitbit_activity)")).fetchall()]
            assert "calories_bmr" not in cols, "test setup failed to strip column"

        # Second init: should detect current<target and run migrations.
        await init_db(path)

        async with aiosqlite.connect(path) as db:
            cols = [r[1] for r in await (await db.execute("PRAGMA table_info(fitbit_activity)")).fetchall()]
            assert "calories_bmr" in cols, "v2 migration did not run"

            ver = await (await db.execute("SELECT version FROM schema_version")).fetchone()
            assert ver[0] == _TARGET_SCHEMA_VERSION

        # And every versioned migration's SQL executed cleanly (no
        # entries logged as failed). Smoke-check by confirming we are
        # at the max version declared in the list.
        assert _TARGET_SCHEMA_VERSION >= max(v for v, _ in _VERSIONED_MIGRATIONS)

    @pytest.mark.asyncio
    async def test_versions_strictly_increasing(self):
        """Catch accidentally declaring two migrations with the same version."""
        from src.db import _VERSIONED_MIGRATIONS

        versions = [v for v, _ in _VERSIONED_MIGRATIONS]
        assert versions == sorted(set(versions)), (
            "_VERSIONED_MIGRATIONS versions must be strictly increasing and unique"
        )
        if versions:
            assert min(versions) >= 2, "versioned migrations start at v2 (v1 is _MIGRATIONS)"
