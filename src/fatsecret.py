"""FatSecret Platform API: food search with OAuth2 client credentials + SQLite cache."""

import asyncio
import logging
import os
import re
import time

import aiosqlite
import httpx

from src.db_pool import get_db

logger = logging.getLogger("macro_app")

_TOKEN_URL = "https://oauth.fatsecret.com/connect/token"
_SEARCH_URL = "https://platform.fatsecret.com/rest/foods/search/v1"

# In-memory token cache (survives across calls within the same process)
_token: str | None = None
_token_expires_at: float = 0.0

# Limit concurrent FatSecret API calls to avoid rate limiting
# Lazy-init to avoid binding to event loop at import time
_FS_SEMAPHORE: asyncio.Semaphore | None = None
_token_lock: asyncio.Lock | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _FS_SEMAPHORE
    if _FS_SEMAPHORE is None:
        _FS_SEMAPHORE = asyncio.Semaphore(5)
    return _FS_SEMAPHORE


def _get_token_lock() -> asyncio.Lock:
    global _token_lock
    if _token_lock is None:
        _token_lock = asyncio.Lock()
    return _token_lock


async def _get_access_token() -> str | None:
    """Fetch or return a cached OAuth2 bearer token (client credentials flow)."""
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - 60:
        return _token

    async with _get_token_lock():
        # Re-check after acquiring lock (another coroutine may have refreshed)
        if _token and time.time() < _token_expires_at - 60:
            return _token

        client_id = os.environ.get("FAT_SECRET_Client_ID", "").strip()
        client_secret = os.environ.get("FAT_SECRET_Client_Secret", "").strip()
        if not client_id or not client_secret:
            logger.warning("FatSecret credentials not set (FAT_SECRET_Client_ID / FAT_SECRET_Client_Secret)")
            return None

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    _TOKEN_URL,
                    data={"grant_type": "client_credentials", "scope": "basic"},
                    auth=(client_id, client_secret),
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                _token = data.get("access_token")
                _token_expires_at = time.time() + int(data.get("expires_in", 86400))
                logger.debug("FatSecret token acquired, expires in %ds", data.get("expires_in", 86400))
                return _token
        except Exception:
            logger.exception("FatSecret token fetch failed")
            return None


def _parse_food_description(desc: str) -> dict | None:
    """Parse FatSecret food_description string into per-100g macro dict.

    Expected format: 'Per 100g - Calories: 102kcal | Fat: 5.00g | Carbs: 4.86g | Protein: 9.72g'
    Returns {calories, protein, carbs, fat} or None if not parseable / not per-100g.
    """
    if not desc or "Per 100g" not in desc:
        return None
    try:
        cal = re.search(r"Calories:\s*([\d.]+)", desc)
        fat = re.search(r"Fat:\s*([\d.]+)", desc)
        carbs = re.search(r"Carbs:\s*([\d.]+)", desc)
        protein = re.search(r"Protein:\s*([\d.]+)", desc)
        if not all([cal, fat, carbs, protein]):
            return None
        return {
            "calories": float(cal.group(1)),  # type: ignore[union-attr]
            "protein": float(protein.group(1)),  # type: ignore[union-attr]
            "carbs": float(carbs.group(1)),  # type: ignore[union-attr]
            "fat": float(fat.group(1)),  # type: ignore[union-attr]
        }
    except Exception:
        return None


async def _search_one(name: str, token: str, _retries: int = 1) -> dict | None:
    """Search FatSecret for a single food name. Returns per-100g macros or None."""
    last_exc: Exception | None = None
    for attempt in range(_retries + 1):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    _SEARCH_URL,
                    params={"search_expression": name, "max_results": 5, "format": "json"},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                foods = data.get("foods", {}).get("food", [])
                if isinstance(foods, dict):  # single result arrives as dict, not list
                    foods = [foods]
                # Prefer Generic foods - they always report per 100g
                for food in sorted(foods, key=lambda f: f.get("food_type", "") != "Generic"):
                    macros = _parse_food_description(food.get("food_description", ""))
                    if macros:
                        logger.debug(
                            "FatSecret hit: %r → %s (%s)",
                            name,
                            food.get("food_name"),
                            food.get("food_type"),
                        )
                        return macros
                return None  # No matching food found - not a transient error
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            last_exc = exc
            if attempt < _retries:
                await asyncio.sleep(1.0)
                logger.warning("FatSecret transient error for %r (attempt %d/%d): %s", name, attempt + 1, _retries + 1, exc)
        except Exception:
            logger.exception("FatSecret search failed for %r", name)
            return None
    logger.warning("FatSecret lookup failed after %d attempts for %r: %s", _retries + 1, name, last_exc)
    return None


async def lookup_foods(names: list[str], db_path: str | None = None) -> dict[str, dict]:
    """Look up food names in FatSecret, returning per-100g macros.

    Hits SQLite cache first (30-day TTL). Falls back to FatSecret API for misses.
    Returns {name: {calories, protein, carbs, fat}}.
    Names not found in either source are omitted from the result.
    """
    if not names:
        return {}

    results: dict[str, dict] = {}
    uncached: list[str] = []

    if db_path:
        async with get_db(db_path) as db:
            for name in names:
                key = name.lower().strip()
                row = await (
                    await db.execute(
                        "SELECT calories, protein, carbs, fat FROM fatsecret_cache "
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

    token = await _get_access_token()
    if not token:
        return results

    async def _search_with_sem(name: str) -> dict | None:
        async with _get_semaphore():
            return await _search_one(name, token)

    fetched = await asyncio.gather(*[_search_with_sem(n) for n in uncached])

    if db_path:
        async with get_db(db_path) as db:
            for name, macros in zip(uncached, fetched):
                if macros:
                    results[name] = macros
                    await db.execute(
                        "INSERT OR REPLACE INTO fatsecret_cache "
                        "(query_key, calories, protein, carbs, fat, fetched_at) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (
                            name.lower().strip(),
                            macros["calories"],
                            macros["protein"],
                            macros["carbs"],
                            macros["fat"],
                        ),
                    )
            await db.commit()
    else:
        for name, macros in zip(uncached, fetched):
            if macros:
                results[name] = macros

    return results
