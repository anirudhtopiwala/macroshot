"""Settings API routes: targets, preferences, profile, export, delete."""

import asyncio
import csv
import io
import logging
import os
import shutil
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.responses import Response
from PIL import Image as PILImage

from src.db import (
    delete_all_user_data,
    export_user_meals,
    export_user_weights,
    get_user_prefs,
    get_user_profile,
    get_user_target,
    insert_feedback,
    log_event,
    set_user_prefs,
    set_user_profile,
    set_user_target,
    save_push_subscription,
    delete_push_subscription,
    get_push_subscriptions,
)
from src.web.deps import CurrentUser, DbPath, SubInfo
from src.web.rate_limit import limiter
from src.web.schemas import FeedbackRequest, PrefsRequest, PrefsResponse, ProfileRequest, ProfileResponse, PushSubscriptionRequest, PushUnsubscribeRequest, TargetsRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/settings", tags=["settings"])

# Hold references to fire-and-forget background tasks so they aren't GC'd
_bg_tasks: set[asyncio.Task] = set()


@router.get("/targets")
async def get_targets(user: CurrentUser, db_path: DbPath):
    """Get current macro targets."""
    target = await get_user_target(db_path, user["user_id"])
    if not target:
        return {"calories": 2000, "protein": 150, "carbs": 200, "fat": 70, "set_by": "default"}
    return target


@router.put("/targets")
@limiter.limit("30/minute")
async def update_targets(request: Request, req: TargetsRequest, user: CurrentUser, db_path: DbPath):
    """Set daily macro targets."""
    await set_user_target(
        db_path, user["user_id"],
        calories=req.calories, protein=req.protein,
        carbs=req.carbs, fat=req.fat,
        set_by="manual",
    )
    # Evaluate target_set badges (skip when saving defaults during onboarding skip).
    # Only the *first* real target set triggers evaluation - Goal Setter is a
    # milestone, so re-saving targets in Settings → Goals shouldn't burn a
    # second badge_engine pass (badge_earned's UNIQUE constraint already blocks
    # a duplicate row, but the work is wasted).
    new_badges = []
    if not req.skip:
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
    return {"message": "Targets updated", "new_badges": new_badges, **req.model_dump()}


@router.get("/prefs", response_model=PrefsResponse)
async def get_prefs(user: CurrentUser, db_path: DbPath):
    """Get user preferences."""
    return await get_user_prefs(db_path, user["user_id"])


