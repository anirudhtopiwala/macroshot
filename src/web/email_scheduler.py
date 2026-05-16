"""Background task: onboarding drip emails + re-engagement for new users.

Runs every 60 minutes (same cadence as push_scheduler). For each user
with newsletter_opt_in=1, checks signup age and activity to decide
which email is due next. Sends at most one email per user per run.

Email keys and timing:
  welcome        - immediate (sent inline at signup, not by this loop)
  onboarding_d2  - day 2+  (branches on meal_count > 0)
  onboarding_d5  - day 5+  (only if < 5 meals logged)
  onboarding_d7  - day 7+  (branches on meal_count > 0)
  reengage_d5    - 5+ days since last meal (only after onboarding_d7 sent)
  sunset_d14     - 14+ days since last meal (only after reengage_d5 sent)

Timezone-aware: emails are only dispatched between 8 AM and 10 AM in the
user's local timezone so they arrive at a reasonable morning hour.
"""

import asyncio
import logging
import os
import zoneinfo
from datetime import date, datetime

from src.db import (
    count_user_meals,
    get_emails_sent,
    get_engagement_eligible_users,
    get_last_meal_date,
    record_email_sent,
)
from src.web.email_templates import (
    SUBJECTS,
    day2_active_html,
    day2_active_text,
    day2_inactive_html,
    day2_inactive_text,
    day5_feature_html,
    day5_feature_text,
    day7_active_html,
    day7_active_text,
    day7_inactive_html,
    day7_inactive_text,
    reengage_html,
    reengage_text,
    sunset_html,
    sunset_text,
)

logger = logging.getLogger("macro_app")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "")
# "{First name} from MacroShot <noreply@...>" - personal touch if the
# operator set OPERATOR_FIRST_NAME; otherwise fall back to the plain brand.
_OPERATOR_FIRST_NAME = os.environ.get("OPERATOR_FIRST_NAME", "").strip()
if _OPERATOR_FIRST_NAME and RESEND_FROM_EMAIL:
    RESEND_FROM = f"{_OPERATOR_FIRST_NAME} from MacroShot <{RESEND_FROM_EMAIL}>"
else:
    RESEND_FROM = f"MacroShot <{RESEND_FROM_EMAIL}>" if RESEND_FROM_EMAIL else ""

# Hours (in user's local timezone) during which we'll send engagement emails.
# Avoids 3 AM sends. Wide enough window (8-10) so the hourly loop catches it.
_SEND_WINDOW_START = 8
_SEND_WINDOW_END = 10


def _unsubscribe_url_for(user_id: int) -> str | None:
    """Return a one-click unsubscribe URL for a given user, or None.

    HMAC-tied to JWT_SECRET so attackers can't forge a URL that flips
    another user's newsletter pref. APP_URL must be configured.
    """
    import hashlib as _hashlib
    import hmac as _hmac

    from src.web.auth import JWT_SECRET

    app_url = os.environ.get("APP_URL", "").strip()
    if not app_url:
        return None
    base_path = os.environ.get("BASE_PATH", "/macro_app")
    sig = _hmac.new(
        JWT_SECRET.encode(), f"unsub:{user_id}".encode(), _hashlib.sha256,
    ).hexdigest()[:32]
    # Strip any trailing slash from app_url before composing.
    base = app_url.rstrip("/")
    return f"{base}{base_path}/api/v1/auth/unsubscribe?u={user_id}&t={sig}"


