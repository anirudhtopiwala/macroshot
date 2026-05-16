"""Fitbit OAuth (with PKCE) + webhook routes.

All credential-dependent operations are stubbed behind FITBIT_CLIENT_ID
and FITBIT_CLIENT_SECRET environment variable checks. When these are not
set, endpoints return a helpful "not configured" message.

Fitbit uses OAuth 2.0 with PKCE (Proof Key for Code Exchange):
  - code_verifier: random 43-128 char string
  - code_challenge: base64url(sha256(code_verifier))
  - State + code_verifier are stored in fitbit_oauth_state (single-use, 10min TTL)
  - Token exchange uses Basic auth (base64(client_id:client_secret))
  - Refresh tokens are single-use - must save new refresh_token from every refresh
"""

import asyncio
import base64
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Query, Request, Response

from src.web.rate_limit import limiter

_background_tasks: set[asyncio.Task] = set()

# Per-user locks to serialize Fitbit token refreshes. Fitbit refresh tokens are
# single-use - concurrent refreshes (e.g. dashboard auto-sync racing a manual
# sync) would both read the same RT, one wins, the loser gets invalid_grant
# and the tokens get deleted. The lock ensures only one coroutine refreshes at
# a time; others wait, re-read the fresh RT from DB, and reuse it.
_refresh_locks: dict[int, asyncio.Lock] = {}


