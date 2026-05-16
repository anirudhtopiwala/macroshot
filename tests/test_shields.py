"""Tests for streak shield lifecycle - earning, capping, consuming, history."""

import asyncio

import aiosqlite
import pytest
import pytest_asyncio

from src.db import (
    auto_consume_shields_for_streak,
    earn_streak_shield,
    ensure_user,
    get_shield_history,
    get_shield_progress,
    get_shields_used_recently,
    get_streak_shields,
    init_db,
    log_meal,
    set_user_target,
    was_shield_used_recently,
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


USER_ID = 1


async def _add_user(db_path, user_id=USER_ID):
    await ensure_user(db_path, user_id, "alice", "Alice")


async def _insert_shield(db_path, user_id=USER_ID, earned_at="2026-03-01 10:00:00",
                          used_at=None, bridged_date=None):
    """Insert a shield row directly for test setup."""
    from src.db_pool import get_db
    async with get_db(db_path) as conn:
        await conn.execute(
            "INSERT INTO streak_shields (user_id, earned_at, used_at, bridged_date) VALUES (?, ?, ?, ?)",
            (user_id, earned_at, used_at, bridged_date),
        )
        await conn.commit()


async def _get_shield_rows(db_path, user_id=USER_ID):
    """Fetch all shield rows for inspection."""
    from src.db_pool import get_db
    async with get_db(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        rows = await (await conn.execute(
            "SELECT * FROM streak_shields WHERE user_id = ? ORDER BY id",
            (user_id,),
        )).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# earn_streak_shield
# ---------------------------------------------------------------------------

class TestEarnStreakShield:

    @pytest.mark.asyncio
    async def test_earn_first_shield(self, db):
        """Earning a shield when none exist returns True and creates a row."""
        await _add_user(db)
        result = await earn_streak_shield(db, USER_ID)
        assert result is True

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 1
        assert info["total_earned"] == 1

    @pytest.mark.asyncio
    async def test_earn_up_to_three(self, db):
        """Can earn up to 3 unused shields."""
        await _add_user(db)
        for _ in range(3):
            result = await earn_streak_shield(db, USER_ID)
            assert result is True

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 3
        assert info["total_earned"] == 3

    @pytest.mark.asyncio
    async def test_cap_at_three_unused(self, db):
        """Fourth earn attempt returns False when 3 unused shields exist."""
        await _add_user(db)
        for _ in range(3):
            await earn_streak_shield(db, USER_ID)

        result = await earn_streak_shield(db, USER_ID)
        assert result is False

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 3
        assert info["total_earned"] == 3

    @pytest.mark.asyncio
    async def test_earn_after_consuming_one(self, db):
        """After consuming a shield, the slot opens and a new one can be earned."""
        await _add_user(db)
        # Earn 3 shields
        for _ in range(3):
            await earn_streak_shield(db, USER_ID)

        # Consume one by marking used_at
        from src.db_pool import get_db
        async with get_db(db) as conn:
            await conn.execute(
                """UPDATE streak_shields SET used_at = '2026-03-15', bridged_date = '2026-03-14'
                   WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                (USER_ID,),
            )
            await conn.commit()

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 2
        assert info["total_earned"] == 3

        # Now earning should succeed again
        result = await earn_streak_shield(db, USER_ID)
        assert result is True

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 3
        assert info["total_earned"] == 4

    @pytest.mark.asyncio
    async def test_earned_at_is_set(self, db):
        """earned_at is populated with a timestamp on the new row."""
        await _add_user(db)
        await earn_streak_shield(db, USER_ID)
        rows = await _get_shield_rows(db)
        assert len(rows) == 1
        assert rows[0]["earned_at"] is not None
        assert rows[0]["used_at"] is None
        assert rows[0]["bridged_date"] is None


# ---------------------------------------------------------------------------
# get_streak_shields
# ---------------------------------------------------------------------------

class TestGetStreakShields:

    @pytest.mark.asyncio
    async def test_empty_state(self, db):
        """No shields earned returns zeros."""
        await _add_user(db)
        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 0
        assert info["total_earned"] == 0

    @pytest.mark.asyncio
    async def test_all_unused(self, db):
        """All earned shields are available when none consumed."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00")

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 2
        assert info["total_earned"] == 2

    @pytest.mark.asyncio
    async def test_mix_of_used_and_unused(self, db):
        """Counts separate used from unused correctly."""
        await _add_user(db)
        # 2 unused
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00")
        # 1 used
        await _insert_shield(db, earned_at="2026-03-03 10:00:00",
                             used_at="2026-03-05", bridged_date="2026-03-04")

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 2
        assert info["total_earned"] == 3

    @pytest.mark.asyncio
    async def test_all_used(self, db):
        """When all shields are consumed, available is 0 but total is correct."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-05", bridged_date="2026-03-04")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00",
                             used_at="2026-03-06", bridged_date="2026-03-05")

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 0
        assert info["total_earned"] == 2

    @pytest.mark.asyncio
    async def test_per_user_isolation(self, db):
        """Shields for different users are independent."""
        await _add_user(db, user_id=1)
        await _add_user(db, user_id=2)

        await _insert_shield(db, user_id=1, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, user_id=1, earned_at="2026-03-02 10:00:00")
        await _insert_shield(db, user_id=2, earned_at="2026-03-01 10:00:00")

        info1 = await get_streak_shields(db, 1)
        info2 = await get_streak_shields(db, 2)
        assert info1["available"] == 2
        assert info2["available"] == 1


# ---------------------------------------------------------------------------
# was_shield_used_recently
# ---------------------------------------------------------------------------

class TestWasShieldUsedRecently:

    @pytest.mark.asyncio
    async def test_no_shields_at_all(self, db):
        """No shields exist - returns False."""
        await _add_user(db)
        result = await was_shield_used_recently(db, USER_ID, "2026-03-01")
        assert result is False

    @pytest.mark.asyncio
    async def test_only_unused_shields(self, db):
        """Shields exist but none consumed - returns False."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        result = await was_shield_used_recently(db, USER_ID, "2026-03-01")
        assert result is False

    @pytest.mark.asyncio
    async def test_shield_bridged_yesterday(self, db):
        """Shield bridging yesterday - returns True for since_date=yesterday."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-15", bridged_date="2026-03-15")
        result = await was_shield_used_recently(db, USER_ID, "2026-03-15")
        assert result is True

    @pytest.mark.asyncio
    async def test_shield_bridged_old_gap_not_recent(self, db):
        """Shield consumed recently but bridging old gap - returns False."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-15", bridged_date="2026-02-16")
        result = await was_shield_used_recently(db, USER_ID, "2026-03-15")
        assert result is False

    @pytest.mark.asyncio
    async def test_shield_bridged_before_since_date(self, db):
        """Shield bridging date before since_date - returns False."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-10", bridged_date="2026-03-09")
        result = await was_shield_used_recently(db, USER_ID, "2026-03-15")
        assert result is False

    @pytest.mark.asyncio
    async def test_shield_used_after_since_date(self, db):
        """Shield used after since_date - returns True."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-20", bridged_date="2026-03-19")
        result = await was_shield_used_recently(db, USER_ID, "2026-03-15")
        assert result is True

    @pytest.mark.asyncio
    async def test_mixed_used_one_recent(self, db):
        """Multiple shields, only one used recently - returns True."""
        await _add_user(db)
        # Old usage
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-05", bridged_date="2026-03-04")
        # Recent usage
        await _insert_shield(db, earned_at="2026-03-02 10:00:00",
                             used_at="2026-03-20", bridged_date="2026-03-19")
        # Unused
        await _insert_shield(db, earned_at="2026-03-03 10:00:00")

        result = await was_shield_used_recently(db, USER_ID, "2026-03-15")
        assert result is True


