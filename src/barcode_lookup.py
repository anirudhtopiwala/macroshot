"""Barcode lookup: Open Food Facts → FatSecret fallback, with SQLite cache."""

import json
import logging
import re
import time

import httpx

from src.db import get_barcode_cache, set_barcode_cache, set_barcode_cache_not_found
from src.fatsecret import _get_access_token

logger = logging.getLogger("macro_app")

_OFF_BASE_URL = "https://world.openfoodfacts.org/api/v2/product"
_FS_API_URL = "https://platform.fatsecret.com/rest/server.api"
_USER_AGENT = "MacroApp/1.0 (macro_app)"


# Regex matches a volume unit appearing as a whole token - "340 ml", "0.5 l",
# "12 fl oz", "1 bottle (16 fl. oz.)". Deliberately avoids matching "l" inside
# words like "large" by requiring a digit+optional-space prefix.
_VOLUME_UNIT_RE = re.compile(
    r"(?:\d(?:[.,]\d+)?)\s*(?:ml|cl|dl|l|fl\.?\s*oz)\b",
    re.IGNORECASE,
)


def _infer_serving_unit(serving_label: str, quantity_unit: str | None = None) -> str:
    """Return 'ml' for liquid servings, 'g' otherwise.

    Prefers OFF's explicit `serving_quantity_unit` when available; otherwise
    parses the human-readable `serving_size` string (e.g. '340 ml',
    '1 bottle (16 fl oz)'). Defaults to 'g'.
    """
    if quantity_unit:
        q = quantity_unit.strip().lower()
        if q in {"ml", "cl", "dl", "l"} or q.startswith("fl"):
            return "ml"
        if q in {"g", "kg", "mg", "oz", "lb"}:
            return "g"
    if serving_label and _VOLUME_UNIT_RE.search(serving_label):
        return "ml"
    return "g"


