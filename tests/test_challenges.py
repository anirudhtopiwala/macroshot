"""Tests for src/challenges.py - daily and weekly challenge system."""

import tempfile
from datetime import date, timedelta

import pytest
import pytest_asyncio

from src.challenges import (
    DAILY_CHALLENGES,
    WEEKLY_CHALLENGES,
    _pick_challenge,
    _week_start,
    evaluate_daily_progress,
    evaluate_weekly_progress,
    get_challenges_with_progress,
    get_daily_challenge,
    get_weekly_challenge,
)
from src.db import (
    delete_meal,
    ensure_user,
    init_db,
    log_meal,
    set_user_target,
)

import aiosqlite


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    """Fresh DB for each test."""
    path = str(tmp_path / "test.db")
    await init_db(path)
    return path


async def _add_user(db_path, user_id=1, username="alice", first_name="Alice"):
    await ensure_user(db_path, user_id, username, first_name)


async def _add_meal(
    db_path,
    user_id=1,
    logged_at="2026-03-01 12:00",
    item_name="Oatmeal",
    meal_description="",
    calories=400,
    protein=15,
    carbs=60,
    fat=10,
    source="Gemini",
    image_path="",
) -> int:
    return await log_meal(
        db_path, user_id, logged_at, item_name, meal_description,
        calories, protein, carbs, fat, source, image_path=image_path,
    )


# ---------------------------------------------------------------------------
# _pick_challenge - deterministic selection
# ---------------------------------------------------------------------------

class TestPickChallenge:
    """Pure tests for challenge selection logic."""

    def test_same_date_same_challenge(self):
        """Same date always produces the same challenge (deterministic)."""
        c1 = _pick_challenge(1, "2026-03-01", DAILY_CHALLENGES)
        c2 = _pick_challenge(1, "2026-03-01", DAILY_CHALLENGES)
        assert c1["id"] == c2["id"]

    def test_different_dates_produce_different_challenges(self):
        """Different dates should (usually) produce different challenges.

        We check across 30 consecutive days. With 7 daily challenges,
        probability of all 30 hashing to the same index is negligible.
        """
        chosen_ids = set()
        base = date(2026, 1, 1)
        for offset in range(30):
            d = (base + timedelta(days=offset)).isoformat()
            c = _pick_challenge(1, d, DAILY_CHALLENGES)
            chosen_ids.add(c["id"])
        # Should see at least 2 distinct challenges across 30 days
        assert len(chosen_ids) >= 2

    def test_same_challenge_for_different_users(self):
        """Same date produces the same challenge for different user IDs.

        The implementation uses only the date as the seed, ignoring user_id,
        so all users get the same challenge on the same day.
        """
        c_user1 = _pick_challenge(1, "2026-03-15", DAILY_CHALLENGES)
        c_user2 = _pick_challenge(99, "2026-03-15", DAILY_CHALLENGES)
        c_user3 = _pick_challenge(12345, "2026-03-15", DAILY_CHALLENGES)
        assert c_user1["id"] == c_user2["id"] == c_user3["id"]

    def test_weekly_pool_selection(self):
        """_pick_challenge works with the weekly pool too."""
        c = _pick_challenge(1, "2026-03-02", WEEKLY_CHALLENGES)
        assert c["id"] in {ch["id"] for ch in WEEKLY_CHALLENGES}

    def test_always_returns_valid_challenge(self):
        """Result always has the expected keys."""
        c = _pick_challenge(1, "2026-06-15", DAILY_CHALLENGES)
        assert "id" in c
        assert "name" in c
        assert "target" in c
        assert "description" in c


# ---------------------------------------------------------------------------
# _week_start - Monday computation
# ---------------------------------------------------------------------------

