"""Badge evaluation engine - computes progress and awards badges after mutations."""

import logging
from datetime import date, timedelta

import aiosqlite

from src.badges import BADGES, badges_for_trigger, get_tier, get_tier_name
from src.db_pool import get_db

logger = logging.getLogger("macro_app")


# ── Progress compute functions ────────────────────────────────────────────────
# Each returns an int representing the current progress value for a badge.
# All accept (db: aiosqlite.Connection, user_id: int) and optionally context.


async def _count_total_meals(db: aiosqlite.Connection, user_id: int, **_) -> int:
    row = await (await db.execute(
        "SELECT COUNT(*) FROM meal_logs WHERE user_id = ?", (user_id,)
    )).fetchone()
    return row[0] if row else 0


async def _count_days_with_3_meals(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Count days where user logged 3+ meals."""
    row = await (await db.execute(
        """SELECT COUNT(*) FROM (
             SELECT substr(logged_at, 1, 10) AS d
             FROM meal_logs WHERE user_id = ?
             GROUP BY d HAVING COUNT(*) >= 3
           )""",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_distinct_logging_days(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Count distinct days with at least 1 meal logged."""
    row = await (await db.execute(
        "SELECT COUNT(DISTINCT substr(logged_at, 1, 10)) FROM meal_logs WHERE user_id = ?",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_photo_meals(db: aiosqlite.Connection, user_id: int, **_) -> int:
    row = await (await db.execute(
        "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? AND image_path IS NOT NULL AND image_path != ''",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_streak(db: aiosqlite.Connection, user_id: int, today_str: str = "", **_) -> tuple[int, list[str]]:
    """Current logging streak with shield protection.

    Returns (streak_count, newly_shielded_dates).

    Delegates shield bridging to db._auto_bridge_with_conn so the dashboard
    read path and the meal_accept badge eval path share one conservative
    rule: only bridge if shields can cover ALL gap days in the recent
    lookback window. This prevents the "user came back after months away,
    all shields silently burned" failure mode the prod data showed.
    """
    if not today_str:
        return 0, []

    from src.db import _auto_bridge_with_conn

    newly_shielded = await _auto_bridge_with_conn(db, user_id, today_str)

    rows = await (await db.execute(
        "SELECT DISTINCT substr(logged_at, 1, 10) FROM meal_logs WHERE user_id = ? ORDER BY logged_at DESC",
        (user_id,),
    )).fetchall()
    date_set = {r[0] for r in rows}

    shield_rows = await (await db.execute(
        "SELECT bridged_date FROM streak_shields WHERE user_id = ? AND bridged_date IS NOT NULL",
        (user_id,),
    )).fetchall()
    shielded_dates = {r[0] for r in shield_rows if r[0]}

    today = date.fromisoformat(today_str)
    all_valid = date_set | shielded_dates
    if today_str in all_valid:
        anchor = today
    else:
        yesterday_str = (today - timedelta(days=1)).isoformat()
        if yesterday_str in all_valid:
            anchor = today - timedelta(days=1)
        else:
            return 0, newly_shielded

    streak = 0
    current = anchor
    while current.isoformat() in all_valid:
        streak += 1
        current -= timedelta(days=1)
    return streak, newly_shielded


async def _count_early_bird(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Distinct days with breakfast logged before 9 AM."""
    row = await (await db.execute(
        """SELECT COUNT(DISTINCT substr(logged_at, 1, 10)) FROM meal_logs
           WHERE user_id = ? AND meal_type = 'breakfast'
           AND CAST(substr(logged_at, 12, 2) AS INTEGER) < 9""",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_full_days(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Days with all 4 of breakfast, lunch, snack, dinner logged.

    Restricts the DISTINCT count to the 4 known meal-type values. Without
    this, an empty-string or fallback "meal" row could combine with three
    real types to satisfy COUNT(DISTINCT) >= 4 - so a day with only three
    real meals (breakfast/lunch/dinner) plus one mistyped row would
    incorrectly satisfy the badge.
    """
    row = await (await db.execute(
        """SELECT COUNT(*) FROM (
             SELECT substr(logged_at, 1, 10) AS d
             FROM meal_logs
             WHERE user_id = ?
               AND meal_type IN ('breakfast', 'lunch', 'snack', 'dinner')
             GROUP BY d
             HAVING COUNT(DISTINCT meal_type) >= 4
           )""",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_bullseye_days(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Days where total calories within 10% of target."""
    row = await (await db.execute(
        """SELECT COUNT(*) FROM (
             SELECT substr(ml.logged_at, 1, 10) AS d, SUM(ml.calories) AS total_cal
             FROM meal_logs ml WHERE ml.user_id = ? GROUP BY d
           ) day_totals
           JOIN user_targets ut ON ut.user_id = ?
           WHERE ut.calories > 0
             AND day_totals.total_cal >= ut.calories * 0.9
             AND day_totals.total_cal <= ut.calories * 1.1""",
        (user_id, user_id),
    )).fetchone()
    return row[0] if row else 0


async def _count_protein_days(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Days where total protein >= 90% of target."""
    row = await (await db.execute(
        """SELECT COUNT(*) FROM (
             SELECT substr(ml.logged_at, 1, 10) AS d, SUM(ml.protein) AS total_p
             FROM meal_logs ml WHERE ml.user_id = ? GROUP BY d
           ) day_totals
           JOIN user_targets ut ON ut.user_id = ?
           WHERE ut.protein > 0
             AND day_totals.total_p >= ut.protein * 0.9
             AND day_totals.total_p <= ut.protein * 1.1""",
        (user_id, user_id),
    )).fetchone()
    return row[0] if row else 0


async def _count_macro_master_days(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Days where all 4 macros within 10% of target."""
    row = await (await db.execute(
        """SELECT COUNT(*) FROM (
             SELECT substr(ml.logged_at, 1, 10) AS d,
                    SUM(ml.calories) AS c, SUM(ml.protein) AS p,
                    SUM(ml.carbs) AS cb, SUM(ml.fat) AS f
             FROM meal_logs ml WHERE ml.user_id = ? GROUP BY d
           ) dt
           JOIN user_targets ut ON ut.user_id = ?
           WHERE ut.calories > 0 AND ut.protein > 0 AND ut.carbs > 0 AND ut.fat > 0
             AND dt.c BETWEEN ut.calories * 0.9 AND ut.calories * 1.1
             AND dt.p BETWEEN ut.protein * 0.9 AND ut.protein * 1.1
             AND dt.cb BETWEEN ut.carbs * 0.9 AND ut.carbs * 1.1
             AND dt.f BETWEEN ut.fat * 0.9 AND ut.fat * 1.1""",
        (user_id, user_id),
    )).fetchone()
    return row[0] if row else 0


async def _count_perfect_weeks(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Count weeks where all 7 days hit calorie target within 10%."""
    # Get all days that hit calorie target
    rows = await (await db.execute(
        """SELECT substr(ml.logged_at, 1, 10) AS d
           FROM meal_logs ml
           WHERE ml.user_id = ?
           GROUP BY d
           HAVING SUM(ml.calories) BETWEEN
             (SELECT calories * 0.9 FROM user_targets WHERE user_id = ?) AND
             (SELECT calories * 1.1 FROM user_targets WHERE user_id = ?)""",
        (user_id, user_id, user_id),
    )).fetchall()
    if not rows:
        return 0
    hit_dates = sorted({r[0] for r in rows})
    # Count 7-consecutive-day windows
    perfect_weeks = 0
    i = 0
    while i <= len(hit_dates) - 7:
        start = date.fromisoformat(hit_dates[i])
        end = date.fromisoformat(hit_dates[i + 6])
        if (end - start).days == 6:
            perfect_weeks += 1
            i += 7  # Skip past this week
        else:
            i += 1
    return perfect_weeks


async def _count_carbs_days(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Days where total carbs within 10% of target."""
    row = await (await db.execute(
        """SELECT COUNT(*) FROM (
             SELECT substr(ml.logged_at, 1, 10) AS d, SUM(ml.carbs) AS total_c
             FROM meal_logs ml WHERE ml.user_id = ? GROUP BY d
           ) day_totals
           JOIN user_targets ut ON ut.user_id = ?
           WHERE ut.carbs > 0
             AND day_totals.total_c >= ut.carbs * 0.9
             AND day_totals.total_c <= ut.carbs * 1.1""",
        (user_id, user_id),
    )).fetchone()
    return row[0] if row else 0


async def _count_night_owl(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Distinct days with dinner logged at or after 7 PM (hour >= 19)."""
    row = await (await db.execute(
        """SELECT COUNT(DISTINCT substr(logged_at, 1, 10)) FROM meal_logs
           WHERE user_id = ? AND meal_type = 'dinner'
           AND CAST(substr(logged_at, 12, 2) AS INTEGER) >= 19""",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_comebacks(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Count transitions from gap (2+ days without logging) to logging again.

    A comeback = user logged on day X, did NOT log on day X+1,
    and logged again on some day X+N where N >= 2.
    """
    rows = await (await db.execute(
        """SELECT DISTINCT substr(logged_at, 1, 10) AS d
           FROM meal_logs WHERE user_id = ?
           ORDER BY d""",
        (user_id,),
    )).fetchall()
    if len(rows) < 2:
        return 0
    dates = [date.fromisoformat(r[0]) for r in rows]
    comebacks = 0
    for i in range(1, len(dates)):
        gap = (dates[i] - dates[i - 1]).days
        if gap >= 2:
            comebacks += 1
    return comebacks


async def _count_unique_meals(db: aiosqlite.Connection, user_id: int, **_) -> int:
    # TRIM with explicit whitespace chars - SQLite's bare TRIM() only strips
    # ASCII spaces, so a stray tab or newline would still slip through and
    # let "Pizza" vs "Pizza\t" count as two unique meals.
    row = await (await db.execute(
        "SELECT COUNT(DISTINCT LOWER(TRIM(item_name, ' \t\n\r'))) FROM meal_logs WHERE user_id = ?",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_aliases_created(db: aiosqlite.Connection, user_id: int, **_) -> int:
    row = await (await db.execute(
        "SELECT COUNT(*) FROM meal_aliases WHERE user_id = ?", (user_id,)
    )).fetchone()
    return row[0] if row else 0


async def _count_quick_logs(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Count meals logged via quick-log alias (source='Alias')."""
    row = await (await db.execute(
        "SELECT COUNT(*) FROM meal_logs WHERE user_id = ? AND source = 'Alias'",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_weight_entries(db: aiosqlite.Connection, user_id: int, **_) -> int:
    row = await (await db.execute(
        "SELECT COUNT(*) FROM weight_logs WHERE user_id = ?", (user_id,)
    )).fetchone()
    return row[0] if row else 0


async def _count_weight_weeks(db: aiosqlite.Connection, user_id: int, **_) -> int:
    # ISO week computed in Python - SQLite's strftime supports %W (locale,
    # Monday-based with a partial "week 0") but not %G-%V (ISO year/week),
    # and %W splits ISO week 1 across two buckets at the year boundary.
    rows = await (await db.execute(
        "SELECT DISTINCT substr(logged_at, 1, 10) AS d FROM weight_logs WHERE user_id = ?",
        (user_id,),
    )).fetchall()
    iso_weeks: set[tuple[int, int]] = set()
    for r in rows:
        if not r[0]:
            continue
        try:
            d = date.fromisoformat(r[0])
        except ValueError:
            continue
        iso_year, iso_week, _ = d.isocalendar()
        iso_weeks.add((iso_year, iso_week))
    return len(iso_weeks)


async def _count_chat_sessions(db: aiosqlite.Connection, user_id: int, **_) -> int:
    row = await (await db.execute(
        "SELECT COUNT(*) FROM chat_sessions WHERE user_id = ?", (user_id,)
    )).fetchone()
    return row[0] if row else 0


async def _count_corrections(db: aiosqlite.Connection, user_id: int, **_) -> int:
    """Count meal sessions that had user corrections.

    Counts sessions where there are 2+ user-role entries in the conversation.
    The first user entry is the initial prompt; any additional user entries
    are corrections. This is robust regardless of FatSecret turns.
    """
    row = await (await db.execute(
        """SELECT COUNT(*) FROM meal_sessions
           WHERE user_id = ? AND status = 'accepted'
           AND (
             SELECT COUNT(*) FROM json_each(conversation)
             WHERE json_extract(value, '$.role') = 'user'
           ) > 1""",
        (user_id,),
    )).fetchone()
    return row[0] if row else 0


async def _count_target_sets(db: aiosqlite.Connection, user_id: int, db_path: str = "", **_) -> int:
    row = await (await db.execute(
        "SELECT COALESCE(target_set_count, 0) FROM users WHERE user_id = ?", (user_id,)
    )).fetchone()
    return row[0] if row else 0


async def _count_total_badges(db: aiosqlite.Connection, user_id: int, **_) -> int:
    row = await (await db.execute(
        "SELECT COUNT(*) FROM badge_earned WHERE user_id = ?", (user_id,)
    )).fetchone()
    return row[0] if row else 0


# ── Badge → compute function mapping ─────────────────────────────────────────

_COMPUTE = {
    "milestone_first_meal": _count_total_meals,
    "milestone_first_day": _count_days_with_3_meals,
    "milestone_first_week": _count_distinct_logging_days,
    "streak_on_a_roll": _count_streak,
    "streak_early_bird": _count_early_bird,
    "streak_night_owl": _count_night_owl,
    "streak_comeback": _count_comebacks,
    "streak_full_day": _count_full_days,
    "meals_meal_machine": _count_total_meals,
    "meals_snap_happy": _count_photo_meals,
    "target_bullseye": _count_bullseye_days,
    "target_protein": _count_protein_days,
    "target_carbs_master": _count_carbs_days,
    "target_macro_master": _count_macro_master_days,
    "target_perfect_week": _count_perfect_weeks,
    "variety_world_plate": _count_unique_meals,
    "variety_quick_draw": _count_quick_logs,
    "variety_saved_chef": _count_aliases_created,
    "weight_scale_warrior": _count_weight_entries,
    "weight_trend_setter": _count_weight_weeks,
    "explorer_coach_fav": _count_chat_sessions,
    "explorer_perfectionist": _count_corrections,
    "explorer_goal_setter": _count_target_sets,
    "meta_completionist": _count_total_badges,
}


# ── Main evaluation function ──────────────────────────────────────────────────


async def evaluate_badges(
    db_path: str,
    user_id: int,
    trigger: str,
    context: dict | None = None,
) -> list[dict]:
    """Evaluate badges for a trigger event. Returns list of newly earned/upgraded badges.

    Each entry: {"badge_id", "name", "tier", "tier_name", "is_new"}
    """
    ctx = context or {}
    badge_ids = badges_for_trigger(trigger)
    if not badge_ids:
        return []

    new_badges: list[dict] = []
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")

    async with get_db(db_path) as db:
        # Serialize the read-then-UPSERT and the shield-earning loop against
        # concurrent meal_accepts on the same user. Without this, two parallel
        # accepts can both see the same old_tier, both UPSERT the new tier,
        # and both push a "you earned this!" notification - duplicate UX.
        # Same hazard for the shield earning cap (3 unused).
        await db.execute("BEGIN IMMEDIATE")

        # Capture any shield bridges that happened inside _count_streak during
        # this badge eval - we'll surface them via push after commit.
        newly_shielded_dates: list[str] = []
        # Initialized at the top so the post-commit notify_shield_earned hook
        # below can read it safely on non-meal_accept triggers (only the
        # meal_accept branch actually awards shields, but every code path
        # reads this on the way out).
        shields_awarded_this_call = 0

        for badge_id in badge_ids:
            badge_def = BADGES[badge_id]
            compute_fn = _COMPUTE.get(badge_id)
            if not compute_fn:
                continue

            # Compute current value. _count_streak returns (streak, newly_shielded);
            # shields are already written to the DB inside _auto_bridge_with_conn.
            result = await compute_fn(db, user_id, **ctx)
            if badge_id == "streak_on_a_roll" and isinstance(result, tuple):
                value, ns = result
                if ns:
                    newly_shielded_dates.extend(ns)
            else:
                value = result

            # Determine tier
            tier = get_tier(value, badge_def["thresholds"])
            if tier < 0:
                continue  # Below bronze

            # Check if this is an upgrade
            existing = await (await db.execute(
                "SELECT tier FROM badge_earned WHERE user_id = ? AND badge_id = ?",
                (user_id, badge_id),
            )).fetchone()
            old_tier = existing["tier"] if existing else -1

            if tier <= old_tier:
                continue  # No upgrade

            # Award the badge
            await db.execute(
                """INSERT INTO badge_earned (user_id, badge_id, tier, earned_at, seen)
                   VALUES (?, ?, ?, ?, 0)
                   ON CONFLICT(user_id, badge_id) DO UPDATE SET tier = ?, earned_at = ?, seen = 0""",
                (user_id, badge_id, tier, now, tier, now),
            )

            new_badges.append({
                "badge_id": badge_id,
                "name": badge_def["name"],
                "tier": tier,
                "tier_name": get_tier_name(tier),
                "is_new": old_tier == -1,
                "icon": badge_def["icon"],
            })

        # Shield earning: award 1 shield per *run of 3 consecutive* on-target days
        # (within 20% of calorie target). Non-overlapping - a 6-day run earns 2.
        # Free users get max 1 lifetime shield; Pro users earn unlimited (capped at 3 unused).
        if trigger == "meal_accept":
            from src.db import _fetch_on_target_dates, _count_consecutive_runs_of_3
            on_target_dates = await _fetch_on_target_dates(db, user_id)
            shields_deserved = _count_consecutive_runs_of_3(on_target_dates)

            # Count total shields ever earned
            total_earned_row = await (await db.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = ?", (user_id,)
            )).fetchone()
            total_earned = total_earned_row[0] if total_earned_row else 0

            # Available (unused) shields
            avail_row = await (await db.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = ? AND used_at IS NULL", (user_id,)
            )).fetchone()
            available = avail_row[0] if avail_row else 0

            # Earn 1 shield per completed 3-day run, max 3 unused at a time
            # Free users: max 1 lifetime shield
            is_premium = ctx.get("is_premium", False)
            new_shields = max(0, shields_deserved - total_earned)
            if new_shields > 0 and available < 3:
                # Free users can only earn their first shield
                if not is_premium and total_earned >= 1:
                    pass  # Already earned their free shield
                else:
                    to_award = min(new_shields, 3 - available)
                    if not is_premium:
                        to_award = min(to_award, 1 - total_earned)  # Cap at 1 for free
                    for _ in range(max(0, to_award)):
                        await db.execute(
                            "INSERT INTO streak_shields (user_id, earned_at) VALUES (?, ?)",
                            (user_id, now),
                        )
                        shields_awarded_this_call += 1

        await db.commit()

    # After commit: surface earned shields via push so users on webhook-driven
    # log paths (Strava, Fitbit, Oura) - who may not be in the app right now -
    # find out about the reward. Fire-and-forget; in-app Header toast still
    # covers active sessions when this is redundant. Same for shield bridges
    # that happened during streak progress compute - the meal_accept path
    # bridges via _count_streak rather than auto_consume_shields_for_streak,
    # so this hook covers gaps the dashboard-load notification doesn't.
    try:
        import asyncio
        if shields_awarded_this_call > 0:
            from src.web.push_scheduler import notify_shield_earned
            asyncio.create_task(
                notify_shield_earned(db_path, user_id, shields_awarded_this_call)
            )
        if newly_shielded_dates:
            from src.web.push_scheduler import notify_shield_consumed
            asyncio.create_task(
                notify_shield_consumed(db_path, user_id, list(newly_shielded_dates))
            )
    except Exception:
        logger.exception("Failed to schedule shield push for user_id=%s", user_id)

    # If any badges earned, check meta_completionist
    if new_badges and trigger != "any_badge_earn":
        meta_badges = await evaluate_badges(db_path, user_id, "any_badge_earn", ctx)
        new_badges.extend(meta_badges)

    if new_badges:
        logger.info("User %s earned badges: %s", user_id, [b["badge_id"] for b in new_badges])
        # Private telemetry: one row per awarded/upgraded badge so admin
        # metrics can surface badge-earning pace + which badges drive
        # engagement. Recursive call guard: skip meta-badge loop's own log
        # since the outer call already appended them to new_badges above.
        if trigger != "any_badge_earn":
            try:
                from src.db import log_event
                for b in new_badges:
                    await log_event(
                        db_path, user_id, "badge_earned",
                        metadata={
                            "badge_id": b["badge_id"],
                            "tier": b["tier"],
                            "is_new": b["is_new"],
                            "trigger": trigger,
                        },
                    )
            except Exception:
                logger.debug("badge_earned telemetry failed (non-fatal)", exc_info=True)

    return new_badges


async def compute_all_badge_progress(db_path: str, user_id: int, today_str: str = "") -> list[dict]:
    """Compute progress for ALL badges. Used by the achievements page."""
    results = []

    async with get_db(db_path) as db:
        # Get all earned badges
        earned_rows = await (await db.execute(
            "SELECT badge_id, tier, earned_at, seen FROM badge_earned WHERE user_id = ?",
            (user_id,),
        )).fetchall()
        earned = {r["badge_id"]: dict(r) for r in earned_rows}

        for badge_id, badge_def in BADGES.items():
            compute_fn = _COMPUTE.get(badge_id)
            if not compute_fn:
                value = 0
            else:
                result = await compute_fn(db, user_id, today_str=today_str)
                value = result[0] if isinstance(result, tuple) else result

            tier = get_tier(value, badge_def["thresholds"])
            earned_info = earned.get(badge_id)

            # Determine next threshold
            next_threshold = None
            for t in badge_def["thresholds"]:
                if value < t:
                    next_threshold = t
                    break

            # Use the higher of computed tier vs DB tier so badges display
            # correctly even if they haven't been formally awarded yet
            # (e.g., the trigger wasn't wired when the user hit the threshold).
            db_tier = earned_info["tier"] if earned_info else -1
            display_tier = max(tier, db_tier)

            results.append({
                "badge_id": badge_id,
                "name": badge_def["name"],
                "category": badge_def["category"],
                "icon": badge_def["icon"],
                "description": badge_def["descriptions"][max(display_tier, 0)] if display_tier >= 0 else badge_def["descriptions"][0],
                "current_value": value,
                "tier": display_tier,
                "tier_name": get_tier_name(display_tier),
                "thresholds": badge_def["thresholds"],
                "next_threshold": next_threshold,
                "earned_at": earned_info["earned_at"] if earned_info else None,
                "seen": bool(earned_info["seen"]) if earned_info else True,
            })

    return results
