"""Background task: hourly push notification reminders with motivational messages."""

import asyncio
import hashlib
import logging
import zoneinfo
from datetime import datetime, timedelta, timezone

from src.db import (
    get_all_push_subscriptions,
    get_streak_shields,
    get_user_prefs,
    has_meal_in_window,
    get_meal_window_pattern,
    get_today_totals,
    get_user_target,
    get_user_profile,
    get_last_meal,
    delete_push_subscription,
    try_mark_reminder_sent,
    delete_reminder_sent_row,
    purge_old_reminder_sent,
    _calculate_streak,
)
from src.web.push import send_push_notification

logger = logging.getLogger("macro_app")

_MEAL_WINDOWS = {
    "breakfast": (0, 11),
    "lunch": (11, 16),
    "snack": (16, 20),
    "dinner": (20, 24),
}

_MEAL_EMOJI = {
    "breakfast": "\U0001f305",
    "lunch": "\U0001f31e",
    "snack": "\U0001f34e",
    "dinner": "\U0001f319",
}

# ── Motivational Message Pools ──────────────────────────────────────────────
# Pools are branched by user state so the message fits the moment:
#   - breakfast: branched by streak status (meal_count is always 0 in the AM)
#   - lunch/snack/dinner: branched by (meal_count, streak) - already-logging
#     vs streak-save vs new-user tones
# Privacy mode (`notif_show_macros` off, the default) means pools must NOT
# reference {cal}/{target_cal}/{remaining_cal}/{protein}/{target_protein}/
# {meal_count} - those placeholders are only used in the optional macro-augment
# pools, which are mixed in only when the user has opted in AND has data
# worth showing. Streak placeholders ({streak}, {streak_plus_1}) only appear
# in pools that are gated on streak > 0, so they never render as "Day 0".
# Each pool is rotated deterministically by day + user_id via _pick_message.

# ── Breakfast (meal_count is always 0 at this hour) ──

_BREAKFAST_STREAK = [
    "Good morning{name_greeting}! Day {streak} of your streak - keep it rolling. Snap breakfast.",
    "{streak} days strong. Start today the same way - snap your breakfast.",
    "Morning! Your {streak}-day streak is waiting. One photo, 10 seconds.",
    "Day {streak_plus_1} - snap breakfast to keep the rhythm going.",
    "Morning{name_greeting}! {streak} days of consistency. One log keeps the chain alive.",
    "You've made tracking a habit - {streak} days proves it. Snap that breakfast.",
    "Breakfast is the easiest meal to forget. Your {streak}-day streak says you've got this.",
]

_BREAKFAST_NEW = [  # never logged before
    "Good morning{name_greeting}! Today's day 1 - log breakfast and you're tracking.",
    "Morning! The hardest part is starting. One breakfast photo and you're in.",
    "You're at home, breakfast is right there. 10 seconds to log it.",
    "Logging gets much easier after the first week. Today's a great day to begin.",
    "It only takes one meal to start a streak. What's for breakfast?",
    "Fresh start today{name_greeting}. Snap breakfast and see what your day looks like.",
]

_BREAKFAST_RECOVERY = [  # streak broke recently, last_meal in past 1-2 days
    "Welcome back{name_greeting}. Yesterday was rough - today, breakfast resets the streak.",
    "Streaks can be rebuilt. Snap breakfast and you're back on day 1.",
    "Day 1 again, no shame. The habit is still there - log breakfast.",
    "Picking up where you left off. Breakfast log = back on track.",
    "One off day doesn't undo the habit. Log breakfast and the chain restarts.",
]

# ── Lunch ──

_LUNCH_PROGRESS = [  # already logged something today
    "You've already logged something today - keep the momentum going. Lunch next.",
    "Halfway through the day. Snap your lunch to round out the data.",
    "Solid start to the day. Don't skip lunch.",
    "Lunch takes 10 seconds. Round out today's data.",
    "Don't let lunch slip by. Quick photo or a one-line text log.",
    "Halfway there. A logged lunch keeps today's data clean.",
    "Busy day? Text-only log works too. Just type what you had.",
]

_LUNCH_STREAK_SAVE = [  # nothing logged today, streak > 0 - softer at midday
    "Quiet morning? Plenty of day left to keep your {streak}-day streak going.",
    "Snap lunch and your {streak}-day streak stays alive.",
    "Day {streak_plus_1} starts with lunch. One log and the streak continues.",
    "Nothing logged yet, but lunch is enough to keep your {streak}-day streak rolling.",
    "{streak} days of consistency. Lunch keeps it going.",
    "A quick lunch log keeps the streak alive. 10 seconds.",
]