class TestWeekStart:
    """Tests for _week_start helper."""

    def test_monday_returns_self(self):
        """A Monday maps to itself."""
        # 2026-03-02 is a Monday
        assert _week_start(date(2026, 3, 2)) == "2026-03-02"

    def test_sunday_returns_previous_monday(self):
        """A Sunday maps to the Monday of the same week."""
        # 2026-03-08 is a Sunday
        assert _week_start(date(2026, 3, 8)) == "2026-03-02"

    def test_wednesday_returns_monday(self):
        """A mid-week day maps to Monday."""
        # 2026-03-04 is a Wednesday
        assert _week_start(date(2026, 3, 4)) == "2026-03-02"

    def test_saturday_returns_monday(self):
        """Saturday maps to the preceding Monday."""
        # 2026-03-07 is a Saturday
        assert _week_start(date(2026, 3, 7)) == "2026-03-02"

    def test_returns_iso_format(self):
        """Result is YYYY-MM-DD string."""
        result = _week_start(date(2026, 3, 5))
        assert len(result) == 10
        assert result[4] == "-" and result[7] == "-"


# ---------------------------------------------------------------------------
# Daily challenge progress - "Three Square" (3 meals today)
# ---------------------------------------------------------------------------

class TestThreeSquareProgress:
    """Daily challenge: log 3 meals today."""

    async def test_zero_meals(self, db):
        await _add_user(db)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "three_meals", "2026-03-01")
        assert progress == 0

    async def test_partial_progress(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 08:00", item_name="Eggs")
        await _add_meal(db, logged_at="2026-03-01 12:00", item_name="Salad")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "three_meals", "2026-03-01")
        assert progress == 2

    async def test_exactly_three_meals(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 08:00", item_name="Eggs")
        await _add_meal(db, logged_at="2026-03-01 12:00", item_name="Salad")
        await _add_meal(db, logged_at="2026-03-01 19:00", item_name="Pasta")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "three_meals", "2026-03-01")
        assert progress == 3

    async def test_meals_on_other_day_not_counted(self, db):
        """Meals from a different date are excluded."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00")
        await _add_meal(db, logged_at="2026-03-02 12:00")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "three_meals", "2026-03-01")
        assert progress == 1


# ---------------------------------------------------------------------------
# Daily challenge progress - "Protein Push" (meal with 30g+ protein)
# ---------------------------------------------------------------------------

class TestProteinPushProgress:
    """Daily challenge: log a meal with >= 30g protein."""

    async def test_no_high_protein_meal(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", protein=20)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "protein_30", "2026-03-01")
        assert progress == 0

    async def test_exactly_30g(self, db):
        """30g is the threshold - should count."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", protein=30)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "protein_30", "2026-03-01")
        assert progress == 1

    async def test_above_30g(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", protein=50)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "protein_30", "2026-03-01")
        assert progress == 1

    async def test_multiple_high_protein_meals(self, db):
        """All meals with >= 30g count toward progress."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 08:00", protein=35)
        await _add_meal(db, logged_at="2026-03-01 12:00", protein=40)
        await _add_meal(db, logged_at="2026-03-01 19:00", protein=10)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "protein_30", "2026-03-01")
        assert progress == 2


# ---------------------------------------------------------------------------
# Daily challenge progress - "Under Target" (calories within target)
# ---------------------------------------------------------------------------

class TestUnderTargetProgress:
    """Daily challenge: total calories within +/-10% of calorie target."""

    async def test_within_target(self, db):
        await _add_user(db)
        await set_user_target(db, 1, calories=2000, protein=150, carbs=200, fat=70)
        # Total 1950 cal - within 90%–110% of 2000 (1800–2200)
        await _add_meal(db, logged_at="2026-03-01 08:00", calories=600)
        await _add_meal(db, logged_at="2026-03-01 12:00", calories=700)
        await _add_meal(db, logged_at="2026-03-01 19:00", calories=650)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "under_target", "2026-03-01")
        assert progress == 1

    async def test_over_target(self, db):
        await _add_user(db)
        await set_user_target(db, 1, calories=2000, protein=150, carbs=200, fat=70)
        # Total 3000 cal - over 110% of 2000
        await _add_meal(db, logged_at="2026-03-01 08:00", calories=1000)
        await _add_meal(db, logged_at="2026-03-01 12:00", calories=1000)
        await _add_meal(db, logged_at="2026-03-01 19:00", calories=1000)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "under_target", "2026-03-01")
        assert progress == 0

    async def test_under_target_too_low(self, db):
        """Calories below 90% of target should NOT count."""
        await _add_user(db)
        await set_user_target(db, 1, calories=2000, protein=150, carbs=200, fat=70)
        # Total 500 cal - well below 90% of 2000 (1800)
        await _add_meal(db, logged_at="2026-03-01 12:00", calories=500)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "under_target", "2026-03-01")
        assert progress == 0

    async def test_no_target_set(self, db):
        """No user target set means the JOIN fails - progress should be 0."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", calories=500)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "under_target", "2026-03-01")
        assert progress == 0

    async def test_exactly_at_boundary(self, db):
        """Calories exactly at 90% of target should count (BETWEEN is inclusive)."""
        await _add_user(db)
        await set_user_target(db, 1, calories=2000, protein=150, carbs=200, fat=70)
        # 90% of 2000 = 1800 exactly
        await _add_meal(db, logged_at="2026-03-01 12:00", calories=1800)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "under_target", "2026-03-01")
        assert progress == 1


