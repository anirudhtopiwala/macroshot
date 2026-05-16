"""Dashboard API routes: today's progress, totals, trends, stats."""

import asyncio
from datetime import date, timedelta

from fastapi import APIRouter, Request

import aiosqlite

from src.db import (
    auto_consume_shields_for_streak,
    get_db,
    get_meals_for_day,
    get_period_totals,
    get_user_stats,
    get_user_target,
)
from src.services import get_progress, get_trend_data, user_today_str
from src.web.deps import CurrentUser, DbPath, SubInfo
from src.web.rate_limit import limiter
from src.web.schemas import MealOut, StatsResponse, TodayResponse, TrendResponse

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/today", response_model=TodayResponse)
@limiter.limit("60/minute")
async def get_today(request: Request, user: CurrentUser, db_path: DbPath, date: str | None = None):
    """Get totals, target, remaining, and meals for a given date (default: today)."""
    today = date or await user_today_str(db_path, user["user_id"])
    progress, meals = await asyncio.gather(
        get_progress(user["user_id"], db_path, today),
        get_meals_for_day(db_path, user["user_id"], today, limit=5),
    )

    return TodayResponse(
        totals=progress["totals"],
        target=progress["target"],
        adjusted_target=progress.get("adjusted_target"),
        exercise_adjustment=progress.get("exercise_adjustment"),
        remaining=progress["remaining"],
        recent_meals=[MealOut(**m) for m in meals],
        sync_pending=progress.get("sync_pending", False),
    )


@router.get("/totals")
async def get_totals(user: CurrentUser, db_path: DbPath, period: str = "day"):
    """Get totals for a period (day/week/month)."""
    today = await user_today_str(db_path, user["user_id"])
    result = await get_period_totals(db_path, user["user_id"], period, today)
    return result.model_dump()


@router.get("/remaining")
async def get_remaining(user: CurrentUser, db_path: DbPath):
    """Get macros remaining today."""
    today = await user_today_str(db_path, user["user_id"])
    progress = await get_progress(user["user_id"], db_path, today)
    return {
        "remaining": progress["remaining"],
        "totals": progress["totals"],
        "target": progress["target"],
        "adjusted_target": progress.get("adjusted_target"),
        "exercise_adjustment": progress.get("exercise_adjustment"),
    }


@router.get("/trend")
@limiter.limit("60/minute")
async def get_trend(request: Request, user: CurrentUser, db_path: DbPath, sub: SubInfo, days: int = 7, today: str | None = None, calendar: bool = False):
    """Get N-day daily data for charts (default 7). Pass today= for client timezone.

    Free-tier users are limited to 7 days for trend charts. Requests for
    30/90 days return only 7 days with a ``truncated`` flag and upgrade message.
    calendar=true bypasses truncation (for MonthCalendar date rings).
    """
    requested_days = min(max(days, 7), 90)
    truncated = False

    # Free users get 7 days max for trend charts.
    # Calendar mode allows up to 31 days (one month) for date ring display.
    if not sub.is_premium and requested_days > 7:
        if calendar and requested_days <= 31:
            num_days = requested_days  # Calendar needs full month
        else:
            num_days = 7
            truncated = True
    else:
        num_days = requested_days

    if not today:
        today = await user_today_str(db_path, user["user_id"])
    target = await get_user_target(db_path, user["user_id"])
    day_data = await get_trend_data(
        user["user_id"], db_path, num_days=num_days, today_str=today, target=target,
    )

    result = {"days": day_data, "target": target}
    if truncated:
        result["truncated"] = True
        result["upgrade_message"] = "Upgrade to see 30 and 90-day trends"
    return result