_LUNCH_NEW = [  # never logged before
    "Not too late to start today - log lunch and you're on the board.",
    "First meal is always the hardest. Lunch is a perfect place to begin.",
    "It gets much easier once you log your first meal. Today's a great day for it.",
    "Lunch takes 10 seconds. The first week is the steepest part.",
    "Fresh start. Snap lunch and see what the rest of the day looks like.",
]

_LUNCH_RECOVERY = [  # streak broke recently
    "Back today? Lunch is a clean place to restart the streak.",
    "Yesterday's gone. Snap lunch and start fresh.",
    "Welcome back. One lunch log and you're rebuilding.",
    "No catch-up needed - just log lunch from here.",
    "Pick up the habit at lunch. The hard part is already behind you.",
]

# ── Snack ──

_SNACK_PROGRESS = [  # already logged something today
    "Afternoon snack? Log it - every bite counts toward your targets.",
    "You're on a roll today. Snap any snacks.",
    "Snacks are easy to miss. Log it now before you forget.",
    "Quick snack log - 10 seconds.",
    "Mid-afternoon log keeps the picture complete.",
]

_SNACK_STREAK_SAVE = [  # nothing logged today, streak > 0
    "Your {streak}-day streak is at risk. A snack log keeps it alive.",
    "{streak} days strong. A snack log saves today.",
    "Quiet day? Even one item saves your {streak}-day streak.",
    "Day {streak_plus_1} is still in reach. A quick snack log works.",
    "Still time. Even a small snack logged keeps your {streak}-day streak going.",
]

_SNACK_NEW = [  # never logged before
    "Haven't logged today? A snack is the easiest place to start.",
    "Day's not over. Even one item logged is a win.",
    "Tracking doesn't need to be perfect. Log whatever you remember from today.",
    "It gets much easier after your first week. Today, just one snack is enough.",
    "Pick anything you ate today and log it. That's how it starts.",
]

_SNACK_RECOVERY = [  # streak broke recently
    "Back today? A snack log is enough to restart.",
    "Snap a snack and the chain restarts.",
    "Yesterday's behind you. One log today and you're rebuilding.",
    "No need to make up for lost time - just log what you eat from here.",
]

# ── Dinner ──

_DINNER_PROGRESS = [  # already logged something today
    "Almost done. Snap dinner and today's a complete tracking day.",
    "You've put in the work today - finish strong with dinner.",
    "One more meal logged and today's complete.",
    "Evening{name_greeting}! Snap dinner and you're done.",
    "Solid day so far. Dinner's a 10-second log.",
    "Even a quick text log of dinner keeps the picture complete.",
]

_DINNER_STREAK_SAVE = [  # nothing logged today, streak > 0 - panic escalation
    "Last chance to save your {streak}-day streak. Dinner log does it.",
    "{streak} days down - don't lose it tonight. 10 seconds to log dinner.",
    "Your streak is at {streak} days. Dinner is the final shot.",
    "Quiet day. Snap dinner and your {streak}-day streak rolls into tomorrow.",
    "Day {streak_plus_1} starts when you log dinner tonight.",
    "10 seconds to save a {streak}-day streak.",
]

_DINNER_NEW = [  # never logged before
    "Not too late - logging dinner alone counts as a tracking day.",
    "First log is the hardest. Dinner's right in front of you.",
    "Tonight could be day 1. One dinner log starts it.",
    "It gets much easier once you log your first meal. Tonight's a perfect chance.",
    "End the day with one log. Tomorrow gets easier from there.",
]

_DINNER_RECOVERY = [  # streak broke recently
    "Back today? Dinner log restarts the streak.",
    "End today with a log. The chain starts over tomorrow.",
    "Welcome back. Snap dinner and you're rebuilding.",
    "Yesterday's gone. Dinner log = back on track.",
    "One off day, one log to restart. Snap dinner.",
]

# ── Macro-augment pools ──
# Mixed into the base pool only when notif_show_macros == True AND the user has
# already logged something today (so {cal}/{protein}/{meal_count} render as
# real numbers, not zeros). Never used at breakfast (no data yet).

