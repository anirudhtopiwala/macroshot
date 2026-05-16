"""Strava OAuth + webhook routes.

All credential-dependent operations are stubbed behind STRAVA_CLIENT_ID
and STRAVA_CLIENT_SECRET environment variable checks. When these are not
set, endpoints return a helpful "not configured" message.
"""

import asyncio
import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Query, Request, Response

from src.web.rate_limit import limiter

_background_tasks: set[asyncio.Task] = set()

# Per-user locks to serialize concurrent Strava token refreshes so a manual
# sync and a webhook-driven sync don't both swap the refresh_token and race.
# Same pattern as routes/fitbit.py and routes/oura.py.
_refresh_locks: dict[int, asyncio.Lock] = {}


def _get_refresh_lock(user_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[user_id] = lock
    return lock

from src.db import (
    ExternalAccountAlreadyLinked,
    delete_strava_tokens,
    get_and_delete_strava_oauth_state,
    get_last_strava_sync,
    get_strava_tokens,
    get_strava_tokens_by_athlete_id,
    log_event,
    save_strava_oauth_state,
    save_strava_tokens,
    upsert_workout,
    delete_workout,
)
from fastapi import Depends as _Depends
from src.web.deps import CurrentUser, DbPath, require_recent_auth

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/strava", tags=["strava"])

# Strava OAuth URLs
STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE = "https://www.strava.com/api/v3"
STRAVA_DEAUTH_URL = "https://www.strava.com/oauth/deauthorize"

# Environment variables
_CLIENT_ID = "STRAVA_CLIENT_ID"
_CLIENT_SECRET = "STRAVA_CLIENT_SECRET"

# A6: HttpOnly OAuth-binding cookie (mirrors fitbit / oura).
_OAUTH_SESSION_COOKIE = "__Host-strava_oauth_session"
_OAUTH_SESSION_TTL_S = 600  # 10 minutes


def _hash_oauth_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _safe_provider_error(resp) -> str:
    """A9: see fitbit.py:_safe_provider_error."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            return str(data.get("error", "<no body>"))[:120]
    except Exception:
        pass
    return "<no body>"


def _logged_at_for_activity(activity: dict) -> str:
    """Return YYYY-MM-DD for a Strava activity, derived from start_date.

    The workout_logs.logged_at column is queried two ways: by date-prefix
    LIKE for daily workout lookups, and by MAX() for the "last synced"
    label. Both expect a non-empty date string. Falls back to today's
    UTC date if start_date is missing or unparseable so the row is never
    invisible.
    """
    raw = activity.get("start_date") or ""
    # Strava returns ISO-8601 like "2026-03-21T17:34:27Z" - first 10 chars are the date.
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return raw[:10]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _get_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise 501 if not configured."""
    client_id = os.environ.get(_CLIENT_ID, "")
    client_secret = os.environ.get(_CLIENT_SECRET, "")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=501,
            detail="Strava integration not configured. Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET environment variables.",
        )
    return client_id, client_secret


def _get_redirect_uri() -> str:
    """Build the OAuth callback URL from environment.

    Raises 501 if neither BASE_URL nor DOMAIN is configured - Strava's
    authorize endpoint rejects invalid redirect URIs, so we fail loudly
    at our own layer instead of sending a malformed URL.
    """
    base_url = os.environ.get("BASE_URL", "").strip()
    if not base_url:
        domain = os.environ.get("DOMAIN", "").strip()
        if not domain:
            raise HTTPException(
                status_code=501,
                detail="Strava integration requires BASE_URL or DOMAIN to be set.",
            )
        base_path = os.environ.get("BASE_PATH", "/macro_app")
        base_url = f"https://{domain}{base_path}"
    return f"{base_url}/api/v1/strava/callback"