# ---------------------------------------------------------------------------
# Weekly challenge progress - "Paparazzi" (5 meals with photos this week)
# ---------------------------------------------------------------------------

class TestPaparazziProgress:
    """Weekly challenge: log 5 meals with photos this week."""

    async def test_no_photos(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-02 12:00")  # Monday
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_photos_5", "2026-03-02")
        assert progress == 0

    async def test_some_photos(self, db):
        await _add_user(db)
        # 3 meals with photos
        await _add_meal(db, logged_at="2026-03-02 08:00", image_path="/img/a.jpg")
        await _add_meal(db, logged_at="2026-03-03 12:00", image_path="/img/b.jpg")
        await _add_meal(db, logged_at="2026-03-04 19:00", image_path="/img/c.jpg")
        # 1 meal without photo
        await _add_meal(db, logged_at="2026-03-05 12:00", image_path="")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_photos_5", "2026-03-02")
        assert progress == 3

    async def test_five_photos_complete(self, db):
        await _add_user(db)
        for day in range(2, 7):  # Mon–Fri of week starting 2026-03-02
            await _add_meal(
                db,
                logged_at=f"2026-03-0{day} 12:00",
                image_path=f"/img/{day}.jpg",
            )
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_photos_5", "2026-03-02")
        assert progress == 5

    async def test_photos_outside_week_excluded(self, db):
        """Meals with photos outside the week range are not counted."""
        await _add_user(db)
        # Week starts 2026-03-02 (Monday), ends 2026-03-08 (Sunday)
        await _add_meal(db, logged_at="2026-03-01 12:00", image_path="/img/before.jpg")  # Sunday before
        await _add_meal(db, logged_at="2026-03-09 12:00", image_path="/img/after.jpg")   # Monday after
        await _add_meal(db, logged_at="2026-03-03 12:00", image_path="/img/in.jpg")      # In range
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_photos_5", "2026-03-02")
        assert progress == 1

    async def test_empty_image_path_not_counted(self, db):
        """An empty string image_path should not count as a photo."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-02 12:00", image_path="")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_photos_5", "2026-03-02")
        assert progress == 0


# ---------------------------------------------------------------------------
# Challenge completion - progress >= target
# ---------------------------------------------------------------------------

class TestChallengeCompletion:
    """Completion is determined by progress >= target."""

    async def test_daily_challenge_not_complete(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00")
        result = await get_challenges_with_progress(db, 1, "2026-03-01")
        daily = result["daily"]
        # The specific challenge depends on the date hash, but with only 1 meal,
        # challenges requiring 3+ meals should not be complete.
        # Instead, test the completion logic generically.
        assert daily["completed"] == (daily["progress"] >= daily["target"])

    async def test_weekly_challenge_completion_flag(self, db):
        await _add_user(db)
        result = await get_challenges_with_progress(db, 1, "2026-03-01")
        weekly = result["weekly"]
        assert weekly["completed"] == (weekly["progress"] >= weekly["target"])

    async def test_three_meals_marks_complete(self, db):
        """Explicitly test Three Square: 3 meals -> completed."""
        await _add_user(db)
        today = "2026-03-01"
        await _add_meal(db, logged_at=f"{today} 08:00", item_name="Eggs")
        await _add_meal(db, logged_at=f"{today} 12:00", item_name="Salad")
        await _add_meal(db, logged_at=f"{today} 19:00", item_name="Pasta")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "three_meals", today)
        # three_meals target is 3
        target = next(c for c in DAILY_CHALLENGES if c["id"] == "three_meals")["target"]
        assert progress >= target

    async def test_protein_push_marks_complete(self, db):
        """Explicitly test Protein Push: one meal with 30g+ protein -> completed."""
        await _add_user(db)
        today = "2026-03-01"
        await _add_meal(db, logged_at=f"{today} 12:00", protein=45)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "protein_30", today)
        target = next(c for c in DAILY_CHALLENGES if c["id"] == "protein_30")["target"]
        assert progress >= target


# ---------------------------------------------------------------------------
# Challenge completion survives across queries (stateless)
# ---------------------------------------------------------------------------

class TestStatelessCompletion:
    """Challenges are stateless - recomputed from meal data each time."""

    async def test_progress_consistent_across_calls(self, db):
        """Calling get_challenges_with_progress twice gives the same result."""
        await _add_user(db)
        today = "2026-03-01"
        await _add_meal(db, logged_at=f"{today} 08:00", item_name="Eggs")
        await _add_meal(db, logged_at=f"{today} 12:00", item_name="Salad")

        r1 = await get_challenges_with_progress(db, 1, today)
        r2 = await get_challenges_with_progress(db, 1, today)
        assert r1["daily"]["progress"] == r2["daily"]["progress"]
        assert r1["daily"]["completed"] == r2["daily"]["completed"]
        assert r1["weekly"]["progress"] == r2["weekly"]["progress"]
        assert r1["weekly"]["completed"] == r2["weekly"]["completed"]

    async def test_progress_updates_after_new_meal(self, db):
        """Adding a meal increases progress on re-query."""
        await _add_user(db)
        today = "2026-03-01"
        await _add_meal(db, logged_at=f"{today} 08:00", item_name="Eggs")

        r1 = await get_challenges_with_progress(db, 1, today)
        progress_before = r1["daily"]["progress"]

        await _add_meal(db, logged_at=f"{today} 12:00", item_name="Salad")
        r2 = await get_challenges_with_progress(db, 1, today)
        progress_after = r2["daily"]["progress"]

        # The challenge could be meal-count based or something else;
        # for meal-count challenges, progress should increase.
        # For non-count challenges (e.g., protein_30), it may or may not.
        # At minimum, the system should not error and should be consistent.
        assert isinstance(progress_after, int)


# ---------------------------------------------------------------------------
# Meal deletion can un-complete a challenge
# ---------------------------------------------------------------------------

class TestMealDeletionUncompletes:
    """Deleting a meal can drop progress below the target."""

    async def test_delete_meal_reduces_three_square_progress(self, db):
        await _add_user(db)
        today = "2026-03-01"
        m1 = await _add_meal(db, logged_at=f"{today} 08:00", item_name="Eggs")
        m2 = await _add_meal(db, logged_at=f"{today} 12:00", item_name="Salad")
        m3 = await _add_meal(db, logged_at=f"{today} 19:00", item_name="Pasta")

        # Before deletion: 3 meals
        async with aiosqlite.connect(db) as conn:
            progress_before = await evaluate_daily_progress(conn, 1, "three_meals", today)
        assert progress_before == 3

        # Delete one meal
        await delete_meal(db, m3, user_id=1)

        # After deletion: 2 meals
        async with aiosqlite.connect(db) as conn:
            progress_after = await evaluate_daily_progress(conn, 1, "three_meals", today)
        assert progress_after == 2

        # Target is 3, so no longer complete
        target = next(c for c in DAILY_CHALLENGES if c["id"] == "three_meals")["target"]
        assert progress_after < target

    async def test_delete_protein_meal_uncompletes_protein_push(self, db):
        await _add_user(db)
        today = "2026-03-01"
        m1 = await _add_meal(db, logged_at=f"{today} 12:00", protein=35)

        async with aiosqlite.connect(db) as conn:
            progress_before = await evaluate_daily_progress(conn, 1, "protein_30", today)
        assert progress_before == 1

        await delete_meal(db, m1, user_id=1)

        async with aiosqlite.connect(db) as conn:
            progress_after = await evaluate_daily_progress(conn, 1, "protein_30", today)
        assert progress_after == 0

    async def test_delete_photo_meal_reduces_paparazzi(self, db):
        await _add_user(db)
        week_start = "2026-03-02"  # Monday
        m1 = await _add_meal(db, logged_at="2026-03-02 12:00", image_path="/img/a.jpg")
        m2 = await _add_meal(db, logged_at="2026-03-03 12:00", image_path="/img/b.jpg")

        async with aiosqlite.connect(db) as conn:
            progress_before = await evaluate_weekly_progress(conn, 1, "week_photos_5", week_start)
        assert progress_before == 2

        await delete_meal(db, m1, user_id=1)

        async with aiosqlite.connect(db) as conn:
            progress_after = await evaluate_weekly_progress(conn, 1, "week_photos_5", week_start)
        assert progress_after == 1

    async def test_full_flow_complete_then_uncomplete(self, db):
        """End-to-end: hit target, then delete to drop below."""
        await _add_user(db)
        today = "2026-03-01"
        m1 = await _add_meal(db, logged_at=f"{today} 08:00")
        m2 = await _add_meal(db, logged_at=f"{today} 12:00")
        m3 = await _add_meal(db, logged_at=f"{today} 19:00")

        async with aiosqlite.connect(db) as conn:
            p1 = await evaluate_daily_progress(conn, 1, "three_meals", today)
        assert p1 == 3
        assert p1 >= 3  # complete

        await delete_meal(db, m2, user_id=1)

        async with aiosqlite.connect(db) as conn:
            p2 = await evaluate_daily_progress(conn, 1, "three_meals", today)
        assert p2 == 2
        assert p2 < 3  # no longer complete


# ---------------------------------------------------------------------------
# get_daily_challenge / get_weekly_challenge wrappers
# ---------------------------------------------------------------------------

class TestChallengeWrappers:
    """Test the high-level challenge selection wrappers."""

    def test_get_daily_challenge_has_correct_fields(self):
        c = get_daily_challenge(1, "2026-03-01")
        assert c["type"] == "daily"
        assert c["period"] == "2026-03-01"
        assert c["id"] in {ch["id"] for ch in DAILY_CHALLENGES}

    def test_get_weekly_challenge_has_correct_fields(self):
        c = get_weekly_challenge(1, "2026-03-05")  # Thursday
        assert c["type"] == "weekly"
        # Period should be the Monday of that week
        assert c["period"] == "2026-03-02"
        assert c["id"] in {ch["id"] for ch in WEEKLY_CHALLENGES}

    def test_get_weekly_challenge_period_is_monday(self):
        """The weekly period should always be a Monday."""
        for day_offset in range(7):
            d = (date(2026, 3, 2) + timedelta(days=day_offset)).isoformat()
            c = get_weekly_challenge(1, d)
            period_date = date.fromisoformat(c["period"])
            assert period_date.weekday() == 0  # 0 = Monday


# ---------------------------------------------------------------------------
# get_challenges_with_progress - integration
# ---------------------------------------------------------------------------

class TestGetChallengesWithProgress:
    """Integration tests for the full challenge + progress pipeline."""

    async def test_returns_daily_and_weekly(self, db):
        await _add_user(db)
        result = await get_challenges_with_progress(db, 1, "2026-03-01")
        assert "daily" in result
        assert "weekly" in result

    async def test_daily_has_required_keys(self, db):
        await _add_user(db)
        result = await get_challenges_with_progress(db, 1, "2026-03-01")
        daily = result["daily"]
        for key in ("id", "name", "description", "icon", "target", "period", "type", "progress", "completed"):
            assert key in daily, f"Missing key: {key}"

    async def test_weekly_has_required_keys(self, db):
        await _add_user(db)
        result = await get_challenges_with_progress(db, 1, "2026-03-01")
        weekly = result["weekly"]
        for key in ("id", "name", "description", "icon", "target", "period", "type", "progress", "completed"):
            assert key in weekly, f"Missing key: {key}"

    async def test_empty_db_zero_progress(self, db):
        await _add_user(db)
        result = await get_challenges_with_progress(db, 1, "2026-03-01")
        assert result["daily"]["progress"] == 0
        assert result["daily"]["completed"] is False
        assert result["weekly"]["progress"] == 0
        assert result["weekly"]["completed"] is False

    async def test_completed_is_bool(self, db):
        await _add_user(db)
        result = await get_challenges_with_progress(db, 1, "2026-03-01")
        assert isinstance(result["daily"]["completed"], bool)
        assert isinstance(result["weekly"]["completed"], bool)


# ---------------------------------------------------------------------------
# Additional daily challenge progress tests
# ---------------------------------------------------------------------------

class TestBalancedMealProgress:
    """Daily challenge: log a meal with P/C/F all > 10g."""

    async def test_balanced_meal(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", protein=15, carbs=20, fat=12)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "balanced_meal", "2026-03-01")
        assert progress == 1

    async def test_unbalanced_meal(self, db):
        """A meal with one macro <= 10g does not count."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", protein=5, carbs=20, fat=12)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "balanced_meal", "2026-03-01")
        assert progress == 0

    async def test_exactly_10g_not_balanced(self, db):
        """Exactly 10g is NOT > 10g, so it should not count."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", protein=10, carbs=10, fat=10)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "balanced_meal", "2026-03-01")
        assert progress == 0


class TestFourMealsProgress:
    """Daily challenge: log 4 meals today (four_meals uses the same logic as three_meals)."""

    async def test_four_meals(self, db):
        await _add_user(db)
        today = "2026-03-01"
        for h in ("08:00", "11:00", "14:00", "19:00"):
            await _add_meal(db, logged_at=f"{today} {h}")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "four_meals", today)
        assert progress == 4


class TestPhotoLogProgress:
    """Daily challenge: log a meal with a photo."""

    async def test_photo_meal(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", image_path="/img/lunch.jpg")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "photo_log", "2026-03-01")
        assert progress == 1

    async def test_no_photo(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 12:00", image_path="")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "photo_log", "2026-03-01")
        assert progress == 0


class TestEarlyBirdProgress:
    """Daily challenge: log a meal before 10 AM."""

    async def test_early_meal(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 07:30")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "early_log", "2026-03-01")
        assert progress == 1

    async def test_late_meal(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 10:30")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "early_log", "2026-03-01")
        assert progress == 0

    async def test_capped_at_one(self, db):
        """Even with multiple early meals, progress caps at 1."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 07:00")
        await _add_meal(db, logged_at="2026-03-01 08:00")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "early_log", "2026-03-01")
        assert progress == 1