_LUNCH_MACRO_AUGMENT = [
    "Lunch check-in. You're at {cal} / {target_cal} kcal. What are you having?",
    "{remaining_cal} kcal left today - plenty of room for a good lunch.",
    "{protein}g protein so far - build on it with lunch.",
]

_SNACK_MACRO_AUGMENT = [
    "Quick snack check-in. You're at {cal} / {target_cal} kcal today.",
    "{remaining_cal} kcal left. Room for a snack - log it.",
]

_DINNER_MACRO_AUGMENT = [
    "{cal} / {target_cal} kcal so far. What's for dinner?",
    "{cal} kcal across {meal_count} meals. One more to close out the day.",
    "{protein}g / {target_protein}g protein. Aim protein-rich at dinner.",
    "Only {remaining_cal} kcal from your target. A solid dinner and today's done.",
]

# ── Goal-aware augments ──
# Mixed in only when the user has a non-default goal (lose_weight or gain_weight)
# AND has opted into seeing macros AND has data today. Extends the macro augment
# with copy that frames choices around the user's goal direction.

_SNACK_LOSS_AWARE = [
    "{remaining_cal} kcal left. A light snack (fruit, veg, yogurt) keeps you in range.",
    "Close to your deficit target. Light snack is the move.",
    "Snack time. Keep it under 200 kcal and you stay on plan.",
]

_SNACK_GAIN_AWARE = [
    "{remaining_cal} kcal still to hit. Calorie-dense snack pulls double duty.",
    "Surplus day - snack time is a chance to chip away at the gap.",
    "{remaining_cal} kcal to go. Nuts, peanut butter, granola - easy wins.",
]

_DINNER_LOSS_AWARE = [
    "{remaining_cal} kcal left. Lean protein + veggies for dinner keeps you in deficit.",
    "{cal} / {target_cal} kcal so far. Protein-forward dinner closes the day cleanly.",
    "Almost there. A protein-heavy, lower-carb dinner lands you on target.",
]

_DINNER_GAIN_AWARE = [
    "{remaining_cal} kcal to go. Calorie-dense dinner closes the gap.",
    "Surplus target with {remaining_cal} kcal to fill. Make dinner count.",
    "Don't undershoot tonight. {remaining_cal} kcal of room for a real dinner.",
]

_GOAL_AWARE_BY_GOAL_AND_MEAL = {
    ("lose_weight", "snack"): _SNACK_LOSS_AWARE,
    ("lose_weight", "dinner"): _DINNER_LOSS_AWARE,
    ("gain_weight", "snack"): _SNACK_GAIN_AWARE,
    ("gain_weight", "dinner"): _DINNER_GAIN_AWARE,
}

_STREAK_ALERT_SHORT = [  # streak 1-13 days - momentum framing, less catastrophic
    "Log a meal to keep your {streak}-day streak alive.",
    "Snap anything you ate today. 10 seconds to keep day {streak_plus_1} going.",
    "Quiet day - one quick log keeps the streak rolling.",
    "Still time today. A short text log saves your {streak}-day streak.",
    "{streak} days down. One more log keeps it going.",
]

_STREAK_ALERT_LONG = [  # streak >= 14 - heavier loss aversion
    "Don't throw away {streak} days. 10 seconds to save your streak.",
    "{streak} days of consistency - one log keeps it intact.",
    "Log anything from today. A {streak}-day streak is too much to lose.",
    "{streak} days. Save it with a single log right now.",
    "Day {streak_plus_1} is still on the table. Log a meal and your {streak}-day streak survives.",
]

_STREAK_ALERT_NO_STREAK = [
    "No meals tracked today yet - 10 seconds to log one.",
    "Today's still salvageable. One quick log and you're back on the board.",
    "It gets much easier after the first week. Start with one meal tonight.",
    "Not too late to make today count. Log anything you remember eating.",
    "First meal is the hardest - pick something simple and snap it.",
]

# Dormant user re-engagement - sent once at streak_alert_hour when inactive 3+ days
_DORMANT_MSGS = [
    "It's been a few days - one meal logged today is all it takes to get back on track.",
    "We miss you{name_greeting}! Log just one meal today and start fresh.",
    "No pressure - even logging a single meal keeps the habit alive. Come back when you're ready.",
    "A quick check-in: log today's lunch or dinner. Every data point helps your progress.",
    "Tracking doesn't have to be perfect. One meal logged today is better than none.",
]

