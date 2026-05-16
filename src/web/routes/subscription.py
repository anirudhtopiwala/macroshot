"""Subscription API routes: plan status, trial, Stripe checkout/portal/webhook."""

import asyncio
import logging
import os
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, HTTPException, Request

from src.db import (
    count_aliases,
    create_or_update_subscription,
    get_subscription,
    log_event,
    start_trial,
)
from src.web.constants import (
    BETA_MODE,
    FREE_ALIAS_LIMIT,
)
from src.web.deps import CurrentUser, DbPath, SubInfo
from src.web.rate_limit import limiter
from src.web.schemas import CheckoutRequest, SubscriptionStatusResponse, TrialResponse

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/subscription", tags=["subscription"])

BASE_URL = os.environ.get("BASE_URL", "")


def _require_base_url() -> str:
    """Return BASE_URL or raise 503 so Stripe never gets a malformed URL."""
    url = os.environ.get("BASE_URL", BASE_URL).strip()
    if not url:
        raise HTTPException(
            status_code=503,
            detail="Billing is not properly configured (BASE_URL missing).",
        )
    return url


def _stripe_configured() -> bool:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    return bool(key and key.startswith(("sk_live_", "sk_test_", "rk_live_", "rk_test_")) and os.environ.get("STRIPE_PRICE_MONTHLY"))


def _init_stripe() -> None:
    stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
    # A13: pin the API version so a Stripe-side schema bump can't change
    # `current_period_start` / `current_period_end` field locations under
    # us. Match what we tested against; bump deliberately when we update.
    stripe.api_version = "2024-06-20"


def _period_fields(stripe_sub) -> tuple[int | None, int | None]:
    """Read (current_period_start, current_period_end) from a Stripe subscription.

    A13: in Stripe API >= 2025-09-30 the period fields are on the first
    item under `items.data[0]` (one row per priced item). We read there
    first, then fall back to the top-level fields for older API versions
    so the handler keeps working through a future API bump.
    """
    items = (stripe_sub.get("items") or {}).get("data") if isinstance(stripe_sub, dict) else None
    if not items:
        try:
            items = (stripe_sub["items"]["data"] if hasattr(stripe_sub, "__getitem__") else None)
        except Exception:
            items = None
    period_start = None
    period_end = None
    if items:
        first = items[0] if items else {}
        period_start = (first or {}).get("current_period_start")
        period_end = (first or {}).get("current_period_end")
    if period_start is None:
        period_start = stripe_sub.get("current_period_start") if isinstance(stripe_sub, dict) else getattr(stripe_sub, "current_period_start", None)
    if period_end is None:
        period_end = stripe_sub.get("current_period_end") if isinstance(stripe_sub, dict) else getattr(stripe_sub, "current_period_end", None)
    return period_start, period_end