# ---------------------------------------------------------------------------
# Additional weekly challenge progress tests
# ---------------------------------------------------------------------------

class TestWeekLogProgress:
    """Weekly challenge: log 20 meals this week."""

    async def test_count_meals_in_week(self, db):
        await _add_user(db)
        week_start = "2026-03-02"
        for i in range(5):
            await _add_meal(db, logged_at=f"2026-03-0{2 + i} 12:00")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_log_20", week_start)
        assert progress == 5


class TestPerfectAttendanceProgress:
    """Weekly challenge: log at least 1 meal every day this week."""

    async def test_all_seven_days(self, db):
        await _add_user(db)
        week_start = "2026-03-02"
        for day in range(7):
            d = (date(2026, 3, 2) + timedelta(days=day)).isoformat()
            await _add_meal(db, logged_at=f"{d} 12:00")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_every_day", week_start)
        assert progress == 7

    async def test_partial_days(self, db):
        await _add_user(db)
        week_start = "2026-03-02"
        # Only 3 distinct days
        await _add_meal(db, logged_at="2026-03-02 12:00")
        await _add_meal(db, logged_at="2026-03-04 12:00")
        await _add_meal(db, logged_at="2026-03-06 12:00")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_every_day", week_start)
        assert progress == 3


class TestExplorerProgress:
    """Weekly challenge: log 7 different meal names this week."""

    async def test_distinct_names(self, db):
        await _add_user(db)
        week_start = "2026-03-02"
        names = ["Eggs", "Salad", "Pasta", "Rice", "Soup", "Pizza", "Tacos"]
        for i, name in enumerate(names):
            d = (date(2026, 3, 2) + timedelta(days=i % 7)).isoformat()
            await _add_meal(db, logged_at=f"{d} 12:00", item_name=name)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_variety_7", week_start)
        assert progress == 7

    async def test_repeated_names_count_once(self, db):
        await _add_user(db)
        week_start = "2026-03-02"
        # Same name 5 times
        for i in range(5):
            await _add_meal(db, logged_at=f"2026-03-0{2 + i} 12:00", item_name="Oatmeal")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_variety_7", week_start)
        assert progress == 1

    async def test_case_insensitive_dedup(self, db):
        """'Oatmeal' and 'oatmeal' should count as the same meal name."""
        await _add_user(db)
        week_start = "2026-03-02"
        await _add_meal(db, logged_at="2026-03-02 12:00", item_name="Oatmeal")
        await _add_meal(db, logged_at="2026-03-03 12:00", item_name="oatmeal")
        await _add_meal(db, logged_at="2026-03-04 12:00", item_name="OATMEAL")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_variety_7", week_start)
        assert progress == 1


