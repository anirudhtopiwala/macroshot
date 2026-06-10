"""Saved meals (aliases) API routes - reads/writes from SQLite."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from src.db import (
    get_all_aliases,
    get_alias_by_name,
    get_push_subscriptions,
    get_streak_shields,
    save_alias,
    delete_alias,
    reorder_aliases,
    log_event,
    log_meal as db_log_meal,
    get_user_prefs,
)
from src.services import classify_meal_time, get_user_tz, get_progress
from src.web.deps import CurrentUser, DbPath, SubInfo
from src.web.rate_limit import limiter
from src.web.schemas import AliasCreateRequest, AliasOut, FoodItemOut

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/aliases", tags=["aliases"])


def _parse_items(items_json: str) -> list[FoodItemOut]:
    """Parse items_json string into FoodItemOut list."""
    if not items_json or items_json == "[]":
        return []
    try:
        return [FoodItemOut(**i) for i in json.loads(items_json)]
    except Exception:
        return []


@router.get("", response_model=list[AliasOut])
async def list_aliases(user: CurrentUser, db_path: DbPath):
    """List all saved meals for the current user."""
    rows = await get_all_aliases(db_path, user["user_id"])
    return [
        AliasOut(
            name=r["alias_name"],
            item_name=r["item_name"],
            meal_description=r["meal_description"],
            calories=r["calories"],
            protein=r["protein"],
            carbs=r["carbs"],
            fat=r["fat"],
            items=_parse_items(r["items_json"]),
        )
        for r in rows
    ]


@router.post("", response_model=AliasOut)
@limiter.limit("30/minute")
async def create_alias(request: Request, req: AliasCreateRequest, user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Create or update a saved meal."""
    if not sub.is_premium:
        existing = await get_all_aliases(db_path, user["user_id"])
        # Allow update of existing alias, but cap new aliases for free tier
        from src.web.constants import FREE_ALIAS_LIMIT
        is_update = any(r["alias_name"].lower() == req.name.lower() for r in existing)
        if not is_update and len(existing) >= FREE_ALIAS_LIMIT:
            await log_event(
                db_path, user["user_id"], "limit_hit",
                metadata={
                    "feature": "saved_meals",
                    "limit": FREE_ALIAS_LIMIT,
                    "is_premium": False,
                },
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "premium_required",
                    "feature": "saved_meals",
                    "message": f"Free accounts can save up to {FREE_ALIAS_LIMIT} meals. Upgrade for unlimited.",
                    "trial_available": sub.trial_available,
                },
            )
    await save_alias(
        db_path, user["user_id"], req.name,
        item_name=req.item_name,
        meal_description=req.meal_description,
        calories=req.calories,
        protein=req.protein,
        carbs=req.carbs,
        fat=req.fat,
        items_json=req.items_json,
    )
    # Evaluate alias_create badges
    new_badges = []
    try:
        from src.badge_engine import evaluate_badges
        from src.services import user_today_str
        from src.db import get_user_prefs
        today = await user_today_str(db_path, user["user_id"])
        prefs = await get_user_prefs(db_path, user["user_id"])
        if prefs.get("gamification", "full") != "off":
            new_badges = await evaluate_badges(db_path, user["user_id"], "alias_create", {"today_str": today})
    except Exception:
        logger.exception("Badge evaluation failed for alias_create")
    return AliasOut(
        name=req.name,
        item_name=req.item_name,
        meal_description=req.meal_description,
        calories=req.calories,
        protein=req.protein,
        carbs=req.carbs,
        fat=req.fat,
        items=_parse_items(req.items_json),
        new_badges=new_badges,
    )