@router.get("", response_model=SubscriptionStatusResponse)
async def get_subscription_status(user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Return current plan, usage, and billing info.

    Delegates to the `SubInfo` dependency (single source of truth for
    APP_MODE/BETA_MODE/OG branching) and layers on the billing metadata
    (period_end, stripe_customer_id) that the client renders in the
    Settings billing panel.
    """
    user_id = user["user_id"]
    alias_count = await count_aliases(db_path, user_id)

    # Billing metadata lives on the subscription row itself. It's read
    # lazily here because SubInfo hides the row for abstraction reasons.
    raw_sub = await get_subscription(db_path, user_id)
    current_period_start = raw_sub.get("current_period_start") if raw_sub else None
    current_period_end = raw_sub.get("current_period_end") if raw_sub else None
    cancelled_at = raw_sub.get("cancelled_at") if raw_sub else None
    stripe_customer_id = raw_sub.get("stripe_customer_id") if raw_sub else ""

    # Founding member == OG. Keeping the `founding_member` field in the
    # response for backward compat with the current frontend - new code
    # should read `is_og` instead.
    return SubscriptionStatusResponse(
        plan=sub.plan,
        status=sub.status,
        is_premium=sub.is_premium,
        is_og=sub.is_og,
        founding_member=sub.is_og,
        app_mode=sub.app_mode,
        beta_mode=sub.beta_mode,
        trial_available=sub.trial_available,
        trial_ends_at=sub.trial_ends_at,
        usage_image_used=sub.image_queries_used,
        usage_image_limit=sub.image_queries_limit,
        usage_text_meal_used=sub.text_meals_used,
        usage_text_meal_limit=sub.text_meals_limit,
        usage_chat_used=sub.chats_used,
        usage_chat_limit=sub.chats_limit,
        usage_meal_edit_limit=sub.meal_edits_limit,
        usage_saved_meals=alias_count,
        # Saved meals uncapped for any premium user (includes beta + OG).
        # Only the legacy post-beta free tier hits FREE_ALIAS_LIMIT.
        usage_saved_meals_limit=-1 if sub.is_premium else FREE_ALIAS_LIMIT,
        current_period_start=current_period_start,
        current_period_end=current_period_end,
        cancelled_at=cancelled_at,
        stripe_customer_id=stripe_customer_id,
    )


@router.post("/trial", response_model=TrialResponse)
@limiter.limit("5/hour")
async def start_free_trial(request: Request, user: CurrentUser, db_path: DbPath):
    """Start a 7-day free trial. One trial per account."""
    # Beta mode: no trial concept - every signup is already Pro.
    if BETA_MODE:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "beta_mode",
                "message": "You already have full Pro access - MacroShot is in free beta.",
            },
        )

    user_id = user["user_id"]
    started = await start_trial(db_path, user_id)
    if not started:
        raise HTTPException(
            status_code=409,
            detail="Free trial already used. Subscribe to Pro for unlimited access.",
        )

    # Private telemetry: conversion funnel - trial activation
    await log_event(db_path, user_id, "trial_start", metadata={"length_days": 7})

    # Return updated subscription status
    sub = await get_subscription(db_path, user_id)
    if sub:
        is_premium = sub["plan"] == "trial" and sub["status"] == "trialing"
        return TrialResponse(
            message="Trial started! You have 7 days of full Pro access.",
            plan=sub["plan"],
            status=sub["status"],
            is_premium=is_premium,
            trial_ends_at=sub.get("trial_ends_at"),
        )
    return TrialResponse(
        message="Trial started! You have 7 days of full Pro access.",
        plan="trial", status="trialing", is_premium=True,
    )


@router.post("/checkout")
@limiter.limit("10/hour")
async def create_checkout(request: Request, body: CheckoutRequest, user: CurrentUser, db_path: DbPath):
    """Create a Stripe Checkout session and return the URL."""
    # Beta mode: Stripe may be configured but billing is intentionally
    # unreachable - everyone has free Pro access for now.
    if BETA_MODE:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "beta_mode",
                "message": (
                    "MacroShot is in free beta - you already have Pro access. "
                    "A paid tier with larger caps is coming soon."
                ),
            },
        )

    if not _stripe_configured():
        raise HTTPException(
            status_code=503,
            detail="Billing not configured. All features are free in self-hosted mode.",
        )

    if body.plan != "pro_monthly":
        raise HTTPException(status_code=400, detail="Only monthly plan is available.")

    price_id = os.environ.get("STRIPE_PRICE_MONTHLY")
    if not price_id:
        raise HTTPException(status_code=503, detail="Stripe price not configured.")

    _init_stripe()
    user_id = user["user_id"]
    email = user["email"]

    try:
        # Look up existing Stripe customer or create one
        sub = await get_subscription(db_path, user_id)
        stripe_customer_id = sub.get("stripe_customer_id") if sub else None

        if not stripe_customer_id:
            customer = await asyncio.to_thread(
                stripe.Customer.create, email=email, metadata={"user_id": str(user_id)}
            )
            stripe_customer_id = customer.id
            await create_or_update_subscription(db_path, user_id, stripe_customer_id=stripe_customer_id)

        base_url = _require_base_url()
        session = await asyncio.to_thread(
            stripe.checkout.Session.create,
            customer=stripe_customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{base_url}/settings?checkout=success",
            cancel_url=f"{base_url}/settings?checkout=cancel",
            metadata={"user_id": str(user_id)},
        )
    except stripe.AuthenticationError:
        logger.error("Stripe API key is invalid - billing unavailable")
        raise HTTPException(status_code=503, detail="Billing is not properly configured.")
    except stripe.StripeError as e:
        logger.error("Stripe error during checkout: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="Payment provider error. Please try again later.")

    # Private telemetry: conversion funnel - Stripe checkout session created.
    # The actual payment event fires later via the /subscription/webhook endpoint.
    await log_event(
        db_path, user_id, "checkout_initiated",
        metadata={"plan": body.plan},
    )

    return {"url": session.url}


@router.post("/portal")
@limiter.limit("10/hour")
async def create_portal(request: Request, user: CurrentUser, db_path: DbPath):
    """Create a Stripe Customer Portal session and return the URL."""
    # Beta mode: nothing to manage yet - no one has a paid subscription.
    if BETA_MODE:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "beta_mode",
                "message": (
                    "MacroShot is in free beta - there's no billing to manage. "
                    "A paid tier with larger caps is coming soon."
                ),
            },
        )

    if not _stripe_configured():
        raise HTTPException(
            status_code=503,
            detail="Billing not configured. All features are free in self-hosted mode.",
        )

    _init_stripe()
    sub = await get_subscription(db_path, user["user_id"])
    if not sub or not sub.get("stripe_customer_id"):
        raise HTTPException(status_code=400, detail="No billing account found.")

    base_url = _require_base_url()
    try:
        session = await asyncio.to_thread(
            stripe.billing_portal.Session.create,
            customer=sub["stripe_customer_id"],
            return_url=f"{base_url}/settings",
        )
    except stripe.AuthenticationError:
        logger.error("Stripe API key is invalid - billing unavailable")
        raise HTTPException(status_code=503, detail="Billing is not properly configured.")
    except stripe.StripeError as e:
        logger.error("Stripe error during portal: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="Payment provider error. Please try again later.")

    return {"url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events. No auth - verified by Stripe signature."""
    # Return 503 (not 200) when unconfigured so Stripe retries on its own
    # schedule rather than marking the event as delivered and dropping it.
    if not _stripe_configured():
        raise HTTPException(status_code=503, detail="Billing not configured")

    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not webhook_secret:
        logger.warning("STRIPE_WEBHOOK_SECRET not set - cannot verify webhook")
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    _init_stripe()
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except stripe.SignatureVerificationError:
        logger.warning("Stripe webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error("Stripe webhook error: %s", e)
        raise HTTPException(status_code=400, detail="Webhook error")

    event_type = event["type"]
    event_id = event.get("id") or ""
    data = event["data"]["object"]

    from src.web.deps import DB_PATH
    db_path = DB_PATH

    # Idempotency: Stripe retries on any 5xx (and sometimes non-200). Without
    # a dedup gate, a retry after we already applied a subscription update
    # runs the handler twice and can double-apply state transitions. Skip
    # handlers if this event_id has been processed before. See
    # src/db.py:record_stripe_webhook_event for the single-row marker.
    from src.db import was_stripe_webhook_event_processed, record_stripe_webhook_event
    if event_id and await was_stripe_webhook_event_processed(db_path, event_id):
        logger.info("Stripe webhook duplicate: %s (id=%s)", event_type, event_id)
        return {"received": True, "duplicate": True}

    logger.info("Stripe webhook: %s (id=%s)", event_type, event_id)

    # Events we explicitly acknowledge without handling - adding here is a
    # deliberate choice to NOT retry. Everything else falls through to 500
    # so Stripe keeps delivering it until we code a handler; that prevents
    # a new billing-relevant event type from being silently dropped.
    _IGNORED_EVENT_TYPES = {
        "charge.succeeded", "charge.captured", "charge.refunded",
        "charge.dispute.created",
        "invoice.created", "invoice.finalized", "invoice.sent",
        "invoice.payment_succeeded", "invoice.upcoming",
        "payment_intent.created", "payment_intent.succeeded",
        "payment_intent.payment_failed",
        "payment_method.attached", "payment_method.detached",
        "customer.created", "customer.updated", "customer.deleted",
        "customer.subscription.created",
    }

    handled = True
    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(db_path, data)
    elif event_type == "invoice.paid":
        await _handle_invoice_paid(db_path, data)
    elif event_type == "invoice.payment_failed":
        await _handle_invoice_failed(db_path, data)
    elif event_type == "customer.subscription.deleted":
        await _handle_subscription_deleted(db_path, data)
    elif event_type == "customer.subscription.updated":
        await _handle_subscription_updated(db_path, data)
    elif event_type in _IGNORED_EVENT_TYPES:
        logger.debug("Ignoring known-benign Stripe event: %s", event_type)
    else:
        handled = False
        logger.warning(
            "Unknown Stripe event type (will retry): %s. "
            "Add a handler or add to _IGNORED_EVENT_TYPES if safe to drop.",
            event_type,
        )

    if handled and event_id:
        await record_stripe_webhook_event(db_path, event_id, event_type)
    if not handled:
        # 500 → Stripe retries. Legit operational alert if this fires in prod.
        # Don't echo event_type in the response body; it goes to Stripe and
        # surfaces in their dashboard. Log internally instead.
        logger.error("Stripe webhook: unknown event type %s", event_type)
        raise HTTPException(status_code=500, detail="Unknown webhook event type")
    return {"received": True}


# ── Webhook event handlers ──────────────────────────────────────────


async def _resolve_user_id(db_path: str, data: dict) -> int | None:
    """Resolve user_id from webhook data - check metadata first, then customer lookup."""
    # Try metadata
    user_id_str = (data.get("metadata") or {}).get("user_id")
    if user_id_str:
        try:
            return int(user_id_str)
        except (ValueError, TypeError):
            logger.warning("Invalid non-numeric user_id in Stripe metadata: %s", user_id_str)
            return None

    # Fall back to customer ID lookup
    customer_id = data.get("customer")
    if not customer_id:
        return None

    from src.db_pool import get_db
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT user_id FROM subscriptions WHERE stripe_customer_id = ?",
                (customer_id,),
            )
        ).fetchone()
    return row[0] if row else None


