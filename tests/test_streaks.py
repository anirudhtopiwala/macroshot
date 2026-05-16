"""Tests for streak calculation logic.

Covers:
- _calculate_streak (pure function in src/db.py)
- _count_streak (async function in src/badge_engine.py with shield consumption)
"""

from datetime import date, timedelta

import aiosqlite
import pytest
import pytest_asyncio

from src.db import _calculate_streak, ensure_user, init_db, log_meal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_range(end_str: str, days: int) -> list[str]:
    """Generate a list of consecutive ISO date strings ending on *end_str*."""
    end = date.fromisoformat(end_str)
    return [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


@pytest_asyncio.fixture
async def db(tmp_path):
    """Fresh DB for each test."""
    path = str(tmp_path / "test.db")
    await init_db(path)
    return path


async def _add_user(db_path, user_id=1):
    await ensure_user(db_path, user_id, "testuser", "Test")


async def _add_meal(db_path, user_id=1, logged_at="2026-03-01 12:00"):
    await log_meal(
        db_path, user_id, logged_at,
        item_name="Oatmeal", meal_description="",
        calories=400, protein=15, carbs=60, fat=10, source="Gemini",
    )


async def _add_shield(db_path, user_id=1, *, used_at=None, bridged_date=None):
    """Insert a streak shield row. If used_at/bridged_date are set, it is consumed."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute(
            "INSERT INTO streak_shields (user_id, earned_at, used_at, bridged_date) VALUES (?, ?, ?, ?)",
            (user_id, "2026-01-01 00:00:00", used_at, bridged_date),
        )
        await conn.commit()


# ===========================================================================
# _calculate_streak - pure function tests (no DB needed)
# ===========================================================================


class TestCalculateStreakBasic:
    """Basic streak computation without shields."""

    def test_empty_dates_returns_zero(self):
        """1. Empty dates -> streak 0."""
        assert _calculate_streak([], "2026-03-15") == 0

    def test_only_today_returns_one(self):
        """2. Only today -> streak 1."""
        assert _calculate_streak(["2026-03-15"], "2026-03-15") == 1

    def test_today_and_yesterday_returns_two(self):
        """3. Today + yesterday -> streak 2."""
        dates = ["2026-03-14", "2026-03-15"]
        assert _calculate_streak(dates, "2026-03-15") == 2

    def test_gap_in_dates_stops_streak(self):
        """4. Gap in dates -> streak stops at gap."""
        # Logged on 13th, skipped 14th, logged 15th -> streak is 1 (only today)
        dates = ["2026-03-13", "2026-03-15"]
        assert _calculate_streak(dates, "2026-03-15") == 1

    def test_anchor_on_yesterday_if_no_meal_today(self):
        """5. Streak anchors on yesterday if today has no meal."""
        dates = ["2026-03-12", "2026-03-13", "2026-03-14"]
        # Today is 15th, no meal today; anchor = yesterday (14th), walk back 3 days
        assert _calculate_streak(dates, "2026-03-15") == 3

    def test_no_anchor_returns_zero(self):
        """6. No anchor (no meal today or yesterday) -> streak 0."""
        # Last meal was two days ago
        dates = ["2026-03-13"]
        assert _calculate_streak(dates, "2026-03-15") == 0

    def test_three_consecutive_days(self):
        dates = ["2026-03-13", "2026-03-14", "2026-03-15"]
        assert _calculate_streak(dates, "2026-03-15") == 3

    def test_gap_far_back_only_counts_recent(self):
        """Old dates before a gap don't count."""
        # 10th, then gap on 11th, then 12-15 continuous
        dates = ["2026-03-10", "2026-03-12", "2026-03-13", "2026-03-14", "2026-03-15"]
        assert _calculate_streak(dates, "2026-03-15") == 4

    def test_only_yesterday_returns_one(self):
        """Logged yesterday but not today -> streak 1 (user hasn't eaten yet)."""
        assert _calculate_streak(["2026-03-14"], "2026-03-15") == 1

    def test_unsorted_dates_still_works(self):
        """_calculate_streak uses a set internally, so order shouldn't matter."""
        dates = ["2026-03-15", "2026-03-13", "2026-03-14"]
        assert _calculate_streak(dates, "2026-03-15") == 3

    def test_duplicate_dates_handled(self):
        """Multiple meals on the same day should not inflate the streak."""
        dates = ["2026-03-14", "2026-03-14", "2026-03-15", "2026-03-15"]
        assert _calculate_streak(dates, "2026-03-15") == 2


class TestCalculateStreakLong:
    """Long streaks (30+ days)."""

    def test_long_streak_30_days(self):
        """11. Very long streak (30 days)."""
        dates = _date_range("2026-03-15", 30)
        assert _calculate_streak(dates, "2026-03-15") == 30

    def test_long_streak_100_days(self):
        dates = _date_range("2026-03-15", 100)
        assert _calculate_streak(dates, "2026-03-15") == 100

    def test_long_streak_anchored_on_yesterday(self):
        """30-day streak ending yesterday, nothing today -> still 30."""
        dates = _date_range("2026-03-14", 30)
        assert _calculate_streak(dates, "2026-03-15") == 30

    def test_long_streak_broken_by_one_gap(self):
        """31 days of meals with a gap in the middle -> only recent part counts."""
        # Days 1-15 then gap on 16th day back, then 17-31
        recent = _date_range("2026-03-15", 15)  # Mar 1 to Mar 15
        old = _date_range("2026-02-13", 15)      # Jan 30 to Feb 13
        # Gap on Feb 14
        assert _calculate_streak(old + recent, "2026-03-15") == 15


class TestCalculateStreakWithShields:
    """Shielded dates bridge gaps in _calculate_streak."""

    def test_shielded_date_bridges_gap(self):
        """7. Shielded dates bridge gaps."""
        # Logged 13th and 15th, skipped 14th but 14th is shielded
        dates = ["2026-03-13", "2026-03-15"]
        shielded = {"2026-03-14"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 3

    def test_multiple_shielded_dates_bridge_consecutive_gaps(self):
        """8. Multiple shielded dates bridge consecutive gaps."""
        # Logged 12th and 15th, gaps on 13th and 14th, both shielded
        dates = ["2026-03-12", "2026-03-15"]
        shielded = {"2026-03-13", "2026-03-14"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 4

    def test_shield_on_today_counts_as_anchor(self):
        """9. Shield on today counts as anchor."""
        # No meal today, but today is shielded. Meals on 13th and 14th.
        dates = ["2026-03-13", "2026-03-14"]
        shielded = {"2026-03-15"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 3

    def test_shield_on_yesterday_counts_as_anchor(self):
        """10. Shield on yesterday counts as anchor."""
        # No meal today or yesterday, but yesterday is shielded. Meals on 12th and 13th.
        dates = ["2026-03-12", "2026-03-13"]
        shielded = {"2026-03-14"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 3

    def test_shield_without_meals_still_counts(self):
        """A shielded day with no surrounding meals: only the shield day counts."""
        dates: list[str] = []
        shielded = {"2026-03-15"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 1

    def test_shield_bridges_gap_at_end_of_long_streak(self):
        """Shield at the end of a long streak bridges the gap."""
        # 20-day streak ending on 14th, gap on 15th (today) shielded
        dates = _date_range("2026-03-14", 20)
        shielded = {"2026-03-15"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 21

    def test_shield_does_not_help_if_gap_still_remains(self):
        """Shield on one day doesn't help if another gap remains unbridged."""
        # Logged 12th and 15th. Shield on 13th. Gap on 14th is still open.
        dates = ["2026-03-12", "2026-03-15"]
        shielded = {"2026-03-13"}
        # Streak walks back from 15th: 15th (logged), 14th (not logged, not shielded) -> stops
        assert _calculate_streak(dates, "2026-03-15", shielded) == 1

    def test_shield_empty_set_same_as_none(self):
        """Passing an empty set is the same as None (no shields)."""
        dates = ["2026-03-13", "2026-03-15"]
        assert _calculate_streak(dates, "2026-03-15", set()) == 1
        assert _calculate_streak(dates, "2026-03-15", None) == 1

    def test_consecutive_shields_no_meals_at_all(self):
        """Only shields, no meals: streak equals shield count if consecutive."""
        shielded = {"2026-03-13", "2026-03-14", "2026-03-15"}
        assert _calculate_streak([], "2026-03-15", shielded) == 3

    def test_shield_and_meal_on_same_day(self):
        """Having both a meal and a shield on the same day counts once."""
        dates = ["2026-03-14", "2026-03-15"]
        shielded = {"2026-03-15"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 2


# ===========================================================================
# _count_streak - async tests with real DB (badge_engine.py)
# ===========================================================================


class TestCountStreakAsync:
    """Tests for _count_streak which reads from the DB and handles shield consumption."""

    async def _run_count_streak(self, db_path, user_id=1, today_str="2026-03-15"):
        """Helper to call _count_streak with an open DB connection."""
        from src.badge_engine import _count_streak
        async with aiosqlite.connect(db_path) as conn:
            return await _count_streak(conn, user_id, today_str=today_str)

    async def test_basic_streak_matches_pure_function(self, db):
        """12. Basic streak counting matches _calculate_streak."""
        await _add_user(db)
        # Log meals for 3 consecutive days: Mar 13, 14, 15
        for d in ["2026-03-13", "2026-03-14", "2026-03-15"]:
            await _add_meal(db, logged_at=f"{d} 12:00")

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 3
        assert newly_shielded == []

    async def test_shield_consumption_one_gap(self, db):
        """13. Shield consumption: 1 available shield, 1 gap -> consumed."""
        await _add_user(db)
        # Log on 13th and 15th, gap on 14th
        await _add_meal(db, logged_at="2026-03-13 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")
        # Add 1 unused shield
        await _add_shield(db)

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 3
        assert newly_shielded == ["2026-03-14"]

    async def test_shield_consumption_three_gaps(self, db):
        """14. Shield consumption: 3 available shields, 3 consecutive gaps -> all 3 consumed."""
        await _add_user(db)
        # Log on 11th and 15th, gaps on 12th, 13th, 14th
        await _add_meal(db, logged_at="2026-03-11 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")
        # Add 3 unused shields
        for _ in range(3):
            await _add_shield(db)

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 5
        assert sorted(newly_shielded) == ["2026-03-12", "2026-03-13", "2026-03-14"]

    async def test_no_shields_gap_breaks_streak(self, db):
        """15. Shield consumption: 0 shields, gap -> streak breaks."""
        await _add_user(db)
        # Log on 13th and 15th, gap on 14th, no shields
        await _add_meal(db, logged_at="2026-03-13 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 1
        assert newly_shielded == []

    async def test_already_shielded_dates_not_reconsumed(self, db):
        """16. Already-shielded dates are counted but not re-consumed."""
        await _add_user(db)
        # Log on 13th and 15th, gap on 14th. 14th already has a consumed shield.
        await _add_meal(db, logged_at="2026-03-13 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")
        await _add_shield(db, used_at="2026-03-14", bridged_date="2026-03-14")

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 3
        # No new shields consumed - the existing one already covers the gap
        assert newly_shielded == []

    async def test_empty_today_str_returns_zero(self, db):
        """Empty today_str returns (0, [])."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-15 12:00")

        streak, newly_shielded = await self._run_count_streak(db, today_str="")
        assert streak == 0
        assert newly_shielded == []

    async def test_no_meals_returns_zero(self, db):
        """No meals at all -> streak 0."""
        await _add_user(db)

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 0
        assert newly_shielded == []

    async def test_extra_shields_dont_extend_before_first_meal(self, db):
        """Shields bridge gaps between meals but never before the user's first logged meal."""
        await _add_user(db)
        # 5-day stretch with one gap on the 13th
        await _add_meal(db, logged_at="2026-03-11 12:00")
        await _add_meal(db, logged_at="2026-03-12 12:00")
        # gap on 13th
        await _add_meal(db, logged_at="2026-03-14 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")
        # 2 shields: one bridges 13th, the other can't bridge 10th (before first meal)
        await _add_shield(db)
        await _add_shield(db)

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        # 15(meal) + 14(meal) + 13(shield) + 12(meal) + 11(meal) = 5
        # Mar 10 is before earliest meal (Mar 11), so shield NOT consumed
        assert streak == 5
        assert newly_shielded == ["2026-03-13"]

    async def test_partial_shields_partial_gap(self, db):
        """Fewer shields than gaps -> nothing bridged; preserve shields.

        The earlier behavior here partial-bridged 2 of 4 gap days and
        still reported the streak broken. Users lost shields without
        gaining anything. The all-or-nothing rule preserves them for a
        future short gap they can actually save.
        """
        await _add_user(db)
        # Log on 10th and 15th - 4 days of gap (11th, 12th, 13th, 14th)
        await _add_meal(db, logged_at="2026-03-10 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")
        # Only 2 shields available
        await _add_shield(db)
        await _add_shield(db)

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        # 15th logged, 14th gap (no bridging). Streak = 1, both shields preserved.
        assert streak == 1
        assert newly_shielded == []

    async def test_anchor_on_yesterday(self, db):
        """Streak anchors on yesterday when today has no meal."""
        await _add_user(db)
        await _add_meal(db, logged_at="2026-03-13 12:00")
        await _add_meal(db, logged_at="2026-03-14 12:00")
        # Nothing on 15th (today)

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 2
        assert newly_shielded == []

    async def test_long_streak_async(self, db):
        """Long streak works correctly via the async path."""
        await _add_user(db)
        for d in _date_range("2026-03-15", 35):
            await _add_meal(db, logged_at=f"{d} 12:00")

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 35
        assert newly_shielded == []

    async def test_mix_of_used_and_unused_shields(self, db):
        """Used shields cover old gaps, unused shields cover new gaps."""
        await _add_user(db)
        # Meals on 11th, 13th, 15th - gaps on 12th and 14th
        await _add_meal(db, logged_at="2026-03-11 12:00")
        await _add_meal(db, logged_at="2026-03-13 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")
        # 12th is already shielded (consumed)
        await _add_shield(db, used_at="2026-03-12", bridged_date="2026-03-12")
        # 1 unused shield available for the 14th gap
        await _add_shield(db)

        streak, newly_shielded = await self._run_count_streak(db, today_str="2026-03-15")
        assert streak == 5
        assert newly_shielded == ["2026-03-14"]


# ===========================================================================
# Edge-case tests
# ===========================================================================


class TestCalculateStreakEdgeCases:
    """Additional edge-case tests for _calculate_streak and _count_streak."""

    def test_calculate_streak_empty_today_str_raises(self):
        """_calculate_streak("") raises ValueError because date.fromisoformat
        rejects the empty string - unlike _count_streak which guards with an
        early return of (0, [])."""
        with pytest.raises(ValueError):
            _calculate_streak(["2026-03-15"], "")

    def test_shield_not_on_today_or_yesterday_no_anchor(self):
        """A shield on day-before-yesterday does NOT serve as an anchor.
        Only today and yesterday are valid anchor candidates, so the streak is 0."""
        # Today is March 15. Shield on March 13 (day-before-yesterday).
        # No meals, no shield on 14th or 15th.
        dates: list[str] = []
        shielded = {"2026-03-13"}
        assert _calculate_streak(dates, "2026-03-15", shielded) == 0

    def test_streak_beyond_90_days_pure_function(self):
        """_calculate_streak has no cap - a 100-day streak is computed correctly.

        NOTE: The dashboard endpoint in src/db.py only queries the last 90 days
        of meal_logs for performance, which effectively truncates displayed
        streaks to ~90. This divergence is intentional: the pure function is
        correct for any range, but the DB query limits lookback to keep the
        dashboard fast.
        """
        dates = _date_range("2026-03-15", 100)
        assert _calculate_streak(dates, "2026-03-15") == 100
        # Also verify with shields extending beyond the 90-day mark.
        # 99 consecutive dates = Mar 15 back to Dec 7 (99 days).
        # Bridge Dec 6 (the 100th day, offset 99) with a shield to reach 100.
        dates_with_gap = _date_range("2026-03-15", 99)
        gap_day = (date(2026, 3, 15) - timedelta(days=99)).isoformat()  # Dec 7 - 1 = Dec 6
        shielded = {gap_day}
        assert _calculate_streak(dates_with_gap, "2026-03-15", shielded) == 100


class TestCountStreakIdempotency:
    """Verify _count_streak is a read-only function (doesn't write to DB)."""

    async def _run_count_streak(self, db_path, user_id=1, today_str="2026-03-15"):
        from src.badge_engine import _count_streak
        async with aiosqlite.connect(db_path) as conn:
            return await _count_streak(conn, user_id, today_str=today_str)

    async def test_count_streak_idempotency(self, db):
        """Calling _count_streak twice with the same data returns identical
        newly_shielded lists - it does not consume shields itself (caller does)."""
        await _add_user(db)
        # Log on 13th and 15th, gap on 14th
        await _add_meal(db, logged_at="2026-03-13 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")
        # Add 1 unused shield
        await _add_shield(db)

        streak1, shielded1 = await self._run_count_streak(db, today_str="2026-03-15")
        streak2, shielded2 = await self._run_count_streak(db, today_str="2026-03-15")

        assert streak1 == streak2 == 3
        assert shielded1 == shielded2 == ["2026-03-14"]

        # Verify the shield is still unused in the DB (not consumed by _count_streak)
        async with aiosqlite.connect(db) as conn:
            row = await (await conn.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = 1 AND used_at IS NULL"
            )).fetchone()
            assert row[0] == 1, "Shield should still be unused - _count_streak must not write"


class TestEvaluateBadgesConsumesShields:
    """Integration test: evaluate_badges actually marks shields used in the DB."""

    async def test_evaluate_badges_consumes_shields_in_db(self, db):
        """evaluate_badges with a 'meal_accept' trigger evaluates streak_on_a_roll,
        which calls _count_streak, finds newly_shielded dates, then the caller
        (evaluate_badges) consumes those shields - setting used_at and bridged_date."""
        await _add_user(db)

        # Set up calorie targets (needed for shield-earning logic in evaluate_badges)
        async with aiosqlite.connect(db) as conn:
            await conn.execute(
                "INSERT INTO user_targets (user_id, calories, protein, carbs, fat, set_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (1, 2000, 150, 250, 65, "manual", "2026-03-15 00:00:00"),
            )
            await conn.commit()

        # Log meals on 13th and 15th - gap on 14th
        await _add_meal(db, logged_at="2026-03-13 12:00")
        await _add_meal(db, logged_at="2026-03-15 12:00")

        # Add 1 unused shield
        await _add_shield(db)

        # Verify shield is unused before
        async with aiosqlite.connect(db) as conn:
            row = await (await conn.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = 1 AND used_at IS NULL"
            )).fetchone()
            assert row[0] == 1

        # Run evaluate_badges with meal_accept trigger and today_str context
        from src.badge_engine import evaluate_badges
        await evaluate_badges(db, user_id=1, trigger="meal_accept", context={"today_str": "2026-03-15"})

        # Verify the shield was consumed: used_at set and bridged_date = "2026-03-14"
        async with aiosqlite.connect(db) as conn:
            row = await (await conn.execute(
                "SELECT used_at, bridged_date FROM streak_shields WHERE user_id = 1"
            )).fetchone()
            assert row is not None, "Shield row should exist"
            assert row[0] is not None, "used_at should be set after evaluate_badges"
            assert row[1] == "2026-03-14", "bridged_date should be the gap date (March 14)"

            # No unused shields remain
            unused_row = await (await conn.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = 1 AND used_at IS NULL"
            )).fetchone()
            assert unused_row[0] == 0, "All shields should be consumed"
