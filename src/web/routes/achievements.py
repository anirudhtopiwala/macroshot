"""Achievement / badge / challenge API routes."""

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Request

from src.badge_engine import compute_all_badge_progress
from src.badges import BADGES
from src.challenges import get_challenges_with_progress
from src.db import (
    auto_consume_shields_for_streak,
    get_shield_progress,
    get_streak_shields,
    get_shields_used_recently,
    get_unseen_badges,
    get_user_badges,
    get_user_prefs,
    mark_badges_seen,
    was_shield_used_recently,
    get_shield_history,
)
from src.services import user_today_str
from src.web.deps import CurrentUser, DbPath
from src.web.rate_limit import limiter
from src.web.schemas import (
    AchievementsResponse,
    AchievementSummaryResponse,
    BadgeOut,
    BadgeSeenRequest,
    ChallengeOut,
    ChallengesResponse,
    ShieldProgressOut,
)

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/achievements", tags=["achievements"])


@router.get("", response_model=AchievementsResponse)
async def get_achievements(user: CurrentUser, db_path: DbPath):
    """Return full badge catalog with user progress + shield info."""
    user_id = user["user_id"]
    today = await user_today_str(db_path, user_id)
    await auto_consume_shields_for_streak(db_path, user_id, today)

    badges_progress = await compute_all_badge_progress(db_path, user_id, today)
    shields = await get_streak_shields(db_path, user_id)

    prefs = await get_user_prefs(db_path, user_id)
    gamification = prefs.get("gamification", "full") if prefs else "full"

    return AchievementsResponse(
        badges=[BadgeOut(**b) for b in badges_progress],
        shields_available=shields["available"],
        shields_total_earned=shields["total_earned"],
        gamification=gamification,
    )


@router.get("/challenges", response_model=ChallengesResponse)
async def get_challenges(user: CurrentUser, db_path: DbPath):
    """Return active daily + weekly challenge with progress."""
    user_id = user["user_id"]
    today = await user_today_str(db_path, user_id)
    data = await get_challenges_with_progress(db_path, user_id, today)

    return ChallengesResponse(
        daily=ChallengeOut(**data["daily"]),
        weekly=ChallengeOut(**data["weekly"]),
    )


@router.get("/summary", response_model=AchievementSummaryResponse)
async def get_achievement_summary(user: CurrentUser, db_path: DbPath):
    """Lightweight summary for header/settings display."""
    user_id = user["user_id"]
    today = await user_today_str(db_path, user_id)
    await auto_consume_shields_for_streak(db_path, user_id, today)

    earned = await get_user_badges(db_path, user_id)
    unseen = await get_unseen_badges(db_path, user_id)
    shields = await get_streak_shields(db_path, user_id)

    yesterday = (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    shield_used = await was_shield_used_recently(db_path, user_id, yesterday)

    # Shields used in last 7 days with bridged_date
    seven_days_ago = (date.fromisoformat(today) - timedelta(days=7)).isoformat()
    shield_used_dates = await get_shields_used_recently(db_path, user_id, seven_days_ago)

    # Shield earning progress
    progress = await get_shield_progress(db_path, user_id, today)
    shield_progress = ShieldProgressOut(
        on_target_days=progress["on_target_days"],
        shields_earned_total=progress["shields_earned_total"],
        days_until_next=progress["days_until_next"],
    )

    return AchievementSummaryResponse(
        total_earned=len(earned),
        total_badges=len(BADGES),
        unseen_count=len(unseen),
        shields_available=shields["available"],
        shield_used_today=shield_used,
        shield_used_dates=shield_used_dates,
        shield_progress=shield_progress,
        shields_at_max=shields["available"] >= 3,
    )


@router.get("/shields/history")
async def get_shields_history(user: CurrentUser, db_path: DbPath):
    """Return shield earned/used history for the achievements tab."""
    history = await get_shield_history(db_path, user["user_id"])
    return {"history": history}


@router.post("/seen")
@limiter.limit("60/minute")
async def mark_seen(request: Request, body: BadgeSeenRequest, user: CurrentUser, db_path: DbPath):
    """Mark badges as seen (after celebration overlay)."""
    await mark_badges_seen(db_path, user["user_id"], body.badge_ids)
    return {"ok": True}
