"""Tests for push scheduler message selection and formatting."""

from datetime import datetime, timezone

from src.web.push_scheduler import (
    _pick_message,
    _format_msg,
    _seconds_until_next_tick,
    _select_pool,
    _in_quiet_hours,
    _BREAKFAST_STREAK,
    _BREAKFAST_NEW,
    _LUNCH_PROGRESS,
    _LUNCH_STREAK_SAVE,
    _LUNCH_NEW,
    _SNACK_PROGRESS,
    _SNACK_STREAK_SAVE,
    _SNACK_NEW,
    _DINNER_PROGRESS,
    _DINNER_STREAK_SAVE,
    _DINNER_NEW,
    _LUNCH_MACRO_AUGMENT,
    _DINNER_MACRO_AUGMENT,
    _BREAKFAST_RECOVERY,
    _LUNCH_RECOVERY,
    _SNACK_RECOVERY,
    _DINNER_RECOVERY,
    _STREAK_ALERT_SHORT,
    _STREAK_ALERT_LONG,
    _STREAK_ALERT_NO_STREAK,
    _SNACK_LOSS_AWARE,
    _SNACK_GAIN_AWARE,
    _DINNER_LOSS_AWARE,
    _DINNER_GAIN_AWARE,
    _user_skips_meal,
    _MEAL_SKIP_MIN_ACTIVE_DAYS,
)


# ── _pick_message ──


def test_pick_message_deterministic():
    """Same user + date always returns the same message."""
    pool = ["a", "b", "c", "d", "e"]
    msg1 = _pick_message(pool, user_id=42, today_str="2026-03-15")
    msg2 = _pick_message(pool, user_id=42, today_str="2026-03-15")
    assert msg1 == msg2


def test_pick_message_different_dates_vary():
    """Different dates produce different selections (at least some variation)."""
    pool = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    messages = set()
    for day in range(1, 11):
        msg = _pick_message(pool, user_id=1, today_str=f"2026-03-{day:02d}")
        messages.add(msg)
    assert len(messages) >= 2, "Expected at least 2 different messages over 10 days"


def test_pick_message_different_users_may_differ():
    """Different users on the same date may get different messages."""
    pool = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    messages = set()
    for uid in range(1, 20):
        msg = _pick_message(pool, user_id=uid, today_str="2026-03-15")
        messages.add(msg)
    assert len(messages) >= 2, "Expected variation across different users"


# ── _format_msg ──


def test_format_msg_empty_name():
    """Empty name produces no name_greeting."""
    result = _format_msg("Hello{name_greeting}!", name="")
    assert result == "Hello!"


def test_format_msg_with_name():
    """Name produces ', Name' greeting."""
    result = _format_msg("Hello{name_greeting}!", name="Alice")
    assert result == "Hello, Alice!"


def test_format_msg_zero_streak():
    """Zero streak renders correctly."""
    result = _format_msg("Day {streak}, next is {streak_plus_1}", streak=0)
    assert result == "Day 0, next is 1"


def test_format_msg_all_placeholders():
    """All placeholders format correctly."""
    template = "{name} {streak} {cal}/{target_cal} {remaining_cal}cal {protein}/{target_protein}g {meal_count}meals"
    result = _format_msg(
        template,
        name="Bob", streak=7, cal=1500, target_cal=2000,
        remaining_cal=500, protein=80, target_protein=150, meal_count=3,
    )
    assert result == "Bob 7 1500/2000 500cal 80/150g 3meals"


def test_format_msg_unknown_placeholder_returns_template():
    """Unknown placeholder gracefully returns the raw template."""
    result = _format_msg("{unknown_key} test")
    assert "{unknown_key}" in result  # Returns raw template


# ── Follow-up modulo ──


def test_followup_modulo_normal():
    """Normal case: meal_hour=11, follow-up at 13."""
    assert (11 + 2) % 24 == 13


def test_followup_modulo_wraps_midnight():
    """Dinner at 22: follow-up should be at 0 (midnight)."""
    assert (22 + 2) % 24 == 0


def test_followup_modulo_wraps_past_midnight():
    """Dinner at 23: follow-up should be at 1 AM."""
    assert (23 + 2) % 24 == 1


# ── _seconds_until_next_tick ──


