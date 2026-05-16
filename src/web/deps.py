"""FastAPI dependency injection: DB path, current user, subscription info."""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status

from src.db import (
    create_or_update_subscription,
    get_subscription,
    get_usage,
    get_web_user_by_id,
)
from src.web.auth import decode_jwt
from src.web.constants import (
    APP_MODE,
    BETA_MODE,
    FREE_IMAGE_LIMIT,
    PRO_AI_EDITS_PER_MEAL,
    PRO_CHAT_LIMIT,
    PRO_IMAGE_LIMIT,
    PRO_TEXT_MEAL_LIMIT,
)

logger = logging.getLogger("macro_app")

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "macro_app.db"))

# Sentinel limit value meaning "no cap". Mirrored to the frontend where
# any usage whose limit == -1 renders as unlimited instead of a progress bar.
UNLIMITED = -1


def get_db_path() -> str:
    """Return the SQLite database path."""
    return DB_PATH


async def get_current_user(
    db_path: Annotated[str, Depends(get_db_path)],
    session: str = Cookie(default=None, alias="__Host-macro_session"),
    legacy_session: str = Cookie(default=None, alias="macro_session"),
) -> dict:
    """Extract and verify the current user from the JWT session cookie.

    Returns a dict with user_id, email, username, first_name, google_sub.
    Raises 401 if not authenticated.

    Reads the host-prefixed cookie first; falls back to the legacy
    unprefixed cookie so users with an in-flight session at deploy time
    don't get logged out. Once the next deploy cycle elapses (>7d max
    cookie lifetime) the legacy fallback can be removed.
    """
    session = session or legacy_session
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    payload = decode_jwt(session)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    jti = payload.get("jti")
    if jti:
        from src.db import is_jwt_revoked
        if await is_jwt_revoked(db_path, jti):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session revoked",
            )

    user_id = int(payload["sub"])
    user = await get_web_user_by_id(db_path, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


# Type alias for dependency injection
CurrentUser = Annotated[dict, Depends(get_current_user)]
DbPath = Annotated[str, Depends(get_db_path)]


# ── A23: "recently authenticated" guard for sensitive ops ───────────


_SUDO_WINDOW_SECONDS = 300  # 5 minutes


async def require_recent_auth(
    session: str = Cookie(default=None, alias="__Host-macro_session"),
    legacy_session: str = Cookie(default=None, alias="macro_session"),
) -> None:
    """Reject if the JWT's `iat` is older than _SUDO_WINDOW_SECONDS.

    A23: gates OAuth disconnect (and any future sensitive op) so a long-
    lived session cookie can't be silently used to revoke integrations.
    User is expected to re-authenticate (login or PIN-verify) within the
    window before performing the action.
    """
    raw = session or legacy_session
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_jwt(raw)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid session")
    iat = payload.get("iat")
    now_ts = datetime.now(timezone.utc).timestamp()
    if not isinstance(iat, (int, float)) or (now_ts - iat) > _SUDO_WINDOW_SECONDS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "reauth_required",
                "message": (
                    "Please sign in again to confirm this action."
                ),
            },
        )


# ── Subscription dependency ─────────────────────────────────────────


@dataclass
class SubscriptionInfo:
    """Resolved subscription state for the current request.

    Limit fields use UNLIMITED (-1) to mean "no cap." Callers that need
    to enforce a quota should check `limit != UNLIMITED` before gating.
    """

    plan: str                     # 'free' | 'pro_monthly' | 'pro_yearly' | 'trial' | 'self_hosted'
    status: str                   # 'active' | 'trialing' | 'cancelled' | 'expired' | 'past_due'
    is_premium: bool              # True if the user has Pro access (any path)
    is_og: bool                   # True if user is a founding member (OG)
    app_mode: str                 # 'self' | 'hosted'
    beta_mode: bool               # True when BETA_MODE=on

    # Daily counters
    image_queries_used: int
    image_queries_limit: int      # UNLIMITED in self mode; PRO_IMAGE_LIMIT in hosted
    text_meals_used: int
    text_meals_limit: int
    chats_used: int
    chats_limit: int

    # Per-meal counter - NOT daily. meal_edits_used is tracked per session,
    # not aggregated here. Surfaced only so the frontend knows the cap.
    meal_edits_limit: int

    # Billing metadata (only meaningful outside beta when Stripe is live)
    trial_available: bool
    trial_ends_at: str | None


