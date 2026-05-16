"""Post-workout nutrition nudge via push notification.

Called from Strava/Fitbit webhook handlers after a workout is synced.
Sends a push notification suggesting protein intake after exercise.

Respects:
- reminders_on preference (off = no nudges)
- Quiet hours (10 PM - 7 AM in user's timezone)
- Cooldown: max 1 workout nudge per 4 hours per user
"""

import logging
import zoneinfo
from datetime import datetime

from src.db import (
    delete_push_subscription,
    get_all_push_subscriptions,
    get_user_prefs,
    get_today_totals,
    get_user_target,
)
from src.web.push import send_push_notification

logger = logging.getLogger("macro_app")

# In-memory cooldown tracker: user_id -> last nudge timestamp
_last_nudge: dict[int, float] = {}
_COOLDOWN_SECONDS = 4 * 3600  # 4 hours

# A8: per-(user, local-date) throttle to prevent push-spam from duplicate
# Strava webhooks. Strava is known to deliver retries up to several times
# for the same activity; without this, every retry within 4h would be
# blocked by _COOLDOWN_SECONDS but the *first* nudge for the next day
# could still re-fire if a duplicate landed near midnight. The day-key
# (set, never expired in-process — bounded by single-worker, restart
# clears it) closes that gap.
_day_nudged: set[tuple[int, str]] = set()


async def send_workout_nudge(
    db_path: str,
    user_id: int,
    activity_type: str = "",
    calories_burned: float = 0,
) -> bool:
    """Send a post-workout nutrition push notification.

    Returns True if a notification was sent, False if skipped.
    """
    import time

    # Cooldown check
    now_ts = time.time()
    last = _last_nudge.get(user_id, 0)
    if now_ts - last < _COOLDOWN_SECONDS:
        logger.debug("Workout nudge skipped for user %d: cooldown", user_id)
        return False

    # Check user prefs
    try:
        prefs = await get_user_prefs(db_path, user_id)
    except Exception:
        return False

    if not prefs.get("reminders_on", 1):
        return False

    # Quiet hours check (10 PM - 7 AM)
    try:
        tz = zoneinfo.ZoneInfo(prefs.get("timezone", "UTC"))
    except Exception:
        tz = zoneinfo.ZoneInfo("UTC")

    user_now = datetime.now(tz)
    if user_now.hour >= 22 or user_now.hour < 7:
        logger.debug("Workout nudge skipped for user %d: quiet hours", user_id)
        return False

    # A8: per-(user, day) throttle. Skip if we've already nudged this user
    # today (their local-tz day, since that's how the rest of the app
    # measures days). Resets on process restart - acceptable since the
    # _COOLDOWN_SECONDS cap is the primary spam guard.
    day_key = (user_id, user_now.date().isoformat())
    if day_key in _day_nudged:
        logger.debug("Workout nudge skipped for user %d: already nudged today", user_id)
        return False

    # Get push subscriptions
    all_subs = await get_all_push_subscriptions(db_path)
    user_subs = [s for s in all_subs if s["user_id"] == user_id]
    if not user_subs:
        return False

    # Build notification
    activity_label = activity_type.replace("_", " ").title() if activity_type else "Workout"
    cal_text = f" ({int(calories_burned)} cal burned)" if calories_burned > 0 else ""

    # Add progress context
    today_str = user_now.date().isoformat()
    progress_text = ""
    try:
        totals = await get_today_totals(db_path, user_id, today_str)
        target = await get_user_target(db_path, user_id)
        if target:
            remaining = target["protein"] - totals["protein"]
            if remaining > 0:
                progress_text = f"\n{int(remaining)}g protein still to go today."
    except Exception:
        pass

    title = f"\U0001f4aa {activity_label} complete!"
    body = f"Nice work{cal_text}! A protein-rich meal helps recovery.{progress_text}"

    # Send to all user subscriptions
    sent = False
    for sub in user_subs:
        sub_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        result = await send_push_notification(
            sub_info,
            title=title,
            body=body,
            url="/macro_app/log",
            tag=f"workout-nudge-{today_str}",
        )
        if result is True:
            sent = True
        elif result is False:
            await delete_push_subscription(db_path, user_id, sub["endpoint"])

    if sent:
        _last_nudge[user_id] = now_ts  # Only set cooldown after successful send
        _day_nudged.add(day_key)
        logger.info("Workout nudge sent to user %d: %s%s", user_id, activity_label, cal_text)
    return sent