def _get_refresh_lock(user_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[user_id] = lock
    return lock

from src.db import (
    ExternalAccountAlreadyLinked,
    delete_fitbit_tokens,
    get_and_delete_fitbit_oauth_state,
    get_fitbit_activity,
    get_fitbit_tokens,
    get_fitbit_tokens_by_fitbit_user_id,
    get_last_fitbit_sync,
    log_event,
    save_fitbit_oauth_state,
    save_fitbit_tokens,
    upsert_fitbit_activity,
    upsert_workout,
)
from fastapi import Depends as _Depends
from src.web.deps import CurrentUser, DbPath, require_recent_auth

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/fitbit", tags=["fitbit"])

# Fitbit OAuth / API URLs
FITBIT_AUTH_URL = "https://www.fitbit.com/oauth2/authorize"
FITBIT_TOKEN_URL = "https://api.fitbit.com/oauth2/token"
FITBIT_API_BASE = "https://api.fitbit.com"
FITBIT_REVOKE_URL = "https://api.fitbit.com/oauth2/revoke"

# Environment variables
_CLIENT_ID = "FITBIT_CLIENT_ID"
_CLIENT_SECRET = "FITBIT_CLIENT_SECRET"

# PKCE verifier length (RFC 7636 recommends 43-128)
_VERIFIER_LENGTH = 64

# OAuth state TTL in minutes
_STATE_TTL_MIN = 10

# A6: HttpOnly cookie that binds an OAuth round-trip to the originating
# session so a CSRF lure can't make the victim's browser complete a
# Connect/Disconnect flow on the attacker's behalf. SameSite=Lax keeps the
# cookie travelling on the top-level callback redirect.
_OAUTH_SESSION_COOKIE = "__Host-fitbit_oauth_session"
_OAUTH_SESSION_TTL_S = _STATE_TTL_MIN * 60


def _hash_oauth_session_token(token: str) -> str:
    """sha256 of the OAuth session token; stored in fitbit_oauth_state."""
    return hashlib.sha256(token.encode("ascii")).hexdigest()


# A20: TTL cache of seen webhook signatures so a captured POST can't be
# replayed. Single-worker deploy assumption - in-process dict is enough.
_WEBHOOK_SIG_TTL_S = 86400
_WEBHOOK_SIG_MAX = 2048
_seen_webhook_sigs: dict[str, float] = {}


def _replay_check(sig: str) -> bool:
    """Return True if the signature is fresh, False if it's a replay.

    Inserts on success. Trims the cache when it exceeds _WEBHOOK_SIG_MAX
    by dropping oldest entries.
    """
    import time as _time
    now = _time.time()
    # Drop expired entries lazily.
    if len(_seen_webhook_sigs) > _WEBHOOK_SIG_MAX:
        cutoff = now - _WEBHOOK_SIG_TTL_S
        for k in [k for k, t in _seen_webhook_sigs.items() if t < cutoff]:
            _seen_webhook_sigs.pop(k, None)
        # Still too big? evict oldest.
        if len(_seen_webhook_sigs) > _WEBHOOK_SIG_MAX:
            for k in sorted(_seen_webhook_sigs, key=_seen_webhook_sigs.get)[:512]:
                _seen_webhook_sigs.pop(k, None)
    last = _seen_webhook_sigs.get(sig)
    if last and (now - last) < _WEBHOOK_SIG_TTL_S:
        return False
    _seen_webhook_sigs[sig] = now
    return True


def _safe_provider_error(resp) -> str:
    """Extract a non-PII error string from a token-exchange response.

    A9: never log resp.text - the body can contain access/refresh tokens
    on a successful response, or scoped error payloads with user IDs and
    request context. Pull only the JSON `error` (or fall back to a fixed
    placeholder).
    """
    try:
        data = resp.json()
        if isinstance(data, dict):
            return str(data.get("error", "<no body>"))[:120]
    except Exception:
        pass
    return "<no body>"


def _get_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise 501 if not configured."""
    client_id = os.environ.get(_CLIENT_ID, "")
    client_secret = os.environ.get(_CLIENT_SECRET, "")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=501,
            detail="Fitbit integration not configured. Set FITBIT_CLIENT_ID and FITBIT_CLIENT_SECRET environment variables.",
        )
    return client_id, client_secret


def _get_redirect_uri() -> str:
    """Build the OAuth callback URL from environment.

    Raises 501 if neither BASE_URL nor DOMAIN is configured.
    """
    base_url = os.environ.get("BASE_URL", "").strip()
    if not base_url:
        domain = os.environ.get("DOMAIN", "").strip()
        if not domain:
            raise HTTPException(
                status_code=501,
                detail="Fitbit integration requires BASE_URL or DOMAIN to be set.",
            )
        base_path = os.environ.get("BASE_PATH", "/macro_app")
        base_url = f"https://{domain}{base_path}"
    return f"{base_url}/api/v1/fitbit/callback"


def _generate_pkce() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge pair.

    Returns (code_verifier, code_challenge) where code_challenge is
    base64url(sha256(code_verifier)).
    """
    code_verifier = secrets.token_urlsafe(_VERIFIER_LENGTH)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _basic_auth_header(client_id: str, client_secret: str) -> str:
    """Build the Basic auth header value for Fitbit token requests."""
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode("ascii")).decode("ascii")
    return f"Basic {encoded}"


