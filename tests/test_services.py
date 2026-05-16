"""Tests for the service layer (src/services.py)."""

import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("TZ", "America/Los_Angeles")

from src.db import init_db, log_meal, set_user_target, ensure_user, upsert_workout, set_user_prefs
from src.services import (
    classify_meal_time,
    get_progress,
    get_trend_data,
    get_user_tz,
    user_today_str,
    _reference_plausible,
    _extract_reply_text,
)


@pytest.fixture
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    asyncio.get_event_loop().run_until_complete(init_db(path))
    return path


def test_classify_meal_time():
    assert classify_meal_time(3) == "breakfast"
    assert classify_meal_time(7) == "breakfast"
    assert classify_meal_time(10) == "breakfast"
    assert classify_meal_time(11) == "lunch"
    assert classify_meal_time(14) == "lunch"
    assert classify_meal_time(15) == "snack"
    assert classify_meal_time(17) == "snack"
    assert classify_meal_time(18) == "dinner"
    assert classify_meal_time(23) == "dinner"
    # Dinner wraps midnight through 02:59
    assert classify_meal_time(0) == "dinner"
    assert classify_meal_time(2) == "dinner"


def test_classify_meal_time_edge():
    """Hour 24 wraps around."""
    # 24 is not in any window, should return "meal"
    assert classify_meal_time(24) == "meal"


def test_get_user_tz():
    tz = get_user_tz("America/New_York")
    assert str(tz) == "America/New_York"


def test_get_user_tz_invalid():
    tz = get_user_tz("Invalid/Zone")
    assert str(tz) == "UTC"


def test_get_user_tz_none():
    tz = get_user_tz(None)
    # Should fall back to env TZ or UTC
    assert tz is not None


def test_reference_plausible_basic():
    fs = {"protein": 20, "carbs": 30, "fat": 10, "calories": 290}
    assert _reference_plausible(fs, None) is True


def test_reference_plausible_too_much_macros():
    fs = {"protein": 50, "carbs": 40, "fat": 20, "calories": 540}
    assert _reference_plausible(fs, None) is False


def test_reference_plausible_vs_gemini():
    fs = {"protein": 20, "carbs": 30, "fat": 10, "calories": 270}
    gemini = {"protein": 19, "carbs": 28, "fat": 9, "calories": 260}
    assert _reference_plausible(fs, gemini) is True


def test_reference_plausible_divergent():
    fs = {"protein": 50, "carbs": 30, "fat": 10, "calories": 410}
    gemini = {"protein": 20, "carbs": 25, "fat": 8, "calories": 250}
    assert _reference_plausible(fs, gemini) is False


@pytest.mark.asyncio
async def test_get_progress_no_target(db_path):
    uid = 100
    await ensure_user(db_path, uid, "test", "Test")
    progress = await get_progress(uid, db_path, "2026-03-17")
    assert progress["totals"]["calories"] == 0.0
    assert progress["target"] is None
    assert progress["remaining"] is None


@pytest.mark.asyncio
async def test_get_progress_with_meals_and_target(db_path):
    uid = 200
    await ensure_user(db_path, uid, "test2", "Test2")
    await set_user_target(db_path, uid, 2000, 150, 200, 70)
    await log_meal(
        db_path, uid, "2026-03-17 12:00",
        "Lunch", "", 500.0, 40.0, 50.0, 20.0, "Gemini",
    )

    progress = await get_progress(uid, db_path, "2026-03-17")
    assert progress["totals"]["calories"] == 500.0
    assert progress["target"]["calories"] == 2000.0
    assert progress["remaining"]["calories"] == 1500.0


@pytest.mark.asyncio
async def test_get_trend_data(db_path):
    uid = 300
    await ensure_user(db_path, uid, "test3", "Test3")
    trend = await get_trend_data(uid, db_path)
    assert len(trend) == 7
    # All zeros for new user
    for day in trend:
        assert day["calories"] == 0.0


@pytest.mark.asyncio
async def test_user_today_str(db_path):
    uid = 400
    await ensure_user(db_path, uid, "test4", "Test4")
    today = await user_today_str(db_path, uid)
    # Should be a valid date string
    assert len(today) == 10
    assert today.count("-") == 2


# ── Trend data with workout adjustments ────────────────────────────


