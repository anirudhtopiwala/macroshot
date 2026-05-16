"""Meal analysis, CRUD, and session routes."""

import asyncio
import json
import logging
import os
import uuid
from io import BytesIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from src.web.rate_limit import limiter

from src.db import (
    delete_meal,
    get_meals_for_date,
    get_push_subscriptions,
    get_streak_shields,

    get_user_prefs,
    increment_usage,
    insert_meal_feedback,
    log_event,
    log_meal as db_log_meal,
    get_meal_by_id,
    get_meals_paginated,
    get_meal_session,
    update_meal as db_update_meal,
    update_meal_session,
)
from src.web.budget_gate import BudgetExceededError
from src.web.constants import (
    BUDGET_EXCEEDED_MESSAGE,
    FREE_CORRECTION_LIMIT,
    limit_message,
)
from src.services import accept_meal, analyze_meal, send_correction, classify_meal_time, get_user_tz, create_session_from_meal, user_today_str, note_item_removed
from src.web.deps import CurrentUser, DbPath, SubInfo, UNLIMITED
from src.web.schemas import (
    AcceptRequest,
    AcceptResponse,
    AnalyzeResponse,
    CopyDayRequest,
    CorrectionRequest,
    CorrectionResponse,
    FoodItemOut,
    ItemRemovedRequest,
    MealOut,
    MealUpdateRequest,
    NutritionOut,
)

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/meals", tags=["meals"])

# Hold references to fire-and-forget background tasks so they aren't GC'd mid-flight
_background_tasks: set[asyncio.Task] = set()

IMAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data", "images")

# B19: bound concurrent GCS uploads. The fire-and-forget upload
# fan-out can balloon to hundreds of in-flight TCP connections under
# burst traffic on the shared 1 GB VM.
_GCS_UPLOAD_SEM = asyncio.Semaphore(8)

# B20: bound concurrent EXIF-strip / re-encode operations. A single
# re-encode of a 25 MP image holds ~75 MB of pixel data; five concurrent
# 5-image uploads would peak well over 1 GB of RAM.
_REENCODE_SEM = asyncio.Semaphore(2)

# B17: per-user lifetime cap on stored meal images. This is intentionally
# generous (most users log < 5 meals/day with images = ~1825/yr); the cap
# exists to bound disk/GCS spend per user rather than enforce a quota.
MAX_USER_MEAL_IMAGES = 10000


def _strip_exif_to_jpeg(data: bytes) -> bytes:
    """Re-encode an uploaded image as JPEG with no EXIF metadata.

    Strips GPS coordinates, camera info, timestamps, and any other EXIF fields
    that phones embed in photos. Applies EXIF orientation as actual pixel
    rotation before stripping (so iPhone photos still display right-side-up).

    Returns the sanitized JPEG bytes.

    Raises:
        HTTPException(400): if Pillow detects a decompression bomb (image
            larger than MAX_IMAGE_PIXELS, set in src/web/app.py) or otherwise
            fails to decode the image.  We REFUSE the upload rather than
            silently passing raw bytes through, because that fallback would
            (a) leak EXIF metadata into GCS and (b) write a hostile file to
            disk that we cannot render anyway.
    """
    from PIL import Image as PILImage, ImageOps
    from PIL import UnidentifiedImageError
    try:
        from PIL.Image import DecompressionBombError
    except ImportError:  # pragma: no cover - older Pillow
        DecompressionBombError = Exception  # type: ignore[assignment,misc]

    try:
        img = PILImage.open(BytesIO(data))
        # B34: reject animated PNG/WebP/GIF — re-encoding silently drops
        # frames, so refuse them up front rather than producing a misleading
        # single-frame meal photo.
        if getattr(img, "is_animated", False):
            raise HTTPException(status_code=415, detail="Animated images not supported")
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        out = BytesIO()
        # Note: not passing exif=... means EXIF is dropped entirely.
        img.save(out, "JPEG", quality=85)
        return out.getvalue()
    except HTTPException:
        raise
    except DecompressionBombError:
        logger.warning("Rejected decompression bomb image upload")
        raise HTTPException(status_code=400, detail="Image dimensions too large")
    except (UnidentifiedImageError, OSError, ValueError):
        logger.warning("Rejected unprocessable image upload", exc_info=True)
        raise HTTPException(status_code=400, detail="Could not process image")


