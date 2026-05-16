"""USDA FoodData Central API: text search for per-100g nutrition data + SQLite cache."""

import asyncio
import logging
import os

import aiosqlite
import httpx

logger = logging.getLogger("macro_app")

_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"

# Limit concurrent USDA API calls to avoid rate limiting
_USDA_SEMAPHORE = asyncio.Semaphore(5)

# USDA nutrient number mapping
_NUTRIENT_MAP = {
    208: "calories",   # Energy (kcal)
    203: "protein",    # Protein
    205: "carbs",      # Carbohydrate, by difference
    204: "fat",        # Total lipid (fat)
}


def _parse_food_nutrients(food: dict) -> dict | None:
    """Extract per-100g macros from a USDA FDC food result.

    USDA FDC 'Foundation' and 'SR Legacy' data report nutrients per 100g by default.
    Returns {calories, protein, carbs, fat} or None if essential nutrients are missing.
    """
    nutrients = food.get("foodNutrients", [])
    macros: dict[str, float] = {}

    for nutrient in nutrients:
        num = nutrient.get("nutrientNumber")
        if num is None:
            # Some responses use 'nutrientId' instead
            num = nutrient.get("nutrientId")
        try:
            num = int(float(str(num)))
        except (TypeError, ValueError):
            continue

        if num in _NUTRIENT_MAP:
            value = nutrient.get("value", 0)
            try:
                macros[_NUTRIENT_MAP[num]] = float(value)
            except (TypeError, ValueError):
                continue

    # Require at least calories to consider this a valid result
    if "calories" not in macros:
        return None

    return {
        "calories": macros.get("calories", 0),
        "protein": macros.get("protein", 0),
        "carbs": macros.get("carbs", 0),
        "fat": macros.get("fat", 0),
    }


async def _search_one(name: str, api_key: str) -> tuple[dict | None, int | None, str]:
    """Search USDA FDC for a single food name.

    Returns (per-100g macros or None, fdc_id or None, data_type).
    """
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                _SEARCH_URL,
                params={
                    "query": name,
                    "dataType": "Foundation,SR Legacy",
                    "pageSize": 3,
                    "api_key": api_key,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            foods = data.get("foods", [])

            for food in foods:
                macros = _parse_food_nutrients(food)
                if macros:
                    fdc_id = food.get("fdcId")
                    data_type = food.get("dataType", "")
                    logger.debug(
                        "USDA FDC hit: %r -> %s (fdcId=%s, type=%s)",
                        name,
                        food.get("description", ""),
                        fdc_id,
                        data_type,
                    )
                    return macros, fdc_id, data_type
    except Exception:
        logger.exception("USDA FDC search failed for %r", name)
    return None, None, ""


async def lookup_foods(names: list[str], db_path: str | None = None) -> dict[str, dict]:
    """Look up food names in USDA FoodData Central, returning per-100g macros.

    Hits SQLite cache first (30-day TTL). Falls back to USDA FDC API for misses.
    Returns {name: {calories, protein, carbs, fat}}.
    Names not found in either source are omitted from the result.
    Gracefully returns empty dict if API key not set.
    """
    if not names:
        return {}

    api_key = os.environ.get("USDA_FDC_API_KEY", "").strip()
    if not api_key:
        logger.debug("USDA FDC API key not set (USDA_FDC_API_KEY), skipping lookup")
        return {}

    results: dict[str, dict] = {}
    uncached: list[str] = []

    if db_path:
        async with aiosqlite.connect(db_path) as db:
            for name in names:
                key = name.lower().strip()
                row = await (
                    await db.execute(
                        "SELECT calories, protein, carbs, fat FROM usda_fdc_cache "
                        "WHERE query_key = ? AND fetched_at > datetime('now', '-30 days')",
                        (key,),
                    )
                ).fetchone()
                if row:
                    results[name] = {
                        "calories": row[0],
                        "protein": row[1],
                        "carbs": row[2],
                        "fat": row[3],
                    }
                else:
                    uncached.append(name)
    else:
        uncached = list(names)

    if not uncached:
        return results

    async def _search_with_sem(name: str) -> tuple[dict | None, int | None, str]:
        async with _USDA_SEMAPHORE:
            return await _search_one(name, api_key)

    fetched = await asyncio.gather(*[_search_with_sem(n) for n in uncached])

    if db_path:
        async with aiosqlite.connect(db_path) as db:
            for name, (macros, fdc_id, data_type) in zip(uncached, fetched):
                if macros:
                    results[name] = macros
                    await db.execute(
                        "INSERT OR REPLACE INTO usda_fdc_cache "
                        "(query_key, calories, protein, carbs, fat, fdc_id, data_type, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                        (
                            name.lower().strip(),
                            macros["calories"],
                            macros["protein"],
                            macros["carbs"],
                            macros["fat"],
                            fdc_id,
                            data_type,
                        ),
                    )
            await db.commit()
    else:
        for name, (macros, _fdc_id, _data_type) in zip(uncached, fetched):
            if macros:
                results[name] = macros

    return results