def test_next_tick_mid_hour():
    """From 12:49:30 UTC, next tick (top-of-hour + 5s) is ~10m35s away."""
    now = datetime(2026, 4, 15, 12, 49, 30, tzinfo=timezone.utc)
    assert abs(_seconds_until_next_tick(now) - (10 * 60 + 35)) < 1


def test_next_tick_exact_hour():
    """At HH:00:00 we should target the NEXT HH:00:05 (not the same one)."""
    now = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
    secs = _seconds_until_next_tick(now)
    # Next HH+1 fire is 60m + 5s away
    assert abs(secs - (60 * 60 + 5)) < 1


def test_next_tick_just_past_fire_second():
    """At HH:00:06 (1 second after this hour's fire), next is 59m59s away."""
    now = datetime(2026, 4, 15, 12, 0, 6, tzinfo=timezone.utc)
    secs = _seconds_until_next_tick(now)
    assert abs(secs - (59 * 60 + 59)) < 1


def test_next_tick_wraps_midnight():
    """23:59:00 → next tick is at 00:00:05 the next day (65s)."""
    now = datetime(2026, 4, 15, 23, 59, 0, tzinfo=timezone.utc)
    secs = _seconds_until_next_tick(now)
    assert abs(secs - 65) < 1


# ── _select_pool: state-branched selection ──


def test_select_pool_breakfast_with_streak():
    """Breakfast + streak>0 picks the streak pool."""
    pool = _select_pool("breakfast", meal_count=0, streak=12, show_macros=False)
    assert pool is _BREAKFAST_STREAK


def test_select_pool_breakfast_no_streak():
    """Breakfast + streak=0, never logged before, picks the new-user pool."""
    pool = _select_pool("breakfast", meal_count=0, streak=0, show_macros=False, days_inactive=0)
    assert pool is _BREAKFAST_NEW


def test_select_pool_breakfast_recovery():
    """Breakfast + streak=0 + recently active (1-2 days inactive) picks
    the recovery pool, not the new-user pool. They had a streak; it broke."""
    for di in (1, 2):
        pool = _select_pool("breakfast", meal_count=0, streak=0, show_macros=False, days_inactive=di)
        assert pool is _BREAKFAST_RECOVERY, f"days_inactive={di} should recover"


def test_select_pool_recovery_falls_back_at_3_days():
    """At 3+ days inactive the user is dormant - the dispatch loop suppresses
    meal reminders entirely, but if _select_pool is called the new-user pool
    is the safer fallback than recovery (recovery copy assumes recent rhythm)."""
    pool = _select_pool("lunch", meal_count=0, streak=0, show_macros=False, days_inactive=3)
    assert pool is _LUNCH_NEW


def test_select_pool_lunch_recovery():
    pool = _select_pool("lunch", meal_count=0, streak=0, show_macros=False, days_inactive=2)
    assert pool is _LUNCH_RECOVERY


def test_select_pool_snack_recovery():
    pool = _select_pool("snack", meal_count=0, streak=0, show_macros=False, days_inactive=1)
    assert pool is _SNACK_RECOVERY


def test_select_pool_dinner_recovery():
    pool = _select_pool("dinner", meal_count=0, streak=0, show_macros=False, days_inactive=2)
    assert pool is _DINNER_RECOVERY


def test_select_pool_goal_aware_loss_dinner():
    """lose_weight + show_macros + meal_count>0 + dinner mixes in loss-aware."""
    pool = _select_pool(
        "dinner", meal_count=2, streak=0, show_macros=True, goal="lose_weight",
    )
    for tmpl in _DINNER_LOSS_AWARE:
        assert tmpl in pool
    for tmpl in _DINNER_GAIN_AWARE:
        assert tmpl not in pool


def test_select_pool_goal_aware_gain_snack():
    pool = _select_pool(
        "snack", meal_count=1, streak=0, show_macros=True, goal="gain_weight",
    )
    for tmpl in _SNACK_GAIN_AWARE:
        assert tmpl in pool


def test_select_pool_goal_aware_skipped_in_privacy_mode():
    """Goal-aware copy references {remaining_cal} etc. - never include when
    show_macros is False."""
    pool = _select_pool(
        "dinner", meal_count=2, streak=0, show_macros=False, goal="lose_weight",
    )
    for tmpl in _DINNER_LOSS_AWARE:
        assert tmpl not in pool


