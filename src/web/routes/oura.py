"""Oura Ring OAuth + sync routes.

All credential-dependent operations are stubbed behind OURA_CLIENT_ID
and OURA_CLIENT_SECRET environment variable checks. When these are not
set, endpoints return a helpful "not configured" message.

Oura uses classic OAuth 2.0 (no PKCE):
  - State stored in oura_oauth_state (single-use, 10min TTL)
  - Token exchange uses client_id + client_secret in form body
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

from src.db import (
    ExternalAccountAlreadyLinked,
    delete_oura_tokens,
    get_and_delete_oura_oauth_state,
    get_last_oura_sync,
    get_oura_activity,
    get_oura_tokens,
    get_oura_tokens_by_oura_user_id,
    log_event,
    save_oura_oauth_state,
    save_oura_tokens,
    upsert_oura_activity,
    upsert_workout,
)
from fastapi import Depends as _Depends
from src.web.deps import CurrentUser, DbPath, require_recent_auth
from src.web.rate_limit import limiter

_background_tasks: set[asyncio.Task] = set()

# Per-user locks to serialize Oura token refreshes. Oura refresh tokens are
# single-use - concurrent refreshes would both read the same RT, one wins,
# the loser gets invalid_grant and the tokens get deleted.
_refresh_locks: dict[int, asyncio.Lock] = {}


def _get_refresh_lock(user_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[user_id] = lock
    return lock


logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/oura", tags=["oura"])

# Oura OAuth / API URLs
OURA_AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"
OURA_TOKEN_URL = "https://api.ouraring.com/oauth/token"
OURA_REVOKE_URL = "https://api.ouraring.com/oauth/revoke"
OURA_API_BASE = "https://api.ouraring.com/v2"

_CLIENT_ID = "OURA_CLIENT_ID"
_CLIENT_SECRET = "OURA_CLIENT_SECRET"

_STATE_TTL_MIN = 10

# Scopes requested at OAuth time. Match the set registered in the Oura
# developer portal so users grant all of them once and we don't need to
# force a reconnect when we add sleep/readiness/session features later.
_OURA_SCOPES = "personal daily workout heartrate session"

# A6: HttpOnly OAuth-binding cookie (mirrors fitbit / strava).
_OAUTH_SESSION_COOKIE = "__Host-oura_oauth_session"
_OAUTH_SESSION_TTL_S = _STATE_TTL_MIN * 60

# A21: PKCE verifier length (RFC 7636 recommends 43-128).
_VERIFIER_LENGTH = 64


def _hash_oauth_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


# A20: TTL cache of seen webhook signatures so a captured POST can't be
# replayed. Single-worker deploy assumption - in-process dict is enough.
_WEBHOOK_SIG_TTL_S = 86400
_WEBHOOK_SIG_MAX = 2048
_seen_webhook_sigs: dict[str, float] = {}


def _replay_check(sig: str) -> bool:
    import time as _time
    now = _time.time()
    if len(_seen_webhook_sigs) > _WEBHOOK_SIG_MAX:
        cutoff = now - _WEBHOOK_SIG_TTL_S
        for k in [k for k, t in _seen_webhook_sigs.items() if t < cutoff]:
            _seen_webhook_sigs.pop(k, None)
        if len(_seen_webhook_sigs) > _WEBHOOK_SIG_MAX:
            for k in sorted(_seen_webhook_sigs, key=_seen_webhook_sigs.get)[:512]:
                _seen_webhook_sigs.pop(k, None)
    last = _seen_webhook_sigs.get(sig)
    if last and (now - last) < _WEBHOOK_SIG_TTL_S:
        return False
    _seen_webhook_sigs[sig] = now
    return True


def _safe_provider_error(resp) -> str:
    """A9: see fitbit.py:_safe_provider_error."""
    try:
        data = resp.json()
        if isinstance(data, dict):
            return str(data.get("error", "<no body>"))[:120]
    except Exception:
        pass
    return "<no body>"


def _generate_pkce() -> tuple[str, str]:
    """Generate (code_verifier, code_challenge=S256(code_verifier))."""
    code_verifier = secrets.token_urlsafe(_VERIFIER_LENGTH)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _get_credentials() -> tuple[str, str]:
    """Return (client_id, client_secret) or raise 501 if not configured."""
    client_id = os.environ.get(_CLIENT_ID, "")
    client_secret = os.environ.get(_CLIENT_SECRET, "")
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=501,
            detail="Oura integration not configured. Set OURA_CLIENT_ID and OURA_CLIENT_SECRET environment variables.",
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
                detail="Oura integration requires BASE_URL or DOMAIN to be set.",
            )
        base_path = os.environ.get("BASE_PATH", "/macro_app")
        base_url = f"https://{domain}{base_path}"
    return f"{base_url}/api/v1/oura/callback"


async def _refresh_token_if_needed(db_path: str, tokens: dict) -> str:
    """Return a valid access token, refreshing if expired.

    IMPORTANT: Oura refresh tokens are single-use. Every refresh response
    contains a new refresh_token that must be saved immediately.

    A per-user asyncio lock serializes concurrent refreshes.
    """
    expires_at_str = tokens["expires_at"]
    expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    if expires_at > datetime.now(timezone.utc) + timedelta(seconds=60):
        return tokens["access_token"]

    user_id = tokens["user_id"]
    async with _get_refresh_lock(user_id):
        fresh = await get_oura_tokens(db_path, user_id)
        if fresh is None:
            raise HTTPException(
                status_code=401,
                detail="Oura connection expired. Please reconnect.",
            )
        fresh_expires = datetime.strptime(
            fresh["expires_at"], "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=timezone.utc)
        if fresh_expires > datetime.now(timezone.utc) + timedelta(seconds=60):
            return fresh["access_token"]

        client_id, client_secret = _get_credentials()

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                OURA_TOKEN_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": fresh["refresh_token"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )

        if resp.status_code != 200:
            logger.error("Oura token refresh failed: %s %s", resp.status_code, _safe_provider_error(resp))
            if resp.status_code in (400, 401):
                await delete_oura_tokens(db_path, user_id)
                logger.warning("Deleted revoked Oura tokens for user %d", user_id)
                raise HTTPException(status_code=401, detail="Oura connection expired. Please reconnect.")
            raise HTTPException(status_code=502, detail="Oura token refresh failed")

        data = resp.json()
        expires_in = data.get("expires_in", 86400)  # default 24h
        new_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).strftime("%Y-%m-%d %H:%M:%S")

        await save_oura_tokens(
            db_path=db_path,
            user_id=user_id,
            oura_user_id=fresh["oura_user_id"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=new_expires_at,
            scope=fresh.get("scope", ""),
        )
        logger.info("Oura token refreshed for user %d", user_id)
        return data["access_token"]


# ── Sync primitives (shared between manual /sync, auto-sync, and background loop) ──


async def _fetch_oura_personal_info(access_token: str) -> str:
    """Return Oura's internal user id for the authenticated token."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{OURA_API_BASE}/usercollection/personal_info",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        return ""
    return str(resp.json().get("id", ""))


