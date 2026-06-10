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
import re

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from src.barcode_lookup import lookup_barcode
from src.gemini import gemini_analyze_meal
from src.models import FoodItem, NutritionResult
from src.web.budget_gate import BudgetExceededError
from src.web.constants import BUDGET_EXCEEDED_MESSAGE
from src.web.deps import DbPath
from src.web.rate_limit import limiter
from src.web.schemas import FoodItemOut, NutritionOut

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/guest", tags=["guest"])

_BARCODE_RE = re.compile(r"^\d{8,14}$")


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


# ── Guest barcode lookup ────────────────────────────────────────────


class GuestBarcodeRequest(BaseModel):
    barcode: str
    servings: float = Field(default=1.0, gt=0, le=1000)


class GuestBarcodeResponse(BaseModel):
    """Subset of /meals/barcode AnalyzeResponse — no session_id (no DB row).

    Carries the per-serving product metadata the LogMeal barcode review
    UI renders (serving label / size, image, item name). The guest
    accept path mints a synthetic session id client-side so the
    existing UI can consume this verbatim.
    """

    nutrition: NutritionOut | None = None
    error: str | None = None
    image_url: str | None = None
    serving_label: str | None = None
    serving_size_g: float | None = None
    serving_size_unit: str = "g"


@router.post("/barcode", response_model=GuestBarcodeResponse)
@limiter.limit("20/minute")
async def guest_barcode(
    request: Request,
    req: GuestBarcodeRequest,
    db_path: DbPath,
):
    """Look up a barcode for a non-signed-in visitor.

    No DB writes, no per-user correction overlay, no telemetry row.
    Returns the per-serving nutrition (multiplied by ``servings``) so
    the existing barcode review UI can consume the response shape.
    OFF/FatSecret caches in ``barcode_cache`` ARE consulted (and
    populated) because they're product-keyed, not user-keyed — so
    guest scans accelerate future authed scans of the same product.
    """
    barcode = req.barcode.strip()
    if not _BARCODE_RE.match(barcode):
        raise HTTPException(status_code=400, detail="Invalid barcode format (must be 8-14 digits)")

    product = await lookup_barcode(barcode, db_path, user_id=None)
    if not product:
        return GuestBarcodeResponse(
            nutrition=None,
            error="Product not found. Try logging this meal with a photo or text description instead.",
        )

    servings = req.servings
    calories = round(product["calories"] * servings, 1)
    protein = round(product["protein"] * servings, 1)
    carbs = round(product["carbs"] * servings, 1)
    fat = round(product["fat"] * servings, 1)
    serving_g = product.get("serving_size_g")
    weight_g = round(serving_g * servings, 1) if serving_g else None

    product_name = product["product_name"]
    brand = product.get("brand", "") or ""
    serving_label = product.get("serving_label", "") or ""

    desc_parts = []
    if brand:
        desc_parts.append(brand)
    if serving_label:
        desc_parts.append(serving_label)
    if servings != 1.0:
        desc_parts.append(f"{servings}x servings")
    description = " | ".join(desc_parts) if desc_parts else ""

    item_name = f"{brand} {product_name}".strip() if brand else product_name

    food_item = FoodItem(
        name=product_name,
        description=description,
        brand=brand or None,
        has_label=False,
        calories=calories,
        protein=protein,
        carbs=carbs,
        fat=fat,
        weight_g=weight_g,
        source="barcode",
    )
    result = NutritionResult(
        item_name=item_name,
        meal_description=description,
        items=[food_item],
        calories=calories,
        protein=protein,
        carbs=carbs,
        fat=fat,
        source="barcode",
    )

    return GuestBarcodeResponse(
        nutrition=_nutrition_to_out(result),
        error=None,
        image_url=product.get("image_url"),
        serving_label=serving_label or None,
        serving_size_g=serving_g,
        serving_size_unit=product.get("serving_size_unit") or "g",
    )