async def _refresh_token_if_needed(db_path: str, tokens: dict) -> str:
    """Return a valid access token, refreshing if expired.

    IMPORTANT: Fitbit refresh tokens are single-use. Every refresh response
    contains a new refresh_token that must be saved immediately.

    A per-user asyncio lock serializes concurrent refreshes. Without the lock,
    two callers reading the same refresh_token would both call Fitbit; one
    wins, the other gets invalid_grant and delete_fitbit_tokens wipes the
    connection.
    """
    expires_at_str = tokens["expires_at"]  # "YYYY-MM-DD HH:MM:SS" UTC
    expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    if expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
        return tokens["access_token"]

    user_id = tokens["user_id"]
    async with _get_refresh_lock(user_id):
        # Another coroutine may have refreshed while we waited - re-read.
        fresh = await get_fitbit_tokens(db_path, user_id)
        if fresh is None:
            raise HTTPException(
                status_code=401,
                detail="Fitbit connection expired. Please reconnect.",
            )
        fresh_expires = datetime.strptime(
            fresh["expires_at"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        if fresh_expires > datetime.now(timezone.utc) + timedelta(seconds=60):
            return fresh["access_token"]

        client_id, client_secret = _get_credentials()

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                FITBIT_TOKEN_URL,
                headers={
                    "Authorization": _basic_auth_header(client_id, client_secret),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": fresh["refresh_token"],
                },
            )

        if resp.status_code != 200:
            logger.error("Fitbit token refresh failed: %s %s", resp.status_code, _safe_provider_error(resp))
            if resp.status_code in (400, 401):
                await delete_fitbit_tokens(db_path, user_id)
                logger.warning("Deleted revoked Fitbit tokens for user %d", user_id)
                raise HTTPException(status_code=401, detail="Fitbit connection expired. Please reconnect.")
            raise HTTPException(status_code=502, detail="Fitbit token refresh failed")

        data = resp.json()

        expires_in = data.get("expires_in", 28800)
        new_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).strftime("%Y-%m-%d %H:%M:%S")

        await save_fitbit_tokens(
            db_path=db_path,
            user_id=user_id,
            fitbit_user_id=fresh["fitbit_user_id"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=new_expires_at,
            scope=fresh.get("scope", ""),
        )
        logger.info("Fitbit token refreshed for user %d", user_id)
        return data["access_token"]


# Staleness threshold for auto-sync (avoid hitting Fitbit API on every page load)
_AUTO_SYNC_STALE_MINUTES = 15


async def _fetch_and_save_activity_logs(
    db_path: str,
    user_id: int,
    access_token: str,
    after_date: str,
    limit: int = 50,
) -> int:
    """Fetch Fitbit individual activity logs (strength, walk, etc.) and upsert them
    into workout_logs so they render as individual cards alongside Strava.

    Fitbit's daily summary already rolls these up into activityCalories/caloriesOut,
    so the max(workout_cals, daily_source_cals) guard in workouts.py prevents
    double-counting.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{FITBIT_API_BASE}/1/user/-/activities/list.json",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "afterDate": after_date,
                    "sort": "asc",
                    "limit": limit,
                    "offset": 0,
                },
            )
    except Exception:
        logger.debug("Fitbit activity list fetch error", exc_info=True)
        return 0

    if resp.status_code != 200:
        logger.warning("Fitbit activity list fetch failed: %s", resp.status_code)
        return 0

    activities = resp.json().get("activities", []) or []
    saved = 0
    for act in activities:
        log_id = act.get("logId")
        if not log_id:
            continue
        start_time = act.get("startTime", "") or ""
        duration_ms = act.get("duration") or 0
        duration_sec = int(duration_ms / 1000) if duration_ms else 0
        logged_at = start_time[:10] if start_time else ""

        # Fitbit returns distance in the user's unit system (km or mi).
        # Convert to meters for storage consistency with Strava.
        distance = act.get("distance") or 0
        unit = (act.get("distanceUnit") or "").lower()
        if unit.startswith("mile"):
            distance_m = float(distance) * 1609.34
        elif unit.startswith("kilo"):
            distance_m = float(distance) * 1000.0
        else:
            # default: Fitbit docs say km for metric users, mi for imperial;
            # when we don't know, assume km (Fitbit's canonical).
            distance_m = float(distance) * 1000.0

        activity_name = act.get("activityName") or "Activity"

        await upsert_workout(
            db_path=db_path,
            user_id=user_id,
            source="fitbit",
            external_id=str(log_id),
            activity_type=activity_name,
            name=activity_name,
            started_at=start_time,
            duration_sec=duration_sec,
            calories_burned=float(act.get("calories") or 0),
            distance_m=distance_m,
            avg_heart_rate=float(act.get("averageHeartRate") or 0),
            logged_at=logged_at,
        )
        saved += 1

    if saved:
        logger.info("Fitbit activity logs synced for user %d: %d entries", user_id, saved)
    return saved


async def fitbit_needs_sync(db_path: str, user_id: int, date_str: str) -> bool:
    """Cheap probe: would auto_sync_fitbit_today actually hit the network?

    Mirrors the early-return checks in auto_sync_fitbit_today so callers can
    decide whether to spawn the sync as a background task vs. skip it.
    Returns False on any error (treat as "no sync needed").
    """
    try:
        if not os.environ.get(_CLIENT_ID, ""):
            return False
        tokens = await get_fitbit_tokens(db_path, user_id)
        if not tokens:
            return False
        existing = await get_fitbit_activity(db_path, user_id, date_str)
        if existing and existing.get("fetched_at"):
            fetched_at = datetime.strptime(
                existing["fetched_at"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at < timedelta(
                minutes=_AUTO_SYNC_STALE_MINUTES
            ):
                return False
        return True
    except Exception:
        return False


async def auto_sync_fitbit_today(db_path: str, user_id: int, date_str: str) -> None:
    """Auto-sync today's Fitbit data if stale (>15 min) or missing.

    Called from get_progress() so the dashboard always shows fresh workout
    data without requiring a manual sync. Safe to call from anywhere -
    returns silently on any error (Fitbit not configured, not connected,
    API failure, token expired, etc.).
    """
    try:
        # Quick exit if Fitbit not configured
        if not os.environ.get(_CLIENT_ID, ""):
            return

        tokens = await get_fitbit_tokens(db_path, user_id)
        if not tokens:
            return

        # Check staleness - skip if recent data exists
        existing = await get_fitbit_activity(db_path, user_id, date_str)
        if existing and existing.get("fetched_at"):
            fetched_at = datetime.strptime(
                existing["fetched_at"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at < timedelta(
                minutes=_AUTO_SYNC_STALE_MINUTES
            ):
                return  # Fresh enough

        access_token = await _refresh_token_if_needed(db_path, tokens)

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{FITBIT_API_BASE}/1/user/-/activities/date/{date_str}.json",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code != 200:
            logger.warning("Fitbit auto-sync failed: %s", resp.status_code)
            return

        summary = resp.json().get("summary", {})
        await upsert_fitbit_activity(
            db_path=db_path,
            user_id=user_id,
            date_str=date_str,
            calories_out=summary.get("caloriesOut", 0),
            activity_calories=summary.get("activityCalories", 0),
            calories_bmr=summary.get("caloriesBMR", 0),
            steps=summary.get("steps", 0),
            fairly_active_min=summary.get("fairlyActiveMinutes", 0),
            very_active_min=summary.get("veryActiveMinutes", 0),
            resting_heart_rate=summary.get("restingHeartRate", 0),
        )

        # Also fetch today's individual activity logs (Walk, Strength training, etc.)
        await _fetch_and_save_activity_logs(
            db_path=db_path,
            user_id=user_id,
            access_token=access_token,
            after_date=date_str,
            limit=20,
        )

        logger.info("Fitbit auto-sync for user %d on %s", user_id, date_str)

    except Exception:
        logger.debug("Fitbit auto-sync skipped for user %d", user_id, exc_info=True)


@router.get("/connect")
@limiter.limit("10/hour")
async def fitbit_connect(
    request: Request,
    response: Response,
    user: CurrentUser,
    db_path: DbPath,
):
    """Return the Fitbit OAuth authorization URL with PKCE.

    Generates a code_verifier + code_challenge pair and stores them
    alongside a random state token in the fitbit_oauth_state table.
    The state row expires after 10 minutes and is single-use.

    A6: also generates a 256-bit oauth_session_token, sets it as an
    HttpOnly cookie, and stores its sha256 hash on the state row. The
    /callback handler enforces that the cookie round-trips, defeating
    account-linking CSRF.
    """
    client_id, _ = _get_credentials()

    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(32)
    oauth_session_token = secrets.token_urlsafe(32)

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=_STATE_TTL_MIN)).strftime("%Y-%m-%d %H:%M:%S")

    await save_fitbit_oauth_state(
        db_path=db_path,
        state=state,
        user_id=user["user_id"],
        code_verifier=code_verifier,
        expires_at=expires_at,
        session_token_hash=_hash_oauth_session_token(oauth_session_token),
    )

    response.set_cookie(
        key=_OAUTH_SESSION_COOKIE,
        value=oauth_session_token,
        max_age=_OAUTH_SESSION_TTL_S,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",  # required by __Host- prefix
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _get_redirect_uri(),
        "scope": "activity heartrate profile",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    auth_url = f"{FITBIT_AUTH_URL}?{urlencode(params)}"
    return {"auth_url": auth_url}


@router.get("/callback")
async def fitbit_callback(
    db_path: DbPath,
    code: str = Query(...),
    state: str = Query(...),
    oauth_session: str | None = Cookie(default=None, alias=_OAUTH_SESSION_COOKIE),
):
    """Handle the OAuth callback from Fitbit.

    Looks up the PKCE state row, exchanges the authorization code for
    tokens using Basic auth + code_verifier, and saves the tokens.

    Returns an HTML page that redirects back to settings.
    """
    client_id, client_secret = _get_credentials()

    # Retrieve and consume the single-use state row
    state_row = await get_and_delete_fitbit_oauth_state(db_path, state)
    if state_row is None:
        # Cookie has 10-min TTL so a stuck one expires harmlessly — but
        # clear it eagerly anyway so callbacks rejected for state-not-found
        # don't leave a stale binding cookie sitting in the browser.
        from fastapi.responses import JSONResponse
        resp = JSONResponse(
            status_code=400,
            content={"detail": "Invalid or expired OAuth state. Please try connecting again."},
        )
        # __Host- prefix REQUIRES Secure on every Set-Cookie (including the
        # delete pseudo-cookie); without it browsers ignore the clear.
        resp.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return resp

    # A6: enforce session-token binding. State rows created before this
    # change have session_token_hash=NULL; treat them as legacy and skip
    # the check (single-use TTL of 10 minutes bounds exposure).
    expected_hash = state_row.get("session_token_hash")
    if expected_hash:
        if not oauth_session or _hash_oauth_session_token(oauth_session) != expected_hash:
            logger.warning("Fitbit callback rejected: oauth session cookie mismatch")
            from fastapi.responses import JSONResponse
            resp = JSONResponse(
                status_code=400,
                content={"detail": "OAuth session mismatch. Please try connecting again."},
            )
            resp.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
            return resp

    user_id = state_row["user_id"]
    code_verifier = state_row["code_verifier"]

    # Exchange authorization code for tokens (Basic auth + PKCE verifier)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            FITBIT_TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(client_id, client_secret),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _get_redirect_uri(),
                "code_verifier": code_verifier,
            },
        )

    if resp.status_code != 200:
        logger.error("Fitbit token exchange failed: %s %s", resp.status_code, _safe_provider_error(resp))
        raise HTTPException(
            status_code=502,
            detail=f"Fitbit token exchange failed (HTTP {resp.status_code})",
        )

    data = resp.json()

    # Fitbit returns expires_in (seconds) - compute absolute expiry
    expires_in = data.get("expires_in", 28800)  # default 8 hours
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=expires_in)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        await save_fitbit_tokens(
            db_path=db_path,
            user_id=user_id,
            fitbit_user_id=data.get("user_id", ""),
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=expires_at,
            scope=data.get("scope", ""),
        )
    except ExternalAccountAlreadyLinked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logger.info(
        "Fitbit connected for user %d (fitbit_user=%s)",
        user_id, data.get("user_id"),
    )

    # Private telemetry: integration activation
    await log_event(
        db_path, user_id, "integration_connected",
        metadata={"source": "fitbit"},
    )

    base_path = os.environ.get("BASE_PATH", "/macro_app")
    redirect = _redirect_html(f"{base_path}/settings/connected-apps?fitbit=connected")
    # Clear the OAuth-binding cookie now that the round-trip is done.
    # __Host- prefix REQUIRES Secure on the deletion Set-Cookie too.
    redirect.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return redirect


@router.get("/webhook")
async def fitbit_webhook_verify(
    verify: str = Query("", alias="verify"),
):
    """Fitbit webhook subscription verification.

    Fitbit sends TWO requests: one with the real code, one with a decoy.
    Must return 204 for the real code and 404 for the decoy.
    """
    from fastapi.responses import Response
    expected = os.environ.get("FITBIT_VERIFY_CODE", "")
    if not expected:
        return Response(status_code=404)
    if verify == expected:
        logger.info("Fitbit webhook verified successfully")
        return Response(status_code=204)
    logger.info("Fitbit webhook verify rejected (decoy code)")
    raise HTTPException(status_code=404, detail="Verification failed")


@router.post("/webhook")
async def fitbit_webhook_event(request: Request):
    """Handle Fitbit webhook notification events.

    Fitbit expects a 204 response within seconds, so we acknowledge
    immediately. The actual data fetch happens asynchronously.

    Fitbit webhook body is a JSON array of notifications, each with:
      {collectionType, date, ownerId, ownerType, subscriptionId}

    Fitbit signs webhook payloads with X-Fitbit-Signature (HMAC-SHA1 of body
    using subscriber verification code + '&' as the key). We verify this
    signature to prevent unauthenticated callers from injecting events.
    """
    verify_code = os.environ.get("FITBIT_VERIFY_CODE")
    if not verify_code:
        logger.warning("Fitbit webhook event ignored: FITBIT_VERIFY_CODE not configured")
        from fastapi.responses import Response as Resp
        return Resp(status_code=204)

    # Verify Fitbit signature (HMAC-SHA1 with key = verify_code + "&")
    import hmac
    raw_body = await request.body()
    expected_sig = base64.b64encode(
        hmac.new((verify_code + "&").encode(), raw_body, hashlib.sha1).digest()
    ).decode()
    actual_sig = request.headers.get("X-Fitbit-Signature", "")
    if not hmac.compare_digest(expected_sig, actual_sig):
        logger.warning("Fitbit webhook: invalid signature, rejecting")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # A20: reject duplicates of the same signed POST within 24h.
    if not _replay_check(actual_sig):
        logger.warning("Fitbit webhook: replay rejected (signature seen recently)")
        from fastapi.responses import Response as Resp
        return Resp(status_code=204)

    body = await request.json()
    notifications = body if isinstance(body, list) else [body]
    logger.info("Fitbit webhook received: %d notification(s)", len(notifications))

    # Process asynchronously so we return 204 within Fitbit's window
    from src.web.deps import DB_PATH
    db_path = DB_PATH
    for notification in notifications:
        collection = notification.get("collectionType", "")
        date_str = notification.get("date", "")
        fitbit_user_id = notification.get("ownerId", "")

        if collection == "activities" and date_str and fitbit_user_id:
            # Validate that fitbit_user_id maps to a known user
            tokens = await get_fitbit_tokens_by_fitbit_user_id(db_path, fitbit_user_id)
            if not tokens:
                logger.warning("Fitbit webhook: unknown ownerId=%s, ignoring", fitbit_user_id)
                continue
            task = asyncio.create_task(
                _process_fitbit_activity(db_path, fitbit_user_id, date_str)
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    from fastapi.responses import Response
    return Response(status_code=204)


async def _process_fitbit_activity(
    db_path: str, fitbit_user_id: str, date_str: str
) -> None:
    """Fetch and save Fitbit activity summary for a given date."""
    try:
        tokens = await get_fitbit_tokens_by_fitbit_user_id(db_path, fitbit_user_id)
        if not tokens:
            logger.warning("Fitbit webhook: no user for fitbit_user_id=%s", fitbit_user_id)
            return

        user_id = tokens["user_id"]
        access_token = await _refresh_token_if_needed(db_path, tokens)

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{FITBIT_API_BASE}/1/user/-/activities/date/{date_str}.json",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if resp.status_code != 200:
            logger.error(
                "Fitbit activity fetch failed: %s %s", resp.status_code, _safe_provider_error(resp)
            )
            return

        data = resp.json()
        summary = data.get("summary", {})

        await upsert_fitbit_activity(
            db_path=db_path,
            user_id=user_id,
            date_str=date_str,
            calories_out=summary.get("caloriesOut", 0),
            activity_calories=summary.get("activityCalories", 0),
            calories_bmr=summary.get("caloriesBMR", 0),
            steps=summary.get("steps", 0),
            fairly_active_min=summary.get("fairlyActiveMinutes", 0),
            very_active_min=summary.get("veryActiveMinutes", 0),
            resting_heart_rate=summary.get("restingHeartRate", 0),
        )

        # Pull individual activity logs for this date as well
        await _fetch_and_save_activity_logs(
            db_path=db_path,
            user_id=user_id,
            access_token=access_token,
            after_date=date_str,
            limit=20,
        )

        logger.info(
            "Fitbit activity synced for user %d on %s: %d steps, %d cal",
            user_id, date_str, summary.get("steps", 0), summary.get("caloriesOut", 0),
        )

        # Send post-workout nudge if significant activity detected
        activity_cals = summary.get("activityCalories", 0)
        active_min = summary.get("fairlyActiveMinutes", 0) + summary.get("veryActiveMinutes", 0)
        if activity_cals > 200 or active_min > 30:
            try:
                from src.web.workout_nudge import send_workout_nudge
                await send_workout_nudge(
                    db_path, user_id,
                    activity_type="Activity",
                    calories_burned=activity_cals,
                )
            except Exception:
                logger.debug("Workout nudge failed for user %d", user_id)

    except Exception:
        logger.exception(
            "Error processing Fitbit webhook (fitbit_user=%s, date=%s)",
            fitbit_user_id, date_str,
        )


@router.delete("/disconnect", dependencies=[_Depends(require_recent_auth)])
async def fitbit_disconnect(user: CurrentUser, db_path: DbPath):
    """Disconnect Fitbit - revokes tokens via Fitbit API and removes stored tokens."""
    client_id, client_secret = _get_credentials()

    user_id = user["user_id"]
    tokens = await get_fitbit_tokens(db_path, user_id)
    if not tokens:
        raise HTTPException(status_code=404, detail="Fitbit is not connected")

    # Revoke via Fitbit API (best effort)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                FITBIT_REVOKE_URL,
                headers={
                    "Authorization": _basic_auth_header(client_id, client_secret),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"token": tokens["access_token"]},
            )
        logger.info("Fitbit token revoked for user %d", user_id)
    except Exception:
        logger.warning(
            "Fitbit revoke API call failed for user %d (continuing with local disconnect)",
            user_id,
        )

    await delete_fitbit_tokens(db_path, user_id)
    logger.info("Fitbit disconnected for user %d", user_id)
    return {"status": "disconnected"}


@router.post("/sync")
@limiter.limit("3/minute")
async def fitbit_sync(request: Request, user: CurrentUser, db_path: DbPath):
    """Sync recent Fitbit activity summaries (last 30 days).

    Uses Fitbit time-series API to fetch 30 days of data in 6 parallel
    requests (one per metric) instead of 30 sequential per-day calls.
    """
    _get_credentials()

    user_id = user["user_id"]
    tokens = await get_fitbit_tokens(db_path, user_id)
    if not tokens:
        raise HTTPException(status_code=404, detail="Fitbit is not connected")

    access_token = await _refresh_token_if_needed(db_path, tokens)

    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=29)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {access_token}"}

    # Fetch 7 time-series in parallel (vs. 30 sequential per-day calls)
    metrics = {
        "calories": f"/1/user/-/activities/calories/date/{start}/{end}.json",
        "activityCalories": f"/1/user/-/activities/activityCalories/date/{start}/{end}.json",
        "caloriesBMR": f"/1/user/-/activities/caloriesBMR/date/{start}/{end}.json",
        "steps": f"/1/user/-/activities/steps/date/{start}/{end}.json",
        "minutesFairlyActive": f"/1/user/-/activities/minutesFairlyActive/date/{start}/{end}.json",
        "minutesVeryActive": f"/1/user/-/activities/minutesVeryActive/date/{start}/{end}.json",
        "heart": f"/1/user/-/activities/heart/date/{start}/{end}.json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        responses = await asyncio.gather(
            *[client.get(f"{FITBIT_API_BASE}{path}", headers=headers) for path in metrics.values()],
            return_exceptions=True,
        )

    # Parse each time-series response into a {date: value} map
    metric_keys = list(metrics.keys())
    series: dict[str, dict[str, float]] = {}  # date -> {metric: value}

    for i, resp in enumerate(responses):
        key = metric_keys[i]
        if isinstance(resp, Exception) or resp.status_code != 200:
            if isinstance(resp, Exception):
                logger.warning("Fitbit time-series fetch error for %s: %s", key, resp)
            else:
                logger.warning("Fitbit time-series fetch failed for %s: %s", key, resp.status_code)
            continue

        data = resp.json()
        # Time-series keys: "activities-calories", "activities-steps", etc.
        # Heart rate uses "activities-heart"
        for ts_key, entries in data.items():
            if not ts_key.startswith("activities-"):
                continue
            for entry in entries:
                date_str = entry["dateTime"]
                if date_str not in series:
                    series[date_str] = {}
                if key == "heart":
                    # Heart rate value is an object with restingHeartRate
                    val = entry.get("value", {})
                    if isinstance(val, dict):
                        series[date_str]["resting_heart_rate"] = val.get("restingHeartRate", 0)
                else:
                    series[date_str][key] = float(entry.get("value", 0))

    # Upsert each day that has data
    synced = 0
    for date_str, day in series.items():
        steps = int(day.get("steps", 0))
        calories_out = day.get("calories", 0)
        if steps == 0 and calories_out == 0:
            continue

        await upsert_fitbit_activity(
            db_path=db_path,
            user_id=user_id,
            date_str=date_str,
            calories_out=calories_out,
            activity_calories=day.get("activityCalories", 0),
            calories_bmr=day.get("caloriesBMR", 0),
            steps=steps,
            fairly_active_min=int(day.get("minutesFairlyActive", 0)),
            very_active_min=int(day.get("minutesVeryActive", 0)),
            resting_heart_rate=int(day.get("resting_heart_rate", 0)),
        )
        synced += 1

    # Also sync the 30-day activity log list so individual workouts
    # (Walk, Strength training, etc.) show as cards alongside Strava.
    activity_count = await _fetch_and_save_activity_logs(
        db_path=db_path,
        user_id=user_id,
        access_token=access_token,
        after_date=start,
        limit=100,
    )

    logger.info(
        "Fitbit sync complete for user %d: %d days, %d activity logs",
        user_id, synced, activity_count,
    )
    return {"synced": synced, "activity_logs": activity_count}


@router.get("/status")
async def fitbit_status(user: CurrentUser, db_path: DbPath):
    """Check whether Fitbit is connected for the current user."""
    client_id = os.environ.get(_CLIENT_ID, "")
    if not client_id:
        return {"connected": False, "available": False}

    user_id = user["user_id"]
    tokens = await get_fitbit_tokens(db_path, user_id)
    last_synced = await get_last_fitbit_sync(db_path, user_id) if tokens else None

    return {
        "connected": tokens is not None,
        "available": True,
        "fitbit_user_id": tokens["fitbit_user_id"] if tokens else None,
        "last_synced": last_synced,
        "health": "ok" if tokens else None,
    }


def _redirect_html(url: str) -> "fastapi.responses.HTMLResponse":
    """Return a small HTML page that redirects to the given URL."""
    from src.web.routes._oauth_utils import redirect_html
    return redirect_html(url)
