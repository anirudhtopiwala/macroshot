"""Barcode scanning route: look up nutrition by barcode and create a meal session."""

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import datetime
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.barcode_lookup import lookup_barcode
from src.db import (
    create_meal_session,
    delete_barcode_correction,
    get_barcode_correction,
    get_user_prefs,
    log_event,
)
from src.models import FoodItem, NutritionResult
from src.services import analyze_meal, classify_meal_time, get_user_tz
from src.web.deps import CurrentUser, DbPath, SubInfo, UNLIMITED
from src.web.rate_limit import limiter
from src.web.schemas import AnalyzeResponse, FoodItemOut, NutritionOut
from src.web.constants import limit_message
from src.db import increment_usage
from src.services import user_today_str

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/meals", tags=["meals"])

_BARCODE_RE = re.compile(r"^\d{8,14}$")

# Project-root/data/images - matches src/web/routes/meals.py IMAGE_DIR
_IMAGE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "data",
    "images",
)


# SECURITY: Open Food Facts is community-editable - anyone can publish a
# product and set its image_front_url to whatever they want.  We treat
# every image_url as untrusted user input and validate before fetching to
# prevent SSRF (e.g. http://169.254.169.254/, http://localhost:6379/).
_BARCODE_IMAGE_HOST_ALLOWLIST = {
    # Open Food Facts CDN hosts (the only legitimate sources)
    "images.openfoodfacts.org",
    "static.openfoodfacts.org",
    "world.openfoodfacts.org",
    "us.openfoodfacts.org",
    "openfoodfacts-images.s3.amazonaws.com",
}