@pytest.mark.asyncio
async def test_trend_data_includes_effective_target_with_workout(db_path):
    """When a day has workouts, effective_target_calories reflects the adjustment."""
    uid = 500
    await ensure_user(db_path, uid, "test5", "Test5")
    await set_user_target(db_path, uid, 2500, 150, 250, 80)

    # Log a meal and a workout on the same day
    await log_meal(db_path, uid, "2026-03-20 12:00", "Lunch", "", 800, 40, 80, 30, "Gemini")
    await upsert_workout(
        db_path, uid, source="strava", external_id="w1",
        activity_type="Run", name="Morning Run",
        started_at="2026-03-20 07:00:00", duration_sec=3600,
        calories_burned=400, logged_at="2026-03-20 08:00",
    )

    target = {"calories": 2500, "protein": 150, "carbs": 250, "fat": 80}
    trend = await get_trend_data(uid, db_path, num_days=7, today_str="2026-03-20", target=target)

    assert len(trend) == 7
    # Find the day with the workout
    mar20 = next(d for d in trend if d["date"] == "2026-03-20")
    assert mar20["calories"] == 800.0
    # effective_target should be base + workout * eat_back_pct (default 0.75)
    assert mar20["effective_target_calories"] == 2500 + round(400 * 0.75)  # 2800

    # Days without workouts should have base target
    mar19 = next(d for d in trend if d["date"] == "2026-03-19")
    assert mar19["effective_target_calories"] == 2500


@pytest.mark.asyncio
async def test_trend_data_no_target_skips_adjustment(db_path):
    """Without a target, effective_target_calories is not included."""
    uid = 501
    await ensure_user(db_path, uid, "test501", "Test501")
    trend = await get_trend_data(uid, db_path, num_days=7, today_str="2026-03-20", target=None)
    assert len(trend) == 7
    for day in trend:
        assert "effective_target_calories" not in day


@pytest.mark.asyncio
async def test_trend_data_exercise_adjustment_off(db_path):
    """When exercise_adjustment_on is disabled, no per-day adjustment."""
    uid = 502
    await ensure_user(db_path, uid, "test502", "Test502")
    await set_user_target(db_path, uid, 2500, 150, 250, 80)
    await set_user_prefs(db_path, uid, exercise_adjustment_on=0)
    await upsert_workout(
        db_path, uid, source="strava", external_id="w2",
        activity_type="Run", name="Run",
        started_at="2026-03-20 07:00:00", duration_sec=3600,
        calories_burned=500, logged_at="2026-03-20 08:00",
    )

    target = {"calories": 2500, "protein": 150, "carbs": 250, "fat": 80}
    trend = await get_trend_data(uid, db_path, num_days=7, today_str="2026-03-20", target=target)
    # With adjustment off, effective_target_calories should NOT be present
    mar20 = next(d for d in trend if d["date"] == "2026-03-20")
    assert "effective_target_calories" not in mar20


@pytest.mark.asyncio
async def test_trend_data_custom_eat_back_pct(db_path):
    """Custom eat_back_pct is respected in the per-day adjustment."""
    uid = 503
    await ensure_user(db_path, uid, "test503", "Test503")
    await set_user_target(db_path, uid, 2000, 120, 200, 60)
    await set_user_prefs(db_path, uid, exercise_eat_back_pct=0.5)
    await upsert_workout(
        db_path, uid, source="strava", external_id="w3",
        activity_type="Ride", name="Bike",
        started_at="2026-03-20 07:00:00", duration_sec=3600,
        calories_burned=600, logged_at="2026-03-20 08:00",
    )

    target = {"calories": 2000, "protein": 120, "carbs": 200, "fat": 60}
    trend = await get_trend_data(uid, db_path, num_days=7, today_str="2026-03-20", target=target)
    mar20 = next(d for d in trend if d["date"] == "2026-03-20")
    assert mar20["effective_target_calories"] == 2000 + round(600 * 0.5)  # 2300


# ---------------------------------------------------------------------------
# _extract_reply_text - JSON block + announcer-phrase stripping
# ---------------------------------------------------------------------------
#
# Gemini's system prompt used to tell it to "always end with an updated
# JSON object", which caused the model to narrate the instruction back:
# "Here's the updated JSON". After the JSON block is stripped, those
# orphan sentences leaked into the chat UI. _extract_reply_text now
# strips them - but must preserve any useful prose Gemini wrote around
# the announcer, since the model sometimes continues with real content.


