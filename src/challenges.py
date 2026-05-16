"""Daily and weekly challenge definitions + evaluation."""

import hashlib
from datetime import date, datetime, timedelta

import aiosqlite

from src.db_pool import get_db


def _next_day_prefix(day_str: str) -> str:
    """Return YYYY-MM-DD for day_str + 1 day (for half-open range queries)."""
    d = datetime.strptime(day_str, "%Y-%m-%d").date()
    return (d + timedelta(days=1)).strftime("%Y-%m-%d")


# ── Challenge Pool ────────────────────────────────────────────────────────────

DAILY_CHALLENGES = [
    {
        "id": "protein_30",
        "name": "Protein Push",
        "description": "Log a meal with 30g+ protein",
        "icon": "\U0001f4aa",
        "target": 1,
    },
    {
        "id": "three_meals",
        "name": "Meal Machine",
        "description": "Log 3 meals today",
        "icon": "\U0001f374",
        "target": 3,
    },
    {
        "id": "photo_log",
        "name": "Photo Day",
        "description": "Log a meal with a photo",
        "icon": "\U0001f4f8",
        "target": 1,
    },
    {
        "id": "balanced_meal",
        "name": "Balance",
        "description": "Log a meal with P/C/F all > 10g",
        "icon": "\u2696\ufe0f",
        "target": 1,
    },
    {
        "id": "four_meals",
        "name": "Full House",
        "description": "Log 4 meals today",
        "icon": "\U0001f3e0",
        "target": 4,
    },
    {
        "id": "early_log",
        "name": "Early Bird",
        "description": "Log your first meal before 10 AM",
        "icon": "\U0001f305",
        "target": 1,
    },
    {
        "id": "under_target",
        "name": "On Target",
        "description": "Stay within your calorie target today",
        "icon": "\U0001f3af",
        "target": 1,
    },
]

WEEKLY_CHALLENGES = [
    {
        "id": "week_target_5",
        "name": "Target Week",
        "description": "Hit calorie target 5 out of 7 days",
        "icon": "\U0001f3af",
        "target": 5,
    },
    {
        "id": "week_log_20",
        "name": "Logging Machine",
        "description": "Log 20 meals this week",
        "icon": "\U0001f374",
        "target": 20,
    },
    {
        "id": "week_every_day",
        "name": "Perfect Attendance",
        "description": "Log at least 1 meal every day this week",
        "icon": "\u2b50",
        "target": 7,
    },
    {
        "id": "week_photos_5",
        "name": "Paparazzi",
        "description": "Log 5 meals with photos this week",
        "icon": "\U0001f4f8",
        "target": 5,
    },
    {
        "id": "week_variety_7",
        "name": "Explorer",
        "description": "Log 7 different meal names this week",
        "icon": "\U0001f30d",
        "target": 7,
    },
]


def _pick_challenge(user_id: int, date_str: str, pool: list[dict]) -> dict:
    """Deterministic challenge selection based on date (same for all users so they can compare)."""
    seed = hashlib.md5(date_str.encode()).hexdigest()
    idx = int(seed, 16) % len(pool)
    return pool[idx]


def _week_start(d: date) -> str:
    """Return Monday of the week containing d, as YYYY-MM-DD."""
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def get_daily_challenge(user_id: int, today_str: str) -> dict:
    """Return today's challenge for a user."""
    challenge = _pick_challenge(user_id, today_str, DAILY_CHALLENGES)
    return {**challenge, "period": today_str, "type": "daily"}


def get_weekly_challenge(user_id: int, today_str: str) -> dict:
    """Return this week's challenge for a user."""
    week = _week_start(date.fromisoformat(today_str))
    challenge = _pick_challenge(user_id, week, WEEKLY_CHALLENGES)
    return {**challenge, "period": week, "type": "weekly"}


# ── Progress Evaluation ───────────────────────────────────────────────────────