def test_select_pool_goal_aware_skipped_with_no_data():
    """Goal-aware needs meal_count>0 (the {cal}/{remaining_cal} numbers are
    misleading when nothing's been logged yet)."""
    pool = _select_pool(
        "dinner", meal_count=0, streak=0, show_macros=True, goal="lose_weight",
    )
    for tmpl in _DINNER_LOSS_AWARE:
        assert tmpl not in pool


def test_select_pool_goal_aware_maintain_no_augment():
    """Maintain is the default - no goal augment, just standard macro pool."""
    pool = _select_pool(
        "dinner", meal_count=2, streak=0, show_macros=True, goal="maintain",
    )
    for tmpl in _DINNER_LOSS_AWARE + _DINNER_GAIN_AWARE:
        assert tmpl not in pool


def test_select_pool_goal_aware_lunch_unaffected():
    """No loss/gain pool exists for lunch - it stays the standard augmented set."""
    pool = _select_pool(
        "lunch", meal_count=2, streak=0, show_macros=True, goal="lose_weight",
    )
    # Should still include standard macro augment
    for tmpl in _LUNCH_MACRO_AUGMENT:
        assert tmpl in pool


def test_select_pool_streak_overrides_recovery():
    """If streak > 0, days_inactive doesn't matter - streak pool wins."""
    pool = _select_pool("lunch", meal_count=0, streak=5, show_macros=False, days_inactive=2)
    assert pool is _LUNCH_STREAK_SAVE


def test_select_pool_lunch_logged_today():
    """Lunch + meal_count>0 picks the progress pool."""
    pool = _select_pool("lunch", meal_count=1, streak=5, show_macros=False)
    assert pool is _LUNCH_PROGRESS


def test_select_pool_lunch_streak_save():
    """Lunch + nothing logged + streak>0 picks the streak-save pool."""
    pool = _select_pool("lunch", meal_count=0, streak=7, show_macros=False)
    assert pool is _LUNCH_STREAK_SAVE


def test_select_pool_lunch_new_user():
    """Lunch + nothing logged + streak=0 picks the new-user pool."""
    pool = _select_pool("lunch", meal_count=0, streak=0, show_macros=False)
    assert pool is _LUNCH_NEW


def test_select_pool_snack_state_branches():
    assert _select_pool("snack", meal_count=2, streak=3, show_macros=False) is _SNACK_PROGRESS
    assert _select_pool("snack", meal_count=0, streak=4, show_macros=False) is _SNACK_STREAK_SAVE
    assert _select_pool("snack", meal_count=0, streak=0, show_macros=False) is _SNACK_NEW


def test_select_pool_dinner_state_branches():
    assert _select_pool("dinner", meal_count=2, streak=3, show_macros=False) is _DINNER_PROGRESS
    assert _select_pool("dinner", meal_count=0, streak=4, show_macros=False) is _DINNER_STREAK_SAVE
    assert _select_pool("dinner", meal_count=0, streak=0, show_macros=False) is _DINNER_NEW


def test_select_pool_macro_augment_only_with_data_and_optin():
    """Macro-augment only mixes in when show_macros=True AND meal_count>0."""
    base = _LUNCH_PROGRESS
    augmented = _select_pool("lunch", meal_count=2, streak=0, show_macros=True)
    assert len(augmented) == len(base) + len(_LUNCH_MACRO_AUGMENT)
    assert all(t in augmented for t in _LUNCH_MACRO_AUGMENT)


def test_select_pool_no_macro_augment_when_optout():
    """Privacy mode (show_macros=False) never mixes in macro pool."""
    pool = _select_pool("lunch", meal_count=2, streak=5, show_macros=False)
    assert pool is _LUNCH_PROGRESS  # unchanged base reference
    for tmpl in _LUNCH_MACRO_AUGMENT:
        assert tmpl not in pool


def test_select_pool_no_macro_augment_when_no_meals():
    """Macro augment skipped when meal_count=0 even with opt-in (no data to show)."""
    pool = _select_pool("dinner", meal_count=0, streak=5, show_macros=True)
    assert pool is _DINNER_STREAK_SAVE
    for tmpl in _DINNER_MACRO_AUGMENT:
        assert tmpl not in pool