@router.delete("/{name}")
@limiter.limit("30/minute")
async def delete_alias_route(request: Request, name: str, user: CurrentUser, db_path: DbPath):
    """Delete a saved meal."""
    deleted = await delete_alias(db_path, user["user_id"], name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Alias not found")
    return {"ok": True, "deleted": name}


class AliasReorderRequest(BaseModel):
    # A24: bound the list and per-name length so a giant payload can't
    # waste a write transaction or blow up logs.
    names: list[str] = Field(max_length=200)

    @field_validator("names")
    @classmethod
    def _bound_entries(cls, v: list[str]) -> list[str]:
        return [n[:100] for n in v]


@router.put("/reorder")
@limiter.limit("20/minute")
async def reorder_aliases_route(request: Request, req: AliasReorderRequest, user: CurrentUser, db_path: DbPath):
    """Update the display order of saved meals."""
    await reorder_aliases(db_path, user["user_id"], req.names)
    return {"ok": True}


@router.post("/{name}/edit-session")
@limiter.limit("20/minute")
async def create_alias_edit_session(request: Request, name: str, user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Create an analysis session from a saved meal for AI editing.

    B7: gated against the daily text_meal cap - this triggers a Gemini call
    server-side via create_session_from_alias, so it must consume the same
    text-meal quota a typed analysis does.
    """
    from src.services import user_today_str
    from src.db import increment_usage
    from src.web.constants import limit_message
    from src.web.deps import UNLIMITED

    today_str = await user_today_str(db_path, user["user_id"])
    tier_limit = sub.text_meals_limit
    if tier_limit != UNLIMITED:
        usage_result = await increment_usage(
            db_path, user["user_id"], "text_meal", today_str, limit=tier_limit,
        )
        if not usage_result.get("allowed"):
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "limit_reached",
                    "feature": "text_meal",
                    "used": usage_result["used_count"],
                    "limit": tier_limit,
                    "is_premium": sub.is_premium,
                    "message": limit_message("text_meal", tier_limit),
                },
            )

    alias = await get_alias_by_name(db_path, user["user_id"], name)
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")

    from src.services import create_session_from_alias
    result = await create_session_from_alias(alias, user["user_id"], db_path)

    if result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])

    from src.web.routes.meals import _nutrition_to_out
    from src.web.schemas import AnalyzeResponse
    return AnalyzeResponse(
        session_id=result["session_id"],
        nutrition=_nutrition_to_out(result.get("nutrition")),
        questions=[],
        raw_text="",
        error=None,
    )


class AliasLogRequest(BaseModel):
    # A25: cap shape - either "YYYY-MM-DD" or "YYYY-MM-DD HH:MM[:SS]".
    logged_at: str | None = Field(
        default=None,
        max_length=30,
        pattern=r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$",
    )


@router.post("/{name}/log")
@limiter.limit("30/minute")
async def log_alias(request: Request, name: str, user: CurrentUser, db_path: DbPath, sub: SubInfo, req: AliasLogRequest | None = None):
    """Quick re-log a saved meal. Optionally backdate with logged_at."""
    alias = await get_alias_by_name(db_path, user["user_id"], name)
    if not alias:
        raise HTTPException(status_code=404, detail="Alias not found")

    if req and req.logged_at:
        logged_at = req.logged_at
        try:
            hour = int(logged_at.split(" ")[1].split(":")[0])
        except Exception:
            prefs = await get_user_prefs(db_path, user["user_id"])
            tz = get_user_tz(prefs.get("timezone"))
            hour = datetime.now(tz).hour
    else:
        prefs = await get_user_prefs(db_path, user["user_id"])
        tz = get_user_tz(prefs.get("timezone"))
        now = datetime.now(tz)
        logged_at = now.strftime("%Y-%m-%d %H:%M")
        hour = now.hour
    meal_type = classify_meal_time(hour)

    meal_log_id = await db_log_meal(
        db_path=db_path,
        user_id=user["user_id"],
        logged_at=logged_at,
        item_name=alias["item_name"],
        meal_description=alias["meal_description"] or "",
        calories=alias["calories"],
        protein=alias["protein"],
        carbs=alias["carbs"],
        fat=alias["fat"],
        source="Alias",
        meal_type=meal_type,
        items_json=alias["items_json"] or "[]",
    )

    # Private telemetry: quick-log (saved meal) as a separate category
    await log_event(
        db_path, user["user_id"], "meal_quick_log",
        metadata={"alias_name": name, "meal_type": meal_type},
    )

    progress_date = logged_at.split(" ")[0]
    progress = await get_progress(user["user_id"], db_path, progress_date)

    # Evaluate badges for alias log (meal_accept + alias_log triggers)
    new_badges = []
    try:
        from src.badge_engine import evaluate_badges
        from src.services import user_today_str
        today = await user_today_str(db_path, user["user_id"])
        p = await get_user_prefs(db_path, user["user_id"])
        if p.get("gamification", "full") != "off":
            # Snapshot shield count before badge evaluation
            shields_before = (await get_streak_shields(db_path, user["user_id"]))["available"]

            new_badges = await evaluate_badges(db_path, user["user_id"], "meal_accept", {"today_str": today, "has_image": False, "is_premium": sub.is_premium})
            alias_badges = await evaluate_badges(db_path, user["user_id"], "alias_log", {"today_str": today})
            new_badges.extend(alias_badges)

            # Detect shield consumption and send push notification
            shields_after = (await get_streak_shields(db_path, user["user_id"]))["available"]
            if shields_after < shields_before:
                await log_event(
                    db_path, user["user_id"], "shield_consumed",
                    metadata={"available_after": shields_after, "via": "quick_log"},
                )
                try:
                    from src.web.push import send_push_notification
                    subs = await get_push_subscriptions(db_path, user["user_id"])
                    for push_sub in subs:
                        sub_info = {
                            "endpoint": push_sub["endpoint"],
                            "keys": {"p256dh": push_sub["p256dh"], "auth": push_sub["auth"]},
                        }
                        await send_push_notification(
                            sub_info,
                            title="\U0001f6e1\ufe0f Streak Shield Used!",
                            body="A shield was used to protect your streak. Keep logging!",
                            url="/macro_app/settings/achievements?section=shields",
                            tag=f"shield-used-{today}",
                        )
                except Exception:
                    logger.debug("Shield push notification failed", exc_info=True)
    except Exception:
        logger.exception("Badge evaluation failed for alias log")

    return {"ok": True, "meal_id": meal_log_id, "meal_type": meal_type, "progress": progress, "new_badges": new_badges}