# ---------------------------------------------------------------------------
# Shield consumption (used_at and bridged_date)
# ---------------------------------------------------------------------------

class TestShieldConsumption:

    @pytest.mark.asyncio
    async def test_consumption_sets_used_at_to_current_date(self, db):
        """Consuming a shield sets used_at to the current date, not the bridged date."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")

        # Simulate consumption as done in badge_engine.py evaluate_badges
        consumption_date = "2026-03-20"
        gap_date = "2026-03-18"
        from src.db_pool import get_db
        async with get_db(db) as conn:
            await conn.execute(
                """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                   WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                (consumption_date, gap_date, USER_ID),
            )
            await conn.commit()

        rows = await _get_shield_rows(db)
        assert len(rows) == 1
        assert rows[0]["used_at"] == consumption_date
        assert rows[0]["bridged_date"] == gap_date
        # used_at != bridged_date (used_at = today, bridged_date = gap day)
        assert rows[0]["used_at"] != rows[0]["bridged_date"]

    @pytest.mark.asyncio
    async def test_consumption_sets_bridged_date_to_gap(self, db):
        """bridged_date records the actual missed day, not when the shield was consumed."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")

        gap_date = "2026-03-14"
        from src.db_pool import get_db
        async with get_db(db) as conn:
            await conn.execute(
                """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                   WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                ("2026-03-20", gap_date, USER_ID),
            )
            await conn.commit()

        rows = await _get_shield_rows(db)
        assert rows[0]["bridged_date"] == gap_date

    @pytest.mark.asyncio
    async def test_multiple_shields_consumed_for_consecutive_gaps(self, db):
        """Multiple shields consumed for consecutive gap days get distinct bridged_dates."""
        await _add_user(db)
        # Earn 3 shields
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00")
        await _insert_shield(db, earned_at="2026-03-03 10:00:00")

        # Consume 2 shields for 2 consecutive gap days
        consumption_date = "2026-03-20"
        gap_dates = ["2026-03-17", "2026-03-18"]

        from src.db_pool import get_db
        for gap_date in gap_dates:
            async with get_db(db) as conn:
                await conn.execute(
                    """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                       WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                    (consumption_date, gap_date, USER_ID),
                )
                await conn.commit()

        rows = await _get_shield_rows(db)
        used_rows = [r for r in rows if r["used_at"] is not None]
        unused_rows = [r for r in rows if r["used_at"] is None]

        assert len(used_rows) == 2
        assert len(unused_rows) == 1

        # Both used_at should be the consumption date
        for r in used_rows:
            assert r["used_at"] == consumption_date

        # Each should have a distinct bridged_date
        bridged = sorted([r["bridged_date"] for r in used_rows])
        assert bridged == sorted(gap_dates)

    @pytest.mark.asyncio
    async def test_consumption_picks_oldest_unused_shield(self, db):
        """LIMIT 1 picks the shield with the lowest id (earliest earned)."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-05 10:00:00")
        await _insert_shield(db, earned_at="2026-03-10 10:00:00")

        from src.db_pool import get_db
        async with get_db(db) as conn:
            await conn.execute(
                """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                   WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                ("2026-03-20", "2026-03-19", USER_ID),
            )
            await conn.commit()

        rows = await _get_shield_rows(db)
        # The first-earned shield (lowest id) should be the consumed one
        assert rows[0]["used_at"] == "2026-03-20"
        assert rows[0]["earned_at"] == "2026-03-01 10:00:00"
        # Others remain unused
        assert rows[1]["used_at"] is None
        assert rows[2]["used_at"] is None

    @pytest.mark.asyncio
    async def test_consumption_reduces_available_count(self, db):
        """After consumption, get_streak_shields shows reduced available count."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00")

        info_before = await get_streak_shields(db, USER_ID)
        assert info_before["available"] == 2

        from src.db_pool import get_db
        async with get_db(db) as conn:
            await conn.execute(
                """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                   WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                ("2026-03-20", "2026-03-19", USER_ID),
            )
            await conn.commit()

        info_after = await get_streak_shields(db, USER_ID)
        assert info_after["available"] == 1
        assert info_after["total_earned"] == 2  # total unchanged


# ---------------------------------------------------------------------------
# get_shield_history
# ---------------------------------------------------------------------------

class TestGetShieldHistory:

    @pytest.mark.asyncio
    async def test_empty_history(self, db):
        """No shields - empty list."""
        await _add_user(db)
        history = await get_shield_history(db, USER_ID)
        assert history == []

    @pytest.mark.asyncio
    async def test_history_returns_correct_fields(self, db):
        """History entries have earned_at, used_at, bridged_date fields."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00",
                             used_at="2026-03-10", bridged_date="2026-03-09")

        history = await get_shield_history(db, USER_ID)
        assert len(history) == 2
        for entry in history:
            assert "earned_at" in entry
            assert "used_at" in entry
            assert "bridged_date" in entry

    @pytest.mark.asyncio
    async def test_history_ordered_by_earned_at_desc(self, db):
        """History is ordered by earned_at DESC (most recent first)."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-05 10:00:00")
        await _insert_shield(db, earned_at="2026-03-03 10:00:00")

        history = await get_shield_history(db, USER_ID)
        assert len(history) == 3
        assert history[0]["earned_at"] == "2026-03-05 10:00:00"
        assert history[1]["earned_at"] == "2026-03-03 10:00:00"
        assert history[2]["earned_at"] == "2026-03-01 10:00:00"

    @pytest.mark.asyncio
    async def test_history_shows_used_and_unused(self, db):
        """History includes both consumed and unconsumed shields."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-05", bridged_date="2026-03-04")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00")

        history = await get_shield_history(db, USER_ID)
        used = [h for h in history if h["used_at"] is not None]
        unused = [h for h in history if h["used_at"] is None]
        assert len(used) == 1
        assert len(unused) == 1
        assert used[0]["bridged_date"] == "2026-03-04"
        assert unused[0]["bridged_date"] is None

    @pytest.mark.asyncio
    async def test_history_limited_to_10(self, db):
        """History returns at most 10 entries."""
        await _add_user(db)
        for i in range(15):
            await _insert_shield(db, earned_at=f"2026-03-{i+1:02d} 10:00:00")

        history = await get_shield_history(db, USER_ID)
        assert len(history) == 10