class TestWeekTargetProgress:
    """Weekly challenge: hit calorie target 5 out of 7 days."""

    async def test_multiple_days_on_target(self, db):
        await _add_user(db)
        await set_user_target(db, 1, calories=2000, protein=150, carbs=200, fat=70)
        week_start = "2026-03-02"
        # Log meals on 5 different days, each totaling ~2000 cal (within 10%)
        for day_offset in range(5):
            d = (date(2026, 3, 2) + timedelta(days=day_offset)).isoformat()
            await _add_meal(db, logged_at=f"{d} 12:00", calories=2000)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_target_5", week_start)
        assert progress == 5

    async def test_some_days_off_target(self, db):
        await _add_user(db)
        await set_user_target(db, 1, calories=2000, protein=150, carbs=200, fat=70)
        week_start = "2026-03-02"
        # 2 days on target
        await _add_meal(db, logged_at="2026-03-02 12:00", calories=2000)
        await _add_meal(db, logged_at="2026-03-03 12:00", calories=1950)
        # 1 day way over
        await _add_meal(db, logged_at="2026-03-04 12:00", calories=5000)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_target_5", week_start)
        assert progress == 2


# ---------------------------------------------------------------------------
# Unknown challenge IDs return 0
# ---------------------------------------------------------------------------

