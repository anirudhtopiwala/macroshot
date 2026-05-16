"""Meal-name embeddings for semantic lookup.

Uses Gemini ``gemini-embedding-001`` with Matryoshka-truncated 768-dim output.
Embeddings are persisted as float32 little-endian BLOBs in
``meal_logs.name_embedding`` (added in schema v15).

The runtime path is fire-and-forget: ``schedule_embed_for_meal`` returns
immediately and runs the API call + UPDATE in a detached asyncio task so a
slow or failing embedding never blocks the user-facing log path. The offline
backfill script (``scripts/backfill_meal_embeddings.py``) handles existing
rows and any rows the runtime hook missed.
"""

from __future__ import annotations

import asyncio
import logging
import os

import aiosqlite
import numpy as np
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

EMBED_MODEL = "gemini-embedding-001"
EMBED_DIM = 768  # Matryoshka-truncated; gemini-embedding-001 supports 768/1536/3072
_DOC_TASK = "RETRIEVAL_DOCUMENT"
_QUERY_TASK = "RETRIEVAL_QUERY"


def _meal_text(item_name: str | None, meal_description: str | None) -> str:
    """Canonical text used for both embed-on-log and backfill — keep in sync."""
    name = (item_name or "").strip()
    desc = (meal_description or "").strip()
    if name and desc:
        return f"{name}. {desc}"
    return name or desc


def to_blob(vec) -> bytes:
    """Serialize an embedding vector to a compact float32 LE blob."""
    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1 or arr.size == 0:
        raise ValueError(f"Expected non-empty 1-D vector, got shape {arr.shape}")
    return arr.tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


async def _embed_once(text: str, task_type: str) -> list[float] | None:
    """Single embed call. Returns None on missing key or API failure."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set; skipping embedding")
        return None
    if not text.strip():
        return None
    try:
        async with genai.Client(api_key=api_key).aio as aclient:
            res = await aclient.models.embed_content(
                model=EMBED_MODEL,
                contents=[text],
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=EMBED_DIM,
                ),
            )
        if not res.embeddings:
            return None
        return list(res.embeddings[0].values)
    except Exception as exc:
        logger.warning("Embedding API call failed: %s", exc)
        return None


async def embed_document(text: str) -> list[float] | None:
    """Embed text for storage (meal-side)."""
    return await _embed_once(text, _DOC_TASK)


async def embed_query(text: str) -> list[float] | None:
    """Embed text for retrieval (chat-side)."""
    return await _embed_once(text, _QUERY_TASK)


async def _embed_and_save(db_path: str, meal_log_id: int, text: str) -> None:
    vec = await embed_document(text)
    if vec is None:
        return
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE meal_logs SET name_embedding = ? WHERE id = ?",
                (to_blob(vec), meal_log_id),
            )
            await db.commit()
    except Exception:
        logger.exception("Failed to persist embedding for meal_log_id=%d", meal_log_id)


def schedule_embed_for_meal(
    db_path: str,
    meal_log_id: int,
    item_name: str | None,
    meal_description: str | None,
) -> None:
    """Fire-and-forget: embed the meal text and write it back. Never raises."""
    text = _meal_text(item_name, meal_description)
    if not text or meal_log_id is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Called from sync context (e.g., backfill script). Caller should await
        # _embed_and_save directly instead.
        logger.warning("schedule_embed_for_meal called without a running loop")
        return
    task = loop.create_task(_embed_and_save(db_path, meal_log_id, text))
    # Swallow exceptions so the unawaited task doesn't surface as a warning;
    # _embed_and_save already logs.
    task.add_done_callback(lambda t: t.exception())


async def _embed_and_save_memory(db_path: str, memory_id: int, text: str) -> None:
    vec = await embed_document(text)
    if vec is None:
        # API failure or missing key. embed_document already logged. Telemeter
        # so we can quantify how often coach memories ship without an embedding
        # (matters for V2 retrieval recall and for picking the backfill window).
        await _log_embed_failure(db_path, memory_id, "embed_returned_none")
        return
    try:
        async with aiosqlite.connect(db_path) as db:
            # Stale-write guard: if the row's text changed while the
            # embedding API call was in flight (concurrent edit), don't
            # overwrite a fresher vector with one computed for the old
            # text. The `AND text = ?` clause makes the write a no-op
            # in that case; the second edit's task will commit the
            # correct embedding.
            await db.execute(
                "UPDATE user_memories SET text_embedding = ? "
                "WHERE id = ? AND text = ?",
                (to_blob(vec), memory_id, text),
            )
            await db.commit()
    except Exception as exc:
        logger.exception("Failed to persist embedding for user_memory id=%d", memory_id)
        await _log_embed_failure(db_path, memory_id, f"persist_failed: {type(exc).__name__}")


async def _log_embed_failure(db_path: str, memory_id: int, reason: str) -> None:
    """Emit a memory_embed_failed user_event without ever raising into the
    async embedding task. Best-effort - if telemetry write itself fails, we
    swallow (logger.exception above already covered the embed itself)."""
    try:
        # Look up the owning user so the event is correctly scoped. Without
        # this, the event is unattributable.
        async with aiosqlite.connect(db_path) as db:
            row = await (await db.execute(
                "SELECT user_id FROM user_memories WHERE id = ?", (memory_id,),
            )).fetchone()
        if not row:
            return
        from src.db import log_event
        await log_event(
            db_path, row[0], "memory_embed_failed",
            metadata={"memory_id": memory_id, "reason": reason[:120]},
        )
    except Exception:
        logger.warning("memory_embed_failed telemetry write failed for id=%d", memory_id)


def schedule_embed_for_memory(db_path: str, memory_id: int, text: str | None) -> None:
    """Fire-and-forget: embed a coach memory's text and write it back. Never raises.

    V1 doesn't read these embeddings (seed context injects all memories
    directly), but writing them now avoids a backfill migration when V2
    adds retrieval-time cosine ranking once a user crosses ~80 memories.
    """
    text = (text or "").strip()
    if not text or memory_id is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("schedule_embed_for_memory called without a running loop")
        return
    task = loop.create_task(_embed_and_save_memory(db_path, memory_id, text))
    task.add_done_callback(lambda t: t.exception())


def cosine_top_k(query: np.ndarray, candidates: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (indices, scores) of the top-k cosine matches.

    ``candidates`` is shape (N, D). Both inputs are float32. Vectors with
    zero norm score 0. Indices are returned highest-score first.
    """
    if candidates.ndim != 2 or candidates.shape[0] == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)
    q_norm = float(np.linalg.norm(query))
    if q_norm == 0.0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float32)
    c_norms = np.linalg.norm(candidates, axis=1)
    safe = c_norms > 0
    scores = np.zeros(candidates.shape[0], dtype=np.float32)
    if safe.any():
        scores[safe] = (candidates[safe] @ query) / (c_norms[safe] * q_norm)
    k = min(k, scores.size)
    top = np.argpartition(-scores, k - 1)[:k]
    top = top[np.argsort(-scores[top])]
    return top, scores[top]