@router.put("/prefs")
@limiter.limit("30/minute")
async def update_prefs(request: Request, req: PrefsRequest, user: CurrentUser, db_path: DbPath):
    """Update user preferences."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    # Detect gamification toggle before persisting - we want the OLD value
    # so the telemetry row captures the transition, not the final state.
    old_gamification = None
    if "gamification" in updates:
        prev = await get_user_prefs(db_path, user["user_id"])
        old_gamification = prev.get("gamification", "full")

    if updates:
        await set_user_prefs(db_path, user["user_id"], **updates)

    # Private telemetry: gamification on/off is a strong engagement signal.
    # Only log when the value actually changed, not on every prefs PUT.
    if old_gamification is not None and old_gamification != updates.get("gamification"):
        await log_event(
            db_path, user["user_id"], "gamification_toggled",
            metadata={
                "from": old_gamification,
                "to": updates.get("gamification"),
            },
        )

    return await get_user_prefs(db_path, user["user_id"])


@router.get("/profile", response_model=ProfileResponse)
async def get_profile_route(user: CurrentUser, db_path: DbPath):
    """Get user profile (age, weight, height, sex)."""
    return await get_user_profile(db_path, user["user_id"])


@router.put("/profile")
@limiter.limit("30/minute")
async def update_profile(request: Request, req: ProfileRequest, user: CurrentUser, db_path: DbPath):
    """Update user profile."""
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if updates:
        await set_user_profile(db_path, user["user_id"], **updates)
    return await get_user_profile(db_path, user["user_id"])


# Magic-byte signatures used to validate image uploads (mirrors meals.py).
# Client-supplied content_type is trivially spoofable; this enforces real
# image-format identification at the byte level.
_AVATAR_IMAGE_SIGNATURES = (
    b'\xff\xd8\xff',  # JPEG
    b'\x89PNG',       # PNG
    b'RIFF',          # RIFF (need WEBP secondary check)
    b'GIF8',          # GIF
)


@router.post("/avatar")
@limiter.limit("10/hour")
async def upload_avatar(request: Request, file: UploadFile, user: CurrentUser, db_path: DbPath):
    """Upload a profile picture. Resizes to 256x256 JPEG."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    # B35: stream the upload in 64 KB chunks so a chunked-encoding
    # request (no Content-Length, bypasses the global body-size middleware)
    # cannot push 100s of MB through `await file.read()` before we cap it.
    # Mirrors the meals.py streaming pattern.
    AVATAR_MAX_SIZE = 10 * 1024 * 1024  # 10 MB
    CHUNK = 64 * 1024
    buf = bytearray()
    while True:
        chunk = await file.read(CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > AVATAR_MAX_SIZE:
            raise HTTPException(status_code=400, detail="Image too large (max 10 MB)")
    data = bytes(buf)

    # Magic-byte validation - content_type is client-controlled and spoofable.
    header = data[:12]
    if not any(header.startswith(sig) for sig in _AVATAR_IMAGE_SIGNATURES):
        raise HTTPException(status_code=400, detail="Invalid image format (JPEG, PNG, WebP, GIF only)")
    if header[:4] == b'RIFF' and data[8:12] != b'WEBP':
        raise HTTPException(status_code=400, detail="Invalid image format (JPEG, PNG, WebP, GIF only)")

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    avatar_dir = os.path.join(project_root, "data", "images", str(user["user_id"]))
    os.makedirs(avatar_dir, exist_ok=True)

    avatar_path = os.path.join(avatar_dir, "avatar.jpg")
    try:
        from PIL import ImageOps
        try:
            from PIL.Image import DecompressionBombError
        except ImportError:
            DecompressionBombError = Exception  # type: ignore[assignment,misc]

        # B35: gate decode + resize behind the meals re-encode semaphore so
        # a flood of avatar uploads can't blow the VM's RSS ceiling on top
        # of concurrent meal-image re-encodes.
        from src.web.routes.meals import _REENCODE_SEM

        def _process() -> None:
            img = PILImage.open(io.BytesIO(data))
            # B35: reject animated avatars — re-encoding silently drops
            # frames (matches meals.py policy).
            if getattr(img, "is_animated", False):
                raise HTTPException(status_code=415, detail="Animated images not supported")
            # Apply EXIF orientation (phone photos are often rotated)
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            # Center-crop to square, then resize
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            img = img.crop((left, top, left + side, top + side))
            img = img.resize((256, 256), PILImage.LANCZOS)
            img.save(avatar_path, "JPEG", quality=85)

        async with _REENCODE_SEM:
            await asyncio.to_thread(_process)
    except HTTPException:
        raise
    except DecompressionBombError:
        raise HTTPException(status_code=400, detail="Image dimensions too large")
    except Exception:
        raise HTTPException(status_code=400, detail="Could not process image")

    # Upload avatar to GCS (non-blocking, task reference held to prevent GC)
    from src.gcs import gcs_upload_file
    relative_path = f"{user['user_id']}/avatar.jpg"
    task = asyncio.create_task(gcs_upload_file(relative_path, avatar_path))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)

    # Store path with cache-busting timestamp so browsers fetch the new image
    import time
    cache_bust = int(time.time())
    url = f"/macro_app/api/v1/images/{relative_path}?v={cache_bust}"
    await set_user_profile(db_path, user["user_id"], avatar_url=url)
    return {"avatar_url": url}


@router.delete("/avatar")
@limiter.limit("10/hour")
async def delete_avatar(request: Request, user: CurrentUser, db_path: DbPath):
    """Remove the user's profile picture; frontend falls back to initials."""
    from src.db_pool import get_db
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE users SET avatar_url = NULL WHERE user_id = ?",
            (user["user_id"],),
        )
        await db.commit()
    return {"avatar_url": None}


# ── Push Notifications ──────────────────────────────────────────────

@router.post("/push/subscribe")
@limiter.limit("30/minute")
async def push_subscribe(request: Request, req: PushSubscriptionRequest, user: CurrentUser, db_path: DbPath):
    await save_push_subscription(
        db_path, user["user_id"],
        endpoint=req.endpoint,
        p256dh=req.keys.p256dh,
        auth=req.keys.auth,
    )
    return {"message": "Subscribed"}