def _parse_nutriments(nutriments: dict, serving_g: float | None) -> dict:
    """Extract per-serving and per-100g macros from OFF nutriments dict.

    Prefers per-serving values when available and serving_g is known.
    Falls back to per-100g values otherwise.
    """
    # Per-100g values (always available if product has nutrition data)
    cal_100 = nutriments.get("energy-kcal_100g")
    prot_100 = nutriments.get("proteins_100g")
    carb_100 = nutriments.get("carbohydrates_100g")
    fat_100 = nutriments.get("fat_100g")

    # Fallback: convert kJ to kcal if kcal fields are missing (1 kcal = 4.184 kJ)
    if cal_100 is None:
        kj_100 = nutriments.get("energy-kj_100g") or nutriments.get("energy_100g")
        if kj_100 is not None:
            cal_100 = float(kj_100) / 4.184

    # Per-serving values
    cal_srv = nutriments.get("energy-kcal_serving")
    prot_srv = nutriments.get("proteins_serving")
    carb_srv = nutriments.get("carbohydrates_serving")
    fat_srv = nutriments.get("fat_serving")

    # Fallback: convert kJ to kcal for per-serving too
    if cal_srv is None:
        kj_srv = nutriments.get("energy-kj_serving") or nutriments.get("energy_serving")
        if kj_srv is not None:
            cal_srv = float(kj_srv) / 4.184

    has_serving = all(v is not None for v in (cal_srv, prot_srv, carb_srv, fat_srv))
    has_100g = all(v is not None for v in (cal_100, prot_100, carb_100, fat_100))

    if has_serving:
        calories = float(cal_srv)
        protein = float(prot_srv)
        carbs = float(carb_srv)
        fat = float(fat_srv)

        # Sanity check: OFF often has per-serving = per_100g × factor because
        # contributors enter US label per-serving values into per-100g fields.
        # Detect this: if serving > 150g and per-serving looks inflated (just
        # per_100g × factor) with unreasonable values, use per_100g as per-serving.
        if has_100g and serving_g and serving_g > 150:
            factor = serving_g / 100.0
            computed_cal = float(cal_100) * factor
            # Check if per_serving ≈ per_100g × factor (OFF computed, not independent)
            if abs(calories - computed_cal) < computed_cal * 0.05:
                # per_serving was derived from per_100g, not independently entered.
                # If the result looks unreasonable, per_100g is likely per-serving.
                if calories > 900 or protein > 60:
                    logger.info(
                        "Barcode sanity fix: per_100g looks like per-serving "
                        "(cal=%s→%s, prot=%s→%s), using per_100g as per-serving",
                        cal_100, calories, prot_100, protein,
                    )
                    calories = float(cal_100)
                    protein = float(prot_100)
                    carbs = float(carb_100)
                    fat = float(fat_100)
                    serving_g = 100.0  # Macros now represent per-100g after sanity fix
    elif has_100g and serving_g:
        # Compute per-serving from per-100g
        factor = serving_g / 100.0
        calories = float(cal_100) * factor
        protein = float(prot_100) * factor
        carbs = float(carb_100) * factor
        fat = float(fat_100) * factor

        # Same sanity check for computed values
        if serving_g > 150 and (calories > 900 or protein > 60):
            logger.info(
                "Barcode sanity fix: computed per-serving unreasonable "
                "(cal=%s, prot=%s), using per_100g as per-serving",
                round(calories, 1), round(protein, 1),
            )
            calories = float(cal_100)
            protein = float(prot_100)
            carbs = float(carb_100)
            fat = float(fat_100)
            serving_g = 100.0  # Macros now represent per-100g after sanity fix
    elif has_100g:
        # No serving info at all - return per-100g as the serving
        calories = float(cal_100)
        protein = float(prot_100)
        carbs = float(carb_100)
        fat = float(fat_100)
        serving_g = 100.0
    else:
        return {}

    # Reject negative values
    if any(v is not None and v < 0 for v in [calories, protein, carbs, fat]):
        return {}

    # Reject if all macros are zero (no usable data)
    if calories == 0 and protein == 0 and carbs == 0 and fat == 0:
        return {}

    # Atwater sanity check. The stated kcal must be within ~30% of the value
    # implied by the macros (protein*4 + carbs*4 + fat*9). OFF contributors
    # frequently mis-enter the energy field (e.g. "19000 kcal/100g" for peanut
    # butter, "2 kcal/serving" for ranch dressing) while the protein/carbs/fat
    # fields stay correct. When stated and implied diverge, trust the macros
    # and recompute kcal from them. Works for any serving size, unlike the
    # >150g heuristic in the branches above.
    implied_kcal = protein * 4 + carbs * 4 + fat * 9
    if implied_kcal > 10:  # skip near-zero-macro products where ratio is noisy
        ratio = abs(calories - implied_kcal) / implied_kcal
        if ratio > 0.30:
            logger.warning(
                "Barcode Atwater fix: stated=%.1f kcal vs macro-implied=%.1f "
                "kcal (%.0f%% off); using implied. p=%.1fg c=%.1fg f=%.1fg "
                "serving=%sg",
                calories, implied_kcal, ratio * 100,
                protein, carbs, fat, serving_g,
            )
            calories = implied_kcal
            # cal_100 was likely wrong in the same way - recompute from the
            # corrected per-serving so the cached per-100g stays consistent.
            if serving_g and serving_g > 0:
                cal_100 = calories * 100.0 / serving_g

    return {
        "calories": round(calories, 1),
        "protein": round(protein, 1),
        "carbs": round(carbs, 1),
        "fat": round(fat, 1),
        "cal_per_100g": round(float(cal_100), 1) if cal_100 is not None else None,
        "protein_per_100g": round(float(prot_100), 1) if prot_100 is not None else None,
        "carbs_per_100g": round(float(carb_100), 1) if carb_100 is not None else None,
        "fat_per_100g": round(float(fat_100), 1) if fat_100 is not None else None,
        "serving_size_g": round(serving_g, 1) if serving_g else None,
    }


async def _off_barcode_lookup(barcode: str) -> dict | None:
    """Look up a barcode via Open Food Facts API."""
    url = f"{_OFF_BASE_URL}/{barcode}.json"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                url,
                params={"fields": "product_name,brands,nutriments,serving_size,serving_quantity,serving_quantity_unit,image_front_url,image_front_small_url,completeness"},
                headers={"User-Agent": _USER_AGENT},
                timeout=10.0,
            )
            if resp.status_code == 404:
                logger.debug("Open Food Facts: barcode %s not found (404)", barcode)
                return None
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.debug("Open Food Facts: barcode %s not found (404)", barcode)
            return None
        logger.exception("Open Food Facts lookup failed for barcode %s", barcode)
        return None
    except Exception:
        logger.exception("Open Food Facts lookup failed for barcode %s", barcode)
        return None

    if data.get("status") != 1 or not data.get("product"):
        logger.debug("Barcode %s not found on Open Food Facts", barcode)
        return None

    product = data["product"]

    completeness = product.get("completeness", 0)
    low_confidence = completeness < 0.7

    product_name = product.get("product_name", "").strip()
    if not product_name:
        logger.debug("Barcode %s: product has no name", barcode)
        return None

    brand = (product.get("brands") or "").strip()
    nutriments = product.get("nutriments", {})

    serving_g = product.get("serving_quantity")
    if serving_g is not None:
        try:
            serving_g = float(serving_g)
        except (ValueError, TypeError):
            serving_g = None

    serving_label = (product.get("serving_size") or "").strip()
    serving_unit = _infer_serving_unit(
        serving_label, product.get("serving_quantity_unit")
    )
    macros = _parse_nutriments(nutriments, serving_g)
    if not macros:
        logger.debug("Barcode %s: no usable nutrition data", barcode)
        return None

    # Prefer the full-resolution image; fall back to the small variant if the
    # full-res URL is missing. Backend downscales to 1280 px before saving.
    image_url = (product.get("image_front_url") or product.get("image_front_small_url") or "").strip()
    raw_json = json.dumps(product, ensure_ascii=False)[:5000]

    return {
        "barcode": barcode,
        "product_name": product_name,
        "brand": brand,
        "serving_size_g": macros.get("serving_size_g"),
        "serving_size_unit": serving_unit,
        "serving_label": serving_label,
        "calories": macros["calories"],
        "protein": macros["protein"],
        "carbs": macros["carbs"],
        "fat": macros["fat"],
        "cal_per_100g": macros.get("cal_per_100g"),
        "protein_per_100g": macros.get("protein_per_100g"),
        "carbs_per_100g": macros.get("carbs_per_100g"),
        "fat_per_100g": macros.get("fat_per_100g"),
        "image_url": image_url,
        "source": "openfoodfacts",
        "low_confidence": low_confidence,
        "raw_json": raw_json,
        "fetched_at": "",
    }


