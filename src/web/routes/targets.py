"""AI-powered target suggestion routes."""

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request

from src.web.rate_limit import limiter

from src.db import (
    create_meal_session,
    get_meal_session,
    get_user_profile,
    set_user_profile,
    set_user_target,
    update_meal_session,
)
from src.gemini import gemini_suggest_targets
from src.web.budget_gate import BudgetExceededError
from src.web.constants import BUDGET_EXCEEDED_MESSAGE
from src.web.deps import CurrentUser, DbPath
from src.web.schemas import (
    TargetAcceptRequest,
    TargetRefineRequest,
    TargetSuggestRequest,
    TargetSuggestResponse,
    TargetsRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings/targets", tags=["targets"])


def _mifflin_st_jeor(req) -> dict | None:
    """Deterministic target calculation using Mifflin-St Jeor. Returns targets dict or None if insufficient data."""
    if not req.weight_kg or not req.height_cm or not req.age:
        return None
    sex = (req.sex or "").lower()
    if sex == "male":
        bmr = (10 * req.weight_kg) + (6.25 * req.height_cm) - (5 * req.age) + 5
    else:
        bmr = (10 * req.weight_kg) + (6.25 * req.height_cm) - (5 * req.age) - 161

    multipliers = {"sedentary": 1.2, "lightly_active": 1.375, "active": 1.55, "very_active": 1.725}
    tdee = bmr * multipliers.get(req.activity_level, 1.375)

    rate = abs(req.weight_change_rate_kg or 0.5)
    daily_adjust = rate * 7700 / 7  # 7700 kcal per kg of fat
    if req.goal == "lose_weight":
        calories = max(1200 if sex != "male" else 1500, tdee - daily_adjust)
    elif req.goal == "gain_weight":
        calories = tdee + daily_adjust
    else:
        calories = tdee

    # Evidence-based macro split
    protein = req.weight_kg * (2.0 if req.goal == "lose_weight" else 1.8 if req.goal == "gain_weight" else 1.6)
    fat = calories * 0.28 / 9  # ~28% of calories from fat
    remaining_cal = calories - (protein * 4) - (fat * 9)
    carbs = max(remaining_cal / 4, 50)

    return {
        "calories": round(calories),
        "protein": round(protein),
        "carbs": round(carbs),
        "fat": round(fat),
        "explanation": f"Based on Mifflin-St Jeor equation: BMR {bmr:.0f} kcal × {multipliers.get(req.activity_level, 1.375)} activity = {tdee:.0f} TDEE.",
    }


def _build_context(req: TargetSuggestRequest) -> str:
    """Build natural language context string from request fields."""
    parts: list[str] = []
    if req.age is not None:
        parts.append(f"{req.age}-year-old")
    if req.sex:
        parts.append(req.sex)
    if req.height_cm is not None:
        parts.append(f"{req.height_cm:.0f}cm")
    if req.weight_kg is not None:
        parts.append(f"{req.weight_kg:.1f}kg")

    goal_map = {
        "lose_weight": "lose weight",
        "maintain": "maintain weight",
        "gain_weight": "gain weight / build muscle",
    }
    goal = goal_map.get(req.goal, req.goal)

    activity_map = {
        "sedentary": "sedentary",
        "lightly_active": "lightly active",
        "active": "active",
        "very_active": "very active",
    }
    activity = activity_map.get(req.activity_level, req.activity_level)

    intro = f"I'm a {', '.join(parts)}. " if parts else ""
    context = (
        f"{intro}Goal: {goal}. "
        f"Daily lifestyle activity level (NEAT only, excluding structured workouts): {activity}."
    )
    if req.workouts_per_week is not None:
        context += f" Structured workouts: {req.workouts_per_week} sessions per week (separate from the lifestyle activity above)."
    if req.weight_change_rate_kg is not None and req.goal != "maintain":
        daily_cal = abs(req.weight_change_rate_kg) * 7700 / 7
        direction = "deficit" if req.goal == "lose_weight" else "surplus"
        context += f" Target rate: {req.weight_change_rate_kg:.2f} kg/week ({direction} of ~{daily_cal:.0f} kcal/day)."
    context += " Please generate my daily macro targets now."
    return context


def _build_profile(req: TargetSuggestRequest) -> dict:
    """Build user_profile dict from request fields."""
    return {
        "age": req.age,
        "sex": req.sex,
        "weight_kg": req.weight_kg,
        "height_cm": req.height_cm,
    }


@router.post("/suggest", response_model=TargetSuggestResponse)
@limiter.limit("5/minute")
async def suggest_targets(request: Request, req: TargetSuggestRequest, user: CurrentUser, db_path: DbPath):
    """Start an AI target-setting session."""
    session_id = str(uuid.uuid4())

    # Merge request fields with saved profile so known data is never lost
    saved = await get_user_profile(db_path, user["user_id"])
    for field in ("age", "sex", "weight_kg", "height_cm"):
        if getattr(req, field) is None and saved.get(field) is not None:
            setattr(req, field, saved[field])

    context = _build_context(req)
    # Fold the user's first-message intent into the prompt so the initial
    # suggestion already reflects what they asked for — saves a round-trip
    # vs the legacy suggest→refine sequence when the user is editing
    # existing targets.
    if req.seed_message:
        seed = req.seed_message.strip()
        if seed:
            context = f"{context}\n\nUser's first message: {seed}"
    profile = _build_profile(req)

    # gemini_suggest_targets mutates conversation in-place (appends user + model turns).
    # We pass a fresh list and use the mutated result as the canonical conversation.
    conversation: list[dict] = []

    try:
        raw_text, parsed, user_requested_change = await gemini_suggest_targets(
            user_context=context,
            conversation=conversation,
            db_path=db_path,
            user_id=user["user_id"],
            user_profile=profile,
        )
    except BudgetExceededError:
        raise HTTPException(
            status_code=503,
            detail={"reason": "budget_exceeded", "message": BUDGET_EXCEEDED_MESSAGE},
        )
    except Exception as e:
        # Gemini failed - try deterministic fallback
        fallback = _mifflin_st_jeor(req)
        if fallback:
            targets_out = TargetsRequest(
                calories=fallback["calories"], protein=fallback["protein"],
                carbs=fallback["carbs"], fat=fallback["fat"],
            )
            await create_meal_session(
                db_path, session_id=session_id, user_id=user["user_id"],
                conversation=json.dumps(conversation), meal_type="target_setting",
            )
            return TargetSuggestResponse(
                session_id=session_id, targets=targets_out,
                explanation=fallback["explanation"] + " (AI was unavailable, using formula-based estimate.)",
                reply_text=fallback["explanation"],
            )
        await create_meal_session(
            db_path, session_id=session_id, user_id=user["user_id"],
            conversation=json.dumps(conversation), meal_type="target_setting",
        )
        # Do NOT echo the underlying exception text to the client - it can
        # leak Gemini URLs, internal stack frames, or config state.  Log
        # internally and return a generic message.
        logger.exception("AI target suggestion failed for user_id=%s", user["user_id"])
        return TargetSuggestResponse(session_id=session_id, error="AI suggestion is temporarily unavailable. Please try again.")

    targets_out = None
    explanation = ""
    if parsed:
        targets_out = TargetsRequest(
            calories=parsed["calories"],
            protein=parsed["protein"],
            carbs=parsed["carbs"],
            fat=parsed["fat"],
        )
        explanation = parsed.get("explanation", "")

        # Save profile data Gemini extracted
        gemini_profile = parsed.get("profile", {})
        if gemini_profile:
            profile_updates = {k: v for k, v in gemini_profile.items() if v is not None}
            if profile_updates:
                await set_user_profile(db_path, user["user_id"], **profile_updates)
    else:
        # Gemini replied but extraction didn't produce structured targets.
        # Fall back to Mifflin-St Jeor so the UI always has seed numbers the
        # user can review / edit - skip the "chat first" dead-end.
        fallback = _mifflin_st_jeor(req)
        if fallback:
            targets_out = TargetsRequest(
                calories=fallback["calories"],
                protein=fallback["protein"],
                carbs=fallback["carbs"],
                fat=fallback["fat"],
            )
            explanation = fallback["explanation"]

    # Store session with the full conversation (includes system prompt from first turn)
    await create_meal_session(
        db_path,
        session_id=session_id,
        user_id=user["user_id"],
        conversation=json.dumps(conversation),
        nutrition=json.dumps(parsed) if parsed else "",
        meal_type="target_setting",
    )

    return TargetSuggestResponse(
        session_id=session_id,
        targets=targets_out,
        explanation=explanation,
        reply_text=raw_text,
        user_requested_change=user_requested_change,
    )


@router.post("/suggest/{session_id}/refine", response_model=TargetSuggestResponse)
@limiter.limit("10/minute")
async def refine_targets(request: Request, session_id: str, req: TargetRefineRequest, user: CurrentUser, db_path: DbPath):
    """Refine targets via chat."""
    session = await get_meal_session(db_path, session_id, user_id=user["user_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("meal_type") != "target_setting":
        raise HTTPException(status_code=400, detail="Not a target-setting session")

    # gemini_suggest_targets mutates this list in-place (appends user + model turns)
    conversation = json.loads(session.get("conversation", "[]"))

    # Load user profile for context re-injection on each turn
    saved_profile = await get_user_profile(db_path, user["user_id"])

    try:
        raw_text, parsed, user_requested_change = await gemini_suggest_targets(
            user_context=req.text,
            conversation=conversation,
            db_path=db_path,
            user_id=user["user_id"],
            user_profile=saved_profile,
        )
    except BudgetExceededError:
        raise HTTPException(
            status_code=503,
            detail={"reason": "budget_exceeded", "message": BUDGET_EXCEEDED_MESSAGE},
        )
    except Exception:
        logger.exception("AI target refinement failed for user_id=%s", user["user_id"])
        return TargetSuggestResponse(
            session_id=session_id,
            error="AI refinement is temporarily unavailable. Please try again.",
        )

    targets_out = None
    explanation = ""
    if parsed:
        targets_out = TargetsRequest(
            calories=parsed["calories"],
            protein=parsed["protein"],
            carbs=parsed["carbs"],
            fat=parsed["fat"],
        )
        explanation = parsed.get("explanation", "")

    await update_meal_session(
        db_path,
        session_id,
        user_id=user["user_id"],
        conversation=json.dumps(conversation),
        nutrition=json.dumps(parsed) if parsed else session.get("nutrition", ""),
    )

    return TargetSuggestResponse(
        session_id=session_id,
        targets=targets_out,
        explanation=explanation,
        reply_text=raw_text,
        user_requested_change=user_requested_change,
    )


@router.post("/suggest/{session_id}/accept")
@limiter.limit("10/minute")
async def accept_targets(request: Request, session_id: str, req: TargetAcceptRequest, user: CurrentUser, db_path: DbPath):
    """Accept and save the suggested targets."""
    session = await get_meal_session(db_path, session_id, user_id=user["user_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.get("meal_type") != "target_setting":
        raise HTTPException(status_code=400, detail="Not a target-setting session")

    await set_user_target(
        db_path, user["user_id"],
        calories=req.calories,
        protein=req.protein,
        carbs=req.carbs,
        fat=req.fat,
        set_by="gemini",
    )

    await update_meal_session(db_path, session_id, user_id=user["user_id"], status="accepted")

    # Mirror the accepted targets into the user_memories store so the chat
    # coach has the user's current baseline as semantic context. Overwrites
    # any prior "Targets baseline:" memory for this user (single-slot
    # semantics — only the latest baseline should be visible to the coach).
    # If the chat refine session has substantive user turns, also generate
    # a one-sentence "Goal context:" memory from the user-stated intent.
    try:
        from src.db import (
            get_user_profile, upsert_marker_memory,
            build_targets_baseline_text,
            ONBOARDING_TARGETS_MARKER, GOAL_CONTEXT_MARKER,
        )
        from src.embeddings import schedule_embed_for_memory
        profile = await get_user_profile(db_path, user["user_id"])
        baseline_text = build_targets_baseline_text(
            req.calories, req.protein, req.carbs, req.fat,
            goal=profile.get("goal"), activity_level=profile.get("activity_level"),
        )
        baseline_id = await upsert_marker_memory(
            db_path, user["user_id"], "note", baseline_text, ONBOARDING_TARGETS_MARKER,
        )
        schedule_embed_for_memory(db_path, baseline_id, baseline_text)

        # Goal-context memory from the refine chat (only when one exists).
        # Pull only the user's turns so the summarizer doesn't echo the AI.
        user_turns: list[str] = []
        try:
            conversation = json.loads(session.get("conversation") or "[]")
            for turn in conversation:
                if isinstance(turn, dict) and turn.get("role") == "user":
                    txt = turn.get("text") or turn.get("content")
                    if isinstance(txt, str) and txt.strip():
                        user_turns.append(txt)
        except Exception:
            user_turns = []
        if user_turns:
            from src.gemini import gemini_summarize_target_chat
            summary = await gemini_summarize_target_chat(
                user_turns, db_path=db_path, user_id=user["user_id"],
            )
            if summary:
                ctx_text = f"{GOAL_CONTEXT_MARKER} {summary}"[:200]
                ctx_id = await upsert_marker_memory(
                    db_path, user["user_id"], "note", ctx_text, GOAL_CONTEXT_MARKER,
                )
                schedule_embed_for_memory(db_path, ctx_id, ctx_text)
    except Exception:
        # Memory write is best-effort - never block target acceptance on it.
        logger.exception("onboarding-targets memory upsert failed")

    # Evaluate target_set badges - only the *first* real target set triggers
    # evaluation. See settings.update_targets for the same guard.
    new_badges: list = []
    try:
        from src.badge_engine import evaluate_badges
        from src.db import increment_target_set_count, get_target_set_count, get_user_prefs
        from src.services import user_today_str
        prior_count = await get_target_set_count(db_path, user["user_id"])
        await increment_target_set_count(db_path, user["user_id"])
        if prior_count == 0:
            today = await user_today_str(db_path, user["user_id"])
            prefs = await get_user_prefs(db_path, user["user_id"])
            if prefs.get("gamification", "full") != "off":
                new_badges = await evaluate_badges(db_path, user["user_id"], "target_set", {"today_str": today})
    except Exception:
        logger.exception("Badge evaluation failed for target_set")

    return {
        "message": "Targets saved",
        "calories": req.calories,
        "protein": req.protein,
        "carbs": req.carbs,
        "fat": req.fat,
        "new_badges": new_badges,
    }