# Follow-up messages - sent 2 hours after primary if meal still not logged
_FOLLOWUP_MSGS = {
    "breakfast": [
        "Still haven't logged breakfast? Even a quick text entry helps.",
        "Breakfast slipped by? Log it now before you forget what you had.",
        "Late breakfast? No worries - log it whenever. Every meal counts.",
    ],
    "lunch": [
        "Lunch still untracked. A quick log now keeps your data complete.",
        "Did you eat lunch? Snap a photo or type it - takes 10 seconds.",
        "Don't forget lunch! Your tracking is only as good as the data you give it.",
    ],
    "snack": [
        "Snack check - did you have one? Log it if so.",
        "Afternoon snack untracked. Quick log before you move on.",
    ],
    "dinner": [
        "Dinner still not logged. Round out your day - log it now.",
        "Almost bedtime? Don't forget to log dinner before the day ends.",
        "One last thing - log dinner and today's tracking is complete.",
    ],
}

_MACRO_AUGMENT = {
    "lunch": _LUNCH_MACRO_AUGMENT,
    "snack": _SNACK_MACRO_AUGMENT,
    "dinner": _DINNER_MACRO_AUGMENT,
}


def _select_pool(
    meal_name: str,
    *,
    meal_count: int,
    streak: int,
    show_macros: bool,
    days_inactive: int = 0,
    goal: str = "",
) -> list[str]:
    """Pick the right message pool for the user's current state.

    Branching rules (highest priority first):
      - breakfast: streak>0 -> streak pool; streak==0 with recent activity
        (1-2 days inactive) -> recovery pool; otherwise new-user pool
      - lunch/snack/dinner: meal_count>0 -> progress pool;
        meal_count==0 & streak>0 -> streak-save pool;
        meal_count==0 & streak==0 with 1-2 days inactive -> recovery pool;
        meal_count==0 & streak==0 -> new-user pool

    A "recovery" user is one whose streak just broke (last meal was yesterday
    or 2 days ago). They need different copy from a brand-new signup, since
    they already know how to log - the friction is restart, not onboarding.

    Macro-augment pool is mixed in only when the user opted into showing
    macros AND has data worth displaying (meal_count > 0).
    """
    is_recovery = streak == 0 and 1 <= days_inactive < 3

    if meal_name == "breakfast":
        if streak > 0:
            base = _BREAKFAST_STREAK
        elif is_recovery:
            base = _BREAKFAST_RECOVERY
        else:
            base = _BREAKFAST_NEW
    else:
        if meal_count > 0:
            base = {
                "lunch": _LUNCH_PROGRESS,
                "snack": _SNACK_PROGRESS,
                "dinner": _DINNER_PROGRESS,
            }[meal_name]
        elif streak > 0:
            base = {
                "lunch": _LUNCH_STREAK_SAVE,
                "snack": _SNACK_STREAK_SAVE,
                "dinner": _DINNER_STREAK_SAVE,
            }[meal_name]
        elif is_recovery:
            base = {
                "lunch": _LUNCH_RECOVERY,
                "snack": _SNACK_RECOVERY,
                "dinner": _DINNER_RECOVERY,
            }[meal_name]
        else:
            base = {
                "lunch": _LUNCH_NEW,
                "snack": _SNACK_NEW,
                "dinner": _DINNER_NEW,
            }[meal_name]

    if show_macros and meal_count > 0 and meal_name in _MACRO_AUGMENT:
        augmented = base + _MACRO_AUGMENT[meal_name]
        goal_pool = _GOAL_AWARE_BY_GOAL_AND_MEAL.get((goal, meal_name))
        if goal_pool:
            augmented = augmented + goal_pool
        return augmented
    return base


def _pick_message(pool: list[str], user_id: int, today_str: str) -> str:
    """Deterministic message selection - same user gets same message per day,
    different message each day. Avoids repeats within a 7-day window."""
    seed = hashlib.md5(f"{user_id}:{today_str}".encode()).hexdigest()
    idx = int(seed, 16) % len(pool)
    return pool[idx]


def _format_msg(
    template: str,
    *,
    name: str = "",
    streak: int = 0,
    cal: int = 0,
    target_cal: int = 2000,
    remaining_cal: int = 2000,
    protein: int = 0,
    target_protein: int = 150,
    meal_count: int = 0,
) -> str:
    """Fill in message placeholders. Gracefully handles missing keys."""
    name_greeting = f", {name}" if name else ""
    try:
        return template.format(
            name=name,
            name_greeting=name_greeting,
            streak=streak,
            streak_plus_1=streak + 1,
            cal=cal,
            target_cal=target_cal,
            remaining_cal=remaining_cal,
            protein=protein,
            target_protein=target_protein,
            meal_count=meal_count,
        )
    except (KeyError, IndexError):
        return template  # Return raw template if formatting fails