async def _send_email(
    to: str,
    subject: str,
    html: str,
    text: str,
    user_id: int | None = None,
    is_transactional: bool = False,
) -> bool:
    """Send via Resend. Returns True on success.

    A12: when `user_id` is provided AND the email is non-transactional
    (newsletter / engagement / re-engagement), set RFC 8058
    List-Unsubscribe + List-Unsubscribe-Post headers so Gmail and Apple
    Mail render their native one-click unsubscribe button. Skipped for
    transactional mail (PIN, waitlist confirmation) where unsubscribing
    would break critical flows.
    """
    if not RESEND_API_KEY or not RESEND_FROM:
        logger.debug(
            "Engagement email skipped to %s - %s not configured",
            to,
            "RESEND_API_KEY" if not RESEND_API_KEY else "RESEND_FROM_EMAIL",
        )
        return False

    import resend

    if not getattr(resend, "_api_key_set", False):
        resend.api_key = RESEND_API_KEY
        resend._api_key_set = True  # type: ignore[attr-defined]

    payload = {
        "from": RESEND_FROM,
        "to": to,
        "subject": subject,
        "html": html,
        "text": text,
    }
    if user_id is not None and not is_transactional:
        unsub_url = _unsubscribe_url_for(user_id)
        if unsub_url:
            payload["headers"] = {
                "List-Unsubscribe": f"<{unsub_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }

    try:
        await asyncio.wait_for(
            asyncio.to_thread(resend.Emails.send, payload), timeout=10.0
        )
        return True
    except Exception:
        # A17: don't log the raw `to` address - replace with sha256 prefix.
        import hashlib as _hashlib
        to_hash = _hashlib.sha256((to or "").encode()).hexdigest()[:12]
        logger.exception("Engagement email failed: subject=%r to_hash=%s", subject, to_hash)
        return False


def _days_since_signup(signed_up_at: str, today: date) -> int:
    """Return number of whole days since the user signed up."""
    try:
        signup_date = datetime.fromisoformat(signed_up_at).date()
    except (ValueError, TypeError):
        signup_date = date.fromisoformat(signed_up_at[:10])
    return (today - signup_date).days


async def _get_streak(db_path: str, user_id: int) -> int:
    """Quick streak count - reuse the push_scheduler helper."""
    try:
        from src.web.push_scheduler import _get_streak as _ps_streak

        today_str = date.today().isoformat()
        return await _ps_streak(db_path, user_id, today_str)
    except Exception:
        return 0


async def _process_user(
    db_path: str,
    user: dict,
    today: date,
) -> None:
    """Decide which email (if any) to send this user, and send it."""
    user_id: int = user["user_id"]
    email: str = user["email"]
    name: str = user.get("first_name") or ""
    signed_up_at: str = user["signed_up_at"]

    already_sent = await get_emails_sent(db_path, user_id)
    days = _days_since_signup(signed_up_at, today)

    # Welcome is sent inline at signup - if somehow missed, send it now
    if "welcome" not in already_sent:
        # Don't send a welcome email if the user is already days old;
        # they'd get a stale "welcome" which feels wrong. Just record it
        # as sent so the sequence moves forward.
        if days >= 2:
            await record_email_sent(db_path, user_id, "welcome")
            already_sent.add("welcome")
        else:
            # Welcome should have been sent at signup; skip this user
            # until the next cycle in case the signup flow sends it.
            return

    # ── Onboarding sequence (time-based with activity branching) ──

    meal_count = await count_user_meals(db_path, user_id)

    # Day 2+: first follow-up
    if "onboarding_d2" not in already_sent and days >= 2:
        if meal_count > 0:
            html = day2_active_html(name, meal_count)
            text = day2_active_text(name, meal_count)
            subject = SUBJECTS["onboarding_d2_active"]
        else:
            html = day2_inactive_html(name)
            text = day2_inactive_text(name)
            subject = SUBJECTS["onboarding_d2_inactive"]
        if await _send_email(email, subject, html, text, user_id=user_id):
            await record_email_sent(db_path, user_id, "onboarding_d2")
        return  # One email per run per user

    # Day 5+: feature highlight (only if user has < 5 meals)
    if (
        "onboarding_d2" in already_sent
        and "onboarding_d5" not in already_sent
        and days >= 5
    ):
        if meal_count < 5:
            html = day5_feature_html(name)
            text = day5_feature_text(name)
            if await _send_email(email, SUBJECTS["onboarding_d5"], html, text, user_id=user_id):
                await record_email_sent(db_path, user_id, "onboarding_d5")
        else:
            # User is active enough - skip this email, mark as sent
            await record_email_sent(db_path, user_id, "onboarding_d5")
        return

    # Day 7+: week check-in
    if (
        "onboarding_d2" in already_sent
        and "onboarding_d7" not in already_sent
        and days >= 7
    ):
        if meal_count > 0:
            streak = await _get_streak(db_path, user_id)
            html = day7_active_html(name, meal_count, streak)
            text = day7_active_text(name, meal_count, streak)
            subject = SUBJECTS["onboarding_d7_active"]
        else:
            html = day7_inactive_html(name)
            text = day7_inactive_text(name)
            subject = SUBJECTS["onboarding_d7_inactive"]
        if await _send_email(email, subject, html, text, user_id=user_id):
            await record_email_sent(db_path, user_id, "onboarding_d7")
        return

    # ── Post-onboarding re-engagement (activity-triggered) ──

    if "onboarding_d7" not in already_sent:
        return  # Still in onboarding sequence

    last_meal = await get_last_meal_date(db_path, user_id)
    if last_meal:
        days_inactive = (today - date.fromisoformat(last_meal)).days
    else:
        # Never logged a meal - use signup date as baseline
        days_inactive = days

    # 5+ days inactive: gentle re-engagement
    if "reengage_d5" not in already_sent and days_inactive >= 5:
        html = reengage_html(name)
        text = reengage_text(name)
        if await _send_email(email, SUBJECTS["reengage_d5"], html, text, user_id=user_id):
            await record_email_sent(db_path, user_id, "reengage_d5")
        return

    # 14+ days inactive: graceful sunset
    if (
        "reengage_d5" in already_sent
        and "sunset_d14" not in already_sent
        and days_inactive >= 14
    ):
        html = sunset_html(name)
        text = sunset_text(name)
        if await _send_email(email, SUBJECTS["sunset_d14"], html, text, user_id=user_id):
            await record_email_sent(db_path, user_id, "sunset_d14")
        return


async def _dispatch_engagement_emails(db_path: str) -> None:
    """Check all eligible users and send due engagement emails."""
    users = await get_engagement_eligible_users(db_path)
    if not users:
        return

    sent_count = 0
    for user in users:
        try:
            # Timezone gate: only send during the user's morning window
            tz_name = user.get("timezone") or "America/Los_Angeles"
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                tz = zoneinfo.ZoneInfo("UTC")

            user_now = datetime.now(tz)
            local_hour = user_now.hour

            if not (_SEND_WINDOW_START <= local_hour < _SEND_WINDOW_END):
                continue

            today = user_now.date()
            await _process_user(db_path, user, today)
            sent_count += 1  # processed (may or may not have sent)
        except Exception:
            logger.exception(
                "Engagement email processing failed for user_id=%s",
                user.get("user_id"),
            )

    if sent_count:
        logger.info("Engagement email loop processed %d users", sent_count)


async def email_engagement_loop(db_path: str) -> None:
    """Run the engagement email dispatcher every 60 minutes."""
    # Initial delay: let the app fully start + stagger vs push scheduler
    await asyncio.sleep(120)
    while True:
        try:
            await _dispatch_engagement_emails(db_path)
        except Exception:
            logger.exception("Engagement email dispatch failed")
        await asyncio.sleep(3600)