def _nutrition_to_out(n) -> NutritionOut | None:
    """Convert a NutritionResult (or dict) to NutritionOut."""
    if n is None:
        return None
    if isinstance(n, dict):
        items = []
        for i in n.get("items", []):
            items.append(FoodItemOut(**{k: i[k] for k in ("name", "description", "brand", "has_label", "calories", "protein", "carbs", "fat", "weight_g", "source") if k in i}))
        return NutritionOut(
            item_name=n.get("item_name", ""),
            meal_description=n.get("meal_description", ""),
            items=items,
            calories=n.get("calories", 0),
            protein=n.get("protein", 0),
            carbs=n.get("carbs", 0),
            fat=n.get("fat", 0),
            source=n.get("source", "Gemini"),
        )
    # NutritionResult object
    items = [FoodItemOut(name=i.name, description=i.description, brand=i.brand, has_label=i.has_label, calories=i.calories, protein=i.protein, carbs=i.carbs, fat=i.fat, weight_g=i.weight_g, source=i.source) for i in (n.items or [])]
    return NutritionOut(
        item_name=n.item_name,
        meal_description=n.meal_description,
        items=items,
        calories=n.calories,
        protein=n.protein,
        carbs=n.carbs,
        fat=n.fat,
        source=n.source,
    )


