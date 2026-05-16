"""Admin-only routes: private per-user metrics and insights.

Gated by the ADMIN_EMAIL env var (comma-separated emails, case-insensitive).
Non-admin callers get a 404 - the endpoint is not advertised and should not
appear publicly. There is no UI link for non-admin users. Do not loosen this
gate without explicit discussion: per-user activity is private data.
"""

import csv
import io
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.db import (
    get_acquisition_mix,
    get_cost_metrics,
    get_daily_active_users,
    get_event_overview,
    get_feature_adoption,
    get_feedback_thumbs_downs,
    get_feedback_totals,
    get_funnel_counts,
    get_limit_hit_counts,
    get_monthly_gemini_cost_usd,
    get_today_activity,
    get_total_event_counts,
    get_user_event_timeseries,
    get_web_user_by_id,
    list_all_users,
    list_waitlist,
    log_event,
    set_og_status,
)
from src.web.constants import MONTHLY_GEMINI_BUDGET_USD
from src.web.deps import CurrentUser, DbPath
from src.web.rate_limit import limiter

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/admin", tags=["admin"])


def _parse_admin_emails() -> set[str]:
    """Parse the ADMIN_EMAIL env var into a set of lowercased emails.

    Comma-separated list to allow multiple admins. Empty/unset means
    'no admins' which effectively disables the admin endpoints.
    """
    raw = os.environ.get("ADMIN_EMAIL", "")
    emails: set[str] = set()
    for part in raw.split(","):
        part = part.strip().lower()
        if part:
            emails.add(part)
    return emails


async def require_admin(user: CurrentUser) -> dict:
    """Dependency: only allow configured admin users. Returns 404 otherwise.

    We return 404 (not 403) so the endpoint's existence is not advertised to
    non-admin callers - reconnaissance-resistant by default.
    """
    admin_emails = _parse_admin_emails()
    user_email = (user.get("email") or "").strip().lower()
    if not admin_emails or not user_email or user_email not in admin_emails:
        raise HTTPException(status_code=404, detail="Not found")
    return user


AdminUser = Annotated[dict, Depends(require_admin)]


def _since_iso(days: int) -> str:
    """Return an ISO string `days` days ago (UTC)."""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return since.strftime("%Y-%m-%d %H:%M:%S")


@router.get("/metrics/overview")
async def metrics_overview(
    admin: AdminUser,
    db_path: DbPath,
    days: int = Query(default=30, ge=1, le=365),
):
    """Per-user event rollup over the last `days` days.

    Returns both a global aggregate and one row per active user with the
    number of each event type they produced. Users with zero events in the
    window are omitted. Results are sorted by total event count, descending.
    """
    since = _since_iso(days)
    per_user = await get_event_overview(db_path, since)
    totals = await get_total_event_counts(db_path, since)
    return {
        "window_days": days,
        "since": since,
        "totals": totals,
        "users": per_user,
    }