async def _fatsecret_barcode_lookup(barcode: str) -> dict | None:
    """Look up a barcode via FatSecret: find_id_for_barcode → food.get.v4."""
    token = await _get_access_token()
    if not token:
        return None

    try:
        async with httpx.AsyncClient() as client:
            # Step 1: barcode → food_id
            resp = await client.post(
                _FS_API_URL,
                data={
                    "method": "food.find_id_for_barcode",
                    "barcode": barcode,
                    "format": "json",
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 404:
                logger.debug("FatSecret barcode %s: not found (404)", barcode)
                return None
            resp.raise_for_status()
            data = resp.json()
            food_id_obj = data.get("food_id")
            if not food_id_obj:
                logger.debug("FatSecret barcode %s: no food_id returned", barcode)
                return None
            food_id = food_id_obj.get("value") if isinstance(food_id_obj, dict) else str(food_id_obj)

            # Step 2: food_id → full nutrition
            resp2 = await client.post(
                _FS_API_URL,
                data={
                    "method": "food.get.v4",
                    "food_id": food_id,
                    "format": "json",
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp2.status_code == 404:
                logger.debug("FatSecret food_id %s: not found (404)", food_id)
                return None
            resp2.raise_for_status()
            food_data = resp2.json().get("food", {})
    except Exception:
        logger.exception("FatSecret barcode lookup failed for %s", barcode)
        return None

    product_name = (food_data.get("food_name") or "").strip()
    if not product_name:
        return None
    brand = (food_data.get("brand_name") or "").strip()

    # Parse servings - pick the first serving (usually the standard one)
    servings_obj = food_data.get("servings", {}).get("serving", [])
    if isinstance(servings_obj, dict):
        servings_obj = [servings_obj]
    if not servings_obj:
        return None

    srv = servings_obj[0]
    try:
        calories = round(float(srv.get("calories", 0)), 1)
        protein = round(float(srv.get("protein", 0)), 1)
        carbs = round(float(srv.get("carbohydrate", 0)), 1)
        fat = round(float(srv.get("fat", 0)), 1)
    except (ValueError, TypeError):
        return None

    # Reject negative values
    if any(v < 0 for v in [calories, protein, carbs, fat]):
        return None

    if calories == 0 and protein == 0 and carbs == 0 and fat == 0:
        return None

    serving_g = None
    try:
        serving_g = round(float(srv.get("metric_serving_amount", 0)), 1)
        if serving_g == 0:
            serving_g = None
    except (ValueError, TypeError):
        pass

    serving_label = (srv.get("serving_description") or "").strip()
    # FatSecret exposes the unit explicitly as `metric_serving_unit` (e.g. "g" or "ml").
    serving_unit = _infer_serving_unit(
        serving_label, srv.get("metric_serving_unit"),
    )

    # Extract product image if available
    fs_image_url = ""
    try:
        images = food_data.get("food_images", {}).get("food_image", [])
        if isinstance(images, dict):
            images = [images]
        if images:
            fs_image_url = images[0].get("image_url", "") or ""
    except Exception:
        pass

    # Compute per-100g if we have serving weight
    cal_100 = prot_100 = carb_100 = fat_100 = None
    if serving_g and serving_g > 0:
        factor = 100.0 / serving_g
        cal_100 = round(calories * factor, 1)
        prot_100 = round(protein * factor, 1)
        carb_100 = round(carbs * factor, 1)
        fat_100 = round(fat * factor, 1)

    logger.info("FatSecret barcode %s -> %s (%s)", barcode, product_name, brand)

    return {
        "barcode": barcode,
        "product_name": product_name,
        "brand": brand,
        "serving_size_g": serving_g,
        "serving_size_unit": serving_unit,
        "serving_label": serving_label,
        "calories": calories,
        "protein": protein,
        "carbs": carbs,
        "fat": fat,
        "cal_per_100g": cal_100,
        "protein_per_100g": prot_100,
        "carbs_per_100g": carb_100,
        "fat_per_100g": fat_100,
        "image_url": fs_image_url,
        "source": "fatsecret",
        "low_confidence": False,
        "raw_json": "",
        "fetched_at": "",
    }


async def lookup_barcode(barcode: str, db_path: str, user_id: int | None = None) -> dict | None:
    """Look up nutrition by barcode. Returns dict or None if not found.

    Return dict keys:
        product_name, brand, calories, protein, carbs, fat,
        serving_size_g, serving_label,
        cal_per_100g, protein_per_100g, carbs_per_100g, fat_per_100g
    """
    t0 = time.monotonic()

    # 1. Check cache (90-day TTL for found, 2-day TTL for not_found)
    cached = await get_barcode_cache(db_path, barcode)
    if cached:
        duration_ms = round((time.monotonic() - t0) * 1000, 1)
        if cached.get("not_found"):
            logger.info(
                "barcode_lookup barcode=%s source=cached_not_found product_name=None "
                "duration_ms=%.1f user_id=%s",
                barcode, duration_ms, user_id,
            )
            return None
        logger.info(
            "barcode_lookup barcode=%s source=cache product_name=%s "
            "duration_ms=%.1f user_id=%s",
            barcode, cached.get("product_name"), duration_ms, user_id,
        )
        # Annotate with cache metadata for the frontend
        cached["cached"] = True
        fetched_at = cached.get("fetched_at", "")
        if fetched_at:
            try:
                from datetime import datetime, timezone as _tz
                fetched_dt = datetime.strptime(fetched_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=_tz.utc)
                delta = datetime.now(_tz.utc) - fetched_dt
                cached["cached_days_ago"] = delta.days
            except (ValueError, TypeError):
                cached["cached_days_ago"] = None
        return cached

    # 2. Call Open Food Facts
    off_result = await _off_barcode_lookup(barcode)
    result = off_result
    source = "openfoodfacts" if result else None

    # 3. If OFF missed or has low confidence, try FatSecret
    if not result or result.get("low_confidence"):
        logger.debug("Barcode %s: OFF %s, trying FatSecret", barcode, "low confidence" if result else "miss")
        fs_result = await _fatsecret_barcode_lookup(barcode)
        if fs_result:
            result = fs_result
            source = "fatsecret"
        # If FatSecret also missed but OFF had low-confidence data, use it as last resort
        elif off_result:
            logger.debug("Barcode %s: FatSecret miss, using low-confidence OFF data", barcode)
            result = off_result
            source = "openfoodfacts"

    duration_ms = round((time.monotonic() - t0) * 1000, 1)

    if not result:
        # Cache the not-found result (2-day TTL) to avoid repeated external lookups
        try:
            await set_barcode_cache_not_found(db_path, barcode)
        except Exception:
            logger.warning("Failed to cache not-found barcode %s", barcode)
        logger.info(
            "barcode_lookup barcode=%s source=not_found product_name=None "
            "duration_ms=%.1f user_id=%s",
            barcode, duration_ms, user_id,
        )
        return None

    # 4. Cache result
    try:
        await set_barcode_cache(
            db_path=db_path,
            barcode=barcode,
            product_name=result["product_name"],
            brand=result.get("brand", ""),
            serving_size_g=result.get("serving_size_g"),
            serving_size_unit=result.get("serving_size_unit", "g"),
            serving_label=result.get("serving_label", ""),
            calories=result["calories"],
            protein=result["protein"],
            carbs=result["carbs"],
            fat=result["fat"],
            cal_per_100g=result.get("cal_per_100g"),
            protein_per_100g=result.get("protein_per_100g"),
            carbs_per_100g=result.get("carbs_per_100g"),
            fat_per_100g=result.get("fat_per_100g"),
            source=result.get("source", "openfoodfacts"),
            raw_json=result.get("raw_json", ""),
            image_url=result.get("image_url", ""),
        )
    except Exception:
        logger.warning("Failed to cache barcode %s", barcode)

    logger.info(
        "barcode_lookup barcode=%s source=%s product_name=%s "
        "duration_ms=%.1f user_id=%s",
        barcode, source, result.get("product_name"), duration_ms, user_id,
    )

    return result