@router.post("/push/unsubscribe")
@limiter.limit("30/minute")
async def push_unsubscribe(request: Request, req: PushUnsubscribeRequest, user: CurrentUser, db_path: DbPath):
    await delete_push_subscription(db_path, user["user_id"], endpoint=req.endpoint)
    return {"message": "Unsubscribed"}


@router.get("/push/status")
async def push_status(user: CurrentUser, db_path: DbPath):
    subs = await get_push_subscriptions(db_path, user["user_id"])
    return {"subscribed": len(subs) > 0, "count": len(subs)}


# ── Feedback ──────────────────────────────────────────────────────


@router.post("/feedback")
@limiter.limit("5/hour")
async def submit_feedback(req: FeedbackRequest, request: Request, user: CurrentUser, db_path: DbPath):
    """Submit user feedback (bug report, feature request, or general)."""
    await insert_feedback(
        db_path,
        user_id=user["user_id"],
        feedback_type=req.type,
        message=req.message,
        page=req.page or "",
    )
    return {"ok": True}


# ── Export & Delete ───────────────────────────────────────────────


@router.get("/export")
@limiter.limit("5/hour")
async def export_data(request: Request, user: CurrentUser, db_path: DbPath, sub: SubInfo, format: str = "csv"):
    """Export user data. CSV is free; PDF requires Pro."""
    if format == "pdf":
        if not sub.is_premium:
            raise HTTPException(status_code=403, detail="PDF export requires a Pro subscription")
        return await _export_pdf(user, db_path)

    return await _export_csv(user, db_path)


async def _export_csv(user: dict, db_path: str) -> StreamingResponse:
    """Export all user data as CSV (meals + weight in one file). Free for all users."""
    meals = await export_user_meals(db_path, user["user_id"])
    weights = await export_user_weights(db_path, user["user_id"])

    buf = io.StringIO()
    w = csv.writer(buf)

    # Meals section
    w.writerow(["--- MEALS ---"])
    w.writerow(["date", "item_name", "meal_description", "calories", "protein", "carbs", "fat", "meal_type", "source"])
    for m in meals:
        w.writerow([m["logged_at"], m["item_name"], m["meal_description"], m["calories"], m["protein"], m["carbs"], m["fat"], m["meal_type"], m["source"]])

    w.writerow([])

    # Weight section
    w.writerow(["--- WEIGHT ---"])
    w.writerow(["date", "weight_kg"])
    for e in weights:
        w.writerow([e["logged_at"], e["weight_kg"]])

    buf.seek(0)
    filename = f"macroshot_export_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        buf,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


async def _export_pdf(user: dict, db_path: str) -> StreamingResponse:
    """Export a nutrition report as PDF. Pro only."""
    try:
        from src.pdf_report import generate_report
        pdf_bytes = await generate_report(user, db_path, num_days=30)
    except Exception:
        logging.getLogger("macro_app").exception("PDF generation failed for user %s", user["user_id"])
        raise HTTPException(status_code=500, detail="PDF generation failed. Please try again.")

    filename = f"macroshot_report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.delete("/account")
@limiter.limit("3/hour")
async def delete_account(request: Request, user: CurrentUser, db_path: DbPath, response: Response):
    """Permanently delete user account and all associated data."""
    # Delete user's photos from disk + GCS before removing DB records
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    user_image_dir = os.path.join(project_root, "data", "images", str(user["user_id"]))
    photos_deleted = 0
    if os.path.isdir(user_image_dir):
        # Count files before wiping so we can show the user what was erased.
        # Nested session directories each hold 0-5 image files.
        for _root, _dirs, files in os.walk(user_image_dir):
            photos_deleted += len(files)
        shutil.rmtree(user_image_dir)
    from src.gcs import gcs_delete_prefix
    await gcs_delete_prefix(f"{user['user_id']}/")
    table_counts = await delete_all_user_data(db_path, user["user_id"])
    response.delete_cookie("__Host-macro_session", path="/")
    response.delete_cookie("macro_session", path="/macro_app")
    # Surface the counts users recognize - the rest are internal.
    summary = {
        "meals": table_counts.get("meal_logs", 0),
        "weights": table_counts.get("weight_logs", 0),
        "chats": table_counts.get("chat_sessions", 0),
        "saved_meals": table_counts.get("meal_aliases", 0),
        "workouts": table_counts.get("workout_logs", 0),
        "photos": photos_deleted,
    }
    return {
        "message": "Account deleted",
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
    }