def test_extract_reply_strips_json_code_block():
    raw = "Got it, I've updated the chicken.\n\n```json\n{\"calories\": 500}\n```"
    assert _extract_reply_text(raw) == "Got it, I've updated the chicken."


def test_extract_reply_strips_bare_json_object():
    raw = "Here are the macros:\n{\"calories\": 500, \"protein\": 40}"
    # "Here are the macros" is an announcer-shaped phrase and gets stripped too.
    assert _extract_reply_text(raw) == ""


def test_extract_reply_strips_trailing_announcer_with_colon():
    raw = "Got it, I've updated the chicken to 200g. Here's the updated JSON:"
    assert _extract_reply_text(raw) == "Got it, I've updated the chicken to 200g."


def test_extract_reply_strips_trailing_announcer_no_colon():
    raw = "I've adjusted the protein for the grilled chicken.\n\nHere's the updated breakdown"
    assert _extract_reply_text(raw) == "I've adjusted the protein for the grilled chicken."


def test_extract_reply_strips_announcer_variants():
    variants = [
        "Here is the updated JSON",
        "Here's the revised nutrition info",
        "Here's the new breakdown",
        "Here are the updated numbers",
        "Here's the final estimate",
        "Here's the corrected JSON",
        "Here's the adjusted breakdown",
    ]
    for v in variants:
        raw = f"Done. {v}"
        assert _extract_reply_text(raw) == "Done.", f"failed for: {v!r}"


def test_extract_reply_preserves_content_after_announcer():
    """Useful info written AFTER the announcer must survive - the user
    asked for this explicitly: Gemini sometimes adds notes post-JSON."""
    raw = (
        "I've updated the protein. Here's the updated JSON:\n"
        "```json\n{\"calories\": 500}\n```\n"
        "Note: verify the portion size before logging."
    )
    result = _extract_reply_text(raw)
    assert "I've updated the protein." in result
    assert "Note: verify the portion size before logging." in result
    assert "Here's the updated JSON" not in result
    assert "```" not in result
    assert "{" not in result


def test_extract_reply_preserves_content_between_announcer_and_json():
    raw = (
        "Okay, one clarification first. Here's the updated breakdown\n"
        "```json\n{\"calories\": 500}\n```"
    )
    result = _extract_reply_text(raw)
    assert result == "Okay, one clarification first."


def test_extract_reply_does_not_strip_mid_sentence_here():
    """Only orphan announcer sentences should be stripped; the word
    "here" appearing mid-sentence in unrelated prose must survive."""
    raw = "The protein here is a bit low for the portion described."
    assert _extract_reply_text(raw) == "The protein here is a bit low for the portion described."


def test_extract_reply_handles_multiple_announcers():
    raw = (
        "I've reduced the protein.\n"
        "Here's the updated JSON:\n"
        "```json\n{\"calories\": 500}\n```\n"
        "Also, here's the revised breakdown"
    )
    result = _extract_reply_text(raw)
    assert "I've reduced the protein." in result
    assert "Here" not in result  # both announcers gone
    assert "updated JSON" not in result
    assert "revised breakdown" not in result


def test_extract_reply_empty_when_only_announcer_and_json():
    raw = "Here's the updated JSON:\n```json\n{\"calories\": 500}\n```"
    assert _extract_reply_text(raw) == ""


def test_extract_reply_leaves_normal_reply_intact():
    raw = "Got it, I've bumped the chicken to 200g and cut the rice portion in half."
    assert _extract_reply_text(raw) == raw


def test_extract_reply_collapses_blank_lines_from_stripping():
    """Announcer stripping should not leave huge gaps of blank lines."""
    raw = (
        "Updated the chicken.\n\n"
        "Here's the updated JSON:\n\n"
        "```json\n{}\n```\n\n"
        "Hope that helps!"
    )
    result = _extract_reply_text(raw)
    # No run of 3+ newlines in a row after cleanup.
    assert "\n\n\n" not in result
    assert "Updated the chicken." in result
    assert "Hope that helps!" in result