# ---------------------------------------------------------------------------
# get_shields_used_recently
# ---------------------------------------------------------------------------

class TestGetShieldsUsedRecently:

    @pytest.mark.asyncio
    async def test_no_recent_usage(self, db):
        """No shields used recently - empty list."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-05", bridged_date="2026-03-04")
        result = await get_shields_used_recently(db, USER_ID, "2026-03-10")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_bridged_dates(self, db):
        """Returns bridged_date values for recently consumed shields."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-15", bridged_date="2026-03-13")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00",
                             used_at="2026-03-15", bridged_date="2026-03-14")
        # Old usage - should not appear
        await _insert_shield(db, earned_at="2026-03-03 10:00:00",
                             used_at="2026-03-05", bridged_date="2026-03-04")

        result = await get_shields_used_recently(db, USER_ID, "2026-03-10")
        assert len(result) == 2
        assert "2026-03-13" in result
        assert "2026-03-14" in result

    @pytest.mark.asyncio
    async def test_excludes_shields_without_bridged_date(self, db):
        """Shields with used_at but NULL bridged_date are excluded."""
        await _add_user(db)
        # Legacy shield: used but no bridged_date
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-15", bridged_date=None)
        # Proper shield with bridged_date
        await _insert_shield(db, earned_at="2026-03-02 10:00:00",
                             used_at="2026-03-15", bridged_date="2026-03-14")

        result = await get_shields_used_recently(db, USER_ID, "2026-03-10")
        assert len(result) == 1
        assert result[0] == "2026-03-14"