def _parse_iso(dt: str) -> datetime | None:
    """Parse an ISO 8601 datetime (with or without timezone)."""
    if not dt:
        return None
    try:
        # Oura returns e.g. "2026-04-14T10:30:00+00:00" or "...Z"
        if dt.endswith("Z"):
            dt = dt[:-1] + "+00:00"
        return datetime.fromisoformat(dt)
    except Exception:
        return None


async def sync_oura_for_user(
    db_path: str, user_id: int, days: int = 7
) -> dict:
    """Sync recent Oura data for a single user.

    Fetches daily_activity, sleep (for RHR), and workouts in parallel.
    Returns counts of what was synced.
    """
    tokens = await get_oura_tokens(db_path, user_id)
    if not tokens:
        return {"days": 0, "workouts": 0}

    access_token = await _refresh_token_if_needed(db_path, tokens)

    today = datetime.now(timezone.utc).date()
    start = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {access_token}"}

    endpoints = {
        "daily_activity": f"{OURA_API_BASE}/usercollection/daily_activity?start_date={start}&end_date={end}",
        "sleep": f"{OURA_API_BASE}/usercollection/sleep?start_date={start}&end_date={end}",
        "workout": f"{OURA_API_BASE}/usercollection/workout?start_date={start}&end_date={end}",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            responses = await asyncio.gather(
                *[client.get(url, headers=headers) for url in endpoints.values()],
                return_exceptions=True,
            )
    except Exception:
        logger.exception("Oura fetch error for user %d", user_id)
        return {"days": 0, "workouts": 0}

    resp_by_key = dict(zip(endpoints.keys(), responses))

    # ── Daily activity → {date: {calories_out, activity_calories, steps, fairly_active_min, very_active_min}}
    activity_by_date: dict[str, dict] = {}
    daily_resp = resp_by_key.get("daily_activity")
    if not isinstance(daily_resp, Exception) and daily_resp.status_code == 200:
        for item in daily_resp.json().get("data", []):
            day = item.get("day")
            if not day:
                continue
            # Oura: medium/high activity are seconds; convert to minutes.
            medium_sec = item.get("medium_activity_time", 0) or 0
            high_sec = item.get("high_activity_time", 0) or 0
            activity_by_date[day] = {
                "calories_out": float(item.get("total_calories", 0) or 0),
                "activity_calories": float(item.get("active_calories", 0) or 0),
                "steps": int(item.get("steps", 0) or 0),
                "fairly_active_min": int(medium_sec // 60),
                "very_active_min": int(high_sec // 60),
            }
    elif isinstance(daily_resp, Exception):
        logger.warning("Oura daily_activity fetch error for user %d: %s", user_id, daily_resp)
    else:
        logger.warning("Oura daily_activity fetch %d for user %d", daily_resp.status_code, user_id)

    # ── Sleep → pick longest sleep per day, use its lowest_heart_rate as RHR
    rhr_by_date: dict[str, int] = {}
    longest_duration: dict[str, int] = {}
    sleep_resp = resp_by_key.get("sleep")
    if not isinstance(sleep_resp, Exception) and sleep_resp.status_code == 200:
        for item in sleep_resp.json().get("data", []):
            day = item.get("day")
            if not day:
                continue
            dur = int(item.get("total_sleep_duration", 0) or 0)
            lhr = item.get("lowest_heart_rate")
            if lhr is None:
                continue
            if dur >= longest_duration.get(day, -1):
                longest_duration[day] = dur
                rhr_by_date[day] = int(lhr)
    elif isinstance(sleep_resp, Exception):
        logger.warning("Oura sleep fetch error for user %d: %s", user_id, sleep_resp)

    # ── Upsert daily summary (merge activity + RHR) ──
    days_synced = 0
    all_dates = set(activity_by_date.keys()) | set(rhr_by_date.keys())
    for date_str in all_dates:
        act = activity_by_date.get(date_str, {})
        rhr = rhr_by_date.get(date_str, 0)
        # Skip dates with no meaningful activity and no RHR - nothing to store
        if not act and not rhr:
            continue
        await upsert_oura_activity(
            db_path=db_path,
            user_id=user_id,
            date_str=date_str,
            calories_out=act.get("calories_out", 0),
            activity_calories=act.get("activity_calories", 0),
            steps=act.get("steps", 0),
            fairly_active_min=act.get("fairly_active_min", 0),
            very_active_min=act.get("very_active_min", 0),
            resting_heart_rate=rhr,
        )
        days_synced += 1

    # ── Workouts → workout_logs with source='oura' ──
    workouts_synced = 0
    workout_resp = resp_by_key.get("workout")
    if not isinstance(workout_resp, Exception) and workout_resp.status_code == 200:
        for w in workout_resp.json().get("data", []):
            ext_id = w.get("id")
            if not ext_id:
                continue
            start_dt = _parse_iso(w.get("start_datetime", ""))
            end_dt = _parse_iso(w.get("end_datetime", ""))
            if not start_dt:
                continue
            duration_sec = (
                int((end_dt - start_dt).total_seconds()) if end_dt else 0
            )
            logged_at = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
            activity_type = (w.get("activity") or "workout").replace("_", " ").title()
            await upsert_workout(
                db_path=db_path,
                user_id=user_id,
                source="oura",
                external_id=str(ext_id),
                activity_type=activity_type,
                name=w.get("label") or activity_type,
                started_at=w.get("start_datetime", ""),
                duration_sec=duration_sec,
                calories_burned=float(w.get("calories", 0) or 0),
                distance_m=float(w.get("distance", 0) or 0),
                avg_heart_rate=0,  # Oura /workout doesn't include avg HR
                logged_at=logged_at,
            )
            workouts_synced += 1
    elif isinstance(workout_resp, Exception):
        logger.warning("Oura workout fetch error for user %d: %s", user_id, workout_resp)

    return {"days": days_synced, "workouts": workouts_synced}


# Staleness threshold for auto-sync (avoid hitting Oura API on every page load)
_AUTO_SYNC_STALE_MINUTES = 15


async def oura_needs_sync(db_path: str, user_id: int, date_str: str) -> bool:
    """Cheap probe: would auto_sync_oura_today actually hit the network?

    Mirrors early-return checks in auto_sync_oura_today. Returns False on
    any error.
    """
    try:
        if not os.environ.get(_CLIENT_ID, ""):
            return False
        tokens = await get_oura_tokens(db_path, user_id)
        if not tokens:
            return False
        existing = await get_oura_activity(db_path, user_id, date_str)
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


async def auto_sync_oura_today(db_path: str, user_id: int, date_str: str) -> None:
    """Auto-sync today's Oura data if stale (>15 min) or missing.

    Safe to call from anywhere - returns silently on any error.
    """
    try:
        if not os.environ.get(_CLIENT_ID, ""):
            return

        tokens = await get_oura_tokens(db_path, user_id)
        if not tokens:
            return

        existing = await get_oura_activity(db_path, user_id, date_str)
        if existing and existing.get("fetched_at"):
            fetched_at = datetime.strptime(
                existing["fetched_at"], "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_at < timedelta(
                minutes=_AUTO_SYNC_STALE_MINUTES
            ):
                return

        # Short sync window - just today + yesterday to cover midnight rollover
        await sync_oura_for_user(db_path, user_id, days=2)
        logger.info("Oura auto-sync for user %d on %s", user_id, date_str)

    except Exception:
        logger.debug("Oura auto-sync skipped for user %d", user_id, exc_info=True)


# ── HTTP routes ────────────────────────────────────────────────────


@router.get("/connect")
@limiter.limit("10/hour")
async def oura_connect(
    request: Request,
    response: Response,
    user: CurrentUser,
    db_path: DbPath,
):
    """Return the Oura OAuth authorization URL.

    Generates a single-use state token stored in oura_oauth_state (10min TTL).
    A21: also generates an S256 PKCE pair so Oura matches Fitbit's flow.
    A6: oauth_session cookie binds the round-trip to the originating session.
    """
    client_id, _ = _get_credentials()

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = _generate_pkce()
    oauth_session_token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=_STATE_TTL_MIN)).strftime("%Y-%m-%d %H:%M:%S")

    await save_oura_oauth_state(
        db_path=db_path,
        state=state,
        user_id=user["user_id"],
        expires_at=expires_at,
        session_token_hash=_hash_oauth_session_token(oauth_session_token),
        code_verifier=code_verifier,
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
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _get_redirect_uri(),
        "scope": _OURA_SCOPES,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{OURA_AUTH_URL}?{urlencode(params)}"
    return {"auth_url": auth_url}


@router.get("/callback")
async def oura_callback(
    db_path: DbPath,
    code: str = Query(...),
    state: str = Query(...),
    oauth_session: str | None = Cookie(default=None, alias=_OAUTH_SESSION_COOKIE),
):
    """Handle the OAuth callback from Oura.

    Exchanges the authorization code for tokens and saves them.
    Returns an HTML page that redirects back to Connected Apps.
    """
    client_id, client_secret = _get_credentials()

    state_row = await get_and_delete_oura_oauth_state(db_path, state)
    if state_row is None:
        # Clear the binding cookie on rejection so it doesn't linger past
        # its 10-min TTL after a state-not-found rejection.
        from fastapi.responses import JSONResponse
        resp = JSONResponse(
            status_code=400,
            content={"detail": "Invalid or expired OAuth state. Please try connecting again."},
        )
        # __Host- prefix REQUIRES Secure on every Set-Cookie (deletes too).
        resp.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
        return resp
    expected_hash = state_row.get("session_token_hash")
    if expected_hash:
        if not oauth_session or _hash_oauth_session_token(oauth_session) != expected_hash:
            logger.warning("Oura callback rejected: oauth session cookie mismatch")
            from fastapi.responses import JSONResponse
            resp = JSONResponse(
                status_code=400,
                content={"detail": "OAuth session mismatch. Please try connecting again."},
            )
            resp.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
            return resp
    user_id = state_row["user_id"]
    code_verifier = state_row.get("code_verifier") or ""

    token_payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _get_redirect_uri(),
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if code_verifier:
        token_payload["code_verifier"] = code_verifier

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            OURA_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=token_payload,
        )

    if resp.status_code != 200:
        logger.error("Oura token exchange failed: %s %s", resp.status_code, _safe_provider_error(resp))
        raise HTTPException(
            status_code=502,
            detail=f"Oura token exchange failed (HTTP {resp.status_code})",
        )

    data = resp.json()
    expires_in = data.get("expires_in", 86400)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=expires_in)).strftime("%Y-%m-%d %H:%M:%S")

    # Fetch Oura user id (stable identifier for webhook lookup)
    oura_user_id = await _fetch_oura_personal_info(data["access_token"])

    try:
        await save_oura_tokens(
            db_path=db_path,
            user_id=user_id,
            oura_user_id=oura_user_id,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=expires_at,
            scope=data.get("scope", _OURA_SCOPES),
        )
    except ExternalAccountAlreadyLinked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logger.info("Oura connected for user %d (oura_user=%s)", user_id, oura_user_id)

    await log_event(
        db_path, user_id, "integration_connected",
        metadata={"source": "oura"},
    )

    # Kick off an immediate sync so first-connect data lands quickly
    task = asyncio.create_task(_initial_sync(db_path, user_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    base_path = os.environ.get("BASE_PATH", "/macro_app")
    redirect = _redirect_html(f"{base_path}/settings/connected-apps?oura=connected")
    redirect.delete_cookie(_OAUTH_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return redirect


async def _initial_sync(db_path: str, user_id: int) -> None:
    try:
        await sync_oura_for_user(db_path, user_id, days=7)
    except Exception:
        logger.debug("Oura initial sync failed for user %d", user_id, exc_info=True)


@router.get("/webhook")
async def oura_webhook_verify(
    challenge: str = Query("", alias="challenge"),
    verification_token: str = Query("", alias="verification_token"),
):
    """Oura webhook subscription verification.

    When creating a subscription, Oura pings this URL with a `challenge`
    and `verification_token` - we must echo the challenge back.
    """
    from fastapi.responses import JSONResponse
    expected = os.environ.get("OURA_VERIFY_TOKEN", "")
    if not expected or verification_token != expected:
        raise HTTPException(status_code=404, detail="Verification failed")
    return JSONResponse({"challenge": challenge})


@router.post("/webhook")
async def oura_webhook_event(request: Request):
    """Handle Oura webhook notification events.

    Oura pushes JSON like:
      {"event_type": "create", "data_type": "daily_activity",
       "object_id": "...", "user_id": "oura-user-uuid", "event_time": "..."}

    We acknowledge with 200 quickly and fetch the data asynchronously.

    Signature verification: Oura signs POST bodies with HMAC-SHA256 using the
    subscription's verification_token as the key; signature is delivered in
    the `X-Oura-Signature` header. We enforce it when the header is present.
    If the header is missing we reject (safer default than accept-anything);
    a legitimate notification from Oura always includes it.
    """
    from fastapi.responses import Response
    import hmac as _hmac
    import hashlib as _hashlib

    verify_token = os.environ.get("OURA_VERIFY_TOKEN")
    if not verify_token:
        logger.warning("Oura webhook ignored: OURA_VERIFY_TOKEN not configured")
        return Response(status_code=204)

    raw_body = await request.body()
    sig_header = request.headers.get("X-Oura-Signature", "") or request.headers.get("x-oura-signature", "")
    if not sig_header:
        logger.warning("Oura webhook rejected: missing X-Oura-Signature header")
        return Response(status_code=403)
    expected = _hmac.new(verify_token.encode(), raw_body, _hashlib.sha256).hexdigest()
    # Oura may prefix the sig with "v1=" per webhook best-practice; strip if present.
    provided = sig_header.split("=", 1)[1] if "=" in sig_header else sig_header
    if not _hmac.compare_digest(expected, provided):
        logger.warning("Oura webhook rejected: signature mismatch")
        return Response(status_code=403)

    # A20: drop replays of the same signed POST within 24h.
    if not _replay_check(provided):
        logger.warning("Oura webhook: replay rejected (signature seen recently)")
        return Response(status_code=204)

    try:
        import json as _json
        body = _json.loads(raw_body) if raw_body else {}
    except Exception:
        return Response(status_code=400)

    notifications = body if isinstance(body, list) else [body]
    logger.info("Oura webhook received: %d notification(s)", len(notifications))

    from src.web.deps import DB_PATH
    db_path = DB_PATH
    for notification in notifications:
        oura_user_id = notification.get("user_id", "")
        data_type = notification.get("data_type", "")
        if not oura_user_id or not data_type:
            continue
        tokens = await get_oura_tokens_by_oura_user_id(db_path, oura_user_id)
        if not tokens:
            logger.warning("Oura webhook: unknown user_id=%s, ignoring", oura_user_id)
            continue
        # Refresh the last couple of days for the affected user.
        task = asyncio.create_task(
            sync_oura_for_user(db_path, tokens["user_id"], days=2)
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    return Response(status_code=204)


@router.delete("/disconnect", dependencies=[_Depends(require_recent_auth)])
async def oura_disconnect(user: CurrentUser, db_path: DbPath):
    """Disconnect Oura - revokes tokens via Oura API and removes stored tokens."""
    client_id, client_secret = _get_credentials()

    user_id = user["user_id"]
    tokens = await get_oura_tokens(db_path, user_id)
    if not tokens:
        raise HTTPException(status_code=404, detail="Oura is not connected")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                OURA_REVOKE_URL,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "access_token": tokens["access_token"],
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        logger.info("Oura token revoked for user %d", user_id)
    except Exception:
        logger.warning(
            "Oura revoke API call failed for user %d (continuing with local disconnect)",
            user_id,
        )

    await delete_oura_tokens(db_path, user_id)
    logger.info("Oura disconnected for user %d", user_id)
    return {"status": "disconnected"}


@router.post("/sync")
@limiter.limit("3/minute")
async def oura_sync(request: Request, user: CurrentUser, db_path: DbPath):
    """Manually sync recent Oura data (last 30 days)."""
    _get_credentials()

    user_id = user["user_id"]
    tokens = await get_oura_tokens(db_path, user_id)
    if not tokens:
        raise HTTPException(status_code=404, detail="Oura is not connected")

    result = await sync_oura_for_user(db_path, user_id, days=30)
    logger.info(
        "Oura manual sync for user %d: %d days, %d workouts",
        user_id, result["days"], result["workouts"],
    )
    # Return `synced` as days to match frontend expectations
    return {"synced": result["days"], "workouts": result["workouts"]}


@router.get("/status")
async def oura_status(user: CurrentUser, db_path: DbPath):
    """Check whether Oura is connected for the current user."""
    client_id = os.environ.get(_CLIENT_ID, "")
    if not client_id:
        return {"connected": False, "available": False}

    user_id = user["user_id"]
    tokens = await get_oura_tokens(db_path, user_id)
    last_synced = await get_last_oura_sync(db_path, user_id) if tokens else None

    return {
        "connected": tokens is not None,
        "available": True,
        "oura_user_id": tokens["oura_user_id"] if tokens else None,
        "last_synced": last_synced,
        "health": "ok" if tokens else None,
    }


def _redirect_html(url: str):
    from src.web.routes._oauth_utils import redirect_html
    return redirect_html(url)