async def evaluate_daily_progress(
    db: aiosqlite.Connection, user_id: int, challenge_id: str, today_str: str
) -> int:
    """Compute current progress for a daily challenge.

    All day filters here use a prefix range (logged_at >= today AND < next)
    instead of substr(logged_at, 1, 10) = today so SQLite can use the
    composite index idx_meal_logs_user_logged(user_id, logged_at).
    Wrapping logged_at in substr() defeats that index and forces a scan
    of every row for the user - which on e2-micro under fsync pressure
    is what produced the 2026-04-14 disk-I/O-error cluster on this code
    path.
    """
    # Lexicographic next-day prefix: "2026-04-14" → "2026-04-15". The
    # string form makes range scans on logged_at's stored TEXT value
    # index-friendly without a tz-aware datetime round-trip.
    next_str = _next_day_prefix(today_str)

    if challenge_id == "protein_30":
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? "
            "AND logged_at >= ? AND logged_at < ? AND protein >= 30",
            (user_id, today_str, next_str),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "three_meals" or challenge_id == "four_meals":
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? "
            "AND logged_at >= ? AND logged_at < ?",
            (user_id, today_str, next_str),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "photo_log":
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? "
            "AND logged_at >= ? AND logged_at < ? "
            "AND image_path IS NOT NULL AND image_path != ''",
            (user_id, today_str, next_str),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "balanced_meal":
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? "
            "AND logged_at >= ? AND logged_at < ? "
            "AND protein > 10 AND carbs > 10 AND fat > 10",
            (user_id, today_str, next_str),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "early_log":
        # Hour filter still needs substr on the time portion - but the
        # day-range gate now uses the index, narrowing the substr scan
        # to at most one day's worth of rows per user.
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? "
            "AND logged_at >= ? AND logged_at < ? "
            "AND CAST(substr(logged_at,12,2) AS INTEGER) < 10",
            (user_id, today_str, next_str),
        )).fetchone()
        return min(row[0] if row else 0, 1)

    if challenge_id == "under_target":
        row = await (await db.execute(
            """SELECT CASE WHEN SUM(ml.calories) BETWEEN ut.calories * 0.9 AND ut.calories * 1.1 THEN 1 ELSE 0 END
               FROM meal_logs ml JOIN user_targets ut ON ut.user_id = ml.user_id
               WHERE ml.user_id = ? AND substr(ml.logged_at,1,10) = ?
               GROUP BY substr(ml.logged_at,1,10)""",
            (user_id, today_str),
        )).fetchone()
        return row[0] if row else 0

    return 0


async def evaluate_weekly_progress(
    db: aiosqlite.Connection, user_id: int, challenge_id: str, week_start: str
) -> int:
    """Compute current progress for a weekly challenge."""
    week_end = (date.fromisoformat(week_start) + timedelta(days=6)).isoformat()

    if challenge_id == "week_target_5":
        row = await (await db.execute(
            """SELECT COUNT(*) FROM (
                 SELECT substr(ml.logged_at,1,10) AS d
                 FROM meal_logs ml JOIN user_targets ut ON ut.user_id = ml.user_id
                 WHERE ml.user_id = ? AND substr(ml.logged_at,1,10) >= ? AND substr(ml.logged_at,1,10) <= ?
                 GROUP BY d
                 HAVING SUM(ml.calories) BETWEEN ut.calories * 0.9 AND ut.calories * 1.1
               )""",
            (user_id, week_start, week_end),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "week_log_20":
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? AND substr(logged_at,1,10) >= ? AND substr(logged_at,1,10) <= ?",
            (user_id, week_start, week_end),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "week_every_day":
        row = await (await db.execute(
            "SELECT COUNT(DISTINCT substr(logged_at,1,10)) FROM meal_logs WHERE user_id = ? AND substr(logged_at,1,10) >= ? AND substr(logged_at,1,10) <= ?",
            (user_id, week_start, week_end),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "week_photos_5":
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? AND substr(logged_at,1,10) >= ? AND substr(logged_at,1,10) <= ? AND image_path IS NOT NULL AND image_path != ''",
            (user_id, week_start, week_end),
        )).fetchone()
        return row[0] if row else 0

    if challenge_id == "week_variety_7":
        row = await (await db.execute(
            "SELECT COUNT(DISTINCT LOWER(item_name)) FROM meal_logs WHERE user_id = ? AND substr(logged_at,1,10) >= ? AND substr(logged_at,1,10) <= ?",
            (user_id, week_start, week_end),
        )).fetchone()
        return row[0] if row else 0

    return 0


async def get_challenges_with_progress(db_path: str, user_id: int, today_str: str) -> dict:
    """Return daily + weekly challenge with current progress."""
    daily = get_daily_challenge(user_id, today_str)
    weekly = get_weekly_challenge(user_id, today_str)

    async with get_db(db_path) as db:
        daily_progress = await evaluate_daily_progress(db, user_id, daily["id"], today_str)
        weekly_progress = await evaluate_weekly_progress(db, user_id, weekly["id"], weekly["period"])

    daily["progress"] = daily_progress
    daily["completed"] = daily_progress >= daily["target"]
    weekly["progress"] = weekly_progress
    weekly["completed"] = weekly_progress >= weekly["target"]

    return {"daily": daily, "weekly": weekly}