async def _get_streak(db_path: str, user_id: int, today_str: str) -> int:
    """Get current streak for a user."""
    from src.db import get_db
    async with get_db(db_path) as db:
        rows = await (await db.execute(
            "SELECT DISTINCT substr(logged_at, 1, 10) FROM meal_logs WHERE user_id = ? ORDER BY logged_at DESC",
            (user_id,),
        )).fetchall()
        dates = [r[0] for r in rows]

        # Get shielded dates
        shield_rows = await (await db.execute(
            "SELECT bridged_date FROM streak_shields WHERE user_id = ? AND bridged_date IS NOT NULL",
            (user_id,),
        )).fetchall()
        shielded = {r[0] for r in shield_rows if r[0]}

    return _calculate_streak(dates, today_str, shielded)


# Threshold for the meal-skip pattern: only suppress reminders for a
# consistently-skipped meal once we have enough data. A user with <7 active
# days in the lookback window is too new for the signal to be reliable.
_MEAL_SKIP_MIN_ACTIVE_DAYS = 7


def _user_skips_meal(meal_name: str, pattern: dict) -> bool:
    """True if the user is consistently active but never logs this specific
    meal. Used to suppress reminders for meals the user clearly skips by
    routine (e.g., intermittent fasters who never log breakfast)."""
    total = pattern.get("total_days", 0)
    if total < _MEAL_SKIP_MIN_ACTIVE_DAYS:
        return False
    key = {
        "breakfast": "breakfast_days",
        "lunch": "lunch_days",
        "snack": "snack_days",
        "dinner": "dinner_days",
    }.get(meal_name)
    if key is None:
        return False
    return pattern.get(key, 0) == 0


def _in_quiet_hours(local_hour: int, start: int, end: int) -> bool:
    """True if local_hour falls in the [start, end) quiet window. The window
    wraps midnight when start > end (e.g., 23 -> 7 means 23, 0, 1, ..., 6)."""
    if start == end:
        return False
    if start < end:
        return start <= local_hour < end
    return local_hour >= start or local_hour < end


async def _days_inactive(db_path: str, user_id: int, today_str: str) -> int:
    """Days since the user's last logged meal. 0 if logged today, large int
    (treat as new user) if no meals at all."""
    from datetime import date as _date
    last_meal = await get_last_meal(db_path, user_id)
    if not last_meal:
        return 10**6  # No history - caller decides what to do
    last_date_str = (last_meal.get("logged_at") or "")[:10]
    if not last_date_str:
        return 10**6
    try:
        return (_date.fromisoformat(today_str) - _date.fromisoformat(last_date_str)).days
    except ValueError:
        return 10**6


async def _send_to_subs_with_rollback(
    db_path: str,
    user_id: int,
    subs: list[dict],
    *,
    title: str,
    body: str,
    url: str,
    tag: str,
) -> int:
    """Send to every device, return success count. Expired subs are deleted.
    If zero devices accepted the push but at least one returned a transient
    error, the reminder_sent claim is rolled back so the next dispatch tick
    can retry. Without this rollback, a single transient failure permanently
    silences the reminder for the day."""
    success_count = 0
    transient_count = 0
    for sub in subs:
        sub_info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
        }
        result = await send_push_notification(
            sub_info, title=title, body=body, url=url, tag=tag,
        )
        if result is True:
            success_count += 1
        elif result is False:
            await delete_push_subscription(db_path, user_id, sub["endpoint"])
        else:
            transient_count += 1
    if success_count == 0 and transient_count > 0:
        try:
            await delete_reminder_sent_row(db_path, user_id, tag)
        except Exception:
            logger.exception("Failed to roll back reminder_sent for user_id=%s tag=%s", user_id, tag)
    return success_count