def test_select_pool_breakfast_never_macro_augmented():
    """Breakfast pools don't get macro augment (meal_count is always 0 in AM)."""
    pool = _select_pool("breakfast", meal_count=0, streak=5, show_macros=True)
    assert pool is _BREAKFAST_STREAK


# ── Privacy invariant: private pools must not reference macro placeholders ──


def test_private_pools_have_no_macro_placeholders():
    """Pools used in privacy mode must not reference macro placeholders -
    otherwise privacy-mode users see broken substitutions like 'Day - kcal'."""
    macro_placeholders = ["{cal}", "{target_cal}", "{remaining_cal}",
                          "{protein}", "{target_protein}", "{meal_count}"]
    private_pools = [
        ("breakfast_streak", _BREAKFAST_STREAK),
        ("breakfast_new", _BREAKFAST_NEW),
        ("breakfast_recovery", _BREAKFAST_RECOVERY),
        ("lunch_progress", _LUNCH_PROGRESS),
        ("lunch_streak_save", _LUNCH_STREAK_SAVE),
        ("lunch_new", _LUNCH_NEW),
        ("lunch_recovery", _LUNCH_RECOVERY),
        ("snack_progress", _SNACK_PROGRESS),
        ("snack_streak_save", _SNACK_STREAK_SAVE),
        ("snack_new", _SNACK_NEW),
        ("snack_recovery", _SNACK_RECOVERY),
        ("dinner_progress", _DINNER_PROGRESS),
        ("dinner_streak_save", _DINNER_STREAK_SAVE),
        ("dinner_new", _DINNER_NEW),
        ("dinner_recovery", _DINNER_RECOVERY),
    ]
    for name, pool in private_pools:
        for tmpl in pool:
            for ph in macro_placeholders:
                assert ph not in tmpl, f"{name} template leaks macro placeholder {ph}: {tmpl!r}"


def test_new_user_pools_have_no_streak_placeholders():
    """New-user pools (streak=0 path) must not reference {streak} or
    {streak_plus_1} - they'd render as 'Day 0' / 'Day 1' awkwardly."""
    streak_placeholders = ["{streak}", "{streak_plus_1}"]
    new_user_pools = [
        ("breakfast_new", _BREAKFAST_NEW),
        ("lunch_new", _LUNCH_NEW),
        ("snack_new", _SNACK_NEW),
        ("dinner_new", _DINNER_NEW),
        ("breakfast_recovery", _BREAKFAST_RECOVERY),
        ("lunch_recovery", _LUNCH_RECOVERY),
        ("snack_recovery", _SNACK_RECOVERY),
        ("dinner_recovery", _DINNER_RECOVERY),
        ("streak_alert_no_streak", _STREAK_ALERT_NO_STREAK),
    ]
    for name, pool in new_user_pools:
        for tmpl in pool:
            for ph in streak_placeholders:
                assert ph not in tmpl, f"{name} leaks streak placeholder {ph}: {tmpl!r}"


# ── _user_skips_meal: don't reminders for meals the user routinely skips ──


def test_skips_meal_classic_intermittent_faster():
    """User with 14 active days, never logged a meal in the breakfast window."""
    pattern = {
        "total_days": 14,
        "breakfast_days": 0,
        "lunch_days": 14,
        "snack_days": 5,
        "dinner_days": 14,
    }
    assert _user_skips_meal("breakfast", pattern) is True
    assert _user_skips_meal("lunch", pattern) is False
    assert _user_skips_meal("dinner", pattern) is False


def test_skips_meal_one_breakfast_keeps_reminders():
    """If they logged breakfast even once recently, keep nudging."""
    pattern = {
        "total_days": 14,
        "breakfast_days": 1,
        "lunch_days": 14,
        "snack_days": 5,
        "dinner_days": 14,
    }
    assert _user_skips_meal("breakfast", pattern) is False