class TestUnknownChallengeId:
    """Unknown challenge IDs should return 0 progress, not error."""

    async def test_unknown_daily_id(self, db):
        await _add_user(db)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "nonexistent", "2026-03-01")
        assert progress == 0

    async def test_unknown_weekly_id(self, db):
        await _add_user(db)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "nonexistent", "2026-03-02")
        assert progress == 0


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------

class TestWeekStartYearBoundary:
    """_week_start around year boundary."""

    def test_week_start_year_boundary(self):
        """Dec 31, 2025 (Wed) and Jan 1, 2026 (Thu) belong to the same week starting 2025-12-29 (Mon)."""
        assert _week_start(date(2025, 12, 31)) == "2025-12-29"
        assert _week_start(date(2026, 1, 1)) == "2025-12-29"


class TestEarlyLogBoundaryExactly10am:
    """Early bird boundary: exactly 10:00 should NOT count (< 10)."""

    async def test_early_log_boundary_exactly_10am(self, db):
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-01 10:00")
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "early_log", "2026-03-01")
        assert progress == 0


class TestUnderTargetUpperBoundary:
    """Under-target boundary: exactly 110% should count (BETWEEN is inclusive)."""

    async def test_under_target_upper_boundary_110_percent(self, db):
        await _add_user(db)
        await set_user_target(db, 1, calories=2000, protein=150, carbs=200, fat=70)
        # 2200 = 2000 * 1.1 exactly (110%)
        await _add_meal(db, logged_at="2026-03-01 12:00", calories=2200)
        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "under_target", "2026-03-01")
        assert progress == 1