async def get_subscription_info(
    user: CurrentUser,
    db_path: DbPath,
) -> SubscriptionInfo:
    """Resolve the current user's subscription state.

    Branches on APP_MODE/BETA_MODE:

    - **self**: everyone is premium with UNLIMITED caps. Subscription table
      is never consulted. For local dev and self-hosters.
    - **hosted + beta**: the subscription row is expected to exist with
      plan='pro_monthly'. If missing (e.g., pre-existing user before beta
      flip), treat as Pro anyway. Limits come from PRO_*. OG users get the
      same limits - OG only affects UI and skips renewal.
    - **hosted + no beta**: classic free/trial/pro flow. Trials auto-expire
      via Stripe webhook; non-OG pros downgrade to free when
      current_period_end is past.
    """
    user_id = user["user_id"]

    # Self-host mode: short-circuit to unlimited before touching usage_tracking
    # at all. Nothing is logged, nothing is enforced.
    if APP_MODE != "hosted":
        return SubscriptionInfo(
            plan="self_hosted",
            status="active",
            is_premium=True,
            is_og=False,
            app_mode=APP_MODE,
            beta_mode=False,
            image_queries_used=0,
            image_queries_limit=UNLIMITED,
            text_meals_used=0,
            text_meals_limit=UNLIMITED,
            chats_used=0,
            chats_limit=UNLIMITED,
            meal_edits_limit=UNLIMITED,
            trial_available=False,
            trial_ends_at=None,
        )

    # Hosted mode - read usage counters in the user's local date. Keeping
    # the date derivation before the plan branches so the same today_str is
    # used for all counter lookups (consistent with how meals.py increments).
    from src.services import user_today_str
    today_str = await user_today_str(db_path, user_id)

    image_usage = await get_usage(db_path, user_id, "image_query", today_str)
    text_usage = await get_usage(db_path, user_id, "text_meal", today_str)
    chat_usage = await get_usage(db_path, user_id, "chat_session", today_str)

    sub = await get_subscription(db_path, user_id)
    is_og = bool(sub.get("is_og")) if sub else False

    # ── Hosted + beta: everyone is Pro ──────────────────────────────
    if BETA_MODE:
        # A row may or may not exist yet (old accounts from pre-beta would
        # have had 'trial' or 'free'). We report Pro either way - a separate
        # backfill migration on deploy brings rows into alignment.
        plan = sub["plan"] if sub and sub.get("plan", "").startswith("pro_") else "pro_monthly"
        sub_status = sub["status"] if sub else "active"
        return SubscriptionInfo(
            plan=plan,
            status=sub_status,
            is_premium=True,
            is_og=is_og,
            app_mode="hosted",
            beta_mode=True,
            image_queries_used=image_usage.get("used_count", 0),
            image_queries_limit=PRO_IMAGE_LIMIT,
            text_meals_used=text_usage.get("used_count", 0),
            text_meals_limit=PRO_TEXT_MEAL_LIMIT,
            chats_used=chat_usage.get("used_count", 0),
            chats_limit=PRO_CHAT_LIMIT,
            meal_edits_limit=PRO_AI_EDITS_PER_MEAL,
            trial_available=False,
            trial_ends_at=None,
        )

    # ── Hosted + no beta: classic Stripe-driven flow ───────────────
    # (Post-beta state - preserved for when BETA_MODE flips off and paying
    # users flow through Stripe checkout.)

    if sub is None:
        # No subscription row - free tier
        return SubscriptionInfo(
            plan="free",
            status="active",
            is_premium=False,
            is_og=False,
            app_mode="hosted",
            beta_mode=False,
            image_queries_used=image_usage.get("used_count", 0),
            image_queries_limit=FREE_IMAGE_LIMIT,
            text_meals_used=text_usage.get("used_count", 0),
            # Free tier gets the SAME text-meal cap as Pro. Historically
            # this was UNLIMITED, but that made free users look more
            # generous than Pro in Settings - contradicting the upsell
            # story. We bound it tighter here; operators can widen post-
            # beta by bumping FREE_TEXT_MEAL_LIMIT separately if needed.
            text_meals_limit=PRO_TEXT_MEAL_LIMIT,
            chats_used=chat_usage.get("used_count", 0),
            chats_limit=0,                  # free tier has no chat in post-beta
            meal_edits_limit=0,             # free tier has no AI edits in post-beta
            trial_available=True,
            trial_ends_at=None,
        )

    # Defensive: `plan` is NOT NULL in the schema, but `.get("plan")`
    # returns None if the column is missing (e.g., from a partial read or
    # a future schema change). Coerce to empty string so `.startswith` is
    # safe, and fall through to the free tier defaults.
    plan = sub.get("plan") or ""
    sub_status = sub.get("status") or ""
    trial_ends_at = sub.get("trial_ends_at")
    trial_used = bool(sub.get("trial_used", 0))

    # Auto-expire trial if past trial_ends_at (OG never expires)
    if not is_og and plan == "trial" and trial_ends_at:
        try:
            trial_end_dt = datetime.fromisoformat(trial_ends_at).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > trial_end_dt:
                await create_or_update_subscription(db_path, user_id, plan="free", status="expired")
                plan = "free"
                sub_status = "expired"
        except (ValueError, TypeError):
            pass

    # Cancelled subscriptions remain premium until current_period_end
    cancelled_but_active = False
    if (
        not is_og
        and plan.startswith("pro_")
        and sub_status == "cancelled"
        and sub.get("current_period_end")
    ):
        try:
            period_end = datetime.fromisoformat(sub["current_period_end"]).replace(tzinfo=timezone.utc)
            cancelled_but_active = datetime.now(timezone.utc) < period_end
        except (ValueError, TypeError):
            pass

    is_premium = (
        is_og
        or (plan.startswith("pro_") and sub_status == "active")
        or (plan == "trial" and sub_status == "trialing")
        or cancelled_but_active
    )

    image_limit = PRO_IMAGE_LIMIT if is_premium else FREE_IMAGE_LIMIT

    # OG users always see Pro plan labels in the UI, regardless of the
    # raw `plan`/`status` columns - this stops Settings from showing
    # "Plan: free, Status: expired" for an OG whose Stripe sub was
    # deleted. The underlying DB row is left alone; this is purely a
    # presentation override.
    display_plan = "pro_monthly" if is_og and not plan.startswith("pro_") else plan
    display_status = "active" if is_og else sub_status

    return SubscriptionInfo(
        plan=display_plan,
        status=display_status,
        is_premium=is_premium,
        is_og=is_og,
        app_mode="hosted",
        beta_mode=False,
        image_queries_used=image_usage.get("used_count", 0),
        image_queries_limit=image_limit,
        text_meals_used=text_usage.get("used_count", 0),
        # Free + Pro share the same text-meal cap here; free users never
        # see "more than Pro" headroom in Settings.
        text_meals_limit=PRO_TEXT_MEAL_LIMIT,
        chats_used=chat_usage.get("used_count", 0),
        chats_limit=PRO_CHAT_LIMIT if is_premium else 0,
        meal_edits_limit=PRO_AI_EDITS_PER_MEAL if is_premium else 0,
        trial_available=not trial_used,
        trial_ends_at=trial_ends_at,
    )


SubInfo = Annotated[SubscriptionInfo, Depends(get_subscription_info)]