def test_skips_meal_brand_new_user_not_suppressed():
    """A new user with only 3 active days hasn't generated reliable signal -
    keep sending all reminders."""
    pattern = {
        "total_days": 3,
        "breakfast_days": 0,
        "lunch_days": 3,
        "snack_days": 0,
        "dinner_days": 3,
    }
    assert _user_skips_meal("breakfast", pattern) is False
    assert _user_skips_meal("snack", pattern) is False


def test_skips_meal_at_threshold_boundary():
    """Boundary check: at exactly _MEAL_SKIP_MIN_ACTIVE_DAYS the suppression
    activates."""
    pattern = {
        "total_days": _MEAL_SKIP_MIN_ACTIVE_DAYS,
        "breakfast_days": 0,
        "lunch_days": _MEAL_SKIP_MIN_ACTIVE_DAYS,
        "snack_days": _MEAL_SKIP_MIN_ACTIVE_DAYS,
        "dinner_days": _MEAL_SKIP_MIN_ACTIVE_DAYS,
    }
    assert _user_skips_meal("breakfast", pattern) is True


def test_skips_meal_empty_pattern():
    """No data at all means no suppression - safe default."""
    assert _user_skips_meal("breakfast", {}) is False
    assert _user_skips_meal("dinner", {}) is False


# ── _in_quiet_hours: window logic with midnight wrap ──


def test_quiet_hours_default_window_wraps_midnight():
    """Default 23 -> 7 covers 23, 0-6 inclusive; 7 onward is awake."""
    quiet_hours = [23, 0, 1, 2, 3, 4, 5, 6]
    awake_hours = [7, 8, 9, 12, 17, 22]
    for h in quiet_hours:
        assert _in_quiet_hours(h, 23, 7), f"hour {h} should be quiet"
    for h in awake_hours:
        assert not _in_quiet_hours(h, 23, 7), f"hour {h} should be awake"


def test_quiet_hours_no_wrap_window():
    """A non-wrapping window (e.g., 13-15) covers only 13, 14."""
    assert _in_quiet_hours(13, 13, 15)
    assert _in_quiet_hours(14, 13, 15)
    assert not _in_quiet_hours(15, 13, 15)  # end is exclusive
    assert not _in_quiet_hours(12, 13, 15)


def test_quiet_hours_disabled_when_start_equals_end():
    """start == end means no quiet window at all (don't accidentally silence
    the entire 24h)."""
    for h in range(24):
        assert not _in_quiet_hours(h, 0, 0)
        assert not _in_quiet_hours(h, 12, 12)


def test_quiet_hours_streak_alert_at_22_not_silenced_by_default():
    """The default streak_alert_hour is 22; quiet starts at 23 by default.
    The streak alert must still fire under defaults."""
    assert not _in_quiet_hours(22, 23, 7)


def test_quiet_hours_late_dinner_followup_silenced_by_default():
    """dinner_hour=22 + 2 = 0:00, dinner_hour=23 + 2 = 1:00. Both inside
    default quiet window. This is the worst-case noise the gate fixes."""
    assert _in_quiet_hours(0, 23, 7)
    assert _in_quiet_hours(1, 23, 7)


def test_no_em_dashes_in_message_pools():
    """Em-dashes (U+2014) caused render bugs and visual asymmetry. Stick to
    plain ASCII hyphens in all notification copy."""
    all_pools = [
        _BREAKFAST_STREAK, _BREAKFAST_NEW, _BREAKFAST_RECOVERY,
        _LUNCH_PROGRESS, _LUNCH_STREAK_SAVE, _LUNCH_NEW, _LUNCH_RECOVERY,
        _SNACK_PROGRESS, _SNACK_STREAK_SAVE, _SNACK_NEW, _SNACK_RECOVERY,
        _DINNER_PROGRESS, _DINNER_STREAK_SAVE, _DINNER_NEW, _DINNER_RECOVERY,
        _LUNCH_MACRO_AUGMENT, _DINNER_MACRO_AUGMENT,
        _STREAK_ALERT_SHORT, _STREAK_ALERT_LONG, _STREAK_ALERT_NO_STREAK,
        _SNACK_LOSS_AWARE, _SNACK_GAIN_AWARE, _DINNER_LOSS_AWARE, _DINNER_GAIN_AWARE,
    ]
    for pool in all_pools:
        for tmpl in pool:
            assert "—" not in tmpl, f"em-dash in template: {tmpl!r}"