async def _handle_checkout_completed(db_path: str, data: dict) -> None:
    """checkout.session.completed - activate the subscription.

    Raises on unresolved user_id so the webhook caller surfaces a 500 and
    Stripe retries. Silently returning would leave a paying customer
    stuck on the free plan with no record that we received their checkout.
    """
    user_id = await _resolve_user_id(db_path, data)
    if not user_id:
        logger.error("checkout.session.completed: could not resolve user_id - raising for Stripe retry")
        # Generic detail - body is visible to whoever owns the webhook URL,
        # don't leak resolution-logic specifics to a probe with a forged sig
        # (sig already failed by here, but defense-in-depth on error bodies).
        raise HTTPException(status_code=500, detail="Webhook handler error")

    stripe_sub_id = data.get("subscription", "")
    customer_id = data.get("customer", "")

    # Fetch the subscription from Stripe to get period dates
    period_start = None
    period_end = None
    if stripe_sub_id:
        try:
            stripe_sub = await asyncio.to_thread(stripe.Subscription.retrieve, stripe_sub_id)
            ps, pe = _period_fields(stripe_sub)
            if ps is not None:
                period_start = datetime.fromtimestamp(ps, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if pe is not None:
                period_end = datetime.fromtimestamp(pe, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception as e:
            logger.warning("Could not fetch Stripe subscription: %s", e)

    await create_or_update_subscription(
        db_path,
        user_id,
        plan="pro_monthly",
        status="active",
        stripe_customer_id=customer_id,
        stripe_subscription_id=stripe_sub_id,
        current_period_start=period_start,
        current_period_end=period_end,
        cancelled_at=None,
    )
    logger.info("User %d subscription activated (pro_monthly)", user_id)


async def _handle_invoice_paid(db_path: str, data: dict) -> None:
    """invoice.paid - extend the current period."""
    user_id = await _resolve_user_id(db_path, data)
    if not user_id:
        return

    # Get period from the invoice's subscription
    stripe_sub_id = data.get("subscription")
    if stripe_sub_id:
        try:
            stripe_sub = await asyncio.to_thread(stripe.Subscription.retrieve, stripe_sub_id)
            ps, pe = _period_fields(stripe_sub)
            kwargs = {"status": "active"}
            if ps is not None:
                kwargs["current_period_start"] = datetime.fromtimestamp(ps, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if pe is not None:
                kwargs["current_period_end"] = datetime.fromtimestamp(pe, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            await create_or_update_subscription(db_path, user_id, **kwargs)
        except Exception as e:
            logger.warning("invoice.paid: could not update period: %s", e)


async def _handle_invoice_failed(db_path: str, data: dict) -> None:
    """invoice.payment_failed - mark subscription as past_due."""
    user_id = await _resolve_user_id(db_path, data)
    if not user_id:
        return
    await create_or_update_subscription(db_path, user_id, status="past_due")
    logger.info("User %d subscription past_due (payment failed)", user_id)


async def _handle_subscription_deleted(db_path: str, data: dict) -> None:
    """customer.subscription.deleted - downgrade to free, UNLESS OG.

    OG users (`is_og=1`) are grandfathered Pro forever. If Stripe sends
    a subscription.deleted for an OG user (e.g., they were on a paid
    plan and then got marked OG), we keep their plan='pro_monthly' and
    clear the Stripe linkage instead of dropping them to free. This
    avoids Settings showing 'Plan: free, Status: expired' while the
    backend still reports `is_premium=True` via the is_og bypass.
    """
    user_id = await _resolve_user_id(db_path, data)
    if not user_id:
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    existing = await get_subscription(db_path, user_id)
    if existing and existing.get("is_og"):
        await create_or_update_subscription(
            db_path, user_id,
            plan="pro_monthly", status="active",
            stripe_subscription_id="",
            cancelled_at=None,
        )
        logger.info("User %d subscription deleted but is_og=1 -> kept Pro", user_id)
        return

    await create_or_update_subscription(
        db_path, user_id,
        plan="free", status="expired", cancelled_at=now,
    )
    logger.info("User %d subscription deleted -> free", user_id)


async def _handle_subscription_updated(db_path: str, data: dict) -> None:
    """customer.subscription.updated - sync status and period."""
    user_id = await _resolve_user_id(db_path, data)
    if not user_id:
        return

    status_map = {
        "active": "active",
        "past_due": "past_due",
        "canceled": "cancelled",
        "unpaid": "past_due",
        "trialing": "trialing",
    }
    stripe_status = data.get("status", "active")
    our_status = status_map.get(stripe_status, "active")

    updates: dict = {"status": our_status}

    # A13: read period via _period_fields so this keeps working when Stripe
    # moves the fields under items.data[0] (API >=2025-09-30). The api_version
    # pin (2024-06-20) keeps the old shape today, but new test/prod accounts
    # default to the new shape and the pin can be bumped at any time.
    ps, pe = _period_fields(data)
    if ps is not None:
        updates["current_period_start"] = datetime.fromtimestamp(
            ps, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")
    if pe is not None:
        updates["current_period_end"] = datetime.fromtimestamp(
            pe, tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S")

    # Stripe keeps status=active with cancel_at_period_end=true or cancel_at
    # set when user cancels via portal. Mark as cancelled so we show countdown.
    pending_cancel = data.get("cancel_at_period_end") or data.get("cancel_at")
    if pending_cancel and stripe_status == "active":
        updates["status"] = "cancelled"
        updates["cancelled_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        # Use cancel_at as period end if available (more precise than current_period_end)
        cancel_at = data.get("cancel_at")
        if cancel_at and isinstance(cancel_at, (int, float)):
            updates["current_period_end"] = datetime.fromtimestamp(
                cancel_at, tz=timezone.utc
            ).strftime("%Y-%m-%d %H:%M:%S")
        logger.info("User %d subscription pending cancellation detected", user_id)

    # If user reactivates (un-cancels), restore to active
    if not pending_cancel and stripe_status == "active":
        updates["status"] = "active"
        updates["cancelled_at"] = None

    if stripe_status == "canceled":
        # OG users stay Pro even when Stripe says canceled (see _handle_subscription_deleted).
        existing = await get_subscription(db_path, user_id)
        if existing and existing.get("is_og"):
            updates["plan"] = "pro_monthly"
            updates["status"] = "active"
            updates["cancelled_at"] = None
            updates.pop("current_period_end", None)
            logger.info("User %d stripe=canceled but is_og=1 -> kept Pro", user_id)
        else:
            updates["plan"] = "free"
            updates["cancelled_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    await create_or_update_subscription(db_path, user_id, **updates)
    logger.info("User %d subscription updated: %s", user_id, our_status)
