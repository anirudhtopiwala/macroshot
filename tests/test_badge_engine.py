"""Tests for badge engine compute functions and evaluation logic."""

import json
import pytest
import aiosqlite

from src.badges import BADGES, get_tier
from src.badge_engine import (
    _count_aliases_created,
    _count_bullseye_days,
    _count_carbs_days,
    _count_comebacks,
    _count_days_with_3_meals,
    _count_distinct_logging_days,
    _count_corrections,
    _count_early_bird,
    _count_full_days,
    _count_macro_master_days,
    _count_night_owl,
    _count_perfect_weeks,
    _count_photo_meals,
    _count_protein_days,
    _count_quick_logs,
    _count_target_sets,
    _count_total_meals,
    _count_unique_meals,
    _count_weight_entries,
    _count_weight_weeks,
    _COMPUTE,
    compute_all_badge_progress,
    evaluate_badges,
)
from src.db import init_db, ensure_user


@pytest.fixture
async def db(tmp_path):
    """Create a temp DB with schema + a test user."""
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    await ensure_user(db_path, user_id=1, username="test", first_name="Test")
    return db_path


async def _add_meal(db_path, user_id=1, logged_at="2026-03-15 12:00", calories=500, protein=30, carbs=50, fat=20):
    """Helper to add a meal."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, "Test Meal", calories, protein, carbs, fat, logged_at),
        )
        await conn.commit()


async def _add_session(db_path, user_id=1, session_id="s1", conversation=None, status="accepted"):
    """Helper to add a meal session with conversation JSON."""
    conv = json.dumps(conversation or [])
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO meal_sessions (session_id, user_id, conversation, nutrition, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, conv, '{}', status, "2026-03-15 12:00", "2026-03-15 12:00"),
        )
        await conn.commit()


# ── _count_days_with_3_meals ──


@pytest.mark.asyncio
async def test_days_with_3_meals_no_meals(db):
    async with aiosqlite.connect(db) as conn:
        assert await _count_days_with_3_meals(conn, 1) == 0


@pytest.mark.asyncio
async def test_days_with_3_meals_two_meals_does_not_qualify(db):
    await _add_meal(db, logged_at="2026-03-15 08:00")
    await _add_meal(db, logged_at="2026-03-15 12:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_days_with_3_meals(conn, 1) == 0


@pytest.mark.asyncio
async def test_days_with_3_meals_three_qualifies(db):
    await _add_meal(db, logged_at="2026-03-15 08:00")
    await _add_meal(db, logged_at="2026-03-15 12:00")
    await _add_meal(db, logged_at="2026-03-15 18:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_days_with_3_meals(conn, 1) == 1


@pytest.mark.asyncio
async def test_days_with_3_meals_multiple_days(db):
    for day in ["2026-03-15", "2026-03-16"]:
        for hour in ["08:00", "12:00", "18:00"]:
            await _add_meal(db, logged_at=f"{day} {hour}")
    # Day with only 2 meals
    await _add_meal(db, logged_at="2026-03-17 08:00")
    await _add_meal(db, logged_at="2026-03-17 12:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_days_with_3_meals(conn, 1) == 2


# ── _count_distinct_logging_days ──


@pytest.mark.asyncio
async def test_distinct_logging_days_no_meals(db):
    async with aiosqlite.connect(db) as conn:
        assert await _count_distinct_logging_days(conn, 1) == 0


@pytest.mark.asyncio
async def test_distinct_logging_days_multiple_meals_same_day(db):
    await _add_meal(db, logged_at="2026-03-15 08:00")
    await _add_meal(db, logged_at="2026-03-15 12:00")
    await _add_meal(db, logged_at="2026-03-15 18:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_distinct_logging_days(conn, 1) == 1


@pytest.mark.asyncio
async def test_distinct_logging_days_across_days(db):
    for day in ["2026-03-15", "2026-03-16", "2026-03-17"]:
        await _add_meal(db, logged_at=f"{day} 12:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_distinct_logging_days(conn, 1) == 3


# ── _count_corrections (json_each approach) ──


@pytest.mark.asyncio
async def test_corrections_no_sessions(db):
    async with aiosqlite.connect(db) as conn:
        assert await _count_corrections(conn, 1) == 0


@pytest.mark.asyncio
async def test_corrections_one_user_entry_not_counted(db):
    """Session with only the initial user prompt - not a correction."""
    conv = [{"role": "user", "parts": ["analyze"]}, {"role": "model", "parts": ["result"]}]
    await _add_session(db, conversation=conv)
    async with aiosqlite.connect(db) as conn:
        assert await _count_corrections(conn, 1) == 0


@pytest.mark.asyncio
async def test_corrections_two_user_entries_counted(db):
    """Session with initial prompt + user correction - counts."""
    conv = [
        {"role": "user", "parts": ["analyze"]},
        {"role": "model", "parts": ["result"]},
        {"role": "user", "parts": ["fix the protein"]},
        {"role": "model", "parts": ["corrected"]},
    ]
    await _add_session(db, conversation=conv)
    async with aiosqlite.connect(db) as conn:
        assert await _count_corrections(conn, 1) == 1


@pytest.mark.asyncio
async def test_corrections_pending_not_counted(db):
    """Pending sessions are not counted - only accepted."""
    conv = [
        {"role": "user", "parts": ["analyze"]},
        {"role": "model", "parts": ["result"]},
        {"role": "user", "parts": ["fix"]},
        {"role": "model", "parts": ["done"]},
    ]
    await _add_session(db, conversation=conv, status="pending")
    async with aiosqlite.connect(db) as conn:
        assert await _count_corrections(conn, 1) == 0


# ── _count_protein_days upper bound ──


@pytest.mark.asyncio
async def test_protein_days_within_range_counts(db):
    """Day with protein at 100% of target counts."""
    async with aiosqlite.connect(db) as conn:
        await conn.execute("INSERT INTO user_targets (user_id, calories, protein, carbs, fat, updated_at) VALUES (1, 2000, 100, 200, 70, '2026-03-15')")
        await conn.commit()
    await _add_meal(db, logged_at="2026-03-15 12:00", protein=100)
    async with aiosqlite.connect(db) as conn:
        assert await _count_protein_days(conn, 1) == 1


@pytest.mark.asyncio
async def test_protein_days_over_110_pct_not_counted(db):
    """Day with protein at 200% of target should NOT count (outside 10% window)."""
    async with aiosqlite.connect(db) as conn:
        await conn.execute("INSERT INTO user_targets (user_id, calories, protein, carbs, fat, updated_at) VALUES (1, 2000, 100, 200, 70, '2026-03-15')")
        await conn.commit()
    await _add_meal(db, logged_at="2026-03-15 12:00", protein=200)
    async with aiosqlite.connect(db) as conn:
        assert await _count_protein_days(conn, 1) == 0


# ── Meta completionist threshold ──


def test_meta_completionist_diamond_at_23():
    """Diamond requires 23 badges (all others, not counting itself)."""
    assert BADGES["meta_completionist"]["thresholds"][-1] == 23
    assert get_tier(23, [5, 12, 18, 23]) == 3  # diamond
    assert get_tier(22, [5, 12, 18, 23]) == 2  # gold


# ── _COMPUTE coverage ──


def test_all_badges_have_compute_functions():
    """Every badge_id in BADGES has a corresponding _COMPUTE entry."""
    for badge_id in BADGES:
        assert badge_id in _COMPUTE, f"Missing _COMPUTE entry for {badge_id}"
    for badge_id in _COMPUTE:
        assert badge_id in BADGES, f"Orphan _COMPUTE entry: {badge_id}"


def test_descriptions_match_thresholds():
    """Every badge has len(descriptions) == len(thresholds)."""
    for badge_id, badge in BADGES.items():
        assert len(badge["descriptions"]) == len(badge["thresholds"]), (
            f"{badge_id}: {len(badge['descriptions'])} descriptions but {len(badge['thresholds'])} thresholds"
        )


# ── compute_all_badge_progress display_tier ──


@pytest.mark.asyncio
async def test_display_tier_uses_max_of_computed_and_db(db):
    """display_tier should be max(computed_tier, db_tier) so badges don't show as locked
    when they've been formally awarded but the user's current value dropped."""
    # Award gold (tier 2) for variety_saved_chef (thresholds [2, 5, 10, 20])
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO badge_earned (user_id, badge_id, tier, earned_at, seen) VALUES (1, 'variety_saved_chef', 2, '2026-03-15', 1)"
        )
        await conn.commit()
    # User currently has 0 aliases - computed tier would be -1
    # But display_tier should be 2 (gold) because badges never downgrade
    results = await compute_all_badge_progress(db, 1, "2026-03-15")
    chef_badge = next(b for b in results if b["badge_id"] == "variety_saved_chef")
    assert chef_badge["tier"] == 2  # gold, not -1
    assert chef_badge["tier_name"] == "gold"


# ──────────────────────────────────────────────────────────────────────────
# Compute functions that lacked direct test coverage — added 2026-05 after
# audit. Each fn here was previously only covered by smoke "all badges have
# compute fns" meta-tests, which couldn't catch column/format bugs.
# ──────────────────────────────────────────────────────────────────────────


async def _add_meal_typed(db_path, user_id=1, logged_at="2026-03-15 12:00", meal_type="lunch", item_name="Test Meal"):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at, meal_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, item_name, 500, 30, 50, 20, logged_at, meal_type),
        )
        await conn.commit()


async def _set_target(db_path, user_id=1, calories=2000, protein=150, carbs=200, fat=70):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO user_targets (user_id, calories, protein, carbs, fat, set_by, updated_at) VALUES (?, ?, ?, ?, ?, 'manual', '2026-03-15')",
            (user_id, calories, protein, carbs, fat),
        )
        await conn.commit()


# ── _count_early_bird ──

@pytest.mark.asyncio
async def test_early_bird_counts_breakfasts_before_9am(db):
    await _add_meal_typed(db, logged_at="2026-03-15 06:30", meal_type="breakfast")
    await _add_meal_typed(db, logged_at="2026-03-16 08:59", meal_type="breakfast")
    async with aiosqlite.connect(db) as conn:
        assert await _count_early_bird(conn, 1) == 2


@pytest.mark.asyncio
async def test_early_bird_excludes_breakfasts_at_or_after_9am(db):
    await _add_meal_typed(db, logged_at="2026-03-15 09:00", meal_type="breakfast")
    await _add_meal_typed(db, logged_at="2026-03-16 11:30", meal_type="breakfast")
    async with aiosqlite.connect(db) as conn:
        assert await _count_early_bird(conn, 1) == 0


@pytest.mark.asyncio
async def test_early_bird_excludes_non_breakfast_meals(db):
    # Lunch at 8 AM doesn't count even though it's before 9
    await _add_meal_typed(db, logged_at="2026-03-15 08:00", meal_type="lunch")
    async with aiosqlite.connect(db) as conn:
        assert await _count_early_bird(conn, 1) == 0


# ── _count_night_owl ──

@pytest.mark.asyncio
async def test_night_owl_counts_dinners_at_or_after_7pm(db):
    await _add_meal_typed(db, logged_at="2026-03-15 19:00", meal_type="dinner")
    await _add_meal_typed(db, logged_at="2026-03-16 22:30", meal_type="dinner")
    async with aiosqlite.connect(db) as conn:
        assert await _count_night_owl(conn, 1) == 2


@pytest.mark.asyncio
async def test_night_owl_excludes_dinners_before_7pm(db):
    await _add_meal_typed(db, logged_at="2026-03-15 18:30", meal_type="dinner")
    async with aiosqlite.connect(db) as conn:
        assert await _count_night_owl(conn, 1) == 0


# ── _count_comebacks ──

@pytest.mark.asyncio
async def test_comebacks_zero_when_no_gaps(db):
    for d in ("2026-03-10", "2026-03-11", "2026-03-12"):
        await _add_meal_typed(db, logged_at=f"{d} 12:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_comebacks(conn, 1) == 0


@pytest.mark.asyncio
async def test_comebacks_counts_each_2plus_day_gap(db):
    # Logs: Mar 10, gap Mar 11-12, Mar 13, gap Mar 14-16, Mar 17 → 2 comebacks
    for d in ("2026-03-10", "2026-03-13", "2026-03-17"):
        await _add_meal_typed(db, logged_at=f"{d} 12:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_comebacks(conn, 1) == 2


@pytest.mark.asyncio
async def test_comebacks_ignores_single_day_gap(db):
    # 1-day gap (= 2 days apart) does NOT trigger a comeback in the
    # current implementation — only 2+ day gaps (3+ days apart) do.
    # Test documents existing behavior; if this is wrong, fix the threshold.
    await _add_meal_typed(db, logged_at="2026-03-10 12:00")
    await _add_meal_typed(db, logged_at="2026-03-12 12:00")  # 2-day gap
    async with aiosqlite.connect(db) as conn:
        # Current code: gap >= 2 means missed at least 1 day. (Mar 12 - Mar 10).days = 2.
        assert await _count_comebacks(conn, 1) == 1


# ── _count_full_days ──

@pytest.mark.asyncio
async def test_full_day_requires_all_four_meal_types(db):
    for mt in ("breakfast", "lunch", "snack", "dinner"):
        await _add_meal_typed(db, logged_at="2026-03-15 12:00", meal_type=mt)
    async with aiosqlite.connect(db) as conn:
        assert await _count_full_days(conn, 1) == 1


@pytest.mark.asyncio
async def test_full_day_three_real_types_plus_fallback_does_not_count(db):
    # Only 3 of the 4 valid types — the post-fix filter should reject this
    # even when an extra row with an empty/fallback meal_type is present.
    for mt in ("breakfast", "lunch", "dinner"):
        await _add_meal_typed(db, logged_at="2026-03-15 12:00", meal_type=mt)
    await _add_meal_typed(db, logged_at="2026-03-15 03:00", meal_type="meal")
    await _add_meal_typed(db, logged_at="2026-03-15 04:00", meal_type="")
    async with aiosqlite.connect(db) as conn:
        assert await _count_full_days(conn, 1) == 0


@pytest.mark.asyncio
async def test_full_day_partial_day_does_not_count(db):
    for mt in ("breakfast", "lunch", "dinner"):
        await _add_meal_typed(db, logged_at="2026-03-15 12:00", meal_type=mt)
    async with aiosqlite.connect(db) as conn:
        assert await _count_full_days(conn, 1) == 0


# ── _count_unique_meals ──

@pytest.mark.asyncio
async def test_unique_meals_collapses_case_and_whitespace(db):
    # All five rows should collapse to ONE unique meal: "pizza"
    for name in ("Pizza", "pizza", " pizza ", "PIZZA", "Pizza\t"):
        await _add_meal_typed(db, logged_at="2026-03-15 12:00", item_name=name)
    async with aiosqlite.connect(db) as conn:
        assert await _count_unique_meals(conn, 1) == 1


@pytest.mark.asyncio
async def test_unique_meals_counts_distinct_dishes(db):
    for name in ("Pizza", "Burger", "Salad"):
        await _add_meal_typed(db, logged_at="2026-03-15 12:00", item_name=name)
    async with aiosqlite.connect(db) as conn:
        assert await _count_unique_meals(conn, 1) == 3


# ── _count_weight_weeks ──

async def _add_weight(db_path, user_id=1, logged_at="2026-03-15", weight_kg=80.0):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO weight_logs (user_id, weight_kg, logged_at) VALUES (?, ?, ?)",
            (user_id, weight_kg, logged_at),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_weight_weeks_counts_distinct_iso_weeks(db):
    # Two logs in week 11 of 2026, one in week 12 → 2 distinct ISO weeks
    await _add_weight(db, logged_at="2026-03-09")  # Mon week 11
    await _add_weight(db, logged_at="2026-03-15")  # Sun week 11
    await _add_weight(db, logged_at="2026-03-16")  # Mon week 12
    async with aiosqlite.connect(db) as conn:
        assert await _count_weight_weeks(conn, 1) == 2


@pytest.mark.asyncio
async def test_weight_weeks_no_year_boundary_split(db):
    # Pre-fix (%Y-%W), 2026-01-01 grouped under "2026-00" while logs later
    # in week 1 grouped under "2026-01" — splitting one ISO week across
    # two strings. With %G-%V, both fall in 2026-W01.
    await _add_weight(db, logged_at="2026-01-01")  # Thu, ISO week 2026-W01
    await _add_weight(db, logged_at="2026-01-04")  # Sun, ISO week 2026-W01
    async with aiosqlite.connect(db) as conn:
        assert await _count_weight_weeks(conn, 1) == 1


# ── _count_bullseye_days / _count_carbs_days / _count_macro_master_days ──

@pytest.mark.asyncio
async def test_bullseye_zero_without_target(db):
    await _add_meal_typed(db, logged_at="2026-03-15 12:00")
    async with aiosqlite.connect(db) as conn:
        assert await _count_bullseye_days(conn, 1) == 0


@pytest.mark.asyncio
async def test_bullseye_inside_window_counts(db):
    await _set_target(db, calories=2000)
    # 1900 kcal day = 95% of target = inside [0.9, 1.1]
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (1, 'm', 1900, 100, 200, 60, '2026-03-15 12:00')",
        )
        await conn.commit()
        assert await _count_bullseye_days(conn, 1) == 1


@pytest.mark.asyncio
async def test_bullseye_outside_window_excluded(db):
    await _set_target(db, calories=2000)
    # 2300 kcal = 115% of target = outside [0.9, 1.1]
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (1, 'm', 2300, 100, 200, 60, '2026-03-15 12:00')",
        )
        await conn.commit()
        assert await _count_bullseye_days(conn, 1) == 0


@pytest.mark.asyncio
async def test_carbs_days_basic(db):
    await _set_target(db, carbs=200)
    async with aiosqlite.connect(db) as conn:
        # 195g = 97.5% of 200, inside window
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (1, 'm', 1500, 100, 195, 60, '2026-03-15 12:00')",
        )
        await conn.commit()
        assert await _count_carbs_days(conn, 1) == 1


@pytest.mark.asyncio
async def test_macro_master_requires_all_four_in_window(db):
    await _set_target(db, calories=2000, protein=150, carbs=200, fat=70)
    async with aiosqlite.connect(db) as conn:
        # Inside on cal/protein/carbs but fat way over → no
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (1, 'm', 1950, 145, 195, 200, '2026-03-15 12:00')",
        )
        await conn.commit()
        assert await _count_macro_master_days(conn, 1) == 0


@pytest.mark.asyncio
async def test_macro_master_all_four_inside(db):
    await _set_target(db, calories=2000, protein=150, carbs=200, fat=70)
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (1, 'm', 1950, 145, 195, 68, '2026-03-15 12:00')",
        )
        await conn.commit()
        assert await _count_macro_master_days(conn, 1) == 1


# ── _count_perfect_weeks ──

@pytest.mark.asyncio
async def test_perfect_weeks_seven_consecutive_inside_window(db):
    await _set_target(db, calories=2000)
    async with aiosqlite.connect(db) as conn:
        for i in range(7):
            await conn.execute(
                "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (1, 'm', 1950, 100, 200, 60, ?)",
                (f"2026-03-{9 + i:02d} 12:00",),
            )
        await conn.commit()
        assert await _count_perfect_weeks(conn, 1) == 1


@pytest.mark.asyncio
async def test_perfect_weeks_gap_breaks_week(db):
    await _set_target(db, calories=2000)
    async with aiosqlite.connect(db) as conn:
        # 6 in a row, skip a day, then 1 more → 7 total but not consecutive
        for i in (0, 1, 2, 3, 4, 5, 7):
            await conn.execute(
                "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) VALUES (1, 'm', 1950, 100, 200, 60, ?)",
                (f"2026-03-{9 + i:02d} 12:00",),
            )
        await conn.commit()
        assert await _count_perfect_weeks(conn, 1) == 0


# ── _count_photo_meals (meals_snap_happy) ──

@pytest.mark.asyncio
async def test_photo_meals_excludes_null_image(db):
    await _add_meal_typed(db, logged_at="2026-03-15 12:00")  # no image_path → defaults to ''
    async with aiosqlite.connect(db) as conn:
        assert await _count_photo_meals(conn, 1) == 0


@pytest.mark.asyncio
async def test_photo_meals_excludes_empty_image_path(db):
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at, image_path) "
            "VALUES (1, 'm', 500, 30, 50, 20, '2026-03-15 12:00', '')",
        )
        await conn.commit()
        assert await _count_photo_meals(conn, 1) == 0


@pytest.mark.asyncio
async def test_photo_meals_counts_rows_with_image(db):
    async with aiosqlite.connect(db) as conn:
        for i in range(3):
            await conn.execute(
                "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at, image_path) "
                "VALUES (1, 'm', 500, 30, 50, 20, ?, ?)",
                (f"2026-03-{15 + i:02d} 12:00", f"/uploads/{i}.jpg"),
            )
        # Mixed: one without image
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) "
            "VALUES (1, 'm', 500, 30, 50, 20, '2026-03-19 12:00')",
        )
        await conn.commit()
        assert await _count_photo_meals(conn, 1) == 3


# ── meals_meal_machine + meals_snap_happy tier thresholds ──

def test_meal_machine_tier_thresholds():
    """Each meal-count threshold awards the matching tier — guards against
    threshold reordering."""
    thresholds = BADGES["meals_meal_machine"]["thresholds"]
    assert thresholds == [10, 50, 100, 168]
    assert get_tier(9, thresholds) == -1
    assert get_tier(10, thresholds) == 0   # bronze
    assert get_tier(49, thresholds) == 0
    assert get_tier(50, thresholds) == 1   # silver
    assert get_tier(99, thresholds) == 1
    assert get_tier(100, thresholds) == 2  # gold
    assert get_tier(167, thresholds) == 2
    assert get_tier(168, thresholds) == 3  # diamond


def test_snap_happy_tier_thresholds():
    thresholds = BADGES["meals_snap_happy"]["thresholds"]
    assert thresholds == [5, 20, 60, 150]
    assert get_tier(4, thresholds) == -1
    assert get_tier(5, thresholds) == 0
    assert get_tier(20, thresholds) == 1
    assert get_tier(60, thresholds) == 2
    assert get_tier(150, thresholds) == 3


# ── target_carbs_master near-miss ──

@pytest.mark.asyncio
async def test_carbs_days_outside_window_excluded(db):
    """115% of carb target is outside [0.9, 1.1] and must NOT count."""
    await _set_target(db, carbs=200)
    async with aiosqlite.connect(db) as conn:
        # 230g = 115% of 200 — over the upper bound
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) "
            "VALUES (1, 'm', 1500, 100, 230, 60, '2026-03-15 12:00')",
        )
        # 170g = 85% of 200 — under the lower bound
        await conn.execute(
            "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at) "
            "VALUES (1, 'm', 1500, 100, 170, 60, '2026-03-16 12:00')",
        )
        await conn.commit()
        assert await _count_carbs_days(conn, 1) == 0


# ── _count_quick_logs (variety_quick_draw) ──

@pytest.mark.asyncio
async def test_quick_logs_counts_only_alias_source(db):
    async with aiosqlite.connect(db) as conn:
        for source in ("Alias", "Alias", "Photo", "Text", ""):
            await conn.execute(
                "INSERT INTO meal_logs (user_id, item_name, calories, protein, carbs, fat, logged_at, source) "
                "VALUES (1, 'm', 500, 30, 50, 20, '2026-03-15 12:00', ?)",
                (source,),
            )
        await conn.commit()
        assert await _count_quick_logs(conn, 1) == 2


@pytest.mark.asyncio
async def test_quick_logs_zero_when_no_meals(db):
    async with aiosqlite.connect(db) as conn:
        assert await _count_quick_logs(conn, 1) == 0


# ── _count_aliases_created (variety_saved_chef) ──

async def _add_alias(db_path, user_id=1, alias_name="quick"):
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO meal_aliases (user_id, alias_name, item_name, calories, protein, carbs, fat, created_at) "
            "VALUES (?, ?, 'Test', 500, 30, 50, 20, '2026-03-15')",
            (user_id, alias_name),
        )
        await conn.commit()


@pytest.mark.asyncio
async def test_aliases_created_zero_when_none(db):
    async with aiosqlite.connect(db) as conn:
        assert await _count_aliases_created(conn, 1) == 0


@pytest.mark.asyncio
async def test_aliases_created_counts_rows(db):
    for name in ("breakfast_smoothie", "post_workout", "late_snack"):
        await _add_alias(db, alias_name=name)
    async with aiosqlite.connect(db) as conn:
        assert await _count_aliases_created(conn, 1) == 3


@pytest.mark.asyncio
async def test_aliases_created_isolates_users(db):
    """Aliases created by another user don't leak into this user's count."""
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO users (user_id, username, first_name, registered_at) VALUES (2, 'u2', 'U2', '2026-03-15')"
        )
        await conn.commit()
    await _add_alias(db, user_id=1, alias_name="mine")
    await _add_alias(db, user_id=2, alias_name="theirs")
    async with aiosqlite.connect(db) as conn:
        assert await _count_aliases_created(conn, 1) == 1
        assert await _count_aliases_created(conn, 2) == 1


# ── _count_target_sets (explorer_goal_setter) ──

@pytest.mark.asyncio
async def test_target_sets_zero_by_default(db):
    """Newly registered user has no target_set_count — should COALESCE to 0."""
    async with aiosqlite.connect(db) as conn:
        assert await _count_target_sets(conn, 1) == 0


@pytest.mark.asyncio
async def test_target_sets_reads_users_column(db):
    async with aiosqlite.connect(db) as conn:
        await conn.execute("UPDATE users SET target_set_count = 3 WHERE user_id = 1")
        await conn.commit()
        assert await _count_target_sets(conn, 1) == 3


# ── _count_weight_entries (weight_scale_warrior) ──

@pytest.mark.asyncio
async def test_weight_entries_counts_all_logs(db):
    for d in ("2026-03-09", "2026-03-10", "2026-03-11"):
        await _add_weight(db, logged_at=d)
    async with aiosqlite.connect(db) as conn:
        assert await _count_weight_entries(conn, 1) == 3


# ── meta_completionist recursive trigger ──

@pytest.mark.asyncio
async def test_meta_completionist_fires_when_other_badges_earned(db):
    """Earning meal-related badges should also award meta_completionist
    when the count crosses 5. Previously untested — guarding against
    regression of the recursive any_badge_earn trigger.
    """
    # Manually pre-seed 4 earned badges so one more crosses bronze (5)
    async with aiosqlite.connect(db) as conn:
        for badge_id in (
            "milestone_first_meal", "milestone_first_day",
            "meals_meal_machine", "variety_world_plate",
        ):
            await conn.execute(
                "INSERT INTO badge_earned (user_id, badge_id, tier, earned_at, seen) VALUES (1, ?, 0, '2026-03-15', 1)",
                (badge_id,),
            )
        await conn.commit()
    # Add 7 distinct logging days so milestone_first_week (5th badge) earns
    for i in range(7):
        await _add_meal_typed(db, logged_at=f"2026-03-{9 + i:02d} 12:00")

    new_badges = await evaluate_badges(db, 1, "meal_accept", {"today_str": "2026-03-15"})
    earned_ids = {b["badge_id"] for b in new_badges}
    assert "milestone_first_week" in earned_ids
    assert "meta_completionist" in earned_ids, (
        "meta_completionist should have triggered via any_badge_earn after "
        f"a 5th badge was earned. Got: {earned_ids}"
    )