async def _refresh_token_if_needed(
    db_path: str, tokens: dict
) -> str:
    """Return a valid access token, refreshing if expired.

    A per-user asyncio.Lock serializes concurrent refreshes so a manual
    sync and a webhook-delivered sync can't both race on the refresh_token
    (only one is valid after rotation; the loser's would be invalidated).
    """
    if tokens["expires_at"] > int(time.time()) + 60:
        return tokens["access_token"]

    user_id = tokens["user_id"]
    async with _get_refresh_lock(user_id):
        # Double-check: another coroutine may have already refreshed while
        # we were waiting for the lock.
        fresh = await get_strava_tokens(db_path, user_id)
        if fresh and fresh["expires_at"] > int(time.time()) + 60:
            return fresh["access_token"]
        if fresh is None:
            raise HTTPException(status_code=401, detail="Strava is not connected")

        client_id, client_secret = _get_credentials()

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                STRAVA_TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": fresh["refresh_token"],
                },
            )

        if resp.status_code != 200:
            logger.error("Strava token refresh failed: %s %s", resp.status_code, _safe_provider_error(resp))
            raise HTTPException(status_code=502, detail="Strava token refresh failed")

        data = resp.json()
        await save_strava_tokens(
            db_path=db_path,
            user_id=user_id,
            strava_athlete_id=fresh["strava_athlete_id"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            scope=fresh.get("scope", ""),
        )
        logger.info("Strava token refreshed for user %d", user_id)
        return data["access_token"]


@router.get("/connect")
@limiter.limit("10/hour")
async def strava_connect(
    request: Request,
    response: Response,
    user: CurrentUser,
    db_path: DbPath,
):
    """Return the Strava OAuth authorization URL.

    The frontend redirects the user's browser to this URL. After the user
    authorizes, Strava redirects back to /strava/callback with a code.

    A19: state is now `secrets.token_urlsafe(32)` (256 bits) instead of
    uuid4().hex (122 bits), matching Fitbit/Oura.
    A6: an HttpOnly oauth_session cookie is set whose sha256 must
    round-trip in /callback.
    """
    client_id, _ = _get_credentials()

    state = secrets.token_urlsafe(32)
    oauth_session_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    await save_strava_oauth_state(
        db_path, state, user["user_id"], expires_at,
        session_token_hash=_hash_oauth_session_token(oauth_session_token),
    )

    response.set_cookie(
        key=_OAUTH_SESSION_COOKIE,
        value=oauth_session_token,
        max_age=_OAUTH_SESSION_TTL_S,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )

    params = {
        "client_id": client_id,
        "redirect_uri": _get_redirect_uri(),
        "response_type": "code",
        "scope": "activity:read_all",
        "approval_prompt": "auto",
        "state": state,
    }
    auth_url = f"{STRAVA_AUTH_URL}?{urlencode(params)}"
    return {"auth_url": auth_url}


@router.get("/callback")
async def strava_callback(
    db_path: DbPath,
    code: str = Query(...),
    state: str = Query(""),
    scope: str = Query(""),
    oauth_session: str | None = Cookie(default=None, alias=_OAUTH_SESSION_COOKIE),
):
    """Handle the OAuth callback from Strava.

    Exchanges the authorization code for access + refresh tokens, then
    saves them to the strava_tokens table.

    Returns an HTML page that closes itself / redirects to settings.
    """
    client_id, client_secret = _get_credentials()

    if not state:
        raise HTTPException(status_code=400, detail="Missing state parameter")

    state_row = await get_and_delete_strava_oauth_state(db_path, state)
    if state_row is None:
        # Clear the binding cookie on rejection paths so a stale cookie
        # doesn't linger in the browser past its 10-min TTL.
        from fastapi.responses import JSONResponse
        resp = JSONResponse(
            status_code=400,
            content={"detail": "Invalid or expired state parameter"},
        )
        # __Host- prefix REQUIRES Secure on every Set-Cookie (deletes too).
        resp.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return resp

    expected_hash = state_row.get("session_token_hash")
    if expected_hash:
        if not oauth_session or _hash_oauth_session_token(oauth_session) != expected_hash:
            logger.warning("Strava callback rejected: oauth session cookie mismatch")
            from fastapi.responses import JSONResponse
            resp = JSONResponse(
                status_code=400,
                content={"detail": "OAuth session mismatch. Please try connecting again."},
            )
            resp.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
            return resp

    user_id: int = state_row["user_id"]

    # Exchange authorization code for tokens
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        logger.error("Strava token exchange failed: %s %s", resp.status_code, _safe_provider_error(resp))
        raise HTTPException(
            status_code=502,
            detail=f"Strava token exchange failed (HTTP {resp.status_code})",
        )

    data = resp.json()
    athlete = data.get("athlete", {})

    try:
        await save_strava_tokens(
            db_path=db_path,
            user_id=user_id,
            strava_athlete_id=athlete.get("id", 0),
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            scope=scope or data.get("scope", ""),
        )
    except ExternalAccountAlreadyLinked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logger.info(
        "Strava connected for user %d (athlete %s)",
        user_id, athlete.get("id"),
    )

    # Private telemetry: integration activation
    await log_event(
        db_path, user_id, "integration_connected",
        metadata={"source": "strava"},
    )

    # Return a small HTML page that redirects back to settings
    base_path = os.environ.get("BASE_PATH", "/macro_app")
    redirect = _redirect_html(f"{base_path}/settings/connected-apps?strava=connected")
    redirect.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return redirect


@router.get("/webhook")
async def strava_webhook_verify(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_challenge: str = Query("", alias="hub.challenge"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
):
    """Strava webhook subscription verification.

    Strava sends a GET with hub.mode=subscribe, hub.challenge, and
    hub.verify_token. We must echo back hub.challenge.
    """
    verify_token = os.environ.get("STRAVA_VERIFY_TOKEN", "")
    if not verify_token:
        raise HTTPException(status_code=501, detail="STRAVA_VERIFY_TOKEN not configured")
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        logger.info("Strava webhook verified (challenge=%s)", hub_challenge)
        return {"hub.challenge": hub_challenge}
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
@limiter.limit("30/minute")
async def strava_webhook_event(request: Request):
    """Handle Strava webhook events (activity create/update/delete).

    Strava expects a 200 response within 2 seconds, so we acknowledge
    immediately and process asynchronously via asyncio.create_task.

    A8: response is identical for known and unknown athletes - the
    "unknown athlete" decision is moved into the background task so the
    response cannot be used as an enumeration oracle. Per-IP rate-limited
    at 30/min as a defense against forged-event push spam.
    """
    # Require verify token to be configured as a baseline auth check
    verify_token = os.environ.get("STRAVA_VERIFY_TOKEN")
    if not verify_token:
        logger.warning("Strava webhook event ignored: STRAVA_VERIFY_TOKEN not configured")
        return {"status": "received"}

    body = await request.json()
    object_type = body.get("object_type", "")
    aspect_type = body.get("aspect_type", "")
    object_id = body.get("object_id", 0)
    owner_id = body.get("owner_id", 0)

    logger.info(
        "Strava webhook: %s %s - object_id=%s owner_id=%s",
        object_type, aspect_type, object_id, owner_id,
    )

    if object_type == "activity":
        from src.web.deps import DB_PATH
        task = asyncio.create_task(
            _process_strava_event(DB_PATH, owner_id, object_id, aspect_type)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    # Same response shape regardless of object_type / unknown-owner /
    # any other branch - prevents user enumeration via response diff.
    return {"status": "received"}


async def _process_strava_event(
    db_path: str, owner_id: int, object_id: int, aspect_type: str
) -> None:
    """Process a Strava activity event in the background.

    SECURITY: Strava does not sign webhook payloads (no HMAC header).
    Anyone who knows a user's public Strava athlete_id can forge a POST
    to this endpoint.  We never trust the webhook payload directly -
    every action is verified against the Strava API using the user's
    stored OAuth token.

    - For DELETE: re-fetch the activity from Strava.  If Strava returns
      200, the activity still exists → webhook was forged, IGNORE.
      If Strava returns 404, the activity is genuinely gone → safe to
      delete the local row.
    - For CREATE/UPDATE: fetch from Strava as before.  A forged event
      can at most cost an extra API call and (if the object_id matches
      a real activity owned by this athlete) cause a no-op resync.
    """
    try:
        # Look up user by Strava athlete ID
        tokens = await get_strava_tokens_by_athlete_id(db_path, owner_id)
        if not tokens:
            logger.warning("Strava webhook: no user for athlete_id=%s", owner_id)
            return

        user_id = tokens["user_id"]
        access_token = await _refresh_token_if_needed(db_path, tokens)

        if aspect_type == "delete":
            # Verify the activity is actually gone before deleting locally.
            async with httpx.AsyncClient(timeout=15.0) as client:
                verify_resp = await client.get(
                    f"{STRAVA_API_BASE}/activities/{object_id}",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if verify_resp.status_code == 200:
                logger.warning(
                    "Strava webhook delete IGNORED - activity %s still exists for athlete %s (likely forged)",
                    object_id, owner_id,
                )
                return
            if verify_resp.status_code != 404:
                logger.warning(
                    "Strava delete verify returned %s for activity %s - not deleting",
                    verify_resp.status_code, object_id,
                )
                return
            await delete_workout(db_path, user_id, "strava", str(object_id))
            logger.info("Strava activity %s deleted for user %d (verified via API)", object_id, user_id)
            return

        # create or update - fetch the activity details from Strava
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{STRAVA_API_BASE}/activities/{object_id}",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code != 200:
            logger.error(
                "Strava activity fetch failed: %s %s", resp.status_code, _safe_provider_error(resp)
            )
            return

        activity = resp.json()

        # Map Strava activity to our workout_logs schema
        await upsert_workout(
            db_path=db_path,
            user_id=user_id,
            source="strava",
            external_id=str(activity["id"]),
            activity_type=activity.get("type", activity.get("sport_type", "Workout")),
            name=activity.get("name", ""),
            started_at=activity.get("start_date", ""),
            duration_sec=activity.get("elapsed_time", 0),
            calories_burned=activity.get("calories", 0),
            distance_m=activity.get("distance", 0),
            avg_heart_rate=activity.get("average_heartrate", 0),
            logged_at=_logged_at_for_activity(activity),
            raw_json="",  # skip storing raw JSON to save disk
        )
        logger.info(
            "Strava activity %s (%s) synced for user %d: %s",
            object_id, aspect_type, user_id, activity.get("name", ""),
        )

        # Send post-workout nutrition nudge (only for new activities)
        if aspect_type == "create":
            try:
                from src.web.workout_nudge import send_workout_nudge
                await send_workout_nudge(
                    db_path, user_id,
                    activity_type=activity.get("type", "Workout"),
                    calories_burned=activity.get("calories", 0),
                )
            except Exception:
                logger.debug("Workout nudge failed for user %d", user_id)

    except Exception:
        logger.exception("Error processing Strava webhook event (object_id=%s)", object_id)


@router.delete("/disconnect", dependencies=[_Depends(require_recent_auth)])
async def strava_disconnect(user: CurrentUser, db_path: DbPath):
    """Disconnect Strava - deauthorizes via Strava API and removes stored tokens."""
    _get_credentials()

    user_id = user["user_id"]
    tokens = await get_strava_tokens(db_path, user_id)
    if not tokens:
        raise HTTPException(status_code=404, detail="Strava is not connected")

    # Deauthorize via Strava API (best effort)
    try:
        access_token = await _refresh_token_if_needed(db_path, tokens)
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                STRAVA_DEAUTH_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        logger.info("Strava deauthorized for user %d", user_id)
    except Exception:
        logger.warning("Strava deauth API call failed for user %d (continuing with local disconnect)", user_id)

    await delete_strava_tokens(db_path, user_id)
    # Also drop the per-user entry in the background-loop skip-window cache;
    # otherwise a reconnect is silently blocked for up to 30 minutes.
    from src.web.activity_sync import clear_strava_poll_timestamp
    clear_strava_poll_timestamp(user_id)
    # And forget the refresh lock for this user so memory doesn't grow
    # unboundedly across connect/disconnect cycles.
    _refresh_locks.pop(user_id, None)
    logger.info("Strava disconnected for user %d", user_id)
    return {"status": "disconnected"}


async def sync_strava_for_user(db_path: str, user_id: int) -> int:
    """Sync recent Strava activities for a single user. Returns count synced.

    Returns 0 if the user isn't connected, credentials aren't configured,
    or token refresh fails. Safe to call from a background loop - never
    raises, just logs and returns 0 on any failure.
    """
    if not os.environ.get(_CLIENT_ID):
        return 0
    tokens = await get_strava_tokens(db_path, user_id)
    if not tokens:
        return 0
    try:
        access_token = await _refresh_token_if_needed(db_path, tokens)
    except Exception:
        logger.exception("Strava token refresh failed for user %d", user_id)
        return 0

    # Fetch up to 200 activities from the last 90 days
    after = int((datetime.now(timezone.utc) - timedelta(days=90)).timestamp())
    synced = 0
    page = 1
    detail_limit = 50  # Max activities to fetch details for (avoids Strava rate limits)

    # Phase 1: Collect activity summaries from the list endpoint
    all_summaries: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        while page <= 4:  # max 4 pages of 50 = 200 activities
            resp = await client.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"after": after, "per_page": 50, "page": page},
            )
            if resp.status_code != 200:
                logger.error("Strava activity list failed: %s %s", resp.status_code, _safe_provider_error(resp))
                break

            activities = resp.json()
            if not activities:
                break

            all_summaries.extend(activities)

            if len(activities) < 50:
                break
            page += 1

    # Phase 2: Fetch activity details in parallel batches of 5.
    # The list endpoint returns SummaryActivity objects which lack calories
    # and average_heartrate. We need DetailedActivity for those fields.
    # Limit to first `detail_limit` activities to stay within Strava's
    # rate limit (100 requests per 15 minutes).
    batch_size = 5
    activities_to_upsert: list[dict] = []
    rate_limited = False

    async with httpx.AsyncClient(timeout=15.0) as client:
        for batch_start in range(0, len(all_summaries), batch_size):
            batch = all_summaries[batch_start:batch_start + batch_size]

            if not rate_limited and batch_start < detail_limit:
                # Fetch details for this batch in parallel
                detail_count = min(len(batch), detail_limit - batch_start)

                async def _fetch_detail(summary: dict) -> dict:
                    try:
                        resp = await client.get(
                            f"{STRAVA_API_BASE}/activities/{summary['id']}",
                            headers={"Authorization": f"Bearer {access_token}"},
                        )
                        if resp.status_code == 200:
                            return resp.json()
                        if resp.status_code == 429:
                            return {"_rate_limited": True, **summary}
                        logger.warning("Strava detail failed for %s: %s", summary["id"], resp.status_code)
                    except Exception:
                        logger.warning("Strava detail error for %s", summary["id"])
                    return summary

                results = await asyncio.gather(
                    *[_fetch_detail(s) for s in batch[:detail_count]]
                )

                for r in results:
                    if r.get("_rate_limited"):
                        rate_limited = True
                        r.pop("_rate_limited", None)
                    activities_to_upsert.append(r)
                # Append any remaining in the batch beyond the detail limit
                activities_to_upsert.extend(batch[detail_count:])
            else:
                # Past detail_limit or rate limited - use summary data
                activities_to_upsert.extend(batch)

            # Small delay between batches to be respectful of rate limits
            if batch_start + batch_size < len(all_summaries) and not rate_limited:
                await asyncio.sleep(0.5)

    for activity in activities_to_upsert:
        await upsert_workout(
            db_path=db_path,
            user_id=user_id,
            source="strava",
            external_id=str(activity["id"]),
            activity_type=activity.get("type", activity.get("sport_type", "Workout")),
            name=activity.get("name", ""),
            started_at=activity.get("start_date", ""),
            duration_sec=activity.get("elapsed_time", 0),
            calories_burned=activity.get("calories", 0),
            distance_m=activity.get("distance", 0),
            avg_heart_rate=activity.get("average_heartrate", 0),
            logged_at=_logged_at_for_activity(activity),
            raw_json="",
        )
        synced += 1

    logger.info("Strava sync complete for user %d: %d activities", user_id, synced)
    return synced


@router.post("/sync")
@limiter.limit("3/minute")
async def strava_sync(request: Request, user: CurrentUser, db_path: DbPath):
    """Manual sync endpoint - wraps sync_strava_for_user with auth + 404."""
    _get_credentials()
    user_id = user["user_id"]
    tokens = await get_strava_tokens(db_path, user_id)
    if not tokens:
        raise HTTPException(status_code=404, detail="Strava is not connected")
    synced = await sync_strava_for_user(db_path, user_id)
    return {"synced": synced}


@router.get("/status")
async def strava_status(user: CurrentUser, db_path: DbPath):
    """Check whether Strava is connected for the current user."""
    # If integration isn't configured, report as not available
    client_id = os.environ.get(_CLIENT_ID, "")
    if not client_id:
        return {"connected": False, "available": False}

    user_id = user["user_id"]
    tokens = await get_strava_tokens(db_path, user_id)
    last_synced = await get_last_strava_sync(db_path, user_id) if tokens else None

    return {
        "connected": tokens is not None,
        "available": True,
        "athlete_id": tokens["strava_athlete_id"] if tokens else None,
        "last_synced": last_synced,
        "health": "ok" if tokens else None,
    }


def _redirect_html(url: str) -> "fastapi.responses.HTMLResponse":
    """Return a small HTML page that redirects to the given URL."""
    from src.web.routes._oauth_utils import redirect_html
    return redirect_html(url)