@router.get("/metrics/user/{user_id}")
async def metrics_user(
    user_id: int,
    admin: AdminUser,
    db_path: DbPath,
    days: int = Query(default=30, ge=1, le=365),
):
    """Per-day per-event-type breakdown for a single user.

    Used to drill into one user's activity over the window.
    """
    user = await get_web_user_by_id(db_path, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    since = _since_iso(days)
    rows = await get_user_event_timeseries(db_path, user_id, since)

    # Also return a rolled-up per-event-type count for convenience
    per_type: dict[str, int] = {}
    for r in rows:
        per_type[r["event_type"]] = per_type.get(r["event_type"], 0) + int(r["cnt"])

    # A18: audit trail.
    await log_event(
        db_path, admin["user_id"], "admin_action",
        metadata={"action": "user_drilldown", "target_user_id": user_id, "days": days},
    )
    return {
        "user_id": user_id,
        "email": user.get("email", ""),
        "first_name": user.get("first_name", ""),
        "window_days": days,
        "since": since,
        "totals": per_type,
        "timeseries": rows,
    }


@router.get("/metrics/dashboard")
async def metrics_dashboard(
    admin: AdminUser,
    db_path: DbPath,
    days: int = Query(default=30, ge=1, le=365),
):
    """Bundled analytics payload: funnel + acquisition + adoption + limits + cost + retention.

    Returned as a single object so the AdminMetrics page can render the
    full dashboard with one network request. Keeps the queries independent
    so slow ones don't block fast ones - they're small (indexed GROUP BYs
    on a user_events table that we prune to 365 days).
    """
    since = _since_iso(days)
    funnel = await get_funnel_counts(db_path, since)
    acquisition = await get_acquisition_mix(db_path, since)
    adoption = await get_feature_adoption(db_path, since)
    limits = await get_limit_hit_counts(db_path, since)
    cost = await get_cost_metrics(db_path, since)
    retention = await get_daily_active_users(db_path, since)

    # Month-to-date Gemini spend + gate status. Independent of the
    # `days` window above - always shows the current calendar month so
    # the operator can eyeball headroom against the budget gate.
    mtd_cost = await get_monthly_gemini_cost_usd(db_path)
    budget = {
        "monthly_cost_usd": round(mtd_cost, 4),
        "monthly_budget_usd": MONTHLY_GEMINI_BUDGET_USD,
        "pct_used": round((mtd_cost / MONTHLY_GEMINI_BUDGET_USD) * 100, 1)
            if MONTHLY_GEMINI_BUDGET_USD > 0 else 0.0,
        "gate_tripped": mtd_cost >= MONTHLY_GEMINI_BUDGET_USD,
    }

    return {
        "window_days": days,
        "since": since,
        "funnel": funnel,
        "acquisition": acquisition,
        "adoption": adoption,
        "limits": limits,
        "cost": cost,
        "budget": budget,
        "retention": retention,
    }


@router.get("/metrics/feedback")
async def metrics_feedback(
    admin: AdminUser,
    db_path: DbPath,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=50, ge=1, le=200),
):
    """Accuracy-feedback metrics: totals + drill-down list of thumbs-downs.

    Totals compare thumbs-up vs thumbs-down counts and surface the
    submission rate (fraction of accepted meals that got rated). The
    drill-down includes the analysis snapshot stored on each meal_log so
    an admin can reproduce the Gemini analysis (conversation, nutrition,
    image_path, barcode) for meals users flagged as inaccurate.
    """
    since = _since_iso(days)
    totals = await get_feedback_totals(db_path, since)
    thumbs_downs = await get_feedback_thumbs_downs(db_path, since, limit)
    return {
        "window_days": days,
        "since": since,
        "totals": totals,
        "thumbs_downs": thumbs_downs,
    }


@router.get("/metrics/today")
async def metrics_today(
    admin: AdminUser,
    db_path: DbPath,
    hours: int = Query(default=24, ge=1, le=72),
):
    """Last N hours of individual meal and weight log activity.

    Returns per-meal rows (not aggregates) so the admin can see exactly
    who logged what and when. Grouped by user on the frontend.
    """
    data = await get_today_activity(db_path, hours)
    return {"hours": hours, **data}


@router.get("/whoami")
async def admin_whoami(admin: AdminUser):
    """Tiny introspection endpoint so the frontend can check admin status.

    Returns 200 + user info if admin; returns 404 otherwise (via the gate).
    """
    return {"user_id": admin["user_id"], "email": admin.get("email", ""), "is_admin": True}


# ── Beta release admin actions ──────────────────────────────────────


class OGToggleRequest(BaseModel):
    user_id: int
    is_og: bool