@router.post("/analyze", response_model=AnalyzeResponse)
@limiter.limit("5/minute")
async def analyze(
    request: Request,
    user: CurrentUser,
    db_path: DbPath,
    sub: SubInfo,
    text: str = Form(default="", max_length=5000),
    meal_type: str = Form(default=""),
    images: list[UploadFile] = File(default=[]),
):
    """Analyze a meal from images and/or text description."""
    from datetime import datetime, timezone

    MAX_IMAGES = 5
    MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB per image
    MAX_TOTAL_BYTES = 60 * 1024 * 1024  # 60 MB across all images

    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_IMAGES} images allowed")

    # B17: per-user lifetime cap on stored meal images.
    if images:
        from src.db import count_user_meal_images_with_path
        try:
            stored = await count_user_meal_images_with_path(db_path, user["user_id"])
        except Exception:
            stored = 0  # if helper missing, don't break uploads
        if stored >= MAX_USER_MEAL_IMAGES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Per-account image quota reached ({MAX_USER_MEAL_IMAGES}). "
                    f"Delete old meals to free space."
                ),
            )

    # Valid image magic bytes (JPEG, PNG, WebP, GIF)
    _IMAGE_SIGNATURES = {
        b'\xff\xd8\xff': 'jpeg',
        b'\x89PNG': 'png',
        b'RIFF': 'riff',  # RIFF container - need further check for WEBP
        b'GIF8': 'gif',
    }

    image_bytes = []
    total_bytes = 0
    # B4 (per-image streaming): read in 64 KB chunks so an oversize upload
    # is short-circuited before it occupies 10+ MB of RAM. Cap per-image at
    # 10 MB and total at 60 MB across the multipart payload.
    CHUNK = 64 * 1024
    for img in images:
        buf = bytearray()
        while True:
            chunk = await img.read(CHUNK)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > MAX_IMAGE_SIZE:
                raise HTTPException(status_code=400, detail="Image too large (max 10 MB)")
            total_bytes += len(chunk)
            if total_bytes > MAX_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail="Total upload too large (max 60 MB)")
        data = bytes(buf)
        if data:
            # Validate magic bytes to prevent malicious file uploads
            header = data[:12]
            matched = any(header.startswith(sig) for sig in _IMAGE_SIGNATURES)
            if not matched:
                raise HTTPException(status_code=400, detail="Invalid image format (JPEG, PNG, WebP, GIF only)")
            # RIFF container: verify it's actually WebP (not WAV/AVI)
            if header[:4] == b'RIFF' and data[8:12] != b'WEBP':
                raise HTTPException(status_code=400, detail="Invalid image format (JPEG, PNG, WebP, GIF only)")
            # Strip EXIF metadata (including GPS coordinates) before the bytes
            # touch anything else: AI analysis, disk, GCS, and the fallback
            # save path all see sanitized JPEG bytes from this point on.
            # B20: gate the re-encode behind a small semaphore so concurrent
            # 5-image uploads don't blow the VM's RSS ceiling.
            async with _REENCODE_SEM:
                sanitized = await asyncio.to_thread(_strip_exif_to_jpeg, data)
            image_bytes.append(sanitized)

    if not image_bytes and not text.strip():
        raise HTTPException(status_code=400, detail="Provide at least one image or text description")

    # Gate: atomically check + reserve quota before analysis.
    #
    # Two paths:
    #   • Images attached → counts toward the daily image_query cap
    #     (covers initial analyze + any subsequent corrections on the
    #     same session, since retries hit Gemini with the same images).
    #   • Text only → counts toward the daily text_meal cap (new in beta).
    #
    # A limit of UNLIMITED (self mode) skips the check entirely.
    today_str = await user_today_str(db_path, user["user_id"])
    if image_bytes:
        tier_limit = sub.image_queries_limit
        if tier_limit != UNLIMITED:
            usage_result = await increment_usage(
                db_path, user["user_id"], "image_query", today_str, limit=tier_limit,
            )
            if not usage_result.get("allowed"):
                logger.info(
                    "cap_hit feature=image_analysis user_id=%d limit=%d is_premium=%s",
                    user["user_id"], tier_limit, sub.is_premium,
                )
                await log_event(
                    db_path, user["user_id"], "limit_hit",
                    metadata={
                        "feature": "image_analysis",
                        "limit": tier_limit,
                        "is_premium": sub.is_premium,
                    },
                )
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "limit_reached",
                        "feature": "image_analysis",
                        "used": usage_result["used_count"],
                        "limit": tier_limit,
                        "is_premium": sub.is_premium,
                        "message": limit_message("image_analysis", tier_limit),
                    },
                )
    else:
        tier_limit = sub.text_meals_limit
        if tier_limit != UNLIMITED:
            usage_result = await increment_usage(
                db_path, user["user_id"], "text_meal", today_str, limit=tier_limit,
            )
            if not usage_result.get("allowed"):
                logger.info(
                    "cap_hit feature=text_meal user_id=%d limit=%d is_premium=%s",
                    user["user_id"], tier_limit, sub.is_premium,
                )
                await log_event(
                    db_path, user["user_id"], "limit_hit",
                    metadata={
                        "feature": "text_meal",
                        "limit": tier_limit,
                        "is_premium": sub.is_premium,
                    },
                )
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

    try:
        result = await analyze_meal(
            images=image_bytes,
            user_text=text.strip(),
            user_id=user["user_id"],
            db_path=db_path,
            meal_type=meal_type,
        )
    except BudgetExceededError:
        raise HTTPException(
            status_code=503,
            detail={"reason": "budget_exceeded", "message": BUDGET_EXCEEDED_MESSAGE},
        )

    # Save images to disk + generate thumbnails (in background thread to avoid blocking)
    if image_bytes and result.get("session_id"):
        session_dir = os.path.join(IMAGE_DIR, str(user["user_id"]), result["session_id"])
        os.makedirs(session_dir, exist_ok=True)

        def _save_images_and_thumbs():
            from PIL import Image as PILImage, ImageOps
            for i, img_data in enumerate(image_bytes):
                try:
                    # Apply EXIF orientation (iPhone stores rotation in metadata, not pixels)
                    img = PILImage.open(BytesIO(img_data))
                    img = ImageOps.exif_transpose(img)
                    if img.mode != "RGB":
                        img = img.convert("RGB")
                    # Save orientation-corrected original
                    path = os.path.join(session_dir, f"image_{i}.jpg")
                    img.save(path, "JPEG", quality=85)
                    # Square center-cropped thumbnail for MealCard list
                    # views. Using img.thumbnail() alone would preserve
                    # aspect ratio and leave tall/wide source images as
                    # non-square rectangles that look off in the list slot.
                    w, h = img.size
                    side = min(w, h)
                    left = (w - side) // 2
                    top = (h - side) // 2
                    sq = img.crop((left, top, left + side, top + side))
                    sq = sq.resize((192, 192), PILImage.LANCZOS)
                    thumb_path = os.path.join(session_dir, f"image_{i}_thumb.jpg")
                    sq.save(thumb_path, "JPEG", quality=70)
                except Exception:
                    logger.debug("Image processing failed for image_%d, saving raw", i)
                    path = os.path.join(session_dir, f"image_{i}.jpg")
                    with open(path, "wb") as f:
                        f.write(img_data)

        await asyncio.to_thread(_save_images_and_thumbs)

        # Upload to GCS (fire-and-forget - don't block the response)
        # B19: gate uploads behind _GCS_UPLOAD_SEM so a flood of concurrent
        # accepts can't open more than 8 simultaneous outbound HTTP/2 streams.
        async def _upload_to_gcs():
            try:
                from src.gcs import gcs_upload, gcs_upload_file
                uid = str(user["user_id"])
                sid = result["session_id"]
                for i, img_data in enumerate(image_bytes):
                    async with _GCS_UPLOAD_SEM:
                        rel = f"{uid}/{sid}/image_{i}.jpg"
                        await gcs_upload(rel, img_data)
                        thumb_local = os.path.join(session_dir, f"image_{i}_thumb.jpg")
                        if os.path.isfile(thumb_local):
                            await gcs_upload_file(f"{uid}/{sid}/image_{i}_thumb.jpg", thumb_local)
            except Exception:
                logger.warning("Background GCS upload failed for session %s", result.get("session_id"), exc_info=True)

        task = asyncio.create_task(_upload_to_gcs())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return AnalyzeResponse(
        session_id=result["session_id"],
        nutrition=_nutrition_to_out(result.get("nutrition")),
        questions=result.get("questions", []),
        raw_text=result.get("raw_text", ""),
        error=result.get("error"),
    )


