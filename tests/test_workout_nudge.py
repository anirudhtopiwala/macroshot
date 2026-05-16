"""Tests for post-workout nutrition nudge (src/web/workout_nudge.py)."""

import os
import time
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from src.web import workout_nudge
from src.web.workout_nudge import send_workout_nudge, _last_nudge, _COOLDOWN_SECONDS, _day_nudged


# ── Helpers ──────────────────────────────────────────────────────

FAKE_SUB = {
    "user_id": 1,
    "endpoint": "https://push.example.com/sub1",
    "p256dh": "key_p256dh",
    "auth": "key_auth",
}

DEFAULT_PREFS = {
    "reminders_on": 1,
    "timezone": "UTC",
}


@pytest.fixture(autouse=True)
def clear_cooldown():
    """Clear the in-memory cooldown trackers between tests."""
    _last_nudge.clear()
    _day_nudged.clear()
    yield
    _last_nudge.clear()
    _day_nudged.clear()


def _make_datetime(hour: int, tz_name: str = "UTC"):
    """Create a datetime with a specific hour for quiet-hours testing."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo(tz_name)
    return datetime(2026, 3, 28, hour, 30, 0, tzinfo=tz)


# ── Tests ────────────────────────────────────────────────────────


async def test_returns_false_when_no_push_subscriptions():
    """Returns False when user has no push subscriptions."""
    with (
        patch("src.web.workout_nudge.get_user_prefs", new_callable=AsyncMock, return_value=DEFAULT_PREFS),
        patch("src.web.workout_nudge.get_all_push_subscriptions", new_callable=AsyncMock, return_value=[]) as mock_subs,
        patch("src.web.workout_nudge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = _make_datetime(12)  # noon - outside quiet hours
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await send_workout_nudge("/fake/db", user_id=1, activity_type="Run")
        assert result is False
        mock_subs.assert_called_once()  # verify we actually reached the subscription check


async def test_returns_false_when_reminders_off():
    """Returns False when reminders_on is 0."""
    prefs = {**DEFAULT_PREFS, "reminders_on": 0}
    with patch("src.web.workout_nudge.get_user_prefs", new_callable=AsyncMock, return_value=prefs):
        result = await send_workout_nudge("/fake/db", user_id=1, activity_type="Run")
        assert result is False


async def test_returns_false_during_quiet_hours_late_night():
    """Returns False during quiet hours (10 PM - 7 AM) - testing 11 PM."""
    with (
        patch("src.web.workout_nudge.get_user_prefs", new_callable=AsyncMock, return_value=DEFAULT_PREFS),
        patch("src.web.workout_nudge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = _make_datetime(23)  # 11 PM
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await send_workout_nudge("/fake/db", user_id=1, activity_type="Run")
        assert result is False


async def test_returns_false_during_quiet_hours_early_morning():
    """Returns False during quiet hours - testing 5 AM."""
    with (
        patch("src.web.workout_nudge.get_user_prefs", new_callable=AsyncMock, return_value=DEFAULT_PREFS),
        patch("src.web.workout_nudge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = _make_datetime(5)  # 5 AM
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = await send_workout_nudge("/fake/db", user_id=1, activity_type="Run")
        assert result is False


async def test_returns_false_within_cooldown():
    """Returns False when a nudge was sent within the 4-hour cooldown."""
    # Simulate a recent nudge
    _last_nudge[1] = time.time()
    result = await send_workout_nudge("/fake/db", user_id=1, activity_type="Run")
    assert result is False


async def test_sends_notification_with_correct_format():
    """Sends notification with correct title and body format."""
    with (
        patch("src.web.workout_nudge.get_user_prefs", new_callable=AsyncMock, return_value=DEFAULT_PREFS),
        patch("src.web.workout_nudge.get_all_push_subscriptions", new_callable=AsyncMock, return_value=[FAKE_SUB]),
        patch("src.web.workout_nudge.get_today_totals", new_callable=AsyncMock, return_value={"protein": 50.0}),
        patch("src.web.workout_nudge.get_user_target", new_callable=AsyncMock, return_value=None),
        patch("src.web.workout_nudge.send_push_notification", new_callable=AsyncMock, return_value=True) as mock_send,
        patch("src.web.workout_nudge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = _make_datetime(12)  # noon
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        result = await send_workout_nudge(
            "/fake/db", user_id=1, activity_type="morning_run", calories_burned=350
        )
        assert result is True

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args[1]
        assert call_kwargs["title"] == "\U0001f4aa Morning Run complete!"
        assert "350 cal burned" in call_kwargs["body"]
        assert "protein-rich meal" in call_kwargs["body"]
        assert call_kwargs["url"] == "/macro_app/log"
        assert "workout-nudge-" in call_kwargs["tag"]


async def test_includes_protein_remaining_when_target_exists():
    """Body includes protein remaining text when user has a target."""
    target = {"protein": 150.0, "calories": 2000.0, "carbs": 200.0, "fat": 60.0}
    totals = {"protein": 80.0, "calories": 1200.0, "carbs": 100.0, "fat": 30.0}

    with (
        patch("src.web.workout_nudge.get_user_prefs", new_callable=AsyncMock, return_value=DEFAULT_PREFS),
        patch("src.web.workout_nudge.get_all_push_subscriptions", new_callable=AsyncMock, return_value=[FAKE_SUB]),
        patch("src.web.workout_nudge.get_today_totals", new_callable=AsyncMock, return_value=totals),
        patch("src.web.workout_nudge.get_user_target", new_callable=AsyncMock, return_value=target),
        patch("src.web.workout_nudge.send_push_notification", new_callable=AsyncMock, return_value=True) as mock_send,
        patch("src.web.workout_nudge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = _make_datetime(14)  # 2 PM
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        result = await send_workout_nudge("/fake/db", user_id=1, activity_type="Cycling")
        assert result is True

        call_kwargs = mock_send.call_args[1]
        # 150 - 80 = 70g protein remaining
        assert "70g protein still to go today" in call_kwargs["body"]


async def test_cooldown_respected_between_calls():
    """Second call within 4 hours returns False even with valid setup."""
    with (
        patch("src.web.workout_nudge.get_user_prefs", new_callable=AsyncMock, return_value=DEFAULT_PREFS),
        patch("src.web.workout_nudge.get_all_push_subscriptions", new_callable=AsyncMock, return_value=[FAKE_SUB]),
        patch("src.web.workout_nudge.get_today_totals", new_callable=AsyncMock, return_value={"protein": 0.0}),
        patch("src.web.workout_nudge.get_user_target", new_callable=AsyncMock, return_value=None),
        patch("src.web.workout_nudge.send_push_notification", new_callable=AsyncMock, return_value=True),
        patch("src.web.workout_nudge.datetime") as mock_dt,
    ):
        mock_dt.now.return_value = _make_datetime(12)
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        # First call succeeds
        result1 = await send_workout_nudge("/fake/db", user_id=1, activity_type="Run")
        assert result1 is True

        # Second call within cooldown returns False
        result2 = await send_workout_nudge("/fake/db", user_id=1, activity_type="Run")
        assert result2 is False