def _is_safe_barcode_image_url(url: str) -> bool:
    """Return True if the URL is a safe-to-fetch barcode product image.

    Required: HTTPS scheme, hostname in the OFF allowlist, hostname
    resolves to a public (non-private, non-loopback, non-link-local) IP.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host not in _BARCODE_IMAGE_HOST_ALLOWLIST:
        return False

    # Resolve and verify every A/AAAA record points to a public IP.
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if _is_disallowed_ip(ip):
            return False
    return True

# Pending barcode image downloads keyed by (user_id, session_id). accept_session
# awaits these before scanning the session directory so the file is on disk in time.
# B18: bounded to a small ceiling so a flood of barcode scans can't grow
# this dict without bound. New downloads beyond the cap are best-effort
# refused (the session still works, the image just isn't preloaded).
_PENDING_IMAGE_DOWNLOADS_CAP = 200
_pending_image_downloads: dict[tuple[int, str], asyncio.Task] = {}


async def _download_and_save_barcode_image(
    image_url: str,
    user_id: int,
    session_id: str,
) -> None:
    """Download a barcode product image and save it + thumb to the session dir.

    Best-effort: any failure is logged and swallowed. Same layout as uploaded
    meal photos (image_0.jpg / image_0_thumb.jpg) so accept_session picks it up
    via its existing session-dir scan.

    SECURITY: image_url comes from Open Food Facts (community-editable).
    Validate strictly before fetching to prevent SSRF - HTTPS only,
    hostname must be in allowlist, must resolve to public IP, no
    redirects (we re-validate after each redirect would be safer; we
    just disable them here).
    """
    if not _is_safe_barcode_image_url(image_url):
        logger.info("Barcode image_url rejected by SSRF guard: %s", image_url[:200])
        return

    try:
        # Stream + cap at 10 MB so a malicious / misconfigured CDN can't
        # OOM the worker by sending an arbitrarily-large body (the post-hoc
        # len(resp.content) check only fires AFTER the full body is in RAM).
        max_image_bytes = 10 * 1024 * 1024
        img_buf = bytearray()
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            async with client.stream(
                "GET",
                image_url,
                headers={"User-Agent": "MacroApp/1.0"},
            ) as resp:
                resp.raise_for_status()
                try:
                    cl = int(resp.headers.get("content-length", "0"))
                except ValueError:
                    cl = 0
                if cl and cl > max_image_bytes:
                    logger.info("Barcode image too large (advertised %d bytes)", cl)
                    return
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    img_buf.extend(chunk)
                    if len(img_buf) > max_image_bytes:
                        logger.info("Barcode image too large: aborted at %d bytes", len(img_buf))
                        return
        img_data = bytes(img_buf)
        if not img_data or len(img_data) < 256:
            logger.debug("Barcode image too small/empty: %s", image_url)
            return

        session_dir = os.path.join(_IMAGE_DIR, str(user_id), session_id)
        os.makedirs(session_dir, exist_ok=True)

        def _save():
            from PIL import Image as PILImage, ImageOps
            img = PILImage.open(BytesIO(img_data))
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            # Downscale to 1280 px max dimension to match the camera-photo
            # pipeline (web/src/components/ImageCapture.tsx). OFF full-res
            # images are often 1500-2500 px - we don't need more than 1280.
            img.thumbnail((1280, 1280))
            path = os.path.join(session_dir, "image_0.jpg")
            img.save(path, "JPEG", quality=85)
            # Square center-cropped thumbnail for MealCard list views.
            # OFF product images are typically tall (bottles, boxes);
            # img.thumbnail() alone would preserve aspect ratio and
            # produce a tall rectangle - looks wrong in the 80×80 list
            # slot.  Center-crop to the shortest edge first, then resize.
            w, h = img.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            sq = img.crop((left, top, left + side, top + side))
            sq = sq.resize((192, 192), PILImage.LANCZOS)
            thumb_path = os.path.join(session_dir, "image_0_thumb.jpg")
            sq.save(thumb_path, "JPEG", quality=70)

        await asyncio.to_thread(_save)

        # Fire-and-forget GCS upload so the image survives VM disk loss.
        # Upload the downsized local files (NOT the raw img_data bytes) so the
        # GCS copy matches what the app serves from disk.
        # B19: share the meals._GCS_UPLOAD_SEM cap so the barcode and analyze
        # paths together can't fan out beyond 8 in-flight uploads.
        try:
            from src.gcs import gcs_upload_file
            from src.web.routes.meals import _GCS_UPLOAD_SEM
            uid = str(user_id)
            main_local = os.path.join(session_dir, "image_0.jpg")
            thumb_local = os.path.join(session_dir, "image_0_thumb.jpg")
            if os.path.isfile(main_local):
                async with _GCS_UPLOAD_SEM:
                    await gcs_upload_file(f"{uid}/{session_id}/image_0.jpg", main_local)
            if os.path.isfile(thumb_local):
                async with _GCS_UPLOAD_SEM:
                    await gcs_upload_file(
                        f"{uid}/{session_id}/image_0_thumb.jpg", thumb_local
                    )
        except Exception:
            logger.debug("Barcode image GCS upload failed", exc_info=True)
    except Exception:
        logger.debug(
            "Barcode image download failed for session %s: %s",
            session_id, image_url, exc_info=True,
        )


def _find_pending_for_session(session_id: str) -> tuple[tuple[int, str], asyncio.Task] | None:
    """Locate any pending download task whose session_id matches (any user)."""
    for key, task in _pending_image_downloads.items():
        if key[1] == session_id:
            return key, task
    return None


async def await_barcode_image_download(session_id: str, timeout: float = 3.0) -> None:
    """Await any pending barcode image download for this session (best-effort).

    Called from accept_session before scanning the session directory so that
    fast accepters still get the downloaded image persisted on their meal row.
    Silently returns on timeout or error - the accept flow should continue.
    """
    found = _find_pending_for_session(session_id)
    if not found:
        return
    _, task = found
    if task.done():
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except (asyncio.TimeoutError, Exception):
        pass


async def cancel_barcode_image_download(session_id: str) -> None:
    """Cancel and await any pending barcode image download.

    Called from cancel_session so the download can't race against dir cleanup
    and leave orphan files behind after shutil.rmtree.
    """
    found = _find_pending_for_session(session_id)
    if not found:
        return
    key, task = found
    _pending_image_downloads.pop(key, None)
    if task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


class BarcodeRequest(BaseModel):
    barcode: str
    servings: float = 1.0
    meal_type: str = ""


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
    # NutritionResult dataclass
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


@router.post("/barcode", response_model=AnalyzeResponse)
@limiter.limit("20/minute")
async def barcode_scan(request: Request, req: BarcodeRequest, user: CurrentUser, db_path: DbPath):
    """Look up a product by barcode and create a meal session for review."""
    # 1. Validate barcode format (8-14 digits)
    barcode = req.barcode.strip()
    if not _BARCODE_RE.match(barcode):
        raise HTTPException(status_code=400, detail="Invalid barcode format (must be 8-14 digits)")

    if req.servings <= 0:
        raise HTTPException(status_code=400, detail="Servings must be positive")

    # 2a. Check if the user has a saved correction for this barcode. If so,
    # use it as the source of truth - skip OFF/FatSecret lookup for macros
    # but still hit lookup_barcode for the product image/cache metadata.
    correction = await get_barcode_correction(db_path, user["user_id"], barcode)

    # 2b. Look up barcode (always - we want the image even when we have a
    # correction, and we want to know if the product is still recognized).
    product = await lookup_barcode(barcode, db_path, user_id=user["user_id"])

    # Private telemetry: one row per scan (hit or miss) so admin metrics can
    # surface both habit use and not-found rates. We HASH the barcode so the
    # telemetry table doesn't carry raw product identifiers (GDPR-friendly,
    # and keeps the events table useful for aggregate analysis only).
    import hashlib as _hashlib
    barcode_hash = _hashlib.sha256(barcode.encode()).hexdigest()[:12]
    await log_event(
        db_path, user["user_id"], "meal_barcode_scan",
        metadata={
            "barcode_hash": barcode_hash,
            "found": bool(product),
            "correction": bool(correction),
        },
    )

    # If neither the correction nor the API lookup gave us anything, we're
    # out of options. (A correction alone with no product row is still fine -
    # the user's saved values become the source.)
    if not product and not correction:
        return AnalyzeResponse(
            session_id="",
            nutrition=None,
            questions=[],
            raw_text="",
            error="Product not found. Try logging this meal with a photo or text description instead.",
        )

    # Snapshot the raw OFF/FatSecret per-serving values BEFORE any correction
    # overlay. This becomes original_nutrition for the session: accept_meal
    # compares the final accepted macros against these raw values so a
    # second-pass correction (user re-corrects a previously-corrected product)
    # is still detected and saved. Without this, the threshold would compare
    # against the already-saved correction and small iterative nudges would
    # silently fail to persist.
    raw_per_serving: dict | None = None
    if product:
        raw_per_serving = {
            "calories": float(product.get("calories", 0) or 0),
            "protein": float(product.get("protein", 0) or 0),
            "carbs": float(product.get("carbs", 0) or 0),
            "fat": float(product.get("fat", 0) or 0),
            "serving_size_unit": product.get("serving_size_unit") or "g",
        }

    # If we have a correction, overlay it on the product dict so the rest of
    # the endpoint treats it as the source of truth. Keep the image_url and
    # cache metadata from the OFF/FS response if available.
    if correction:
        base: dict = dict(product) if product else {}
        base["product_name"] = correction["product_name"]
        base["brand"] = correction.get("brand") or base.get("brand", "")
        base["calories"] = correction["calories"]
        base["protein"] = correction["protein"]
        base["carbs"] = correction["carbs"]
        base["fat"] = correction["fat"]
        # Overlay serving metadata atomically: if the correction has a
        # serving_size_g, also use its serving_label (never mix the
        # correction's macros with OFF's serving descriptor - the label
        # would misrepresent what the numbers refer to).
        corr_serving_g = correction.get("serving_size_g")
        if corr_serving_g is not None:
            base["serving_size_g"] = corr_serving_g
            base["serving_size_unit"] = correction.get("serving_size_unit") or "g"
            base["serving_label"] = correction.get("serving_label") or ""
        # Otherwise leave whatever came from OFF alone - the macros are
        # per-serving regardless of which source defined the serving.
        product = base

    # 3. Barcode scans are free for all users (zero AI cost, habit-forming)

    # 4. Compute macros (multiply by servings)
    servings = req.servings
    calories = round(product["calories"] * servings, 1)
    protein = round(product["protein"] * servings, 1)
    carbs = round(product["carbs"] * servings, 1)
    fat = round(product["fat"] * servings, 1)
    serving_g = product.get("serving_size_g")
    weight_g = round(serving_g * servings, 1) if serving_g else None

    product_name = product["product_name"]
    brand = product.get("brand", "") or ""

    # Build description
    desc_parts = []
    if brand:
        desc_parts.append(brand)
    serving_label = product.get("serving_label", "")
    if serving_label:
        desc_parts.append(serving_label)
    if servings != 1.0:
        desc_parts.append(f"{servings}x servings")
    description = " | ".join(desc_parts) if desc_parts else ""

    # Build item name
    item_name = f"{brand} {product_name}".strip() if brand else product_name

    # 5. Create NutritionResult
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

    # 6. Determine meal type
    meal_type = req.meal_type
    if not meal_type:
        prefs = await get_user_prefs(db_path, user["user_id"])
        tz = get_user_tz(prefs.get("timezone"))
        hour = datetime.now(tz).hour
        meal_type = classify_meal_time(hour)

    # 7. Create meal session (same pattern as analyze endpoint)
    session_id = str(uuid.uuid4())
    nutrition_json = json.dumps(result.model_dump())

    # Stash the raw (pre-overlay) OFF/FatSecret per-serving values as
    # original_nutrition. accept_meal compares the final accepted macros
    # against these raw values so iterative re-corrections against a
    # previously-saved correction still get persisted. In the rare case
    # where we only have a correction and no fresh OFF data (product is
    # None), fall back to the correction's own values - this means a
    # re-save against a pure correction-only flow would only trigger on
    # edits exceeding the threshold, which is acceptable.
    if raw_per_serving is None:
        raw_per_serving = {
            "calories": float(product["calories"]),
            "protein": float(product["protein"]),
            "carbs": float(product["carbs"]),
            "fat": float(product["fat"]),
            "serving_size_unit": product.get("serving_size_unit") or "g",
        }
    original_nutrition_json = json.dumps(raw_per_serving)

    # Build a minimal conversation for the session
    conversation = [
        {"role": "user", "text": f"Barcode scan: {barcode}"},
        {"role": "model", "text": nutrition_json},
    ]

    await create_meal_session(
        db_path=db_path,
        session_id=session_id,
        user_id=user["user_id"],
        images_json="[]",
        conversation=json.dumps(conversation),
        nutrition=nutrition_json,
        meal_type=meal_type,
        original_nutrition=original_nutrition_json,
        barcode=barcode,
    )

    logger.info(
        "Barcode %s -> %s (%s) for user %s | %.0f kcal",
        barcode, product_name, brand, user["user_id"], calories,
    )

    # 8. Return AnalyzeResponse format
    image_url = product.get("image_url") or None

    # Kick off a background download of the product image into the session
    # directory so that when the user accepts the meal, the existing
    # session-dir scan picks it up and persists it as meal_logs.image_path.
    # This is what makes the thumbnail show up in Journal / MealDetail / edit.
    if image_url:
        # B18: enforce dict cap. If full, skip the preload - the meal session
        # still works, the user just doesn't get the OFF image preloaded.
        if len(_pending_image_downloads) >= _PENDING_IMAGE_DOWNLOADS_CAP:
            logger.info("barcode preload skipped: pending dict full (%d)", len(_pending_image_downloads))
        else:
            uid = user["user_id"]
            key = (uid, session_id)
            task = asyncio.create_task(
                _download_and_save_barcode_image(image_url, uid, session_id)
            )
            _pending_image_downloads[key] = task

            def _cleanup(_t: asyncio.Task, k: tuple[int, str] = key) -> None:
                _pending_image_downloads.pop(k, None)

            task.add_done_callback(_cleanup)

    return AnalyzeResponse(
        session_id=session_id,
        nutrition=_nutrition_to_out(result),
        questions=[],
        raw_text="",
        error=None,
        image_url=image_url,
        serving_label=serving_label or None,
        serving_size_g=serving_g,
        serving_size_unit=product.get("serving_size_unit") or "g",
        cached=bool(product.get("cached", False)),
        cached_days_ago=product.get("cached_days_ago"),
        correction_applied=bool(correction),
        corrected_at=correction.get("corrected_at") if correction else None,
    )


class _HTMLTextExtractor(HTMLParser):
    """Strip tags/scripts/styles and collect visible text, title, meta desc."""

    _SKIP = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self.title = ""
        self.meta_desc = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            name = (attr_map.get("name") or attr_map.get("property") or "").lower()
            if name in {"description", "og:description", "twitter:description"} and not self.meta_desc:
                self.meta_desc = attr_map.get("content", "")[:500]

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title += data
            return
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self, limit: int) -> str:
        joined = " ".join(self._parts)
        return re.sub(r"\s+", " ", joined).strip()[:limit]


def _classify_qr_payload(payload: str) -> tuple[str, str | None]:
    """Return ('barcode'|'url'|'unsupported', normalized_value_or_reason).

    'barcode' → digits 8–14, value is the barcode string.
    'url' → http/https URL on a public host, value is the URL.
    'unsupported' → value is a short reason (shown to user).
    """
    p = payload.strip()
    if not p:
        return "unsupported", "Empty QR code."
    if _BARCODE_RE.match(p):
        return "barcode", p
    low = p.lower()
    if low.startswith(("upi://", "tel:", "mailto:", "sms:", "geo:", "wifi:", "bitcoin:")):
        return "unsupported", "That QR is a payment/contact code, not a food link."
    if low.startswith(("http://", "https://")):
        return "url", p
    # Some packaging QRs embed bare domains without scheme.
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(/|$)", low):
        return "url", "https://" + p
    return "unsupported", "QR doesn't contain a product barcode or product URL."


def _is_disallowed_ip(ip) -> bool:
    """Return True if the resolved IP is unsafe (private, loopback, metadata, CGNAT, IPv6 ULA/link-local).

    Covers IPv4 and IPv6:
      • Loopback (127/8, ::1)
      • Private (RFC1918, fc00::/7 ULA)
      • Link-local (169.254/16 incl. AWS/GCE metadata 169.254.169.254, fe80::/10)
      • Multicast / reserved / unspecified
      • CGNAT (100.64.0.0/10)
    """
    import ipaddress
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
        return True
    # CGNAT 100.64.0.0/10 - typical NAT operators / mobile carriers; not strictly
    # private but should not be reachable from a public-internet fetch.
    cgnat_v4 = ipaddress.ip_network("100.64.0.0/10")
    if isinstance(ip, ipaddress.IPv4Address) and ip in cgnat_v4:
        return True
    # IPv6 ULA fc00::/7 (more conservative than is_private which already covers it,
    # but explicit for review.)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip in ipaddress.ip_network("fc00::/7"):
            return True
        if ip in ipaddress.ip_network("fe80::/10"):
            return True
    return False


def _is_safe_public_url(url: str, *, https_only: bool = True) -> bool:
    """SSRF guard for arbitrary QR-payload URLs.

    https_only: when True (default), reject http://. QR-URL fetching is
    https-only post-B9 since cleartext payloads are routinely used for
    SSRF / metadata-service exfiltration on cloud hosts.
    """
    import ipaddress
    import socket

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if https_only:
        if parsed.scheme != "https":
            return False
    elif parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host or host in ("localhost",):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if _is_disallowed_ip(ip):
            return False
    return True


async def _fetch_page_text(url: str, max_bytes: int = 256 * 1024) -> tuple[str, str, str] | None:
    """Fetch a page and return (title, meta_desc, body_text) or None on failure.

    SSRF-guarded, https-only, redirect chain capped at 1 hop (B30) with a
    cumulative byte cap of `max_bytes` across all hops (so a 200KB initial
    response + 200KB redirect bait can't blow past the cap).
    """
    seen_hosts: set[str] = set()
    current = url
    cumulative_bytes = 0
    # B30: drop redirect hop count from 3 to 1.
    for _ in range(2):  # one initial fetch + at most one redirect
        if not _is_safe_public_url(current, https_only=True):
            return None
        host = (urlparse(current).hostname or "").lower()
        seen_hosts.add(host)
        # Stream the response so we can bail mid-download if a malicious
        # server tries to push gigabytes through us. Without streaming a
        # passing-the-allowlist host could OOM the worker before the
        # post-hoc len(resp.content) check ever ran.
        try:
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
                async with client.stream(
                    "GET",
                    current,
                    headers={
                        "User-Agent": "MacroApp/1.0 (+qr-lookup)",
                        "Accept": "text/html,application/xhtml+xml",
                    },
                ) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        loc = resp.headers.get("location", "")
                        if not loc:
                            return None
                        from urllib.parse import urljoin
                        current = urljoin(current, loc)
                        next_host = (urlparse(current).hostname or "").lower()
                        if next_host in seen_hosts:
                            return None
                        # Skip body; loop will revalidate and re-fetch.
                        # Exit the stream context cleanly.
                        await resp.aclose()
                        # Continue the outer for-loop with the new URL.
                        # Use a sentinel to break inner with and continue outer.
                        # (Python doesn't have labeled continue; use a flag.)
                        _redirect = True
                    else:
                        _redirect = False
                    if _redirect:
                        continue
                    if resp.status_code >= 400:
                        return None
                    ctype = resp.headers.get("content-type", "").lower()
                    if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                        return None
                    # Up-front Content-Length sanity check: refuse anything
                    # the server claims is larger than 4× our cap (covers
                    # legit gzip/transfer-encoding wiggle).
                    try:
                        cl = int(resp.headers.get("content-length", "0"))
                    except ValueError:
                        cl = 0
                    if cl and cl > max_bytes * 4:
                        return None
                    remaining = max(0, max_bytes - cumulative_bytes)
                    if remaining <= 0:
                        return None
                    body = bytearray()
                    async for chunk in resp.aiter_bytes(chunk_size=16 * 1024):
                        body.extend(chunk)
                        cumulative_bytes += len(chunk)
                        if cumulative_bytes > max_bytes:
                            # Truncate to the cap, stop pulling more.
                            del body[remaining:]
                            break
                    encoding = resp.encoding or "utf-8"
        except Exception:
            return None
        try:
            text = bytes(body).decode(encoding, errors="replace")
        except Exception:
            text = bytes(body).decode("utf-8", errors="replace")
        parser = _HTMLTextExtractor()
        try:
            parser.feed(text)
        except Exception:
            pass
        return (
            parser.title.strip()[:300],
            parser.meta_desc.strip()[:500],
            parser.text(limit=4000),
        )
    return None


class QrRequest(BaseModel):
    payload: str
    meal_type: str = ""


@router.post("/qr", response_model=AnalyzeResponse)
@limiter.limit("10/minute")
async def qr_scan(request: Request, req: QrRequest, user: CurrentUser, db_path: DbPath, sub: SubInfo):
    """Handle a QR-code scan payload.

    Routing:
    - 8–14 digit payload → treat as EAN/UPC, delegate to barcode lookup.
    - http(s) URL → fetch page (SSRF-guarded), hand the visible text to Gemini
      via analyze_meal as a text-mode meal. Gemini decides whether it's a
      single product (extract nutrition) or a menu (ask what was eaten).
    - Anything else → return a clear error; client shows the existing sheet.
    """
    kind, value = _classify_qr_payload(req.payload or "")

    await log_event(
        db_path, user["user_id"], "meal_qr_scan",
        metadata={"kind": kind, "length": len(req.payload or "")},
    )

    if kind == "barcode":
        # Reuse the existing endpoint logic by calling it directly.
        # barcode_scan is free (no Gemini call), so the cap dependency isn't needed.
        return await barcode_scan(
            request,
            BarcodeRequest(barcode=value, servings=1.0, meal_type=req.meal_type),
            user,
            db_path,
        )

    if kind == "unsupported":
        return AnalyzeResponse(session_id="", nutrition=None, error=value)

    # kind == "url"
    fetched = await _fetch_page_text(value)
    if not fetched:
        return AnalyzeResponse(
            session_id="",
            nutrition=None,
            error="Couldn't read that QR link. Try scanning the product barcode or type the meal.",
        )
    title, meta_desc, body_text = fetched

    # Compose a text input for analyze_meal. We describe the provenance so
    # Gemini knows to extract nutrition if the page is a product page, or ask
    # clarifying questions if it's a restaurant menu with multiple items.
    parts = [
        "The user scanned a QR code that pointed to a web page. Use the page below to identify the food.",
        f"URL: {value}",
    ]
    if title:
        parts.append(f"Page title: {title}")
    if meta_desc:
        parts.append(f"Page description: {meta_desc}")
    if body_text:
        parts.append("Page text:")
        parts.append(body_text)
    parts.append(
        "If this is a single packaged product, extract nutrition from the label shown on the page. "
        "If this is a restaurant menu or list of dishes, ask which item the user ate."
    )
    user_text = "\n".join(parts)

    # B6: gate /meals/qr Gemini calls against the same per-user text_meal cap
    # used by /meals/analyze. QR-URL scans do an arbitrary-text analyze_meal,
    # which is the same upstream cost shape as a typed text meal.
    today_str = await user_today_str(db_path, user["user_id"])
    tier_limit = sub.text_meals_limit
    if tier_limit != UNLIMITED:
        usage_result = await increment_usage(
            db_path, user["user_id"], "text_meal", today_str, limit=tier_limit,
        )
        if not usage_result.get("allowed"):
            await log_event(
                db_path, user["user_id"], "limit_hit",
                metadata={
                    "feature": "text_meal",
                    "limit": tier_limit,
                    "is_premium": sub.is_premium,
                    "via": "qr_url",
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

    result = await analyze_meal(
        images=[],
        user_text=user_text,
        user_id=user["user_id"],
        db_path=db_path,
        meal_type=req.meal_type,
    )

    nutrition = result.get("nutrition")
    nutrition_out: NutritionOut | None = None
    if nutrition is not None:
        nutrition_out = _nutrition_to_out(nutrition)

    logger.info(
        "QR url=%s -> session=%s title=%r has_nutrition=%s user=%s",
        value[:200], result.get("session_id"), title[:60], bool(nutrition), user["user_id"],
    )

    # B9: do NOT echo back raw_text from the QR scan - internal page
    # contents must not flow through to the client. The model still has it
    # in-context to inform its reply, but we don't surface it.
    return AnalyzeResponse(
        session_id=result.get("session_id", ""),
        nutrition=nutrition_out,
        questions=result.get("questions", []),
        raw_text="",
        error=result.get("error"),
    )


@router.delete("/barcode/{barcode}/correction")
@limiter.limit("20/minute")
async def reset_barcode_correction(
    request: Request, barcode: str, user: CurrentUser, db_path: DbPath,
):
    """Remove the user's saved correction for a barcode so future scans go
    back to the Open Food Facts / FatSecret database values."""
    barcode = barcode.strip()
    if not _BARCODE_RE.match(barcode):
        raise HTTPException(status_code=400, detail="Invalid barcode format")
    removed = await delete_barcode_correction(db_path, user["user_id"], barcode)
    return {"ok": True, "removed": removed}