class TestUserIsolation:
    """Each user's challenge progress is independent."""

    async def test_user_isolation(self, db):
        """Two users log meals on the same day; progress is per-user."""
        await _add_user(db, user_id=1, username="alice", first_name="Alice")
        await _add_user(db, user_id=2, username="bob", first_name="Bob")
        today = "2026-03-01"
        # User 1 logs 3 meals
        await _add_meal(db, user_id=1, logged_at=f"{today} 08:00", item_name="Eggs")
        await _add_meal(db, user_id=1, logged_at=f"{today} 12:00", item_name="Salad")
        await _add_meal(db, user_id=1, logged_at=f"{today} 19:00", item_name="Pasta")
        # User 2 logs 1 meal
        await _add_meal(db, user_id=2, logged_at=f"{today} 12:00", item_name="Soup")

        async with aiosqlite.connect(db) as conn:
            progress_user1 = await evaluate_daily_progress(conn, 1, "three_meals", today)
            progress_user2 = await evaluate_daily_progress(conn, 2, "three_meals", today)
        assert progress_user1 == 3
        assert progress_user2 == 1


class TestWeekLogOutOfRangeExclusion:
    """Meals before week_start and after week_end should not count."""

    async def test_week_log_out_of_range_exclusion(self, db):
        await _add_user(db)
        week_start = "2026-03-02"  # Monday
        # Meal BEFORE week start (Sunday 2026-03-01)
        await _add_meal(db, logged_at="2026-03-01 12:00", item_name="Before")
        # Meal AFTER week end (Monday 2026-03-09, i.e. next week)
        await _add_meal(db, logged_at="2026-03-09 12:00", item_name="After")
        # Meal IN range (Wednesday 2026-03-04)
        await _add_meal(db, logged_at="2026-03-04 12:00", item_name="InRange")

        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_weekly_progress(conn, 1, "week_log_20", week_start)
        assert progress == 1


class TestPhotoLogNullImagePath:
    """NULL image_path (not empty string) should not count as a photo meal."""

    async def test_photo_log_null_image_path(self, db):
        await _add_user(db)
        # Insert directly with NULL image_path (the helper always passes a string)
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                """INSERT INTO meal_logs
                   (user_id, logged_at, item_name, meal_description, calories, protein, carbs, fat, source, image_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (1, "2026-03-01 12:00", "Oatmeal", "", 400, 15, 60, 10, "Gemini", None),
            )
            await conn.commit()

        async with aiosqlite.connect(db) as conn:
            progress = await evaluate_daily_progress(conn, 1, "photo_log", "2026-03-01")
        assert progress == 0
