"""Coach memory API routes.

These endpoints back the /settings/memory page where users review and
manage the durable facts the AI coach remembers across chats. Memory
writes also flow through the chat /confirm-action path when the coach
proposes a fact via remember_fact / forget_fact tools — that path uses
the same DB helpers as this router.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from src.db import (
    USER_MEMORY_KINDS,
    add_user_memory,
    delete_user_memory,
    get_user_memories,
    get_user_memory_by_id,
    log_event,
    update_user_memory,
)
# MAX_USER_MEMORIES + count_user_memories are lazy-imported inside
# create_memory so a runtime monkeypatch on src.db.MAX_USER_MEMORIES
# (used in tests, and a future env-tunable) propagates here too. Top-
# level import would bind the name at module load and ignore the patch.
from src.embeddings import schedule_embed_for_memory
from src.web.deps import CurrentUser, DbPath
from src.web.rate_limit import limiter

logger = logging.getLogger("macro_app")

router = APIRouter(prefix="/memory", tags=["memory"])


_VALID_KINDS = tuple(sorted(USER_MEMORY_KINDS))


def _validate_kind(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip().lower()
    if v not in USER_MEMORY_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(_VALID_KINDS)}")
    return v


def _validate_text(v: str | None) -> str | None:
    """Strip first, then enforce length. Without the strip a 200-char
    whitespace-only payload would pass the model-level min_length=1 check
    and crash add_user_memory's empty guard with a 500."""
    if v is None:
        return None
    v = v.strip()
    if not v:
        raise ValueError("text cannot be empty or whitespace-only")
    if len(v) > 200:
        raise ValueError("text must be <= 200 characters")
    return v


class MemoryCreateRequest(BaseModel):
    kind: str
    text: str

    @field_validator("kind")
    @classmethod
    def _norm_kind(cls, v: str) -> str:
        return _validate_kind(v)  # never None on create

    @field_validator("text")
    @classmethod
    def _norm_text(cls, v: str) -> str:
        return _validate_text(v)  # never None on create


class MemoryUpdateRequest(BaseModel):
    kind: str | None = None
    text: str | None = None

    @field_validator("kind")
    @classmethod
    def _norm_kind(cls, v: str | None) -> str | None:
        return _validate_kind(v)

    @field_validator("text")
    @classmethod
    def _norm_text(cls, v: str | None) -> str | None:
        return _validate_text(v)


@router.get("")
async def list_memories(user: CurrentUser, db_path: DbPath):
    """Return all memories for the current user, grouped by kind by the client."""
    rows = await get_user_memories(db_path, user["user_id"])
    return {"entries": rows}


@router.post("")
@limiter.limit("30/minute")
async def create_memory(
    request: Request, body: MemoryCreateRequest, user: CurrentUser, db_path: DbPath,
):
    """Create a memory directly from the Memory settings page (source='user')."""
    from src.db import MAX_USER_MEMORIES, count_user_memories
    if await count_user_memories(db_path, user["user_id"]) >= MAX_USER_MEMORIES:
        await log_event(
            db_path, user["user_id"], "memory_cap_hit",
            metadata={"surface": "rest", "cap": MAX_USER_MEMORIES},
        )
        raise HTTPException(
            status_code=409,
            detail=f"Memory storage is full (max {MAX_USER_MEMORIES}). Delete an older memory before saving a new one.",
        )
    memory_id = await add_user_memory(
        db_path, user["user_id"], body.kind, body.text, source="user",
    )
    schedule_embed_for_memory(db_path, memory_id, body.text)
    await log_event(
        db_path, user["user_id"], "memory_create",
        metadata={"kind": body.kind, "source": "user"},
    )
    return {"id": memory_id, "kind": body.kind, "text": body.text}


@router.put("/{memory_id}")
@limiter.limit("30/minute")
async def edit_memory(
    request: Request, memory_id: int, body: MemoryUpdateRequest,
    user: CurrentUser, db_path: DbPath,
):
    """Edit a memory's kind or text. Re-embeds when text changes."""
    fields: dict = {}
    if body.kind is not None:
        fields["kind"] = body.kind
    if body.text is not None:
        fields["text"] = body.text
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    existing = await get_user_memory_by_id(db_path, user["user_id"], memory_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Memory not found")

    changed = await update_user_memory(db_path, user["user_id"], memory_id, **fields)
    if not changed:
        raise HTTPException(status_code=404, detail="Memory not found")

    if "text" in fields and fields["text"] != existing.get("text"):
        schedule_embed_for_memory(db_path, memory_id, fields["text"])

    await log_event(
        db_path, user["user_id"], "memory_update",
        metadata={"memory_id": memory_id, "fields": list(fields.keys())},
    )
    return {"ok": True, "id": memory_id}


@router.delete("/{memory_id}")
@limiter.limit("30/minute")
async def remove_memory(
    request: Request, memory_id: int, user: CurrentUser, db_path: DbPath,
):
    """Delete a memory."""
    deleted = await delete_user_memory(db_path, user["user_id"], memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    await log_event(
        db_path, user["user_id"], "memory_delete",
        metadata={"memory_id": memory_id},
    )
    return {"ok": True}