# ---------------------------------------------------------------------------
# Integration: _count_streak with shield bridging (badge_engine)
# ---------------------------------------------------------------------------

class TestCountStreakWithShields:
    """Test the _count_streak function from badge_engine with shield consumption."""

    @pytest.mark.asyncio
    async def test_streak_bridges_gap_with_shield(self, db):
        """Shield bridges a 1-day gap, extending the streak."""
        from src.badge_engine import _count_streak
        from src.db import log_meal
        from src.db_pool import get_db

        await _add_user(db)
        # Log meals on Mar 18, 19, skip 20, log on 21
        await log_meal(db, USER_ID, "2026-03-18 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await log_meal(db, USER_ID, "2026-03-19 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        # Gap on Mar 20
        await log_meal(db, USER_ID, "2026-03-21 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")

        # Add an unused shield
        await _insert_shield(db, earned_at="2026-03-15 10:00:00")

        async with get_db(db) as conn:
            streak, newly_shielded = await _count_streak(conn, USER_ID, today_str="2026-03-21")

        # Shield should bridge Mar 20, giving a 4-day streak (18, 19, 20[shielded], 21)
        assert streak == 4
        assert newly_shielded == ["2026-03-20"]

    @pytest.mark.asyncio
    async def test_streak_no_shield_available(self, db):
        """Without shields, gap breaks the streak."""
        from src.badge_engine import _count_streak
        from src.db import log_meal
        from src.db_pool import get_db

        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-18 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await log_meal(db, USER_ID, "2026-03-19 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        # Gap on Mar 20
        await log_meal(db, USER_ID, "2026-03-21 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")

        async with get_db(db) as conn:
            streak, newly_shielded = await _count_streak(conn, USER_ID, today_str="2026-03-21")

        # No shield - streak is just 1 (only Mar 21)
        assert streak == 1
        assert newly_shielded == []

    @pytest.mark.asyncio
    async def test_streak_already_shielded_day_not_reconsumed(self, db):
        """A previously shielded day is counted without consuming another shield for it.

        The unused shield may still be consumed for a different gap day further back.
        To isolate the test, we ensure there are no further gaps to bridge.
        """
        from src.badge_engine import _count_streak
        from src.db import log_meal
        from src.db_pool import get_db

        await _add_user(db)
        # Log meals on Mar 19, skip Mar 20, log on Mar 21
        # No meals before Mar 19 - so the unused shield has no further gap to bridge
        await log_meal(db, USER_ID, "2026-03-19 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        # Gap on Mar 20 - already shielded
        await log_meal(db, USER_ID, "2026-03-21 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")

        # Insert an already-consumed shield for Mar 20
        await _insert_shield(db, earned_at="2026-03-15 10:00:00",
                             used_at="2026-03-21", bridged_date="2026-03-20")
        # Insert another unused shield
        await _insert_shield(db, earned_at="2026-03-16 10:00:00")

        async with get_db(db) as conn:
            streak, newly_shielded = await _count_streak(conn, USER_ID, today_str="2026-03-21")

        # Walk: 21 (logged), 20 (already shielded), 19 (logged) = 3
        # Mar 18 is before the earliest logged date (Mar 19) - shields never bridge
        # dates before the user started logging, so the streak stops at 3.
        assert streak == 3
        assert newly_shielded == []  # No new shields consumed

        # The unused shield was proposed for consumption (Mar 18)
        # available count is checked before _count_streak modifies it in memory
        info = await get_streak_shields(db, USER_ID)
        # Still 1 available in DB (consumption happens in evaluate_badges, not _count_streak)
        assert info["available"] == 1

    @pytest.mark.asyncio
    async def test_two_consecutive_gaps_use_two_shields(self, db):
        """Two consecutive gap days consume two shields."""
        from src.badge_engine import _count_streak
        from src.db import log_meal
        from src.db_pool import get_db

        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-17 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        # Gap on Mar 18 and Mar 19
        await log_meal(db, USER_ID, "2026-03-20 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")

        # Add 2 unused shields
        await _insert_shield(db, earned_at="2026-03-10 10:00:00")
        await _insert_shield(db, earned_at="2026-03-11 10:00:00")

        async with get_db(db) as conn:
            streak, newly_shielded = await _count_streak(conn, USER_ID, today_str="2026-03-20")

        # Both gaps bridged: 17, 18[shielded], 19[shielded], 20 = 4
        assert streak == 4
        assert sorted(newly_shielded) == ["2026-03-18", "2026-03-19"]

    @pytest.mark.asyncio
    async def test_three_gaps_but_only_two_shields(self, db):
        """Three gaps and only two shields — the streak can't actually be
        recovered, so don't burn shields for a partial result.

        Earlier behavior burned 2 shields to bridge 19/18 and reported
        streak=3 anyway. That left users out of pocket without restoring
        the prior streak. The "all-or-nothing" rule preserves shields
        for a future short gap they can actually save.
        """
        from src.badge_engine import _count_streak
        from src.db import log_meal
        from src.db_pool import get_db

        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-16 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await log_meal(db, USER_ID, "2026-03-20 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")

        await _insert_shield(db, earned_at="2026-03-10 10:00:00")
        await _insert_shield(db, earned_at="2026-03-11 10:00:00")

        async with get_db(db) as conn:
            streak, newly_shielded = await _count_streak(conn, USER_ID, today_str="2026-03-20")

        # Walk: 20 (logged), 19 (gap, no bridge) -> break. Streak = 1.
        # Both shields untouched.
        assert streak == 1
        assert newly_shielded == []
        unused = [r for r in await _get_shield_rows(db) if r["used_at"] is None]
        assert len(unused) == 2


# ---------------------------------------------------------------------------
# Edge case: consume when zero shields available
# ---------------------------------------------------------------------------

class TestConsumeWhenZeroShieldsAvailable:

    @pytest.mark.asyncio
    async def test_consume_when_zero_shields_available(self, db):
        """Raw consumption SQL is a no-op when no unused shields exist."""
        await _add_user(db)
        # No shields inserted at all - attempt consumption
        from src.db_pool import get_db
        async with get_db(db) as conn:
            cursor = await conn.execute(
                """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                   WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                ("2026-03-20", "2026-03-19", USER_ID),
            )
            assert cursor.rowcount == 0  # no rows affected
            await conn.commit()

        rows = await _get_shield_rows(db)
        assert rows == []

        # Also verify with all shields already consumed
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-05", bridged_date="2026-03-04")

        async with get_db(db) as conn:
            cursor = await conn.execute(
                """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                   WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                ("2026-03-20", "2026-03-19", USER_ID),
            )
            assert cursor.rowcount == 0  # still no rows affected
            await conn.commit()

        # The already-consumed shield is unchanged
        rows = await _get_shield_rows(db)
        assert len(rows) == 1
        assert rows[0]["used_at"] == "2026-03-05"
        assert rows[0]["bridged_date"] == "2026-03-04"


# ---------------------------------------------------------------------------
# get_shield_progress
# ---------------------------------------------------------------------------

class TestGetShieldProgress:

    @pytest.mark.asyncio
    async def test_at_max_shields(self, db):
        """When 3 unused shields exist, days_until_next is -1 (at max)."""
        await _add_user(db)
        # Need targets to make on_target_days meaningful
        await set_user_target(db, USER_ID, calories=2000, protein=150, carbs=200, fat=70)
        # Earn 3 shields directly
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00")
        await _insert_shield(db, earned_at="2026-03-03 10:00:00")

        result = await get_shield_progress(db, USER_ID)
        assert result["days_until_next"] == -1  # at max

    @pytest.mark.asyncio
    async def test_partially_earned(self, db):
        """One on-target day ending at "today" -> 2 more days needed (active run = 1)."""
        await _add_user(db)
        await set_user_target(db, USER_ID, calories=2000, protein=150, carbs=200, fat=70)
        await log_meal(db, USER_ID, "2026-03-01 12:00", "Lunch", "", 2000, 150, 200, 70, "Gemini")

        result = await get_shield_progress(db, USER_ID, today_str="2026-03-01")
        assert result["on_target_days"] == 1
        assert result["shields_earned_total"] == 0
        assert result["days_until_next"] == 2  # active run = 1, need 2 more

    @pytest.mark.asyncio
    async def test_broken_run_resets_progress(self, db):
        """Old isolated on-target day with no recent logging -> days_until_next is 3."""
        await _add_user(db)
        await set_user_target(db, USER_ID, calories=2000, protein=150, carbs=200, fat=70)
        await log_meal(db, USER_ID, "2026-03-01 12:00", "Lunch", "", 2000, 150, 200, 70, "Gemini")

        # "Today" is far past the isolated log, so the active run has ended.
        result = await get_shield_progress(db, USER_ID, today_str="2026-04-01")
        assert result["on_target_days"] == 1
        assert result["shields_earned_total"] == 0
        assert result["days_until_next"] == 3

    @pytest.mark.asyncio
    async def test_capped_with_extra_on_target_days(self, db):
        """3 shields + on-target days beyond what's needed -> days_until_next is -1."""
        await _add_user(db)
        await set_user_target(db, USER_ID, calories=2000, protein=150, carbs=200, fat=70)
        # Insert 3 unused shields (total_earned = 3)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00")
        await _insert_shield(db, earned_at="2026-03-02 10:00:00")
        await _insert_shield(db, earned_at="2026-03-03 10:00:00")
        # Log enough on-target days to deserve more shields beyond the 3 earned
        for i in range(12):  # 12 consecutive on-target days -> 4 runs of 3 -> 4 shields deserved
            await log_meal(db, USER_ID, f"2026-03-{i+1:02d} 12:00", "Lunch", "",
                           2000, 150, 200, 70, "Gemini")

        result = await get_shield_progress(db, USER_ID, today_str="2026-03-12")
        assert result["on_target_days"] == 12
        assert result["shields_earned_total"] == 3
        assert result["days_until_next"] == -1  # at max (3 unused)


# ---------------------------------------------------------------------------
# evaluate_badges shield consumption integration
# ---------------------------------------------------------------------------

class TestEvaluateBadgesShieldConsumptionIntegration:

    @pytest.mark.asyncio
    async def test_used_at_is_current_date_not_bridged_date(self, db):
        """evaluate_badges sets used_at to current date (now_str), not bridged_date."""
        from src.badge_engine import evaluate_badges
        from datetime import date, timezone, datetime as dt

        await _add_user(db)
        # Set up targets so shield earning logic works
        await set_user_target(db, USER_ID, calories=2000, protein=150, carbs=200, fat=70)
        # Insert an unused shield
        await _insert_shield(db, earned_at="2026-03-10 10:00:00")

        # Log meals: yesterday and day-before-yesterday relative to "today_str",
        # skip a day, then log on today_str so _count_streak finds a 1-day gap.
        # We use today_str = the real current date so now_str matches.
        today = dt.now(timezone.utc).strftime("%Y-%m-%d")
        today_date = date.fromisoformat(today)
        day_1 = (today_date - __import__("datetime").timedelta(days=3)).isoformat()
        day_2 = (today_date - __import__("datetime").timedelta(days=2)).isoformat()
        # Gap on (today - 1 day)
        gap_date = (today_date - __import__("datetime").timedelta(days=1)).isoformat()

        await log_meal(db, USER_ID, f"{day_1} 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await log_meal(db, USER_ID, f"{day_2} 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await log_meal(db, USER_ID, f"{today} 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")

        await evaluate_badges(db, USER_ID, "meal_accept",
                              context={"today_str": today})

        # Verify used_at is the current date (today), not the bridged date (gap_date)
        rows = await _get_shield_rows(db)
        consumed = [r for r in rows if r["used_at"] is not None]
        assert len(consumed) >= 1
        for r in consumed:
            # used_at should be today (current date), set by datetime.now() in evaluate_badges
            assert r["used_at"] == today
            # bridged_date is the gap day (yesterday)
            assert r["bridged_date"] == gap_date
            # They must differ (used_at = today, bridged_date = yesterday)
            assert r["used_at"] != r["bridged_date"]


# ---------------------------------------------------------------------------
# Full lifecycle: earn -> consume -> re-earn -> cap
# ---------------------------------------------------------------------------

class TestFullLifecycleEarnConsumeReearnCap:

    @pytest.mark.asyncio
    async def test_earn_consume_reearn_cap(self, db):
        """Earn 3, consume all 3, earn 3 more, verify cap still holds on 4th attempt.
        Check total_earned = 6, available = 3."""
        await _add_user(db)

        # Phase 1: Earn 3
        for _ in range(3):
            result = await earn_streak_shield(db, USER_ID)
            assert result is True
        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 3
        assert info["total_earned"] == 3

        # Phase 2: Consume all 3
        from src.db_pool import get_db
        for i in range(3):
            async with get_db(db) as conn:
                await conn.execute(
                    """UPDATE streak_shields SET used_at = ?, bridged_date = ?
                       WHERE id = (SELECT id FROM streak_shields WHERE user_id = ? AND used_at IS NULL LIMIT 1)""",
                    (f"2026-03-{20+i:02d}", f"2026-03-{15+i:02d}", USER_ID),
                )
                await conn.commit()

        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 0
        assert info["total_earned"] == 3

        # Phase 3: Earn 3 more
        for _ in range(3):
            result = await earn_streak_shield(db, USER_ID)
            assert result is True
        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 3
        assert info["total_earned"] == 6

        # Phase 4: Cap holds - 4th attempt fails
        result = await earn_streak_shield(db, USER_ID)
        assert result is False
        info = await get_streak_shields(db, USER_ID)
        assert info["available"] == 3
        assert info["total_earned"] == 6


# ---------------------------------------------------------------------------
# bridged_date backfill migration
# ---------------------------------------------------------------------------

class TestBridgedDateBackfillMigration:

    @pytest.mark.asyncio
    async def test_backfill_sets_bridged_date_to_used_at(self, db):
        """Legacy shields with used_at set but bridged_date NULL get backfilled."""
        await _add_user(db)
        # Insert shields with used_at but NULL bridged_date (pre-migration state)
        await _insert_shield(db, earned_at="2026-03-01 10:00:00",
                             used_at="2026-03-05", bridged_date=None)
        await _insert_shield(db, earned_at="2026-03-02 10:00:00",
                             used_at="2026-03-10", bridged_date=None)
        # One unused shield (should not be touched)
        await _insert_shield(db, earned_at="2026-03-03 10:00:00")
        # One with bridged_date already set (should not change)
        await _insert_shield(db, earned_at="2026-03-04 10:00:00",
                             used_at="2026-03-15", bridged_date="2026-03-14")

        # Run the migration SQL (from db.py line 455)
        from src.db_pool import get_db
        async with get_db(db) as conn:
            await conn.execute(
                "UPDATE streak_shields SET bridged_date = used_at WHERE used_at IS NOT NULL AND bridged_date IS NULL"
            )
            await conn.commit()

        rows = await _get_shield_rows(db)
        # Shield 1: was NULL bridged_date, now backfilled to used_at
        assert rows[0]["used_at"] == "2026-03-05"
        assert rows[0]["bridged_date"] == "2026-03-05"
        # Shield 2: was NULL bridged_date, now backfilled to used_at
        assert rows[1]["used_at"] == "2026-03-10"
        assert rows[1]["bridged_date"] == "2026-03-10"
        # Shield 3: unused, both still NULL
        assert rows[2]["used_at"] is None
        assert rows[2]["bridged_date"] is None
        # Shield 4: already had bridged_date, unchanged
        assert rows[3]["used_at"] == "2026-03-15"
        assert rows[3]["bridged_date"] == "2026-03-14"


# ---------------------------------------------------------------------------
# _count_streak with empty today_str
# ---------------------------------------------------------------------------

class TestCountStreakEmptyTodayStr:

    @pytest.mark.asyncio
    async def test_returns_zero_when_today_str_empty(self, db):
        """_count_streak returns (0, []) when today_str is empty."""
        from src.badge_engine import _count_streak
        from src.db_pool import get_db

        await _add_user(db)
        # Log a meal so there IS data, but today_str is empty
        await log_meal(db, USER_ID, "2026-03-21 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await _insert_shield(db, earned_at="2026-03-15 10:00:00")

        async with get_db(db) as conn:
            streak, newly_shielded = await _count_streak(conn, USER_ID, today_str="")

        assert streak == 0
        assert newly_shielded == []


# ---------------------------------------------------------------------------
# auto_consume_shields_for_streak — eager shield bridging on dashboard read
# ---------------------------------------------------------------------------

class TestAutoConsumeShieldsForStreak:
    """The dashboard's _calculate_streak only reads already-bridged shields,
    so before this function existed a user who missed a day saw streak=0 with
    shields sitting unused — they only consumed on meal_accept.
    """

    @pytest.mark.asyncio
    async def test_bridges_single_gap_day_with_one_shield(self, db):
        """User logged Mon, missed Tue, opens app Wed without logging."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-23 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert bridged == ["2026-03-24"]
        rows = await _get_shield_rows(db)
        assert rows[0]["bridged_date"] == "2026-03-24"
        assert rows[0]["used_at"] is not None

    @pytest.mark.asyncio
    async def test_no_op_when_logged_today(self, db):
        """User logged today — no gap, no shield consumed."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-25 09:00", "Breakfast", "", 400, 15, 50, 15, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert bridged == []
        rows = await _get_shield_rows(db)
        assert rows[0]["used_at"] is None

    @pytest.mark.asyncio
    async def test_no_op_when_logged_yesterday(self, db):
        """User logged yesterday — anchor is yesterday, no gap to bridge."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-24 18:00", "Dinner", "", 600, 25, 60, 22, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert bridged == []
        rows = await _get_shield_rows(db)
        assert rows[0]["used_at"] is None

    @pytest.mark.asyncio
    async def test_bridges_two_gap_days_with_two_shields(self, db):
        """User missed Tue + Wed, opens Thu without logging — needs 2 shields."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-22 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")
        await _insert_shield(db, earned_at="2026-03-21 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert sorted(bridged) == ["2026-03-23", "2026-03-24"]
        rows = await _get_shield_rows(db)
        assert all(r["used_at"] is not None for r in rows)
        assert {r["bridged_date"] for r in rows} == {"2026-03-23", "2026-03-24"}

    @pytest.mark.asyncio
    async def test_skips_when_not_enough_shields(self, db):
        """2-day gap but only 1 shield — preserve it, don't waste on partial bridge."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-22 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert bridged == []
        rows = await _get_shield_rows(db)
        assert rows[0]["used_at"] is None

    @pytest.mark.asyncio
    async def test_does_not_bridge_today(self, db):
        """Today is left for the user to log themselves; bridge only past days."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-23 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")
        await _insert_shield(db, earned_at="2026-03-21 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        # Bridges 2026-03-24 only — not 2026-03-25 (today)
        assert bridged == ["2026-03-24"]
        unused = [r for r in await _get_shield_rows(db) if r["used_at"] is None]
        assert len(unused) == 1

    @pytest.mark.asyncio
    async def test_caps_lookback_at_seven_days(self, db):
        """Gap of 10 days — too long to bridge, save shields. Prevents the prod
        bug where one user lost 7 shields to a months-old gap."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-15 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        for d in ("2026-03-20", "2026-03-21", "2026-03-22"):
            await _insert_shield(db, earned_at=f"{d} 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        # Last log within 7-day window is None — nothing to bridge
        assert bridged == []
        rows = await _get_shield_rows(db)
        assert all(r["used_at"] is None for r in rows)

    @pytest.mark.asyncio
    async def test_idempotent_when_gap_already_bridged(self, db):
        """Calling twice doesn't double-consume shields for the same date."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-23 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")
        await _insert_shield(db, earned_at="2026-03-21 10:00:00")

        first = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")
        second = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert first == ["2026-03-24"]
        assert second == []
        unused = [r for r in await _get_shield_rows(db) if r["used_at"] is None]
        assert len(unused) == 1

    @pytest.mark.asyncio
    async def test_no_shields_no_op(self, db):
        """User with no shields — no bridging, no error."""
        await _add_user(db)
        await log_meal(db, USER_ID, "2026-03-23 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert bridged == []

    @pytest.mark.asyncio
    async def test_no_logs_no_op(self, db):
        """Brand-new user, no logs — nothing to protect."""
        await _add_user(db)
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")

        bridged = await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")

        assert bridged == []

    @pytest.mark.asyncio
    async def test_streak_reads_correctly_after_auto_bridge(self, db):
        """End-to-end: after auto-bridge, _calculate_streak should report
        the protected streak (the actual user-facing bug we're fixing)."""
        from src.db import _calculate_streak, get_db
        import aiosqlite as _aio

        await _add_user(db)
        # Streak: 2026-03-21, 22, 23. Missed 24. Today = 25, no log yet.
        for d in ("2026-03-21 12:00", "2026-03-22 12:00", "2026-03-23 12:00"):
            await log_meal(db, USER_ID, d, "Lunch", "", 500, 20, 50, 20, "Gemini")
        await _insert_shield(db, earned_at="2026-03-20 10:00:00")

        # Before auto-bridge: dashboard would see streak=0
        async with get_db(db) as conn:
            conn.row_factory = _aio.Row
            rows = await (await conn.execute(
                "SELECT DISTINCT substr(logged_at, 1, 10) AS d FROM meal_logs WHERE user_id = ?",
                (USER_ID,),
            )).fetchall()
            sorted_dates = sorted(r["d"] for r in rows)
        assert _calculate_streak(sorted_dates, "2026-03-25", set()) == 0

        # After auto-bridge: dashboard sees streak=4 (bridged Mar 24, anchor=Mar 24)
        await auto_consume_shields_for_streak(db, USER_ID, "2026-03-25")
        async with get_db(db) as conn:
            shield_rows = await (await conn.execute(
                "SELECT bridged_date FROM streak_shields WHERE user_id = ? AND bridged_date IS NOT NULL",
                (USER_ID,),
            )).fetchall()
            shielded = {r[0] for r in shield_rows}
        assert _calculate_streak(sorted_dates, "2026-03-25", shielded) == 4


class TestCountStreakLookbackCap:
    """The badge engine's _count_streak previously walked back without limit,
    silently burning newly earned shields on months-old gaps. Cap is 7 days."""

    @pytest.mark.asyncio
    async def test_does_not_burn_shields_on_ancient_gap(self, db):
        """User logged Jan, vanished, returned Mar — new shields shouldn't
        bridge the 2-month absence (this is the prod bug from the audit)."""
        from src.badge_engine import _count_streak
        from src.db_pool import get_db

        await _add_user(db)
        # Logged Jan 1, then nothing until Mar 22-25
        await log_meal(db, USER_ID, "2026-01-01 12:00", "Lunch", "", 500, 20, 50, 20, "Gemini")
        for d in ("2026-03-22 12:00", "2026-03-23 12:00", "2026-03-24 12:00", "2026-03-25 12:00"):
            await log_meal(db, USER_ID, d, "Lunch", "", 500, 20, 50, 20, "Gemini")
        # 3 unused shields — pre-fix, they would all be burned bridging Jan→Mar
        for d in ("2026-03-23", "2026-03-24", "2026-03-25"):
            await _insert_shield(db, earned_at=f"{d} 10:00:00")

        async with get_db(db) as conn:
            streak, newly_shielded = await _count_streak(conn, USER_ID, today_str="2026-03-25")

        # Streak of 4 (Mar 22-25), no shields wasted on ancient gap
        assert streak == 4
        assert newly_shielded == []