@router.post("/sessions/{session_id}/correct", response_model=CorrectionResponse)
@limiter.limit("10/minute")
async def correct_session(request: Request, session_id: str, req: CorrectionRequest, user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Send a correction to an existing analysis session."""
    # Always load the session once - we need it for the image-quota check
    # (and for the free-tier per-session correction count below).
    session = await get_meal_session(db_path, session_id, user["user_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Detect whether this session was seeded with images. Tracked only for
    # telemetry below - corrections are text-only Gemini calls (no image
    # bytes are re-sent; see _build_chat_contents in src/gemini.py), so they
    # do NOT consume the daily image_query cap.
    session_img_dir = os.path.join(IMAGE_DIR, str(user["user_id"]), session_id)
    session_has_images = os.path.isdir(session_img_dir) and any(
        f.endswith(('.jpg', '.jpeg', '.png', '.webp')) and '_thumb' not in f
        for f in os.listdir(session_img_dir)
    )

    # Per-meal AI-edit cap: counts only Gemini-backed corrections on this
    # session. Manual field edits (dial pickers, macro tweaks) go through
    # PUT /meals/{id} or PATCH /sessions/{id} - neither of which reach this
    # endpoint - so they don't consume the quota.
    #
    # The count comes from the session's own conversation history. Subtract
    # 1 because the first user turn is the initial analysis prompt, not an
    # edit. Legacy FREE_CORRECTION_LIMIT=2 stays as the fallback when a
    # user is somehow on the free tier post-beta (e.g., trial expired).
    per_meal_limit = sub.meal_edits_limit
    if per_meal_limit == UNLIMITED:
        pass  # self mode - no cap
    else:
        try:
            convo = json.loads(session.get("conversation") or "[]")
        except (json.JSONDecodeError, TypeError):
            convo = []
        user_turns = max(0, sum(1 for m in convo if m.get("role") == "user") - 1)
        # In post-beta free tier (per_meal_limit=0), also enforce legacy free cap.
        effective_limit = per_meal_limit if per_meal_limit > 0 else FREE_CORRECTION_LIMIT
        if user_turns >= effective_limit:
            logger.info(
                "cap_hit feature=meal_edit user_id=%d limit=%d is_premium=%s",
                user["user_id"], effective_limit, sub.is_premium,
            )
            await log_event(
                db_path, user["user_id"], "limit_hit",
                metadata={
                    "feature": "meal_edit",
                    "limit": effective_limit,
                    "is_premium": sub.is_premium,
                },
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "limit_reached",
                    "feature": "meal_edit",
                    "used": user_turns,
                    "limit": effective_limit,
                    "is_premium": sub.is_premium,
                    "message": limit_message("meal_edit", effective_limit, scope="on this meal"),
                },
            )

    try:
        result = await send_correction(
            session_id=session_id,
            user_text=req.text,
            user_id=user["user_id"],
            db_path=db_path,
        )
    except BudgetExceededError:
        raise HTTPException(
            status_code=503,
            detail={"reason": "budget_exceeded", "message": BUDGET_EXCEEDED_MESSAGE},
        )

    # Private telemetry: one row per correction attempt (success or failure)
    await log_event(
        db_path, user["user_id"], "meal_correct",
        metadata={"has_image": session_has_images, "error": bool(result.get("error"))},
    )

    # Evaluate meal_correct badges
    new_badges: list = []
    if not result.get("error"):
        try:
            from src.badge_engine import evaluate_badges
            prefs = await get_user_prefs(db_path, user["user_id"])
            if prefs.get("gamification", "full") != "off":
                today = await user_today_str(db_path, user["user_id"])
                new_badges = await evaluate_badges(db_path, user["user_id"], "meal_correct", {"today_str": today})
        except Exception:
            logger.exception("Badge evaluation failed for meal_correct")

    return CorrectionResponse(
        nutrition=_nutrition_to_out(result.get("nutrition")),
        reply_text=result.get("reply_text", ""),
        error=result.get("error"),
        new_badges=new_badges,
        questions=result.get("questions", []),
    )


@router.post("/sessions/{session_id}/item-removed")
@limiter.limit("30/minute")
async def session_item_removed(
    request: Request,
    session_id: str,
    req: ItemRemovedRequest,
    user: CurrentUser,
    db_path: DbPath,
):
    """Record a manual item deletion so any future Gemini correction on
    this session knows the item is gone. No Gemini call, no quota cost."""
    result = await note_item_removed(
        session_id=session_id,
        item_name=req.item_name,
        user_id=user["user_id"],
        db_path=db_path,
    )
    if not result.get("ok"):
        err = result.get("error", "")
        if err == "Session not found":
            raise HTTPException(status_code=404, detail=err)
        if err == "already_accepted":
            # Idempotent from the client's POV - the deletion is already
            # baked into the persisted meal anyway.
            return {"ok": True, "skipped": "already_accepted"}
        raise HTTPException(status_code=400, detail=err or "Failed to record deletion")
    return {"ok": True}


@router.post("/sessions/{session_id}/accept", response_model=AcceptResponse)
@limiter.limit("30/minute")
async def accept_session(request: Request, session_id: str, user: CurrentUser, db_path: DbPath, sub: SubInfo, req: AcceptRequest | None = None):
    """Accept and log the meal from a session."""
    # Check ownership before any mutations
    session = await get_meal_session(db_path, session_id, user["user_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # If frontend sends modified nutrition, update session before accepting
    if req and req.nutrition:
        nutrition_dict = req.nutrition.model_dump()
        await update_meal_session(db_path, session_id, user_id=user["user_id"], nutrition=json.dumps(nutrition_dict))

    # For barcode-sourced sessions, the product image is downloaded in the
    # background by the /meals/barcode endpoint. Wait briefly for it to finish
    # so the file is on disk before we scan the session directory.
    from src.web.routes.barcode import await_barcode_image_download
    await await_barcode_image_download(session_id)

    # Collect image paths from session directory
    session_img_dir = os.path.join(IMAGE_DIR, str(user["user_id"]), session_id)
    image_paths = []
    if os.path.isdir(session_img_dir):
        for f in sorted(os.listdir(session_img_dir)):
            if f.endswith(('.jpg', '.jpeg', '.png', '.webp')) and '_thumb' not in f:
                image_paths.append(f'{user["user_id"]}/{session_id}/{f}')

    # Get user timezone for correct meal timestamp
    prefs = await get_user_prefs(db_path, user["user_id"])
    tz_str = prefs.get("timezone")

    result = await accept_meal(
        user_id=user["user_id"],
        session_id=session_id,
        db_path=db_path,
        tz_str=tz_str,
        image_paths=image_paths or None,
        logged_at_override=req.logged_at if req else None,
        servings=(req.servings if req and req.servings else 1.0),
    )

    # Evaluate badges after successful meal accept (skip if gamification off)
    new_badges = []
    if result.get("meal_log_id") and not result.get("error"):
        try:
            gamification = prefs.get("gamification", "full") if prefs else "full"
            if gamification != "off":
                from src.badge_engine import evaluate_badges
                from src.services import user_today_str
                today = await user_today_str(db_path, user["user_id"])

                # Snapshot shield count before badge evaluation
                shields_before = (await get_streak_shields(db_path, user["user_id"]))["available"]

                new_badges = await evaluate_badges(db_path, user["user_id"], "meal_accept", {
                    "today_str": today,
                    "has_image": bool(image_paths),
                    "is_premium": sub.is_premium,
                })

                # Detect shield consumption and send push notification
                shields_after = (await get_streak_shields(db_path, user["user_id"]))["available"]
                if shields_after < shields_before:
                    await log_event(
                        db_path, user["user_id"], "shield_consumed",
                        metadata={"available_after": shields_after, "via": "meal_accept"},
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
            logger.exception("Badge evaluation failed for meal_accept")

    return AcceptResponse(
        meal_id=result.get("meal_log_id"),
        nutrition=_nutrition_to_out(result.get("nutrition")),
        progress=result.get("progress", {}),
        new_badges=[{"badge_id": b["badge_id"], "name": b["name"], "tier": b["tier"], "tier_name": b["tier_name"], "is_new": b["is_new"], "icon": b.get("icon", "")} for b in new_badges],
        error=result.get("error"),
    )


@router.post("/sessions/{session_id}/cancel")
@limiter.limit("30/minute")
async def cancel_session(request: Request, session_id: str, user: CurrentUser, db_path: DbPath):
    """Cancel a meal analysis session and clean up uploaded images."""
    import shutil

    session = await get_meal_session(db_path, session_id, user["user_id"])
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # Cancel any in-flight barcode image download so it can't race against
    # directory cleanup below and leave orphan files behind.
    from src.web.routes.barcode import cancel_barcode_image_download
    await cancel_barcode_image_download(session_id)

    # Clean up uploaded images from disk + GCS
    session_img_dir = os.path.join(IMAGE_DIR, str(user["user_id"]), session_id)
    if os.path.isdir(session_img_dir) and os.path.realpath(session_img_dir).startswith(os.path.realpath(IMAGE_DIR)):
        shutil.rmtree(session_img_dir, ignore_errors=True)
    from src.gcs import gcs_delete_prefix
    await gcs_delete_prefix(f"{user['user_id']}/{session_id}/")

    await update_meal_session(db_path, session_id, user_id=user["user_id"], status="cancelled")

    # Private telemetry: funnel drop-off signal (analyzed but didn't accept)
    await log_event(
        db_path, user["user_id"], "meal_cancel",
        metadata={"session_id": session_id},
    )
    return {"ok": True}


@router.get("/recent-unique")
async def get_recent_unique(user: CurrentUser, db_path: DbPath):
    """Get the most recent unique meals for quick re-logging."""
    from src.db import get_recent_unique_meals
    meals = await get_recent_unique_meals(db_path, user["user_id"], limit=10)
    return meals


@router.get("/search", response_model=list[MealOut])
@limiter.limit("60/minute")
async def search_meals_route(
    request: Request,
    user: CurrentUser,
    db_path: DbPath,
    q: str = "",
    limit: int = 20,
):
    """Semantic-first meal search with substring fallback.

    The journal search bar calls this on every (debounced) keystroke;
    the rate limit (60/min) absorbs typing bursts but caps abuse. An
    empty ``q`` returns ``[]`` so the client can short-circuit.
    """
    from src.services import search_user_meals

    q = (q or "").strip()
    if not q:
        return []
    limit = max(1, min(50, limit))
    meals = await search_user_meals(db_path, user["user_id"], q, limit)
    return [MealOut(**m) for m in meals]


@router.post("/{meal_id}/edit-session", response_model=AnalyzeResponse)
@limiter.limit("20/minute")
async def create_edit_session(request: Request, meal_id: int, user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Create an analysis session from an existing meal for AI editing.

    B7: gated against the daily text_meal cap — this triggers a Gemini call
    via create_session_from_meal, same upstream cost shape as a typed analysis.
    """
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

    result = await create_session_from_meal(meal_id, user["user_id"], db_path)

    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])

    return AnalyzeResponse(
        session_id=result["session_id"],
        nutrition=_nutrition_to_out(result.get("nutrition")),
        questions=[],
        raw_text="",
        error=None,
    )


