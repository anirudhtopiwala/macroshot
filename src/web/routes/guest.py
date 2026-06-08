"""Guest-mode meal analysis: unauthenticated, IP-rate-limited, no DB writes.

The guest path lets a visitor try one meal analysis without signing up.
The result is returned to the client and stored in the browser's
IndexedDB; on signup, the client posts those entries to
`/meals/import-guest` (auth-required) to seed the new user's history.

Abuse model:
  * Per-IP slowapi cap (tight) - primary defence.
  * Global Gemini budget gate ($40) + Pub/Sub killswitch ($45) - backstop.
  * No DB rows are created here, so no signup-cap pressure and no
    storage to fill.
"""

from io import BytesIO
import logging

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from src.gemini import gemini_analyze_meal
from src.web.budget_gate import BudgetExceededError
from src.web.constants import BUDGET_EXCEEDED_MESSAGE
from src.web.deps import DbPath
from src.web.rate_limit import limiter
from src.web.schemas import FoodItemOut, NutritionOut

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/guest", tags=["guest"])


# Tighter than authed limits. With killswitch at $45 and ~$0.005 per
# image analysis, even 1000 distinct IPs hitting the daily cap can't
# meaningfully threaten budget - and the gate at $40 fires first.
_GUEST_ANALYZE_PER_DAY = "3/day"

# Far smaller than /meals/analyze. Guests are demoing; a single shot is
# the intended flow. Tight ceiling also caps storage cost on the
# re-encode worker semaphore in /meals/analyze (which we don't share).
MAX_GUEST_IMAGES = 1
MAX_GUEST_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_GUEST_TEXT_LEN = 2000


class GuestAnalyzeResponse(BaseModel):
    """Subset of /meals/analyze response - no session_id (no DB row).

    The frontend stores `nutrition` against a client-generated id in
    IndexedDB and replays it through /meals/import-guest on signup.
    """

    nutrition: NutritionOut | None = None
    raw_text: str = ""
    error: str | None = None


def _nutrition_to_out(n) -> NutritionOut | None:
    """Convert a NutritionResult to NutritionOut (subset of meals._nutrition_to_out)."""
    if n is None:
        return None
    items = [
        FoodItemOut(
            name=i.name,
            description=i.description,
            brand=i.brand,
            has_label=i.has_label,
            calories=i.calories,
            protein=i.protein,
            carbs=i.carbs,
            fat=i.fat,
            weight_g=i.weight_g,
            source=i.source,
        )
        for i in (n.items or [])
    ]
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


_IMAGE_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF", b"GIF8")


def _validate_image(data: bytes) -> None:
    """Reject anything that isn't a recognisable JPEG/PNG/WebP/GIF."""
    header = data[:12]
    if not any(header.startswith(sig) for sig in _IMAGE_SIGNATURES):
        raise HTTPException(status_code=400, detail="Invalid image format")
    if header[:4] == b"RIFF" and data[8:12] != b"WEBP":
        raise HTTPException(status_code=400, detail="Invalid image format")


@router.post("/analyze", response_model=GuestAnalyzeResponse)
@limiter.limit(_GUEST_ANALYZE_PER_DAY)
async def guest_analyze(
    request: Request,
    db_path: DbPath,
    text: str = Form(default="", max_length=MAX_GUEST_TEXT_LEN),
    images: list[UploadFile] = File(default=[]),
):
    """Run a one-shot Gemini analysis for a non-signed-in visitor.

    No DB writes. No session. No FatSecret reference lookup. The result
    is the client's responsibility to persist (IndexedDB), and replays
    through /meals/import-guest on signup.
    """
    if len(images) > MAX_GUEST_IMAGES:
        raise HTTPException(status_code=400, detail=f"Maximum {MAX_GUEST_IMAGES} image allowed for guest analysis")

    image_bytes: list[bytes] = []
    for img in images:
        buf = bytearray()
        while True:
            chunk = await img.read(64 * 1024)
            if not chunk:
                break
            buf.extend(chunk)
            if len(buf) > MAX_GUEST_IMAGE_SIZE:
                raise HTTPException(status_code=400, detail="Image too large (max 10 MB)")
        if buf:
            data = bytes(buf)
            _validate_image(data)
            # Strip EXIF before the bytes hit Gemini. Same hardening as
            # /meals/analyze but inline (we never persist these bytes,
            # so no shared semaphore needed).
            from PIL import Image as PILImage, ImageOps
            try:
                with PILImage.open(BytesIO(data)) as img_pil:
                    if getattr(img_pil, "is_animated", False):
                        raise HTTPException(status_code=415, detail="Animated images not supported")
                    img_pil = ImageOps.exif_transpose(img_pil)
                    if img_pil.mode != "RGB":
                        img_pil = img_pil.convert("RGB")
                    out = BytesIO()
                    img_pil.save(out, "JPEG", quality=85)
                    image_bytes.append(out.getvalue())
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=400, detail="Could not process image")

    if not image_bytes and not text.strip():
        raise HTTPException(status_code=400, detail="Provide at least one image or text description")

    try:
        # user_id=0 is reserved for guest/system calls so gemini_calls
        # accounting can distinguish them. db_path passed so the budget
        # gate applies - guests still respect the global $40 ceiling.
        response_text, result = await gemini_analyze_meal(
            images=image_bytes,
            user_text=text.strip(),
            db_path=db_path,
            user_id=0,
        )
    except BudgetExceededError:
        raise HTTPException(
            status_code=503,
            detail={"reason": "budget_exceeded", "message": BUDGET_EXCEEDED_MESSAGE},
        )

    if not result:
        snippet = (response_text or "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400].rstrip() + "…"
        return GuestAnalyzeResponse(
            nutrition=None,
            raw_text=response_text or "",
            error=(
                f"We couldn't extract macros from that. Here's what the AI saw:\n\n{snippet}"
                if snippet
                else "That one was tricky - our AI couldn't lock it in. Mind giving it another shot?"
            ),
        )

    return GuestAnalyzeResponse(
        nutrition=_nutrition_to_out(result),
        raw_text=response_text or "",
        error=None,
    )
