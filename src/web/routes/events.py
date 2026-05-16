"""Frontend event ingestion route: accepts batched UI events from the SPA.

All events coming through this endpoint carry a `ui_` prefix in their
event_type so they are distinguishable from server-side events in the
user_events table. The allowlist below hard-caps which event names the
frontend may emit - a rogue client cannot inject arbitrary event types.

Design notes:
 - Rate-limited to 60/minute per user: one batch per second is generous
   for a well-behaved client (we flush every 10s and on visibilitychange).
 - Max batch size 50 entries so a single oversized POST can't bloat the
   DB in one shot.
 - Per-event metadata is accepted as a small dict; oversized metadata is
   clipped to keep telemetry rows cheap.
 - All writes go through `log_event` which is non-blocking and swallows
   errors - telemetry must never break the UX.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.db import log_event
from src.web.deps import CurrentUser, DbPath
from src.web.rate_limit import limiter

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/events", tags=["events"])


# Allowlist of event names the frontend is permitted to emit.
# Keep this tight - every new UI event must be added here on purpose.
# Unknown events are dropped silently (no error) so the client doesn't
# need to know which events the server understands for a given version.
_ALLOWED_UI_EVENTS = {
    # Page views (one per unique page mount)
    "ui_page_view",
    # Primary navigation / feature interactions
    "ui_trend_range_selected",     # 7/30/90 on Trends
    "ui_journal_filter_applied",   # meal_type / date filter on Journal
    "ui_fab_action",               # which action chosen from the FAB menu
    "ui_upgrade_cta_clicked",      # user tapped an upgrade/trial button
    "ui_limit_hit_dismissed",      # limit-hit modal dismissed vs. upgraded
    "ui_meal_input_mode",          # photo / text / combined / barcode / alias
    "ui_correction_sent",          # user sent a correction turn
    "ui_chat_opened",              # user navigated into the Chat page
    "ui_onboarding_step",          # onboarding step completed
    "ui_theme_toggled",            # light/dark toggle
    "ui_install_prompt_shown",     # PWA install prompt shown
    "ui_install_prompt_response",  # accepted / dismissed
    "ui_client_error",             # uncaught JS error / unhandled promise rejection
}

_MAX_BATCH = 50
_MAX_METADATA_KEYS = 12
_MAX_METADATA_STR_LEN = 200


class EventIn(BaseModel):
    """One buffered event from the frontend dispatcher."""
    event_type: str = Field(..., min_length=1, max_length=64)
    metadata: dict | None = None


class EventBatchRequest(BaseModel):
    events: list[EventIn] = Field(..., max_length=_MAX_BATCH)


def _sanitize_metadata(raw: dict | None) -> dict | None:
    """Clip metadata so one hostile client can't bloat user_events.

    - Drops non-serializable values
    - Caps number of keys
    - Truncates long strings
    - Refuses nested structures larger than 1 level (flattened to strings)
    """
    if not raw:
        return None
    out: dict = {}
    for i, (k, v) in enumerate(raw.items()):
        if i >= _MAX_METADATA_KEYS:
            break
        if not isinstance(k, str):
            continue
        k = k[:32]
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:_MAX_METADATA_STR_LEN]
        else:
            # Coerce anything else to a truncated string - avoids storing
            # arbitrary nested JSON that could leak large payloads.
            out[k] = str(v)[:_MAX_METADATA_STR_LEN]
    return out or None


@router.post("/batch")
@limiter.limit("60/minute")
async def ingest_events(
    request: Request,
    body: EventBatchRequest,
    user: CurrentUser,
    db_path: DbPath,
):
    """Accept a batch of frontend UI events.

    Returns `{accepted: N}` for observability, where N is the number of
    events that passed the allowlist + sanitization checks. Events with
    unknown types are silently dropped (count still reflects accepted).
    """
    if not body.events:
        return {"accepted": 0}

    accepted = 0
    for ev in body.events:
        if ev.event_type not in _ALLOWED_UI_EVENTS:
            continue
        meta = _sanitize_metadata(ev.metadata)
        # Surface client errors in the service log so blank-page / chunk /
        # render bugs are visible in journalctl without needing Sentry.
        if ev.event_type == "ui_client_error":
            logger.warning(
                "client_error user=%s meta=%s",
                user["user_id"],
                meta,
            )
        await log_event(db_path, user["user_id"], ev.event_type, metadata=meta)
        accepted += 1

    return {"accepted": accepted}