@router.get("", response_model=list[MealOut])
async def list_meals(
    user: CurrentUser,
    db_path: DbPath,
    date: str | None = None,
    type: str | None = None,
    limit: int = 20,
    offset: int = 0,
):
    """List meals with pagination and optional filters."""
    meals = await get_meals_paginated(
        db_path, user["user_id"], limit=min(limit, 100), offset=offset,
        date_filter=date, meal_type=type,
    )
    return [MealOut(**m) for m in meals]


@router.get("/{meal_id}", response_model=MealOut)
async def get_meal(meal_id: int, user: CurrentUser, db_path: DbPath):
    """Get a single meal by ID."""
    meal = await get_meal_by_id(db_path, user["user_id"], meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    return MealOut(**meal)


@router.put("/{meal_id}", response_model=MealOut)
@limiter.limit("30/minute")
async def update_meal(request: Request, meal_id: int, req: MealUpdateRequest, user: CurrentUser, db_path: DbPath):
    """Update a meal's macros/items."""
    meal = await get_meal_by_id(db_path, user["user_id"], meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")

    await db_update_meal(
        db_path=db_path,
        meal_id=meal_id,
        user_id=user["user_id"],
        item_name=req.item_name if req.item_name is not None else meal["item_name"],
        meal_description=req.meal_description if req.meal_description is not None else meal["meal_description"],
        calories=req.calories if req.calories is not None else meal["calories"],
        protein=req.protein if req.protein is not None else meal["protein"],
        carbs=req.carbs if req.carbs is not None else meal["carbs"],
        fat=req.fat if req.fat is not None else meal["fat"],
        items_json=req.items_json if req.items_json is not None else meal["items_json"],
        meal_type=req.meal_type if req.meal_type is not None else meal["meal_type"],
    )

    updated = await get_meal_by_id(db_path, user["user_id"], meal_id)
    return MealOut(**updated)


@router.delete("/{meal_id}")
@limiter.limit("30/minute")
async def delete_meal_route(request: Request, meal_id: int, user: CurrentUser, db_path: DbPath):
    """Delete a meal and its associated image files."""
    import shutil

    meal = await get_meal_by_id(db_path, user["user_id"], meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")

    # Clean up image files from disk + GCS
    # image_path is like "{user_id}/{session_id}/image_0.jpg" (relative to IMAGE_DIR)
    image_path = meal.get("image_path", "")
    if image_path:
        full_path = os.path.join(IMAGE_DIR, image_path)
        session_dir = os.path.dirname(full_path)
        if os.path.isdir(session_dir) and os.path.realpath(session_dir).startswith(os.path.realpath(IMAGE_DIR)):
            shutil.rmtree(session_dir, ignore_errors=True)
        # Delete session prefix from GCS (e.g. "1/abc-def/")
        session_prefix = os.path.dirname(image_path) + "/"
        from src.gcs import gcs_delete_prefix
        await gcs_delete_prefix(session_prefix)

    await delete_meal(db_path, meal_id, user_id=user["user_id"])

    # Private telemetry: post-log regret signal (meal accepted then removed)
    await log_event(
        db_path, user["user_id"], "meal_delete",
        metadata={"meal_id": meal_id, "had_image": bool(image_path)},
    )
    return {"ok": True, "deleted_id": meal_id}


class RelogRequest(BaseModel):
    # A25: client-provided local timestamp "YYYY-MM-DD HH:MM[:SS]" or "YYYY-MM-DD".
    logged_at: str | None = Field(
        default=None,
        max_length=30,
        pattern=r"^\d{4}-\d{2}-\d{2}( \d{2}:\d{2}(:\d{2})?)?$",
    )


@router.post("/{meal_id}/relog")
@limiter.limit("30/minute")
async def relog_meal(request: Request, meal_id: int, user: CurrentUser, db_path: DbPath, sub: SubInfo, req: RelogRequest | None = None):
    """Re-log an existing meal. Client sends local timestamp to avoid timezone mismatch."""
    from datetime import datetime
    meal = await get_meal_by_id(db_path, user["user_id"], meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")

    if req and req.logged_at:
        logged_at = req.logged_at
        # Extract hour for meal type classification
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

    new_id = await db_log_meal(
        db_path=db_path,
        user_id=user["user_id"],
        logged_at=logged_at,
        item_name=meal["item_name"],
        meal_description=meal.get("meal_description", ""),
        calories=meal["calories"],
        protein=meal["protein"],
        carbs=meal["carbs"],
        fat=meal["fat"],
        source="relog",
        meal_type=classify_meal_time(hour),
        items_json=meal.get("items_json", ""),
        image_path=meal.get("image_path", ""),
    )
    # Private telemetry: relog creates a new meal row
    await log_event(
        db_path, user["user_id"], "meal_relog",
        metadata={"source_meal_id": meal_id, "has_image": bool(meal.get("image_path"))},
    )
    # Evaluate badges on relog (creates a new meal)
    new_badges: list = []
    try:
        from src.badge_engine import evaluate_badges
        from src.services import user_today_str
        prefs = await get_user_prefs(db_path, user["user_id"])
        if prefs.get("gamification", "full") != "off":
            today = await user_today_str(db_path, user["user_id"])

            # Snapshot shield count before badge evaluation
            shields_before = (await get_streak_shields(db_path, user["user_id"]))["available"]

            new_badges = await evaluate_badges(db_path, user["user_id"], "meal_accept", {
                "today_str": today, "has_image": bool(meal.get("image_path")),
                "is_premium": sub.is_premium,
            })

            # Detect shield consumption and send push notification
            shields_after = (await get_streak_shields(db_path, user["user_id"]))["available"]
            if shields_after < shields_before:
                await log_event(
                    db_path, user["user_id"], "shield_consumed",
                    metadata={"available_after": shields_after, "via": "meal_relog"},
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
        logger.exception("Badge evaluation failed for meal relog")
    return {"ok": True, "meal_id": new_id, "new_badges": new_badges}


@router.post("/copy-day")
@limiter.limit("5/minute")
async def copy_day(request: Request, req: CopyDayRequest, user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Copy all meals from source_date to target_date (defaults to today)."""
    from datetime import datetime

    # Validate source_date format
    try:
        datetime.strptime(req.source_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source_date format (YYYY-MM-DD)")

    # Determine target date
    if req.target_date:
        try:
            datetime.strptime(req.target_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid target_date format (YYYY-MM-DD)")
        target_date = req.target_date
    else:
        target_date = await user_today_str(db_path, user["user_id"])

    if req.source_date == target_date:
        raise HTTPException(status_code=400, detail="Source and target dates must be different")

    # Fetch meals from source date
    source_meals = await get_meals_for_date(db_path, user["user_id"], req.source_date)
    if not source_meals:
        raise HTTPException(status_code=404, detail="No meals found on source date")

    # Get user prefs for meal type classification
    prefs = await get_user_prefs(db_path, user["user_id"])

    meal_ids = []
    for meal in source_meals:
        # Preserve the original time-of-day but change the date
        original_time = meal["logged_at"].split(" ")[1] if " " in meal["logged_at"] else "12:00"
        new_logged_at = f"{target_date} {original_time}"

        # Classify meal type from original time
        try:
            hour = int(original_time.split(":")[0])
        except Exception:
            hour = 12
        meal_type = meal.get("meal_type") or classify_meal_time(hour)

        new_id = await db_log_meal(
            db_path=db_path,
            user_id=user["user_id"],
            logged_at=new_logged_at,
            item_name=meal["item_name"],
            meal_description=meal.get("meal_description", ""),
            calories=meal["calories"],
            protein=meal["protein"],
            carbs=meal["carbs"],
            fat=meal["fat"],
            source="copy_day",
            meal_type=meal_type,
            items_json=meal.get("items_json", ""),
            image_path=meal.get("image_path", ""),
        )
        meal_ids.append(new_id)

    # Private telemetry: one row per copy_day action (count inside metadata)
    await log_event(
        db_path, user["user_id"], "meal_copy_day",
        metadata={"count": len(meal_ids), "source_date": req.source_date, "target_date": target_date},
    )

    # Evaluate badges once after all meals are copied
    new_badges: list = []
    try:
        if prefs.get("gamification", "full") != "off":
            from src.badge_engine import evaluate_badges
            today = await user_today_str(db_path, user["user_id"])

            shields_before = (await get_streak_shields(db_path, user["user_id"]))["available"]

            any_image = any(m.get("image_path") for m in source_meals)
            new_badges = await evaluate_badges(db_path, user["user_id"], "meal_accept", {
                "today_str": today, "has_image": any_image,
                "is_premium": sub.is_premium,
            })

            shields_after = (await get_streak_shields(db_path, user["user_id"]))["available"]
            if shields_after < shields_before:
                await log_event(
                    db_path, user["user_id"], "shield_consumed",
                    metadata={"available_after": shields_after, "via": "copy_day"},
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
        logger.exception("Badge evaluation failed for copy_day")

    return {
        "ok": True,
        "copied_count": len(meal_ids),
        "meal_ids": meal_ids,
        "new_badges": new_badges,
    }


class MealFeedbackRequest(BaseModel):
    rating: int  # 1 = accurate, 0 = inaccurate
    comment: str = ""


@router.post("/{meal_id}/feedback")
@limiter.limit("10/minute")
async def meal_feedback(request: Request, meal_id: int, req: MealFeedbackRequest, user: CurrentUser, db_path: DbPath):
    """Submit accuracy feedback for a logged meal."""
    if req.rating not in (0, 1):
        raise HTTPException(status_code=400, detail="Rating must be 0 or 1")
    if len(req.comment) > 500:
        raise HTTPException(status_code=400, detail="Comment too long (max 500 chars)")

    # Verify the meal belongs to this user
    meal = await get_meal_by_id(db_path, user["user_id"], meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")

    await insert_meal_feedback(
        db_path=db_path,
        user_id=user["user_id"],
        meal_id=meal_id,
        rating=req.rating,
        comment=req.comment,
    )
    return {"ok": True}