@router.post("/og-user")
@limiter.limit("30/minute")
async def toggle_og_status(
    request: Request,
    req: OGToggleRequest,
    admin: AdminUser,
    db_path: DbPath,
):
    """Flip the OG (founding member) flag on a user's subscription row.

    OG users share the same per-meal/daily caps as regular Pros but never
    require renewal - their Pro access persists even after BETA_MODE flips
    off. Flagging creates the subscription row if it doesn't exist yet
    (for early accounts that pre-date auto-provisioning). Un-flagging a
    user with no subscription row is a no-op (see set_og_status).
    """
    target = await get_web_user_by_id(db_path, req.user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    await set_og_status(db_path, req.user_id, req.is_og)
    logger.info(
        "Admin %d set is_og=%s for user_id=%d",
        admin["user_id"], req.is_og, req.user_id,
    )
    # A18: audit trail for admin write actions.
    await log_event(
        db_path, admin["user_id"], "admin_action",
        metadata={"action": "set_og", "target_user_id": req.user_id, "is_og": req.is_og},
    )
    return {
        "ok": True,
        "user_id": req.user_id,
        "email": target.get("email", ""),
        "is_og": req.is_og,
    }


@router.get("/users.csv")
@limiter.limit("10/minute")
async def export_users_csv(request: Request, admin: AdminUser, db_path: DbPath):
    """Export all web users as CSV for cohort analysis / newsletter sends.

    Columns: user_id, email, first_name, last_name, signed_up_at, plan,
    status, is_og, has_stripe_customer, current_period_end,
    newsletter_opt_in.
    """
    rows = await list_all_users(db_path)
    await log_event(
        db_path, admin["user_id"], "admin_action",
        metadata={"action": "users_csv", "row_count": len(rows)},
    )

    async def _stream():
        # A29: stream the CSV row-by-row instead of materializing the
        # whole file in memory. Hard cap at 500k rows to bound worst-case
        # response size.
        max_rows = 500_000
        header_buf = io.StringIO()
        csv.writer(header_buf).writerow([
            "user_id", "email", "first_name", "last_name", "signed_up_at",
            "plan", "status", "is_og", "has_stripe_customer",
            "current_period_end", "newsletter_opt_in",
        ])
        yield header_buf.getvalue()
        for r in rows[:max_rows]:
            row_buf = io.StringIO()
            csv.writer(row_buf).writerow([
                r.get("user_id", ""),
                r.get("email", ""),
                r.get("first_name") or "",
                r.get("last_name") or "",
                r.get("signed_up_at", ""),
                r.get("plan") or "",
                r.get("status") or "",
                int(r.get("is_og") or 0),
                int(bool(r.get("stripe_customer_id"))),
                r.get("current_period_end") or "",
                int(r.get("newsletter_opt_in") or 0),
            ])
            yield row_buf.getvalue()

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=macro_users.csv"},
    )


@router.get("/waitlist.csv")
@limiter.limit("10/minute")
async def export_waitlist_csv(request: Request, admin: AdminUser, db_path: DbPath):
    """Export the beta waitlist as CSV.

    Columns: id, email, source, first_name, referrer, created_at,
    invited_at, notes.
    """
    rows = await list_waitlist(db_path)
    await log_event(
        db_path, admin["user_id"], "admin_action",
        metadata={"action": "waitlist_csv", "row_count": len(rows)},
    )

    async def _stream():
        max_rows = 500_000
        header_buf = io.StringIO()
        csv.writer(header_buf).writerow([
            "id", "email", "source", "first_name", "referrer",
            "created_at", "invited_at", "notes",
        ])
        yield header_buf.getvalue()
        for r in rows[:max_rows]:
            row_buf = io.StringIO()
            csv.writer(row_buf).writerow([
                r.get("id", ""),
                r.get("email", ""),
                r.get("source") or "",
                r.get("first_name") or "",
                r.get("referrer") or "",
                r.get("created_at", ""),
                r.get("invited_at") or "",
                r.get("notes") or "",
            ])
            yield row_buf.getvalue()

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=macro_waitlist.csv"},
    )