async def notify_shield_earned(db_path: str, user_id: int, count: int) -> None:
    """Fire-and-forget push when streak shield(s) were just awarded.

    Today the in-app Header toast announces this when the user is on the page,
    but a user who earned shields via webhook-triggered meal logs (Strava,
    Fitbit, Oura) may not be in the app for hours. Push surfaces it immediately.

    Idempotent within a 24h window via the reminder_sent table - if multiple
    callers fire close together (rare but possible during badge re-evaluation),
    only the first goes out.
    """
    if count <= 0:
        return
    try:
        from datetime import date as _date
        from src.db import get_push_subscriptions
        today_str = _date.today().isoformat()
        tag = f"shield-earned-{today_str}-{count}"
        if not await try_mark_reminder_sent(db_path, user_id, tag):
            return
        subs = await get_push_subscriptions(db_path, user_id)
        if not subs:
            return
        title = "🛡️ Shield earned"
        if count == 1:
            body = "You earned a streak shield. It auto-protects a missed day if you slip."
        else:
            body = f"You earned {count} streak shields. They auto-protect missed days."
        await _send_to_subs_with_rollback(
            db_path, user_id, subs,
            title=title, body=body, url="/macro_app/achievements", tag=tag,
        )
    except Exception:
        logger.exception("notify_shield_earned failed for user_id=%s", user_id)


async def notify_shield_consumed(
    db_path: str, user_id: int, bridged_dates: list[str]
) -> None:
    """Fire-and-forget push when streak shield(s) bridged a missed day.

    The dashboard's frontend toast already surfaces a single bridge event when
    the user opens the app, but it only renders one of multiple bridged dates
    (Header.tsx takes shield_used_dates[0]). A push with the full date range
    closes the gap for batch consumption.
    """
    if not bridged_dates:
        return
    try:
        from src.db import get_push_subscriptions
        sorted_dates = sorted(bridged_dates)
        # Tag includes first bridged date so re-runs of auto_consume that
        # happen to bridge the *same* dates dedupe, while a new bridge on a
        # different day still notifies.
        tag = f"shield-consumed-{sorted_dates[0]}-{len(sorted_dates)}"
        if not await try_mark_reminder_sent(db_path, user_id, tag):
            return
        subs = await get_push_subscriptions(db_path, user_id)
        if not subs:
            return
        n = len(sorted_dates)
        title = "🛡️ Shield saved your streak"
        if n == 1:
            body = f"A streak shield bridged {sorted_dates[0]}. Your streak's intact."
        else:
            body = (
                f"{n} shields bridged {sorted_dates[0]} to {sorted_dates[-1]}. "
                "Your streak survived."
            )
        await _send_to_subs_with_rollback(
            db_path, user_id, subs,
            title=title, body=body, url="/macro_app/achievements", tag=tag,
        )
    except Exception:
        logger.exception("notify_shield_consumed failed for user_id=%s", user_id)


