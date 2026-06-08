"""Weight tracking API routes."""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.db import (
    delete_weight_entry,
    get_guest_weights_imported_at,
    get_user_prefs,
    get_user_profile,
    get_weight_history,
    log_event,
    log_weight,
    mark_guest_weights_imported,
)
from src.services import get_user_tz
from src.web.deps import CurrentUser, DbPath
from src.web.rate_limit import limiter
from src.web.schemas import WeightHistoryResponse, WeightLogRequest

logger = logging.getLogger("macro_app")

router = APIRouter(prefix="/weight", tags=["weight"])


@router.post("")
@limiter.limit("30/minute")
async def post_weight(request: Request, body: WeightLogRequest, user: CurrentUser, db_path: DbPath):
    """Log a new weight entry."""
    prefs = await get_user_prefs(db_path, user["user_id"])
    if body.logged_at:
        logged_at = body.logged_at
    else:
        tz = get_user_tz(prefs.get("timezone"))
        logged_at = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    row_id = await log_weight(db_path, user["user_id"], body.weight_kg, logged_at)
    # Private telemetry: weight entries for admin insights
    await log_event(
        db_path, user["user_id"], "weight_log",
        metadata={"weight_kg": body.weight_kg},
    )
    # Evaluate weight badges
    new_badges: list = []
    try:
        from src.badge_engine import evaluate_badges
        from src.services import user_today_str
        today = await user_today_str(db_path, user["user_id"])
        if prefs.get("gamification", "full") != "off":
            new_badges = await evaluate_badges(db_path, user["user_id"], "weight_log", {"today_str": today})
    except Exception:
        logger.exception("Badge evaluation failed for weight_log")
    return {"id": row_id, "logged_at": logged_at, "weight_kg": body.weight_kg, "new_badges": new_badges}


@router.get("", response_model=WeightHistoryResponse)
async def get_weight(user: CurrentUser, db_path: DbPath, limit: int = 90):
    """Get weight history with summary fields."""
    entries = await get_weight_history(db_path, user["user_id"], limit)
    latest = entries[0]["weight_kg"] if entries else None
    last_logged_at = entries[0]["logged_at"] if entries else None
    start = entries[-1]["weight_kg"] if entries else None

    # Get goal from profile if set
    profile = await get_user_profile(db_path, user["user_id"])
    goal = profile.get("weight_goal_kg")

    return {
        "entries": entries,
        "latest": latest,
        "start": start,
        "goal": goal,
        "last_logged_at": last_logged_at,
    }


@router.delete("/{weight_id}")
@limiter.limit("30/minute")
async def remove_weight(request: Request, weight_id: int, user: CurrentUser, db_path: DbPath):
    """Delete a weight entry."""
    deleted = await delete_weight_entry(db_path, weight_id, user["user_id"])
    if not deleted:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"ok": True}


# ── Guest weight import (auth-required, once per user) ──────────────


class ImportGuestWeightItem(BaseModel):
    weight_kg: float = Field(gt=0, le=1000)
    logged_at: str = Field(
        max_length=30,
        pattern=r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$",
    )


class ImportGuestWeightsRequest(BaseModel):
    # 90-cap mirrors weightApi.history's default limit so the most
    # active part of a guest's history makes it across; combined with
    # the once-per-user gate this is also the lifetime ceiling for
    # the import path.
    entries: list[ImportGuestWeightItem] = Field(default_factory=list, max_length=90)


class ImportGuestWeightsResponse(BaseModel):
    imported: int
    already_imported: bool = False


@router.post("/import-guest", response_model=ImportGuestWeightsResponse)
@limiter.limit("3/hour")
async def import_guest_weights(
    request: Request,
    req: ImportGuestWeightsRequest,
    user: CurrentUser,
    db_path: DbPath,
):
    """Backfill a fresh signup's pre-signup guest weight entries.

    Same shape and once-per-user gating as /meals/import-guest: the
    Pydantic max_length on entries caps a single call; the
    `users.guest_weights_imported_at` flag makes replay a no-op.
    """
    if not req.entries:
        return ImportGuestWeightsResponse(imported=0, already_imported=False)

    user_id = user["user_id"]
    if not await mark_guest_weights_imported(db_path, user_id):
        return ImportGuestWeightsResponse(imported=0, already_imported=True)

    inserted = 0
    for entry in req.entries:
        try:
            await log_weight(db_path, user_id, entry.weight_kg, entry.logged_at)
            inserted += 1
        except Exception:
            logger.exception("import-guest weights: failed to insert for user_id=%d", user_id)

    await log_event(
        db_path, user_id, "guest_weights_imported",
        metadata={"requested": len(req.entries), "inserted": inserted},
    )

    return ImportGuestWeightsResponse(imported=inserted, already_imported=False)


@router.get("/import-guest/status")
@limiter.limit("30/minute")
async def import_guest_weights_status(request: Request, user: CurrentUser, db_path: DbPath):
    ts = await get_guest_weights_imported_at(db_path, user["user_id"])
    return {"already_imported": ts is not None, "imported_at": ts}
