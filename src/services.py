"""Service layer: business logic for the FastAPI web app.

All functions here return plain dicts/objects - no HTTP specifics.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
import zoneinfo
from datetime import datetime, timezone

from src.db import (
    log_meal as db_log_meal,
    get_today_totals,
    get_user_target,
    get_user_stats,
    get_daily_totals_7days,
    get_user_prefs,
    create_meal_session,
    get_meal_session,
    update_meal_session,
    log_fatsecret_comparison,
    log_event,
    count_user_meals,
    save_barcode_correction,
)
from src.fatsecret import lookup_foods
from src.usda_fdc import lookup_foods as usda_lookup
from src.gemini import (
    CONVERSATIONAL_INITIAL_PROMPT,
    TEXT_ONLY_INITIAL_PROMPT,
    COMBINED_INITIAL_PROMPT,
    build_reference_hint,
    gemini_analyze_meal,
    gemini_chat,
    _extract_json,
    _response_to_nutrition_result,
)
from src.models import NutritionResult

logger = logging.getLogger("macro_app")

_MEAL_WINDOWS = {
    "breakfast": (3, 11),
    "lunch": (11, 15),
    "snack": (15, 18),
    "dinner": (18, 3),  # wraps midnight: 18:00–02:59
}

DEFAULT_TARGETS = {"calories": 2000.0, "protein": 150.0, "carbs": 200.0, "fat": 70.0}


def classify_meal_time(hour: int) -> str:
    """Return breakfast/lunch/snack/dinner based on the hour (0-23).

    Dinner wraps midnight (18:00–02:59) so late-night meals are not
    misclassified as breakfast.
    """
    if not 0 <= hour < 24:
        return "meal"
    for name, (start, end) in _MEAL_WINDOWS.items():
        if start < end:
            if start <= hour < end:
                return name
        else:  # wrap-around window
            if hour >= start or hour < end:
                return name
    return "meal"


def get_user_tz(tz_str: str | None = None) -> zoneinfo.ZoneInfo:
    """Get a ZoneInfo from string, falling back to env TZ or UTC."""
    tz_str = tz_str or os.environ.get("TZ", "UTC")
    try:
        return zoneinfo.ZoneInfo(tz_str.strip())
    except Exception:
        return zoneinfo.ZoneInfo("UTC")


async def user_today_str(db_path: str, user_id: int) -> str:
    """Return today's date string in the user's configured timezone."""
    try:
        prefs = await get_user_prefs(db_path, user_id)
        tz = zoneinfo.ZoneInfo(prefs.get("timezone", "UTC"))
    except Exception:
        tz = get_user_tz()
    return datetime.now(tz).strftime("%Y-%m-%d")


async def search_user_meals(
    db_path: str, user_id: int, query: str, limit: int = 10
) -> list[dict]:
    """Semantic-first meal search with substring fallback.

    Tries a Gemini-embedding cosine match against the user's
    ``meal_logs.name_embedding`` column (populated since schema v15). Falls
    back to a case-insensitive substring match when:

    - the embedding API is unreachable or the key is missing
    - the user has zero embedded meals (very new account, or backfill not
      run yet)

    Returns a list of meal dicts shaped like the rows from ``meal_logs``,
    each augmented with ``"score"`` (cosine similarity 0-1 for semantic
    hits, ``None`` for substring fallback). Highest score first.
    """
    import aiosqlite
    import numpy as np

    from src.embeddings import cosine_top_k, embed_query, from_blob

    limit = max(1, min(50, limit))
    query = (query or "").strip()
    if not query:
        return []

    q_vec = await embed_query(query)
    if q_vec is not None:
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await (await db.execute(
                """SELECT id, logged_at, item_name, meal_description, calories,
                          protein, carbs, fat, meal_type, items_json, image_path,
                          name_embedding
                   FROM meal_logs
                   WHERE user_id = ? AND name_embedding IS NOT NULL""",
                (user_id,),
            )).fetchall()
        if rows:
            candidates = np.stack([from_blob(r["name_embedding"]) for r in rows])
            q_arr = np.asarray(q_vec, dtype=np.float32)
            indices, scores = cosine_top_k(q_arr, candidates, limit)
            out = []
            for idx, score in zip(indices, scores):
                m = dict(rows[int(idx)])
                m.pop("name_embedding", None)
                m["score"] = round(float(score), 4)
                out.append(m)
            return out

    # Fallback: substring (LIKE) search. Used for accounts with no
    # embeddings yet, or when the embedding API is unavailable.
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = f"%{escaped}%"
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """SELECT id, logged_at, item_name, meal_description, calories,
                      protein, carbs, fat, meal_type, items_json, image_path
               FROM meal_logs
               WHERE user_id = ?
                 AND (item_name LIKE ? ESCAPE '\\' OR meal_description LIKE ? ESCAPE '\\')
               ORDER BY id DESC LIMIT ?""",
            (user_id, pat, pat, limit),
        )).fetchall()
    out = []
    for r in rows:
        m = dict(r)
        m["score"] = None
        out.append(m)
    return out


def _reference_plausible(ref: dict, gemini: dict | None) -> bool:
    """Return False if reference per-100g values look like a wrong match.

    Used for both FatSecret and USDA FDC data.
    """
    total = ref.get("protein", 0) + ref.get("carbs", 0) + ref.get("fat", 0)
    if total > 105:
        return False
    if gemini:
        for key, min_ref in (("calories", 10), ("protein", 2)):
            ref_val = ref.get(key, 0) or 0
            g_val = gemini.get(key, 0) or 0
            avg = (ref_val + g_val) / 2
            if avg >= min_ref and avg > 0 and abs(ref_val - g_val) / avg > 0.50:
                return False
    return True


async def apply_references(
    images: list[bytes],
    conversation: list[dict],
    result: NutritionResult,
    db_path: str | None = None,
    user_id: int = 0,
) -> tuple[NutritionResult, bool, bool]:
    """Look up FatSecret + USDA FDC per-100g for identified items, send a
    reference-hint turn to Gemini, and return the refined result.
    Returns (result, fatsecret_used, memory_used)."""
    if not result.items:
        return result, False, False

    item_names = [item.name for item in result.items]

    # Run both lookups in parallel
    fatsecret_data: dict[str, dict] = {}
    usda_data: dict[str, dict] = {}

    async def _fs_lookup() -> dict[str, dict]:
        return await lookup_foods(item_names, db_path=db_path)

    async def _usda_lookup() -> dict[str, dict]:
        return await usda_lookup(item_names, db_path=db_path)

    fs_result, usda_result = await asyncio.gather(
        _fs_lookup(), _usda_lookup(), return_exceptions=True,
    )

    # Process FatSecret results
    if isinstance(fs_result, dict):
        for item in result.items:
            fs_macros = fs_result.get(item.name)
            if fs_macros and _reference_plausible(fs_macros, item.gemini_per_100g):
                fatsecret_data[item.name] = fs_macros
    elif isinstance(fs_result, Exception):
        logger.error("FatSecret lookup failed: %s", fs_result)

    # Process USDA results
    if isinstance(usda_result, dict):
        for item in result.items:
            usda_macros = usda_result.get(item.name)
            if usda_macros and _reference_plausible(usda_macros, item.gemini_per_100g):
                usda_data[item.name] = usda_macros
    elif isinstance(usda_result, Exception):
        logger.error("USDA FDC lookup failed: %s", usda_result)

    # Store reference data on items for debug/logging
    if fatsecret_data or usda_data:
        new_items = []
        for item in result.items:
            updates: dict = {}
            fs = fatsecret_data.get(item.name)
            if fs:
                updates["fatsecret_per_100g"] = fs
            usda = usda_data.get(item.name)
            if usda:
                updates["usda_per_100g"] = usda
            if updates:
                new_items.append(item.model_copy(update=updates))
            else:
                new_items.append(item)
        result = result.model_copy(update={"items": new_items})

    # Skip refinement if neither source has data
    if not fatsecret_data and not usda_data:
        return result, False, False

    hint = build_reference_hint(fatsecret_data, usda_data=usda_data)
    conversation.append({"role": "user", "text": hint})
    try:
        updated_text = await gemini_chat(
            images, conversation, db_path=db_path, user_id=user_id, call_type="reference_hint",
        )
        updated = _response_to_nutrition_result(updated_text, source="Gemini")
        if updated:
            conversation.append({"role": "model", "text": updated_text})
            # Carry over reference data for debug/logging
            if fatsecret_data or usda_data:
                patched = []
                for item in updated.items:
                    updates = {}
                    fs = fatsecret_data.get(item.name)
                    if fs and not item.fatsecret_per_100g:
                        updates["fatsecret_per_100g"] = fs
                    usda = usda_data.get(item.name)
                    if usda and not item.usda_per_100g:
                        updates["usda_per_100g"] = usda
                    if updates:
                        patched.append(item.model_copy(update=updates))
                    else:
                        patched.append(item)
                updated = updated.model_copy(update={"items": patched})
            fs_used = bool(fatsecret_data) and any(
                (item.source or "").lower() == "fatsecret" for item in updated.items
            )
            return updated, fs_used, False
        else:
            conversation.pop()
    except Exception as e:
        # Surface the failure to the caller instead of silently swallowing -
        # without a signal, an observability blind spot masked the case where
        # every image+text meal regressed to the raw Gemini estimate because
        # the reference-hint call hit a rate limit. [metric:enrichment_error]
        logger.warning(
            "[metric:enrichment_error] Reference hint chat failed: %s: %s",
            type(e).__name__, e, exc_info=True,
        )
        conversation.pop()

    return result, False, False



async def analyze_meal(
    images: list[bytes],
    user_text: str,
    user_id: int,
    db_path: str,
    meal_type: str = "",
) -> dict:
    """Run Gemini analysis + reference lookup. Returns a session dict.

    For text-only inputs, uses MCP-enabled analysis so Gemini can look up
    past meals when the user references them (e.g. "same as yesterday").

    Returns:
        {
            "session_id": str,
            "images": list[bytes],
            "conversation": list[dict],
            "nutrition": NutritionResult | None,
            "raw_text": str,
            "questions": list[str],
            "error": str | None,
        }
    """
    session_id = str(uuid.uuid4())
    mode = "combined" if (images and user_text) else ("image" if images else "text")

    # Fire telemetry event (private, server-side only). Never blocks the call.
    await log_event(
        db_path, user_id, f"meal_analyze_{mode}",
        metadata={"image_count": len(images), "has_text": bool(user_text)},
    )

    try:
        # All modes (image, text, combined) use the same reliable direct Gemini call.
        # MCP tools are reserved for the Chat with AI coaching feature where
        # conversational responses are expected (not strict JSON).
        _t0 = time.perf_counter()
        response_text, result = await gemini_analyze_meal(
            images, user_text=user_text, db_path=db_path or None, user_id=user_id
        )
        _elapsed = (time.perf_counter() - _t0) * 1000
        logger.info("Analysis Gemini call: user=%d, mode=%s, %.0fms", user_id, mode, _elapsed)
    except Exception as e:
        _elapsed = (time.perf_counter() - _t0) * 1000
        logger.exception("Gemini analysis error: user=%d, mode=%s, %.0fms", user_id, mode, _elapsed)
        return {
            "session_id": session_id,
            "images": images,
            "conversation": [],
            "nutrition": None,
            "raw_text": "",
            "questions": [],
            "error": f"Analysis failed: {e}",
        }

    if not result:
        # Surface what the model actually saw so the user can tell at a
        # glance whether they uploaded a non-food image, an unclear shot,
        # or hit a genuine parse failure. Truncated to keep the toast
        # readable; full text still available on raw_text for debugging.
        snippet = (response_text or "").strip()
        if len(snippet) > 400:
            snippet = snippet[:400].rstrip() + "…"
        if snippet:
            error_msg = f"We couldn't extract macros from that. Here's what the AI saw:\n\n{snippet}"
        else:
            error_msg = "That one was tricky - our AI needed a few tries and still couldn't lock it in. Mind giving it another shot?"
        return {
            "session_id": session_id,
            "images": images,
            "conversation": [],
            "nutrition": None,
            "raw_text": response_text or "",
            "questions": [],
            "error": error_msg,
        }

    if mode == "combined":
        initial_prompt = COMBINED_INITIAL_PROMPT + f"\n\nUser's note: {user_text}"
    elif mode == "text":
        initial_prompt = TEXT_ONLY_INITIAL_PROMPT + f"\n\nUser's description: {user_text}"
    else:
        initial_prompt = CONVERSATIONAL_INITIAL_PROMPT

    conversation = [
        {"role": "user", "text": initial_prompt},
        {"role": "model", "text": response_text or ""},
    ]

    # Extract questions
    questions = []
    try:
        raw_json = _extract_json(response_text)
        questions = raw_json.get("questions") or []
    except Exception:
        pass

    # Persist session to DB with the unrefined nutrition. The FatSecret/USDA
    # reference-hint Gemini round-trip used to run inline here and added 2-4s
    # to every meal log on the critical path. We now spawn it as a background
    # task that updates the same session row when it completes; accept_meal
    # re-reads the row from DB so it picks up the refined values whenever
    # they land. See _enrich_in_background below.
    nutrition_json = json.dumps(result.model_dump()) if result else ""
    conv_json = json.dumps(conversation)
    if not meal_type:
        try:
            prefs = await get_user_prefs(db_path, user_id)
            tz = zoneinfo.ZoneInfo(prefs.get("timezone", "UTC"))
        except Exception:
            tz = get_user_tz()
        meal_type = classify_meal_time(datetime.now(tz).hour)

    await create_meal_session(
        db_path, session_id, user_id,
        images_json=json.dumps([]),  # Don't store image bytes in DB
        conversation=conv_json,
        nutrition=nutrition_json,
        meal_type=meal_type,
        user_input=user_text,
    )

    # Fire-and-forget enrichment. Held in a module-level set so the task
    # isn't garbage-collected mid-flight.
    enrich_task = asyncio.create_task(
        _enrich_in_background(
            session_id=session_id,
            user_id=user_id,
            db_path=db_path,
            images=images,
            conversation=list(conversation),
            result=result,
        )
    )
    _enrichment_tasks.add(enrich_task)
    enrich_task.add_done_callback(_enrichment_tasks.discard)

    return {
        "session_id": session_id,
        "images": images,
        "conversation": conversation,
        "nutrition": result,
        "raw_text": response_text or "",
        "questions": questions[:2],
        "user_input": user_text,
        "error": None,
    }


# Hold references to fire-and-forget enrichment tasks so they aren't GC'd.
_enrichment_tasks: set[asyncio.Task] = set()


async def _enrich_in_background(
    session_id: str,
    user_id: int,
    db_path: str,
    images: list[bytes],
    conversation: list[dict],
    result: NutritionResult,
) -> None:
    """Run FatSecret/USDA reference-hint enrichment off the analyze critical path.

    On success, persists the refined nutrition + conversation to the meal
    session row so accept_meal (which re-reads the row) and any subsequent
    meal-detail fetches see the corrected values. Logs and swallows any
    exception - a failed enrichment must never propagate to the user, who
    has already been handed the unrefined estimate.
    """
    try:
        _t0 = time.perf_counter()
        refined, fs_used, memory_used = await apply_references(
            images, conversation, result, db_path=db_path or None, user_id=user_id,
        )
        elapsed_ms = (time.perf_counter() - _t0) * 1000
        logger.info(
            "Background enrichment: user=%d session=%s fs_used=%s memory_used=%s %.0fms",
            user_id, session_id, fs_used, memory_used, elapsed_ms,
        )

        # If apply_references returned an unchanged result (no FS/USDA hits or
        # the reference-hint chat failed), it returns the *same* object - we
        # still update conversation in case items got fatsecret_per_100g/
        # usda_per_100g annotations applied.
        nutrition_json = json.dumps(refined.model_dump()) if refined else ""
        conv_json = json.dumps(conversation)
        await update_meal_session(
            db_path, session_id, user_id=user_id,
            nutrition=nutrition_json,
            conversation=conv_json,
        )
    except Exception:
        logger.exception(
            "Background enrichment failed: user=%d session=%s", user_id, session_id,
        )


# Matches orphan "Here's the updated JSON / breakdown / ..." announcer
# sentences that Gemini volunteers before the JSON block. We only strip
# the announcer itself - any useful text Gemini writes after it is
# preserved, so the user doesn't lose follow-up notes.
_JSON_ANNOUNCER_RE = re.compile(
    r"(?im)"
    r"(?:^|(?<=[.!?\n]))"
    r"[ \t]*"
    r"(?:(?:and|also|so|now|then|finally),?\s+)?"
    r"here(?:'s| is|s|\s+are)(?: also)? "
    r"(?:the |an? |your )?"
    # Adjective is optional: without it we only strip JSON-y nouns
    # ("here are the macros" at end of line is almost certainly an
    # announcer); with it any noun in the list is fair game.
    r"(?:"
    r"(?:updated|new|revised|final|corrected|adjusted|refined|current)\s+"
    r"(?:json|breakdown|estimate(?:s)?|nutrition(?:\s+(?:info|breakdown|data))?|"
    r"numbers|values|summary|macros|totals|data|response|object|info)"
    r"|"
    r"(?:json|breakdown|estimate(?:s)?|macros|totals|numbers|values|nutrition(?:\s+(?:info|breakdown|data))?)"
    r")"
    r"(?:\s+(?:object|response|data|below|above|for you|for the meal))?"
    r"[ \t]*[:.\--]*[ \t]*"
    r"(?=\n|$)"
)


def _extract_reply_text(response_text: str) -> str:
    """Extract conversational text from a Gemini response, stripping JSON blocks.

    Gemini typically returns something like:
        "Got it, I've updated the chicken to 200g.\n\n```json\n{...}\n```"
    This function returns just "Got it, I've updated the chicken to 200g."
    If no conversational text exists, returns empty string.

    Also strips orphan "Here's the updated JSON/breakdown" announcer
    sentences - the prompt used to instruct Gemini to always append a
    JSON block, and the model often narrated that instruction back to
    the user. The regex is intentionally scoped to orphan sentences so
    any useful text Gemini writes around/after the announcer survives.
    """
    text = response_text.strip()

    # Strip markdown code blocks (```json ... ```)
    cleaned = re.sub(r"```(?:json)?\s*[\s\S]*?```", "", text).strip()

    # Strip bare JSON objects: find outermost { ... } and remove it
    # This handles responses like "Here are the macros:\n{...}"
    result_parts = []
    i = 0
    while i < len(cleaned):
        if cleaned[i] == "{":
            depth = 0
            for j in range(i, len(cleaned)):
                if cleaned[j] == "{":
                    depth += 1
                elif cleaned[j] == "}":
                    depth -= 1
                    if depth == 0:
                        i = j + 1
                        break
            else:
                # Unbalanced braces - skip from { to end
                i = len(cleaned)
        else:
            result_parts.append(cleaned[i])
            i += 1

    cleaned = "".join(result_parts).strip()

    # Strip orphan announcer phrases Gemini volunteers before the JSON
    # block ("Here's the updated JSON", "Here is the revised breakdown", …).
    cleaned = _JSON_ANNOUNCER_RE.sub("", cleaned)

    # Collapse any blank-line runs the regex may have left behind.
    cleaned = re.sub(r"\n[ \t]*\n[ \t]*(\n[ \t]*)+", "\n\n", cleaned)

    # Strip trailing colons/punctuation artifacts
    cleaned = cleaned.strip().rstrip(":").strip()

    return cleaned


async def note_item_removed(
    session_id: str,
    item_name: str,
    user_id: int,
    db_path: str,
) -> dict:
    """Record a manual item deletion in the session's conversation so
    future Gemini calls stop re-adding the removed item to their
    responses. No Gemini call is made - both the user note and the
    assistant acknowledgement are synthetic, so this is free and does
    not count against the per-meal correction cap.

    Returns:
        {"ok": True} on success, or {"ok": False, "error": str} on failure.
    """
    session = await get_meal_session(db_path, session_id, user_id)
    if not session:
        return {"ok": False, "error": "Session not found"}
    if session.get("status") not in (None, "pending"):
        return {"ok": False, "error": "Session is no longer pending"}

    try:
        conversation = json.loads(session["conversation"])
    except (json.JSONDecodeError, TypeError):
        conversation = []

    # Two-turn synthetic exchange: framing it as user + model means the
    # next real correction sees an unambiguous "this item is gone" signal
    # from BOTH sides of the conversation - reduces the chance Gemini
    # silently re-adds it on a context refresh.
    safe_name = item_name.strip()[:200]
    conversation.append({
        "role": "user",
        "text": (
            f'I have manually removed the item "{safe_name}" from this meal. '
            'Do not include it in any future estimates or item lists.'
        ),
    })
    conversation.append({
        "role": "model",
        "text": (
            f'Understood - "{safe_name}" is no longer part of this meal. '
            "I'll exclude it from any future updates."
        ),
    })

    from src.db import get_db as _get_db
    async with _get_db(db_path) as _db:
        cursor = await _db.execute(
            "UPDATE meal_sessions SET conversation = ?, updated_at = strftime('%Y-%m-%d %H:%M:%S','now') "
            "WHERE session_id = ? AND user_id = ? AND (status = 'pending' OR status IS NULL)",
            (json.dumps(conversation), session_id, user_id),
        )
        await _db.commit()
    if (cursor.rowcount or 0) == 0:
        return {"ok": False, "error": "already_accepted"}
    return {"ok": True}


async def send_correction(
    session_id: str,
    user_text: str,
    user_id: int,
    db_path: str,
    images: list[bytes] | None = None,
) -> dict:
    """Send a correction to an existing session. Returns updated session info.

    Returns:
        {
            "nutrition": NutritionResult | None,
            "reply_text": str,
            "error": str | None,
        }
    """
    session = await get_meal_session(db_path, session_id, user_id)
    if not session:
        return {"nutrition": None, "reply_text": "", "error": "Session not found"}
    if session.get("status") not in (None, "pending"):
        return {"nutrition": None, "reply_text": "", "error": "Session is no longer pending"}

    conversation = json.loads(session["conversation"])
    conversation.append({"role": "user", "text": user_text})

    session_images = images or []
    _t0 = time.perf_counter()
    try:
        response_text = await gemini_chat(
            session_images, conversation, db_path=db_path, user_id=user_id, call_type="correction",
        )
        _elapsed = (time.perf_counter() - _t0) * 1000
        logger.info("Correction Gemini call: session=%s, %.0fms", session_id, _elapsed)
    except Exception as e:
        _elapsed = (time.perf_counter() - _t0) * 1000
        logger.exception("Correction Gemini failed: session=%s, %.0fms", session_id, _elapsed)
        conversation.pop()
        return {"nutrition": None, "reply_text": "", "error": f"Chat failed: {e}"}

    conversation.append({"role": "model", "text": response_text})
    updated_nutrition = _response_to_nutrition_result(response_text, source="Gemini")

    # If Gemini replied conversationally without JSON, send a follow-up to extract
    # structured nutrition. This handles the common case where Gemini says "I've
    # updated your meal..." but doesn't include the JSON block.
    if not updated_nutrition:
        followup = (
            "Now output ONLY the updated JSON nutrition object for the entire meal "
            "with all corrections applied. No extra text, just the JSON."
        )
        conversation.append({"role": "user", "text": followup})
        try:
            _t0 = time.perf_counter()
            json_response = await gemini_chat(
                session_images, conversation, db_path=db_path, user_id=user_id, call_type="correction_json",
            )
            _elapsed = (time.perf_counter() - _t0) * 1000
            updated_nutrition = _response_to_nutrition_result(json_response, source="Gemini")
            if updated_nutrition:
                logger.info("Correction JSON follow-up succeeded: session=%s, %.0fms", session_id, _elapsed)
                # Replace the follow-up user turn with the model's JSON response
                # so multi-turn corrections see the corrected state
                conversation.pop()  # remove user follow-up
                conversation.append({"role": "model", "text": json_response})
            else:
                logger.warning("Correction JSON follow-up returned no JSON: session=%s, %.0fms", session_id, _elapsed)
                conversation.pop()  # remove user follow-up
        except Exception:
            logger.exception("Correction JSON follow-up failed for session %s", session_id)
            conversation.pop()  # remove user follow-up

    nutrition_json = json.dumps(updated_nutrition.model_dump()) if updated_nutrition else session["nutrition"]
    # Race guard: if accept_meal fired while we were calling Gemini, the
    # session is now 'accepted' and the meal_log already carries the pre-
    # correction numbers. Writing here would corrupt the audit trail and
    # not fix the user's logged meal anyway. Skip the write - the caller
    # sees the corrected nutrition in the response but knows nothing was
    # persisted.
    from src.db import get_db as _get_db
    async with _get_db(db_path) as _db:
        cursor = await _db.execute(
            "UPDATE meal_sessions SET conversation = ?, nutrition = ?, updated_at = strftime('%Y-%m-%d %H:%M:%S','now') "
            "WHERE session_id = ? AND user_id = ? AND (status = 'pending' OR status IS NULL)",
            (json.dumps(conversation),
             nutrition_json if isinstance(nutrition_json, str) else json.dumps(nutrition_json),
             session_id, user_id),
        )
        await _db.commit()
    if (cursor.rowcount or 0) == 0:
        logger.info("send_correction: session %s already accepted - correction discarded", session_id)
        return {
            "nutrition": None,
            "reply_text": "This meal was already accepted - start a new log to edit.",
            "error": "already_accepted",
        }

    # Extract just the conversational reply from the ORIGINAL response, stripping JSON
    reply_text = _extract_reply_text(response_text)
    if not reply_text and updated_nutrition:
        reply_text = "Got it, I've updated the nutrition info."

    # If still no parsed nutrition, fall back to the session's existing nutrition
    result_nutrition = updated_nutrition
    if not result_nutrition and session["nutrition"]:
        try:
            result_nutrition = NutritionResult(**json.loads(session["nutrition"]))
        except Exception:
            pass

    # Extract follow-up questions from Gemini's response
    correction_questions: list[str] = []
    try:
        raw_json = _extract_json(response_text)
        correction_questions = raw_json.get("questions") or []
    except Exception:
        pass

    return {
        "nutrition": result_nutrition,
        "reply_text": reply_text,
        "error": None,
        "questions": correction_questions,
    }


# Barcode correction threshold: how much any single macro must differ from
# the original backend response before we save the user's correction. 5% is
# below the "I rounded it" noise floor but above typical editing behavior.
_BARCODE_CORRECTION_THRESHOLD = 0.05


def _macros_differ(a: dict, b: dict, threshold: float) -> bool:
    """Return True if any macro in a differs from b by more than threshold (fraction).

    Zero/small values use an absolute tolerance of 1 to avoid divide-by-zero
    noise on truly empty products (black coffee, etc).
    """
    for key in ("calories", "protein", "carbs", "fat"):
        av = float(a.get(key, 0) or 0)
        bv = float(b.get(key, 0) or 0)
        if max(av, bv) < 1.0:
            continue  # both near-zero - treat as same
        denom = max(abs(bv), 1.0)
        if abs(av - bv) / denom > threshold:
            return True
    return False


async def _maybe_save_barcode_correction(
    db_path: str,
    user_id: int,
    session: dict,
    final_nutrition: "NutritionResult",
    servings: float,
) -> None:
    """If this accepted meal came from a barcode and the user edited it
    meaningfully, save a per-user correction for future scans of the same
    barcode. Best-effort - any failure is logged and swallowed.

    Scope limits:
    - Session must have a non-empty barcode and original_nutrition
    - Final nutrition must still be a single item (multi-item meals are
      compositions, not corrections)
    - Edit must exceed _BARCODE_CORRECTION_THRESHOLD on at least one macro
    """
    try:
        barcode = (session.get("barcode") or "").strip()
        original_json = session.get("original_nutrition") or ""
        if not barcode or not original_json:
            return

        # Don't save corrections on multi-item meals - those aren't
        # "corrections of the product", they're compositions.
        if len(final_nutrition.items) != 1:
            return

        try:
            original = json.loads(original_json)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Barcode correction: malformed original_nutrition for %s", barcode)
            return

        # Back out per-serving from the final nutrition the user logged.
        # UI sends scaled values (servings × per_serving); divide to get 1x.
        if servings <= 0:
            servings = 1.0
        final_per_serving = {
            "calories": round(final_nutrition.calories / servings, 1),
            "protein": round(final_nutrition.protein / servings, 1),
            "carbs": round(final_nutrition.carbs / servings, 1),
            "fat": round(final_nutrition.fat / servings, 1),
        }

        original_macros = {
            "calories": float(original.get("calories", 0) or 0),
            "protein": float(original.get("protein", 0) or 0),
            "carbs": float(original.get("carbs", 0) or 0),
            "fat": float(original.get("fat", 0) or 0),
        }

        if not _macros_differ(final_per_serving, original_macros, _BARCODE_CORRECTION_THRESHOLD):
            return  # User didn't edit enough to be considered a correction

        # Build product_name/brand/serving_label from the final item so we
        # remember whatever the user ended up calling the product.
        item = final_nutrition.items[0]
        item_weight = float(item.weight_g or 0) or None
        serving_size_g = (item_weight / servings) if item_weight and servings > 0 else None
        # Inherit the unit (g/ml) from the original barcode scan - item.weight_g
        # numerically stores the serving amount regardless of unit.
        serving_unit = str(original.get("serving_size_unit") or "g").lower()
        if serving_unit not in {"g", "ml"}:
            serving_unit = "g"

        await save_barcode_correction(
            db_path=db_path,
            user_id=user_id,
            barcode=barcode,
            product_name=(item.name or final_nutrition.item_name or "")[:200],
            brand=(item.brand or "")[:100],
            calories=final_per_serving["calories"],
            protein=final_per_serving["protein"],
            carbs=final_per_serving["carbs"],
            fat=final_per_serving["fat"],
            serving_size_g=round(serving_size_g, 1) if serving_size_g else None,
            serving_label=(final_nutrition.meal_description or "")[:100],
            serving_size_unit=serving_unit,
        )
        logger.info(
            "Barcode correction saved: user=%s barcode=%s cal=%s→%s (servings=%s)",
            user_id, barcode,
            round(original_macros["calories"], 1), final_per_serving["calories"], servings,
        )
    except Exception:
        logger.exception("_maybe_save_barcode_correction failed (non-fatal)")


async def accept_meal(
    user_id: int,
    session_id: str,
    db_path: str,
    tz_str: str | None = None,
    image_paths: list[str] | None = None,
    logged_at_override: str | None = None,
    servings: float = 1.0,
) -> dict:
    """Accept and log a meal from a session.

    Returns:
        {
            "meal_log_id": int | None,
            "nutrition": NutritionResult,
            "progress": dict,
            "error": str | None,
        }
    """
    # Re-read the session row so any background reference enrichment that completed since analyze is picked up; if the user accepts before enrichment lands we use the unrefined estimate currently in the row (intentional - waiting would defeat the optimization).
    session = await get_meal_session(db_path, session_id, user_id)
    if not session:
        return {"meal_log_id": None, "nutrition": None, "progress": {}, "error": "Session not found"}
    # Atomically claim the session: moves pending → accepted in one SQL
    # statement. If two concurrent accepts hit the same session, only one
    # wins the rowcount check - the other gets "already processed" without
    # double-logging.
    from src.db import try_claim_meal_session
    if not await try_claim_meal_session(db_path, session_id, user_id):
        return {"meal_log_id": None, "nutrition": None, "progress": {}, "error": "Session already processed"}

    nutrition_data = json.loads(session["nutrition"]) if session["nutrition"] else None
    if not nutrition_data:
        return {"meal_log_id": None, "nutrition": None, "progress": {}, "error": "No nutrition data"}

    result = NutritionResult(**nutrition_data)
    if logged_at_override:
        logged_at_str = logged_at_override
        try:
            hour = int(logged_at_override.split(" ")[1].split(":")[0])
        except Exception:
            tz = get_user_tz(tz_str)
            hour = datetime.now(tz).hour
        today_date_str = logged_at_str.split(" ")[0]
    else:
        tz = get_user_tz(tz_str)
        now = datetime.now(tz)
        logged_at_str = now.strftime("%Y-%m-%d %H:%M")
        hour = now.hour
        today_date_str = now.strftime("%Y-%m-%d")
    items_json_str = json.dumps([i.model_dump() for i in result.items]) if result.items else "[]"
    meal_type = session.get("meal_type") or classify_meal_time(hour)
    image_path = image_paths[0] if image_paths else ""

    # Detect first-meal moment BEFORE inserting - if the user has no meals
    # yet, this accept is their activation event. Cheap at our scale (SQLite
    # + indexed user_id column) and only runs on accept, not analyze.
    is_first_meal = (await count_user_meals(db_path, user_id)) == 0

    # Snapshot what we'd need to reproduce this analysis later - the session
    # row gets marked "accepted" right after log_meal, so capture it now.
    # Stored as JSON on meal_logs. Image is referenced by path only (never
    # copied) so this stays cheap.
    session_barcode = session.get("barcode") or ""
    if session_barcode:
        snapshot_mode = "barcode"
    elif image_paths and result.meal_description:
        snapshot_mode = "combined"
    elif image_paths:
        snapshot_mode = "image"
    else:
        snapshot_mode = "text"
    try:
        conversation_snapshot = json.loads(session.get("conversation") or "[]")
    except Exception:
        conversation_snapshot = []
    try:
        original_nutrition_snapshot = (
            json.loads(session["original_nutrition"])
            if session.get("original_nutrition") else None
        )
    except Exception:
        original_nutrition_snapshot = None
    analysis_snapshot_json = json.dumps({
        "session_id": session.get("session_id", ""),
        "mode": snapshot_mode,
        "image_path": image_path,
        "barcode": session_barcode,
        "meal_type": meal_type,
        "session_created_at": session.get("created_at", ""),
        "conversation": conversation_snapshot,
        "original_nutrition": original_nutrition_snapshot,
        "final_nutrition": result.model_dump(),
    })

    # Log to SQLite
    meal_log_id = None
    try:
        meal_log_id = await db_log_meal(
            db_path=db_path,
            user_id=user_id,
            logged_at=logged_at_str,
            item_name=result.item_name,
            meal_description=result.meal_description or "",
            calories=result.calories,
            protein=result.protein,
            carbs=result.carbs,
            fat=result.fat,
            source=result.source,
            meal_type=meal_type,
            items_json=items_json_str,
            image_path=image_path,
            analysis_snapshot_json=analysis_snapshot_json,
            user_input=session.get("user_input") or "",
        )
    except Exception:
        logger.exception("DB log_meal failed")
        return {"meal_log_id": None, "nutrition": result, "progress": {}, "error": "Failed to log meal"}

    # Log FatSecret comparison (best-effort)
    if result.items:
        try:
            await log_fatsecret_comparison(
                db_path=db_path, user_id=user_id, meal_log_id=meal_log_id,
                items=result.items, logged_at=logged_at_str,
            )
        except Exception:
            logger.exception("log_fatsecret_comparison failed (non-fatal)")

    # Barcode correction memory: if this session came from a barcode scan and
    # the user meaningfully edited any of the macros, remember their values so
    # future scans of the same barcode skip the OFF/FatSecret lookup and use
    # the user's previous correction instead.
    await _maybe_save_barcode_correction(
        db_path=db_path,
        user_id=user_id,
        session=session,
        final_nutrition=result,
        servings=servings,
    )

    # Status was already transitioned to 'accepted' atomically at the
    # top of this function via try_claim_meal_session - no second write
    # needed here. Leaving the comment as a breadcrumb for future readers.

    # Private telemetry: one meal_accept row per successfully logged meal.
    # The source field distinguishes origin ("Gemini" / "barcode" / "alias" / "edited").
    # is_first_meal flags the activation event (user's very first accepted meal).
    await log_event(
        db_path, user_id, "meal_accept",
        metadata={
            "source": result.source,
            "has_image": bool(image_paths),
            "meal_type": meal_type,
            "is_first_meal": is_first_meal,
        },
    )

    # Get progress
    today_str = today_date_str
    progress = await get_progress(user_id, db_path, today_str)

    return {
        "meal_log_id": meal_log_id,
        "nutrition": result,
        "progress": progress,
        "error": None,
    }


async def get_progress(user_id: int, db_path: str, today_str: str) -> dict:
    """Get today's progress vs targets, with optional exercise calorie adjustment.

    Returns:
        {
            "totals": {calories, protein, carbs, fat, meal_count},
            "target": {calories, protein, carbs, fat} | None,
            "adjusted_target": {calories, protein, carbs, fat} | None,
            "exercise_adjustment": {calories, protein, source} | None,
            "remaining": {calories, protein, carbs, fat} | None,
        }
    """
    import asyncio
    from src.db import get_workouts_for_date

    # Auto-sync Fitbit + Oura data in parallel with initial queries so it's fresh
    # by the time we read it. Adds no latency when data is already recent.
    async def _fitbit_sync() -> None:
        try:
            from src.web.routes.fitbit import auto_sync_fitbit_today
            await auto_sync_fitbit_today(db_path, user_id, today_str)
        except Exception:
            pass

    async def _oura_sync() -> None:
        try:
            from src.web.routes.oura import auto_sync_oura_today
            await auto_sync_oura_today(db_path, user_id, today_str)
        except Exception:
            pass

    totals, target, prefs, _, _ = await asyncio.gather(
        get_today_totals(db_path, user_id, today_str),
        get_user_target(db_path, user_id),
        get_user_prefs(db_path, user_id),
        _fitbit_sync(),
        _oura_sync(),
    )

    adjusted_target = None
    exercise_adjustment = None

    if target and prefs.get("exercise_adjustment_on", 1):
        from src.workout_dedup import dedup_fitbit_vs_strava

        workouts = await get_workouts_for_date(db_path, user_id, today_str)
        # Dedup: Strava + Fitbit often capture the same session (a run
        # Strava logged also gets auto-detected by Fitbit). Without dedup
        # the same workout is counted twice in the eat-back adjustment.
        workouts = dedup_fitbit_vs_strava(workouts)

        # Eat-back adjustment only counts LOGGED WORKOUTS (Strava, Fitbit
        # individual activity logs, manual entries) - not the Fitbit/Oura
        # daily summary's above-BMR number. Reason: the user's daily target
        # already includes NEAT / baseline daily movement via the Mifflin-St
        # Jeor activity multiplier (src/web/routes/targets.py). Adding the
        # full caloriesOut - caloriesBMR on top would double-count NEAT,
        # inflating the target by hundreds of kcal on a normal walking day.
        active_calories = sum(w.get("calories_burned", 0) for w in workouts)

        if active_calories > 0:
            eat_back_pct = prefs.get("exercise_eat_back_pct", 0.75)
            cal_adjustment = round(active_calories * eat_back_pct)

            # Small protein bump: 25% of adjustment calories as protein (4 cal/g)
            protein_adjustment = round((cal_adjustment * 0.25) / 4)

            adjusted_target = {
                "calories": target["calories"] + cal_adjustment,
                "protein": target["protein"] + protein_adjustment,
                "carbs": target["carbs"],
                "fat": target["fat"],
            }

            exercise_adjustment = {
                "calories": cal_adjustment,
                "protein": protein_adjustment,
                "active_calories": active_calories,
            }

    # Remaining is computed against adjusted_target when available
    effective_target = adjusted_target or target
    remaining = None
    if effective_target:
        remaining = {
            "calories": effective_target["calories"] - totals["calories"],
            "protein": effective_target["protein"] - totals["protein"],
            "carbs": effective_target["carbs"] - totals["carbs"],
            "fat": effective_target["fat"] - totals["fat"],
        }

    return {
        "totals": totals,
        "target": target,
        "adjusted_target": adjusted_target,
        "exercise_adjustment": exercise_adjustment,
        "remaining": remaining,
    }


async def create_session_from_meal(
    meal_id: int,
    user_id: int,
    db_path: str,
) -> dict:
    """Create a new analysis session from an existing logged meal.

    This allows 'Edit with AI' on already-logged meals by seeding a session
    with the meal's existing nutrition data so the user can send corrections.

    Returns:
        {
            "session_id": str,
            "nutrition": NutritionResult | None,
            "meal_type": str,
            "error": str | None,
        }
    """
    from src.db import get_meal_by_id

    meal = await get_meal_by_id(db_path, user_id, meal_id)
    if not meal:
        return {"session_id": "", "nutrition": None, "meal_type": "", "error": "Meal not found"}

    # Reconstruct NutritionResult from meal data
    try:
        items_list = json.loads(meal.get("items_json") or "[]")
        from src.models import FoodItem
        items = [FoodItem(**item) for item in items_list]
    except Exception:
        items = []

    result = NutritionResult(
        item_name=meal["item_name"],
        meal_description=meal.get("meal_description", ""),
        items=items,
        calories=meal["calories"],
        protein=meal["protein"],
        carbs=meal["carbs"],
        fat=meal["fat"],
        source="edited",
    )

    # Build a synthetic conversation so send_correction can continue from here
    nutrition_json = json.dumps(result.model_dump())
    conversation = [
        {"role": "user", "text": f"Previously logged meal: {meal['item_name']}. The user wants to edit this meal."},
        {"role": "model", "text": nutrition_json},
    ]

    session_id = str(uuid.uuid4())
    meal_type = meal.get("meal_type", "meal")

    await create_meal_session(
        db_path, session_id, user_id,
        images_json=json.dumps([]),
        conversation=json.dumps(conversation),
        nutrition=nutrition_json,
        meal_type=meal_type,
    )

    return {
        "session_id": session_id,
        "nutrition": result,
        "meal_type": meal_type,
        "error": None,
    }


async def create_session_from_alias(
    alias: dict,
    user_id: int,
    db_path: str,
) -> dict:
    """Create a new analysis session from a saved meal (alias) for AI editing.

    Returns the same shape as create_session_from_meal.
    """
    try:
        items_list = json.loads(alias.get("items_json") or "[]")
        from src.models import FoodItem
        items = [FoodItem(**item) for item in items_list]
    except Exception:
        items = []

    result = NutritionResult(
        item_name=alias["item_name"],
        meal_description=alias.get("meal_description", ""),
        items=items,
        calories=alias["calories"],
        protein=alias["protein"],
        carbs=alias["carbs"],
        fat=alias["fat"],
        source="edited",
    )

    nutrition_json = json.dumps(result.model_dump())
    conversation = [
        {"role": "user", "text": f"Saved meal: {alias['item_name']}. The user wants to edit this meal."},
        {"role": "model", "text": nutrition_json},
    ]

    session_id = str(uuid.uuid4())

    await create_meal_session(
        db_path, session_id, user_id,
        images_json=json.dumps([]),
        conversation=json.dumps(conversation),
        nutrition=nutrition_json,
        meal_type="meal",
    )

    return {
        "session_id": session_id,
        "nutrition": result,
        "error": None,
    }


async def get_trend_data(
    user_id: int,
    db_path: str,
    num_days: int = 7,
    today_str: str | None = None,
    target: dict | None = None,
    prefs: dict | None = None,
) -> list[dict]:
    """Return N-day daily data for charts, with per-day effective target calories."""
    import aiosqlite
    from src.db import get_user_prefs
    from src.db_pool import get_db

    days = await get_daily_totals_7days(db_path, user_id, num_days=num_days, today_str=today_str)

    if not target or not days:
        return days

    # Check if exercise adjustment is enabled
    if prefs is None:
        prefs = await get_user_prefs(db_path, user_id)
    if not prefs.get("exercise_adjustment_on", 1):
        return days

    eat_back_pct = prefs.get("exercise_eat_back_pct", 0.75)
    date_range = [d["date"] for d in days]
    start_date, end_date = date_range[0], date_range[-1]

    # Batch-fetch workout, fitbit, and oura calories for the date range
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        workout_rows = await (await db.execute(
            """SELECT substr(logged_at, 1, 10) AS d, SUM(calories_burned) AS cals
               FROM workout_logs WHERE user_id = ? AND logged_at >= ? AND logged_at <= ?
               GROUP BY d""",
            (user_id, start_date, end_date + "T23:59:59"),
        )).fetchall()
        fitbit_rows = await (await db.execute(
            """SELECT date AS d, activity_calories AS cals
               FROM fitbit_activity WHERE user_id = ? AND date >= ? AND date <= ?""",
            (user_id, start_date, end_date),
        )).fetchall()
        oura_rows = await (await db.execute(
            """SELECT date AS d, activity_calories AS cals
               FROM oura_activity WHERE user_id = ? AND date >= ? AND date <= ?""",
            (user_id, start_date, end_date),
        )).fetchall()

    workout_by_date = {r["d"]: float(r["cals"] or 0) for r in workout_rows}
    fitbit_by_date = {r["d"]: float(r["cals"] or 0) for r in fitbit_rows}
    oura_by_date = {r["d"]: float(r["cals"] or 0) for r in oura_rows}
    base_cal = target["calories"]

    for day in days:
        d = day["date"]
        active = max(
            workout_by_date.get(d, 0),
            fitbit_by_date.get(d, 0),
            oura_by_date.get(d, 0),
        )
        if active > 0:
            day["effective_target_calories"] = base_cal + round(active * eat_back_pct)
        else:
            day["effective_target_calories"] = base_cal

    return days