async def _dispatch_push_reminders(db_path: str) -> None:
    """Check all users with push subscriptions and send reminders if due."""
    all_subs = await get_all_push_subscriptions(db_path)
    if not all_subs:
        return

    user_subs: dict[int, list[dict]] = {}
    for sub in all_subs:
        user_subs.setdefault(sub["user_id"], []).append(sub)

    for user_id, subs in user_subs.items():
        try:
            prefs = await get_user_prefs(db_path, user_id)
        except Exception:
            continue

        if not prefs.get("reminders_on", 1):
            continue

        try:
            tz = zoneinfo.ZoneInfo(prefs["timezone"])
        except Exception:
            tz = zoneinfo.ZoneInfo("UTC")

        user_now = datetime.now(tz)
        local_hour = user_now.hour
        today_str = user_now.date().isoformat()

        try:
            # Check which reminders are due before fetching data
            meal_hours = {
                "breakfast": prefs["breakfast_hour"],
                "lunch": prefs["lunch_hour"],
                "snack": prefs["snack_hour"],
                "dinner": prefs["dinner_hour"],
            }

            # Determine which meals need reminders this hour
            due_meals: list[tuple[str, int, bool]] = []  # (meal_name, meal_hour, is_primary)
            for meal_name, meal_hour in meal_hours.items():
                if local_hour == meal_hour:
                    due_meals.append((meal_name, meal_hour, True))
                elif local_hour == (meal_hour + 2) % 24:
                    due_meals.append((meal_name, meal_hour, False))

            is_streak_alert_hour = local_hour == prefs["streak_alert_hour"]

            if not due_meals and not is_streak_alert_hour:
                continue  # Nothing due this hour - skip data fetching

            # Fetch user data (only when at least one reminder is due)
            totals = await get_today_totals(db_path, user_id, today_str)
            target = await get_user_target(db_path, user_id)
            profile = await get_user_profile(db_path, user_id)
            streak = await _get_streak(db_path, user_id, today_str)

            cal = int(totals.get("calories", 0))
            target_cal = int(target["calories"]) if target else 2000
            target_protein = int(target["protein"]) if target else 150
            remaining_cal = max(0, target_cal - cal)
            protein = int(totals.get("protein", 0))
            meal_count = int(totals.get("meal_count", 0))
            name = profile.get("first_name", "") or ""
            goal = (profile.get("goal") or "").strip().lower()

            # B22: don't surface specific macro/calorie numbers on lock-screen
            # push notifications unless the user has explicitly opted in via
            # `notif_show_macros`. Default is False (privacy-first): people
            # routinely show their phones to friends/coworkers and lock-screen
            # notifications are visible without unlocking. The privacy guarantee
            # is now enforced by pool selection (private pools never reference
            # macro placeholders), so msg_kwargs always carries real numbers.
            show_macros = bool(prefs.get("notif_show_macros", False))

            quiet_start = int(prefs.get("quiet_hours_start", 23))
            quiet_end = int(prefs.get("quiet_hours_end", 7))
            in_quiet_hours = _in_quiet_hours(local_hour, quiet_start, quiet_end)

            # Dormant detection. A user with no meals today AND no streak is
            # the only candidate (an active streak means they logged within the
            # last 1-2 days). Compute lazily to avoid an extra query for the
            # common case of an actively-logging user.
            is_dormant = False
            days_inactive = 0
            if meal_count == 0 and streak == 0:
                try:
                    days_inactive = await _days_inactive(db_path, user_id, today_str)
                    is_dormant = days_inactive >= 3
                except Exception:
                    pass  # Non-critical; treat as not dormant

            # 14-day meal-window pattern. Used to suppress reminders for meals
            # the user consistently skips (intermittent fasters never logging
            # breakfast, etc). Skip the query when there are no meal reminders
            # due this hour - patterns are only consulted in the meal loop.
            meal_pattern: dict = {}
            if due_meals:
                try:
                    meal_pattern = await get_meal_window_pattern(db_path, user_id, today_str)
                except Exception:
                    meal_pattern = {}  # Non-critical; treat as no pattern data

            msg_kwargs = dict(
                name=name, streak=streak, cal=cal,
                target_cal=target_cal, remaining_cal=remaining_cal,
                protein=protein, target_protein=target_protein,
                meal_count=meal_count,
            )

            # Send meal reminders
            for meal_name, meal_hour, is_primary in due_meals:
                # Dormant users (3+ days inactive) get a single re-engagement
                # push at streak_alert_hour. Sending breakfast/lunch/snack/dinner
                # reminders all day on top of that drowns the dormant message
                # and feels tone-deaf - they aren't choosing to skip meals,
                # they're absent from the app.
                if is_dormant:
                    continue

                # Skip-by-routine: a user with 7+ active days in the past 2
                # weeks who has never logged this meal type isn't going to
                # start because of a reminder. Suppress to reduce noise.
                if _user_skips_meal(meal_name, meal_pattern):
                    continue

                # Quiet hours suppress follow-ups (which can wrap into late
                # night when dinner_hour is 22-23). Primary reminders fire at
                # user-configured hours regardless - the user picked them.
                if not is_primary and in_quiet_hours:
                    continue

                w_start, w_end = _MEAL_WINDOWS[meal_name]
                try:
                    already_logged = await has_meal_in_window(db_path, user_id, today_str, w_start, w_end)
                except Exception:
                    continue
                if already_logged:
                    continue

                if is_primary:
                    pool = _select_pool(
                        meal_name,
                        meal_count=meal_count,
                        streak=streak,
                        show_macros=show_macros,
                        days_inactive=days_inactive,
                        goal=goal,
                    )
                    tag_suffix = ""
                else:
                    pool = _FOLLOWUP_MSGS.get(meal_name) or _select_pool(
                        meal_name,
                        meal_count=meal_count,
                        streak=streak,
                        show_macros=show_macros,
                        days_inactive=days_inactive,
                        goal=goal,
                    )
                    tag_suffix = "-followup"

                tag = f"reminder-{meal_name}{tag_suffix}-{today_str}"

                # Idempotency: skip if this exact tag was already sent to the
                # user today. Catches mid-hour service restarts that re-enter
                # the same meal_hour window. If the actual push send all fail
                # transiently, _send_to_subs_with_rollback will undo this claim
                # so the next tick can retry.
                if not await try_mark_reminder_sent(db_path, user_id, tag):
                    continue

                # Race-tight recheck: the user may have logged the meal
                # between the top-of-hour tick and now (dispatch of all
                # users can take seconds under load).
                try:
                    if await has_meal_in_window(db_path, user_id, today_str, w_start, w_end):
                        continue
                except Exception:
                    pass

                template = _pick_message(pool, user_id, today_str)
                body = _format_msg(template, **msg_kwargs)

                emoji = _MEAL_EMOJI.get(meal_name, "\u23f0")
                title = f"{emoji} {meal_name.capitalize()} Reminder"
                await _send_to_subs_with_rollback(
                    db_path, user_id, subs,
                    title=title, body=body, url="/macro_app/log", tag=tag,
                )

            # Streak alert OR dormant re-engagement (not both - avoids double notification).
            # Skip both when gamification is off - streak/dormant pings are
            # gamification-flavored and shouldn't be sent to users who opted out.
            gamification_pref = (prefs.get("gamification") or "full").lower()
            gamification_on = gamification_pref != "off"

            # Don't tell users their streak is at risk when shields will save it -
            # the auto-bridge on dashboard read will silently consume one when
            # the day rolls over. The "save your streak!" push is then a false alarm.
            shield_protected = False
            if is_streak_alert_hour and meal_count == 0 and streak > 0:
                try:
                    shield_info = await get_streak_shields(db_path, user_id)
                    shield_protected = shield_info["available"] > 0
                except Exception:
                    pass  # Non-critical; fall through to default behavior

            if is_streak_alert_hour and meal_count == 0 and gamification_on and not shield_protected:
                # Dormant re-engagement takes precedence over the standard
                # streak alert when the user is 3+ days inactive (precomputed
                # above as is_dormant).
                sent_dormant = False
                if is_dormant:
                    dormant_tag = f"dormant-{today_str}"
                    if await try_mark_reminder_sent(db_path, user_id, dormant_tag):
                        template = _pick_message(_DORMANT_MSGS, user_id, today_str)
                        body = _format_msg(template, **msg_kwargs)
                        await _send_to_subs_with_rollback(
                            db_path, user_id, subs,
                            title="\U0001f44b Coming back?",
                            body=body, url="/macro_app/log", tag=dormant_tag,
                        )
                    sent_dormant = True

                # Regular streak alert (only if dormant message wasn't sent)
                if not sent_dormant:
                    streak_tag = f"streak-{today_str}"
                    if await try_mark_reminder_sent(db_path, user_id, streak_tag):
                        if streak >= 14:
                            pool = _STREAK_ALERT_LONG
                        elif streak > 0:
                            pool = _STREAK_ALERT_SHORT
                        else:
                            pool = _STREAK_ALERT_NO_STREAK
                        template = _pick_message(pool, user_id, today_str)
                        body = _format_msg(template, **msg_kwargs)
                        await _send_to_subs_with_rollback(
                            db_path, user_id, subs,
                            title="\u26a0\ufe0f Streak Alert",
                            body=body, url="/macro_app/log", tag=streak_tag,
                        )
        except Exception:
            logger.exception("Push reminder processing failed for user_id=%s", user_id)


def _seconds_until_next_tick(now: datetime | None = None, fire_second: int = 5) -> float:
    """Seconds until the next HH:00:<fire_second> UTC boundary.

    Anchoring ticks to wall clock keeps meal reminders predictable
    regardless of when the service was last restarted. Without this,
    a restart at e.g. 22:48 means every subsequent tick lands at :48
    of the hour - users who log a few minutes after :48 race the
    reminder.
    """
    now = now or datetime.now(timezone.utc)
    next_tick = (now + timedelta(hours=1)).replace(
        minute=0, second=fire_second, microsecond=0
    )
    return max(1.0, (next_tick - now).total_seconds())


async def push_reminder_loop(db_path: str) -> None:
    """Run the push reminder dispatcher once per hour, aligned to wall clock."""
    while True:
        await asyncio.sleep(_seconds_until_next_tick())
        try:
            await _dispatch_push_reminders(db_path)
        except Exception:
            logger.exception("Push reminder dispatch failed")
        # Prune the idempotency table opportunistically (cheap).
        try:
            await purge_old_reminder_sent(db_path)
        except Exception:
            logger.exception("Reminder idempotency prune failed")