@router.get("/stats", response_model=StatsResponse)
async def get_stats(user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Get user statistics: streak, averages, most-logged."""
    today = await user_today_str(db_path, user["user_id"])
    # Bridge unprotected gap days before reading the streak so users who
    # haven't logged today don't see streak=0 with shields sitting unused.
    await auto_consume_shields_for_streak(db_path, user["user_id"], today)
    stats = await get_user_stats(db_path, user["user_id"], today_str=today)
    if not sub.is_premium:
        return StatsResponse(**stats, locked=True)
    return StatsResponse(**stats)


@router.get("/activity-trend")
@limiter.limit("60/minute")
async def get_activity_trend(
    request: Request,
    user: CurrentUser,
    db_path: DbPath,
    sub: SubInfo,
    days: int = 7,
):
    """Get daily activity data for the last N days (workouts + Fitbit).

    Returns per-day: calories_burned, intake_calories, steps, active_minutes, workout_count.
    Free users limited to 7 days.
    """
    requested_days = min(max(days, 7), 90)
    truncated = False
    if not sub.is_premium and requested_days > 7:
        num_days = 7
        truncated = True
    else:
        num_days = requested_days

    user_id = user["user_id"]
    today = await user_today_str(db_path, user_id)
    start = (date.fromisoformat(today) - timedelta(days=num_days - 1)).isoformat()

    # Fetch workout logs, Fitbit/Oura activity, and meal intake in parallel.
    # Workouts come back as individual rows (not SUM'd in SQL) so Python
    # can dedup Strava+Fitbit overlaps per-day before totaling - without
    # this, any session captured by both services double-counts.
    from src.workout_dedup import dedup_fitbit_vs_strava

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        workout_rows, fitbit_rows, oura_rows, intake_rows = await asyncio.gather(
            (await db.execute(
                "SELECT id, source, started_at, duration_sec, calories_burned, "
                "SUBSTR(logged_at, 1, 10) as day "
                "FROM workout_logs WHERE user_id = ? AND logged_at >= ? AND logged_at <= ? || 'Z'",
                (user_id, start, today),
            )).fetchall(),
            (await db.execute(
                "SELECT date, calories_out, activity_calories, calories_bmr, "
                "steps, fairly_active_min, very_active_min "
                "FROM fitbit_activity WHERE user_id = ? AND date >= ? AND date <= ?",
                (user_id, start, today),
            )).fetchall(),
            (await db.execute(
                "SELECT date, activity_calories, steps, fairly_active_min, very_active_min "
                "FROM oura_activity WHERE user_id = ? AND date >= ? AND date <= ?",
                (user_id, start, today),
            )).fetchall(),
            (await db.execute(
                "SELECT SUBSTR(logged_at, 1, 10) as day, SUM(calories) as cals "
                "FROM meal_logs WHERE user_id = ? AND logged_at >= ? AND logged_at <= ? || 'Z' "
                "GROUP BY day",
                (user_id, start, today),
            )).fetchall(),
        )

    # Group workouts by day, dedup each day, then roll up cals/duration/count
    workouts_by_day: dict[str, list[dict]] = {}
    for r in workout_rows:
        workouts_by_day.setdefault(r["day"], []).append(dict(r))
    workout_map: dict[str, dict] = {}
    for day, ws in workouts_by_day.items():
        deduped = dedup_fitbit_vs_strava(ws)
        workout_map[day] = {
            "cals": sum((w.get("calories_burned") or 0) for w in deduped),
            "dur": sum((w.get("duration_sec") or 0) for w in deduped),
            "cnt": len(deduped),
        }

    fitbit_map = {r["date"]: dict(r) for r in fitbit_rows}
    oura_map = {r["date"]: dict(r) for r in oura_rows}
    intake_map = {r["day"]: r["cals"] for r in intake_rows}

    result_days = []
    for i in range(num_days):
        d = (date.fromisoformat(today) - timedelta(days=num_days - 1 - i)).isoformat()
        w = workout_map.get(d, {})
        f = fitbit_map.get(d, {})
        o = oura_map.get(d, {})

        # burned_calories = workout sessions only (Strava, Fitbit activity
        # logs, manual). NEAT from the daily summary is excluded - the user's
        # target already accounts for baseline activity via the Mifflin-St
        # Jeor multiplier. See src/services.py::compute_progress.
        active_calories = w.get("cals", 0) or 0
        steps = max(f.get("steps", 0) or 0, o.get("steps", 0) or 0)
        active_minutes = max(
            (f.get("fairly_active_min", 0) or 0) + (f.get("very_active_min", 0) or 0),
            (o.get("fairly_active_min", 0) or 0) + (o.get("very_active_min", 0) or 0),
        )

        result_days.append({
            "date": d,
            "burned_calories": round(active_calories),
            "intake_calories": round(intake_map.get(d, 0) or 0),
            "steps": steps,
            "active_minutes": active_minutes,
            "workout_count": w.get("cnt", 0) or 0,
            "duration_min": round((w.get("dur", 0) or 0) / 60),
        })

    response = {"days": result_days}
    if truncated:
        response["truncated"] = True
        response["upgrade_message"] = "Upgrade to see 30 and 90-day activity trends"
    return response
