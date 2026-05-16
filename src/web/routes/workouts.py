"""Combined workout data routes.

Returns workout data from all connected sources (Strava, Fitbit, manual)
in a unified format for the dashboard.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from src.db import delete_workout, get_fitbit_activity, get_fitbit_tokens, get_last_fitbit_sync, get_last_oura_sync, get_last_strava_sync, get_oura_activity, get_oura_tokens, get_strava_tokens, get_user_prefs, get_workouts_for_date, upsert_workout
from src.services import get_user_tz, user_today_str
from src.web.deps import CurrentUser, DbPath
from src.web.rate_limit import limiter

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/workouts", tags=["workouts"])


def _fitbit_net_active_cals(fitbit: dict | None) -> float:
    """Compute true "Active Calories" (net exercise burn) from a fitbit_activity row.

    Fitbit's activityCalories sums all calorie burn during active minutes - BMR
    included. That overstates net exercise burn by the BMR share of active
    minutes (typically 200–400 kcal/day of inflation). The more faithful
    equivalent of Apple Health's "Active Energy" is caloriesOut − caloriesBMR.

    Falls back to activityCalories for legacy rows synced before calories_bmr
    was being stored.
    """
    if not fitbit:
        return 0
    out = float(fitbit.get("calories_out") or 0)
    bmr = float(fitbit.get("calories_bmr") or 0)
    if out > 0 and bmr > 0:
        return max(out - bmr, 0.0)
    return float(fitbit.get("activity_calories") or 0)


from src.workout_dedup import dedup_fitbit_vs_strava as _dedup_fitbit_vs_strava


@router.get("")
@limiter.limit("60/minute")
async def get_workouts(
    request: Request,
    user: CurrentUser,
    db_path: DbPath,
    date: str = Query(
        default="",
        description="Date in YYYY-MM-DD format. Defaults to today (UTC).",
    ),
):
    """Return combined workout data for a given date.

    Merges:
    - workout_logs (Strava activities, manual entries)
    - fitbit_activity (daily summary: steps, active minutes, calories)

    Returns a unified response with both individual workouts and the
    Fitbit daily summary (if available).
    """
    user_id = user["user_id"]
    if not date:
        date = await user_today_str(db_path, user_id)

    # Auto-sync Fitbit + Oura data so dashboard shows fresh info
    try:
        from src.web.routes.fitbit import auto_sync_fitbit_today
        await auto_sync_fitbit_today(db_path, user_id, date)
    except Exception:
        pass
    try:
        from src.web.routes.oura import auto_sync_oura_today
        await auto_sync_oura_today(db_path, user_id, date)
    except Exception:
        pass

    workouts = await get_workouts_for_date(db_path, user_id, date)
    workouts = _dedup_fitbit_vs_strava(workouts)
    fitbit = await get_fitbit_activity(db_path, user_id, date)
    oura = await get_oura_activity(db_path, user_id, date)
    # Check connection status so the frontend can show appropriate empty state
    fitbit_connected = bool(await get_fitbit_tokens(db_path, user_id))
    strava_connected = bool(await get_strava_tokens(db_path, user_id))
    oura_connected = bool(await get_oura_tokens(db_path, user_id))

    # Compute totals from all workout sources
    total_calories_burned = sum((w.get("calories_burned") or 0) for w in workouts)
    total_duration_sec = sum((w.get("duration_sec") or 0) for w in workouts)

    # "active_calories" on the dashboard means workout-session calories only
    # (Strava + Fitbit individual activity logs + manual). NEAT / all-day
    # above-BMR burn from the Fitbit/Oura daily summary is excluded because
    # the user's daily target already accounts for baseline activity via the
    # Mifflin-St Jeor multiplier - see src/services.py::compute_progress.
    # The Fitbit daily summary's true-active number is still surfaced via
    # fitbit_summary.active_calories for informational display elsewhere.
    active_calories = total_calories_burned

    daily_steps = max(
        fitbit["steps"] if fitbit else 0,
        oura["steps"] if oura else 0,
    )
    daily_active_min = max(
        (fitbit["fairly_active_min"] + fitbit["very_active_min"]) if fitbit else 0,
        (oura["fairly_active_min"] + oura["very_active_min"]) if oura else 0,
    )

    return {
        "date": date,
        "workouts": [
            {
                "id": w["id"],
                "source": w["source"],
                "activity_type": w["activity_type"],
                "name": w["name"],
                "started_at": w["started_at"],
                "duration_sec": w["duration_sec"],
                "calories_burned": w["calories_burned"],
                "distance_m": w["distance_m"],
                "avg_heart_rate": w["avg_heart_rate"],
            }
            for w in workouts
        ],
        "fitbit_summary": {
            "calories_out": fitbit["calories_out"],
            "activity_calories": fitbit["activity_calories"],
            "calories_bmr": fitbit.get("calories_bmr") or 0,
            "active_calories": round(_fitbit_net_active_cals(fitbit)),
            "steps": fitbit["steps"],
            "fairly_active_min": fitbit["fairly_active_min"],
            "very_active_min": fitbit["very_active_min"],
            "resting_heart_rate": fitbit["resting_heart_rate"],
            "fetched_at": fitbit["fetched_at"],
        } if fitbit else None,
        "oura_summary": {
            "calories_out": oura["calories_out"],
            "activity_calories": oura["activity_calories"],
            "steps": oura["steps"],
            "fairly_active_min": oura["fairly_active_min"],
            "very_active_min": oura["very_active_min"],
            "resting_heart_rate": oura["resting_heart_rate"],
            "fetched_at": oura["fetched_at"],
        } if oura else None,
        "totals": {
            "active_calories": active_calories,
            "workout_count": len(workouts),
            "total_duration_sec": total_duration_sec,
            "steps": daily_steps,
            "active_minutes": daily_active_min,
        },
        "fitbit_connected": fitbit_connected,
        "strava_connected": strava_connected,
        "oura_connected": oura_connected,
    }


@router.get("/sync-status")
async def get_sync_status(user: CurrentUser, db_path: DbPath):
    """Return the most recent sync time across all connected activity sources.

    Used by Trends and Workout History to show a "Last synced Xm ago"
    caption under the activity chart. Returns null when the user has no
    connected source so the frontend can hide the caption entirely -
    stale workout_logs from a since-disconnected integration must not
    produce a misleading timestamp.
    """
    user_id = user["user_id"]
    strava_connected = bool(await get_strava_tokens(db_path, user_id))
    fitbit_connected = bool(await get_fitbit_tokens(db_path, user_id))
    oura_connected = bool(await get_oura_tokens(db_path, user_id))

    if not strava_connected and not fitbit_connected and not oura_connected:
        return {"last_synced": None}

    candidates: list[str] = []
    if strava_connected:
        ts = await get_last_strava_sync(db_path, user_id)
        if ts:
            candidates.append(ts)
    if fitbit_connected:
        ts = await get_last_fitbit_sync(db_path, user_id)
        if ts:
            candidates.append(ts)
    if oura_connected:
        ts = await get_last_oura_sync(db_path, user_id)
        if ts:
            candidates.append(ts)

    return {"last_synced": max(candidates) if candidates else None}


@router.get("/history")
@limiter.limit("60/minute")
async def get_workout_history(
    request: Request,
    user: CurrentUser,
    db_path: DbPath,
    days: int = Query(default=30, ge=1, le=90),
):
    """Return consolidated workout history from all sources.

    Merges workout_logs (Strava/manual) and fitbit_activity (daily summaries)
    into a single day-grouped response.
    """
    from src.db import get_db
    import aiosqlite

    user_id = user["user_id"]
    today = await user_today_str(db_path, user_id)

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        workout_rows, fitbit_rows, oura_rows = await asyncio.gather(
            (await db.execute(
                "SELECT id, source, activity_type, name, started_at, duration_sec, "
                "calories_burned, distance_m, avg_heart_rate, logged_at "
                "FROM workout_logs WHERE user_id = ? AND logged_at >= date(?, '-' || ? || ' days') "
                "ORDER BY logged_at DESC, started_at DESC",
                (user_id, today, days),
            )).fetchall(),
            (await db.execute(
                "SELECT date, calories_out, activity_calories, calories_bmr, steps, "
                "fairly_active_min, very_active_min, resting_heart_rate "
                "FROM fitbit_activity WHERE user_id = ? AND date >= date(?, '-' || ? || ' days') "
                "ORDER BY date DESC",
                (user_id, today, days),
            )).fetchall(),
            (await db.execute(
                "SELECT date, calories_out, activity_calories, steps, "
                "fairly_active_min, very_active_min, resting_heart_rate "
                "FROM oura_activity WHERE user_id = ? AND date >= date(?, '-' || ? || ' days') "
                "ORDER BY date DESC",
                (user_id, today, days),
            )).fetchall(),
        )

    workouts = [dict(r) for r in workout_rows]
    fitbit_by_date = {r["date"]: dict(r) for r in fitbit_rows}
    oura_by_date = {r["date"]: dict(r) for r in oura_rows}

    # Group workouts by date, then dedup Fitbit-vs-Strava within each day
    workouts_by_date: dict[str, list[dict]] = {}
    for w in workouts:
        d = w["logged_at"][:10]
        workouts_by_date.setdefault(d, []).append(w)
    for d, ws in workouts_by_date.items():
        workouts_by_date[d] = _dedup_fitbit_vs_strava(ws)

    # Merge all dates (workout dates + fitbit dates + oura dates)
    all_dates = sorted(
        set(workouts_by_date.keys()) | set(fitbit_by_date.keys()) | set(oura_by_date.keys()),
        reverse=True,
    )

    total_steps = 0
    total_active_calories = 0
    total_workout_count = 0
    total_duration_sec = 0
    active_days = 0

    result_days = []
    for d in all_dates:
        ws = workouts_by_date.get(d, [])
        fb = fitbit_by_date.get(d)
        ou = oura_by_date.get(d)

        workout_cals = sum((w.get("calories_burned") or 0) for w in ws)
        fitbit_cals = _fitbit_net_active_cals(fb)
        oura_cals = ou["activity_calories"] if ou else 0
        daily_source_cals = max(fitbit_cals, oura_cals)
        # Per-day total now mirrors get_workouts: workouts-only. Daily summary
        # calories still available via fitbit_summary / oura_summary for UI.
        day_active_cals = workout_cals
        day_duration = sum((w.get("duration_sec") or 0) for w in ws)
        day_steps = max(
            fb["steps"] if fb else 0,
            ou["steps"] if ou else 0,
        )

        # Skip days with no meaningful activity
        if not ws and daily_source_cals < 50 and day_steps < 500:
            continue

        active_days += 1
        total_steps += day_steps
        total_active_calories += day_active_cals
        total_workout_count += len(ws)
        total_duration_sec += day_duration

        result_days.append({
            "date": d,
            "workouts": ws,
            "fitbit_summary": {
                "activity_calories": fb["activity_calories"],
                "calories_out": fb.get("calories_out") or 0,
                "calories_bmr": fb.get("calories_bmr") or 0,
                "active_calories": round(_fitbit_net_active_cals(fb)),
                "steps": fb["steps"],
                "fairly_active_min": fb["fairly_active_min"],
                "very_active_min": fb["very_active_min"],
                "resting_heart_rate": fb["resting_heart_rate"],
            } if fb else None,
            "oura_summary": {
                "activity_calories": ou["activity_calories"],
                "steps": ou["steps"],
                "fairly_active_min": ou["fairly_active_min"],
                "very_active_min": ou["very_active_min"],
                "resting_heart_rate": ou["resting_heart_rate"],
            } if ou else None,
            "total_calories": day_active_cals,
            "total_duration_sec": day_duration,
            "total_steps": day_steps,
        })

    return {
        "days": result_days,
        "totals": {
            "active_days": active_days,
            "workout_count": total_workout_count,
            "total_calories": total_active_calories,
            "total_duration_sec": total_duration_sec,
            "total_steps": total_steps,
        },
    }


class ManualWorkoutRequest(BaseModel):
    activity_type: str = Field(..., min_length=1, max_length=50)
    name: str = Field("", max_length=100)
    duration_min: int = Field(..., ge=1, le=600)
    calories_burned: float = Field(0, ge=0, le=10000)
    # A25: pattern-restrict so a malformed string can't reach the SQL layer.
    date: str = Field(
        "", description="YYYY-MM-DD, defaults to today",
        pattern=r"^(\d{4}-\d{2}-\d{2})?$",
    )
    time: str = Field(
        "", description="HH:MM local time; defaults to now",
        pattern=r"^(\d{2}:\d{2})?$",
    )


@router.post("")
@limiter.limit("10/minute")
async def add_manual_workout(
    request: Request,
    user: CurrentUser,
    db_path: DbPath,
    body: ManualWorkoutRequest,
):
    """Log a manual workout entry."""
    user_id = user["user_id"]
    date_str = body.date or await user_today_str(db_path, user_id)

    prefs = await get_user_prefs(db_path, user_id)
    tz = get_user_tz(prefs.get("timezone"))
    started_local: datetime
    if body.time:
        try:
            hh, mm = body.time.split(":")
            y, m, d = date_str.split("-")
            started_local = datetime(int(y), int(m), int(d), int(hh), int(mm), tzinfo=tz)
        except Exception:
            started_local = datetime.now(tz)
    else:
        started_local = datetime.now(tz)
    now = started_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    external_id = f"manual_{uuid.uuid4().hex[:12]}"

    row_id = await upsert_workout(
        db_path=db_path,
        user_id=user_id,
        source="manual",
        external_id=external_id,
        activity_type=body.activity_type,
        name=body.name or body.activity_type,
        started_at=now,
        duration_sec=body.duration_min * 60,
        calories_burned=body.calories_burned,
        logged_at=date_str,
    )
    return {"id": row_id, "status": "created"}


@router.delete("/{workout_id}")
@limiter.limit("30/minute")
async def delete_manual_workout(
    request: Request,
    workout_id: int,
    user: CurrentUser,
    db_path: DbPath,
):
    """Delete a manual workout entry (only manual workouts can be deleted)."""
    from fastapi import HTTPException
    from src.db import get_db
    import aiosqlite

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, source FROM workout_logs WHERE id = ? AND user_id = ?",
                (workout_id, user["user_id"]),
            )
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="Workout not found")
        if row["source"] != "manual":
            raise HTTPException(status_code=400, detail="Only manual workouts can be deleted")

        await db.execute(
            "DELETE FROM workout_logs WHERE id = ? AND user_id = ?",
            (workout_id, user["user_id"]),
        )
        await db.commit()

    return {"status": "deleted"}
