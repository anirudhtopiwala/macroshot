"""Background task: periodic Fitbit + Strava activity sync.

Runs hourly. For each user with a connected source, syncs recent
activity data if the last sync was > 30 minutes ago. Strava only
runs when STRAVA_CLIENT_ID is set; same gating for Fitbit.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from src.db import (
    get_all_fitbit_user_ids,
    get_all_oura_user_ids,
    get_all_strava_user_ids,
    get_fitbit_tokens,
    get_last_fitbit_sync,
    get_last_oura_sync,
    get_last_strava_sync,
    upsert_fitbit_activity,
)

logger = logging.getLogger("macro_app")

FITBIT_API_BASE = "https://api.fitbit.com"

_SYNC_INTERVAL = 3600       # 1 hour
_MIN_SYNC_GAP = 1800        # Skip if last sync < 30 minutes ago
_SYNC_DAYS = 7              # Sync last 7 days (not 30, to be lighter)


async def sync_fitbit_for_user(db_path: str, user_id: int) -> int:
    """Sync recent Fitbit activity for a single user. Returns days synced.

    Delegates token refresh to routes/fitbit._refresh_token_if_needed so the
    per-user lock is honored - without it, a concurrent manual sync and
    background sync could both rotate the refresh_token, invalidating one.
    """
    tokens = await get_fitbit_tokens(db_path, user_id)
    if not tokens:
        return 0

    # Lazy import to keep activity_sync free of route-layer imports at module
    # load time (avoids circulars; routes/fitbit doesn't import us).
    from src.web.routes.fitbit import _refresh_token_if_needed as _refresh_lock_aware
    try:
        access_token = await _refresh_lock_aware(db_path, tokens)
    except Exception:
        logger.exception("Background Fitbit token refresh error for user %d", user_id)
        return 0
    if not access_token:
        return 0

    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=_SYNC_DAYS - 1)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {access_token}"}

    metrics = {
        "calories": f"/1/user/-/activities/calories/date/{start}/{end}.json",
        "activityCalories": f"/1/user/-/activities/activityCalories/date/{start}/{end}.json",
        "steps": f"/1/user/-/activities/steps/date/{start}/{end}.json",
        "minutesFairlyActive": f"/1/user/-/activities/minutesFairlyActive/date/{start}/{end}.json",
        "minutesVeryActive": f"/1/user/-/activities/minutesVeryActive/date/{start}/{end}.json",
        "heart": f"/1/user/-/activities/heart/date/{start}/{end}.json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            responses = await asyncio.gather(
                *[client.get(f"{FITBIT_API_BASE}{path}", headers=headers) for path in metrics.values()],
                return_exceptions=True,
            )
    except Exception:
        logger.exception("Background Fitbit fetch error for user %d", user_id)
        return 0

    metric_keys = list(metrics.keys())
    series: dict[str, dict[str, float]] = {}

    for i, resp in enumerate(responses):
        key = metric_keys[i]
        if isinstance(resp, Exception) or resp.status_code != 200:
            continue
        data = resp.json()
        for ts_key, entries in data.items():
            if not ts_key.startswith("activities-"):
                continue
            for entry in entries:
                date_str = entry["dateTime"]
                if date_str not in series:
                    series[date_str] = {}
                if key == "heart":
                    val = entry.get("value", {})
                    if isinstance(val, dict):
                        series[date_str]["resting_heart_rate"] = val.get("restingHeartRate", 0)
                else:
                    series[date_str][key] = float(entry.get("value", 0))

    synced = 0
    for date_str, day in series.items():
        steps = int(day.get("steps", 0))
        calories_out = day.get("calories", 0)
        if steps == 0 and calories_out == 0:
            continue
        await upsert_fitbit_activity(
            db_path=db_path,
            user_id=user_id,
            date_str=date_str,
            calories_out=calories_out,
            activity_calories=day.get("activityCalories", 0),
            steps=steps,
            fairly_active_min=int(day.get("minutesFairlyActive", 0)),
            very_active_min=int(day.get("minutesVeryActive", 0)),
            resting_heart_rate=int(day.get("resting_heart_rate", 0)),
        )
        synced += 1

    return synced


# Max users to sync concurrently per service. One slow user (e.g., a 30s
# httpx timeout) shouldn't block all the others; asyncio.Semaphore limits
# fan-out so we don't burst into 100+ parallel API calls on e2-micro either.
_CONCURRENT_USERS = 3


async def _sync_all_fitbit(db_path: str) -> None:
    """Parallel-sync all Fitbit-connected users (bounded fan-out)."""
    user_ids = await get_all_fitbit_user_ids(db_path)
    sem = asyncio.Semaphore(_CONCURRENT_USERS)

    async def _one(user_id: int) -> None:
        async with sem:
            try:
                last_sync = await get_last_fitbit_sync(db_path, user_id)
                if last_sync:
                    last_dt = datetime.strptime(last_sync, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_dt).total_seconds() < _MIN_SYNC_GAP:
                        return
                synced = await sync_fitbit_for_user(db_path, user_id)
                if synced:
                    logger.info("Background Fitbit sync: user %d, %d days", user_id, synced)
            except Exception:
                logger.exception("Background Fitbit sync failed for user %d", user_id)

    await asyncio.gather(*(_one(uid) for uid in user_ids), return_exceptions=True)


async def _sync_all_strava(db_path: str) -> None:
    """Parallel-sync all Strava-connected users (bounded fan-out).

    Strava `last_synced` is the date prefix (YYYY-MM-DD) of the most recent
    activity, not a wall-clock sync timestamp - so the skip window also
    consults `_last_strava_poll` (in-memory wall clock). Lost on restart,
    which at worst means one extra poll cycle per user.
    """
    user_ids = await get_all_strava_user_ids(db_path)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sem = asyncio.Semaphore(_CONCURRENT_USERS)

    async def _one(user_id: int) -> None:
        async with sem:
            try:
                last_sync = await get_last_strava_sync(db_path, user_id)
                now = datetime.now(timezone.utc)
                last_poll = _last_strava_poll.get(user_id)
                if last_sync == today_str and last_poll and (now - last_poll).total_seconds() < _MIN_SYNC_GAP:
                    return
                from src.web.routes.strava import sync_strava_for_user
                synced = await sync_strava_for_user(db_path, user_id)
                _last_strava_poll[user_id] = now
                if synced:
                    logger.info("Background Strava sync: user %d, %d activities", user_id, synced)
            except Exception:
                logger.exception("Background Strava sync failed for user %d", user_id)

    await asyncio.gather(*(_one(uid) for uid in user_ids), return_exceptions=True)


_last_strava_poll: dict[int, datetime] = {}


def clear_strava_poll_timestamp(user_id: int) -> None:
    """Drop the cached poll timestamp so the next background cycle polls fresh.

    Called from routes/strava.py on disconnect. Without this, after a
    disconnect → reconnect the 30-minute skip window would still block
    the next poll because the stale entry here lives forever in memory.
    Kept as a setter rather than exporting the dict to avoid circular imports.
    """
    _last_strava_poll.pop(user_id, None)


async def _sync_all_oura(db_path: str) -> None:
    """Parallel-sync all Oura-connected users (bounded fan-out)."""
    user_ids = await get_all_oura_user_ids(db_path)
    sem = asyncio.Semaphore(_CONCURRENT_USERS)

    async def _one(user_id: int) -> None:
        async with sem:
            try:
                last_sync = await get_last_oura_sync(db_path, user_id)
                if last_sync:
                    last_dt = datetime.strptime(last_sync, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - last_dt).total_seconds() < _MIN_SYNC_GAP:
                        return
                from src.web.routes.oura import sync_oura_for_user
                result = await sync_oura_for_user(db_path, user_id, days=_SYNC_DAYS)
                if result["days"] or result["workouts"]:
                    logger.info(
                        "Background Oura sync: user %d, %d days, %d workouts",
                        user_id, result["days"], result["workouts"],
                    )
            except Exception:
                logger.exception("Background Oura sync failed for user %d", user_id)

    await asyncio.gather(*(_one(uid) for uid in user_ids), return_exceptions=True)


async def activity_sync_loop(db_path: str) -> None:
    """Periodically sync Fitbit + Strava + Oura data for all connected users."""
    # Wait 2 minutes after startup before first sync
    await asyncio.sleep(120)
    while True:
        try:
            if os.environ.get("FITBIT_CLIENT_ID"):
                await _sync_all_fitbit(db_path)
            if os.environ.get("STRAVA_CLIENT_ID"):
                await _sync_all_strava(db_path)
            if os.environ.get("OURA_CLIENT_ID"):
                await _sync_all_oura(db_path)
        except Exception:
            logger.exception("Background activity sync loop failed")

        await asyncio.sleep(_SYNC_INTERVAL)
