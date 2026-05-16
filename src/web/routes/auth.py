"""Auth API routes: Google OAuth, Email PIN, JWT management."""

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status

from src.web.rate_limit import limiter

from src.db import (
    accept_tos,
    add_to_waitlist,
    check_and_increment_global_email_cap,
    check_pin_rate_limit,
    check_verify_rate_limit,
    create_pro_subscription,
    create_web_user,
    delete_push_subscriptions_for_user,
    get_tos_acceptance,
    get_user_target,
    get_web_user_by_email,
    get_web_user_by_google_sub,
    log_event,
    SignupCapReached,
    start_trial,
    store_email_pin,
    verify_email_pin,
    CURRENT_TOS_VERSION,
)
from src.web.auth import (
    create_jwt,
    decode_jwt,
    generate_pin,
    hash_pin,
    send_pin_email,
    verify_google_id_token,
)
from src.web.constants import (
    APP_ENV,
    APP_MODE,
    BETA_MODE,
    BETA_SIGNUP_CAP,
    STAGING_ALLOWED_EMAILS,
    STAGING_ALLOWED_SUBSTRINGS,
    STAGING_REDIRECT_URL,
)
from src.web.deps import CurrentUser, DbPath
from src.db import record_email_sent
from src.web.schemas import (
    AuthResponse,
    EmailSendPinRequest,
    EmailVerifyPinRequest,
    GoogleAuthRequest,
    UserMeResponse,
    WaitlistJoinRequest,
    WaitlistJoinResponse,
)

logger = logging.getLogger("macro_app")
router = APIRouter(prefix="/auth", tags=["auth"])


def _hash_email(email: str) -> str:
    """Short stable digest of an email for log lines (PII-safe)."""
    return hashlib.sha256((email or "").encode()).hexdigest()[:12]


async def _send_welcome_email(db_path: str, user_id: int, email: str, name: str | None) -> None:
    """Fire-and-forget welcome email on signup. Errors are logged, never raised.

    Treated as transactional (no List-Unsubscribe header) since the user
    just signed up; subsequent drip emails carry the unsubscribe link.
    """
    try:
        from src.web.email_templates import SUBJECTS, welcome_html, welcome_text
        from src.web.email_scheduler import _send_email

        html = welcome_html(name or "")
        text = welcome_text(name or "")
        sent = await _send_email(
            email, SUBJECTS["welcome"], html, text,
            user_id=user_id, is_transactional=True,
        )
        if sent:
            await record_email_sent(db_path, user_id, "welcome")
            logger.info("Welcome email sent to user_id=%d", user_id)
    except Exception:
        logger.exception("Welcome email failed for user_id=%d", user_id)


def _enforce_staging_allowlist(email: str, method: str) -> None:
    """Block non-allowlisted emails from logging into staging.

    When APP_ENV=staging, only emails in STAGING_ALLOWED_EMAILS can log
    in. Everyone else gets a 403 with a redirect payload the frontend
    uses to send them to production. Case-insensitive match.

    No-op in every non-staging environment.
    """
    if APP_ENV != "staging":
        return
    normalized = email.strip().lower()
    if normalized in STAGING_ALLOWED_EMAILS:
        return
    if any(sub in normalized for sub in STAGING_ALLOWED_SUBSTRINGS):
        return
    logger.info("staging_login_blocked email_hash=%s method=%s",
                _hash_email(email), method)
    raise HTTPException(
        status_code=403,
        detail={
            "error": "staging_not_allowed",
            "redirect": STAGING_REDIRECT_URL,
            "message": (
                "Staging is restricted to the maintainer. "
                "Redirecting you to the production site."
            ),
        },
    )


SESSION_COOKIE_NAME = "__Host-macro_session"
LEGACY_SESSION_COOKIE_NAME = "macro_session"


def _set_session_cookie(response: Response, token: str) -> None:
    """Set the JWT as an httpOnly + __Host-prefixed cookie.

    A14: the `__Host-` prefix forces three constraints (path=/, Secure, no
    Domain attribute) which prevent subdomain takeover attacks from setting
    a competing same-named cookie. The host-prefixed name is always
    Secure, so this only works over HTTPS.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",  # required by __Host- prefix
    )
    # Clear the legacy unprefixed cookie so the browser doesn't keep
    # sending both. Best-effort: a stale legacy cookie scoped to a different
    # path would still be cleared by deps/get_current_user reading either.
    response.delete_cookie(LEGACY_SESSION_COOKIE_NAME, path="/macro_app")


async def _provision_subscription(db_path: str, user_id: int) -> None:
    """Seed a subscription row for a brand-new user.

    Behavior depends on the operating mode:

    - self mode  → no-op (subscription table is never read anyway)
    - hosted + beta → auto-grant free Pro (create_pro_subscription)
    - hosted + no beta → classic 7-day trial (start_trial)

    Failures are logged and swallowed so signup never fails because of
    the subscription side-effect - worst case the user lands with no row
    and get_subscription_info falls back to sensible defaults.
    """
    if APP_MODE != "hosted":
        return
    try:
        if BETA_MODE:
            await create_pro_subscription(db_path, user_id, is_og=False)
        else:
            await start_trial(db_path, user_id)
    except Exception:
        logger.warning(
            "Subscription provisioning failed for user_id=%d (beta=%s)",
            user_id, BETA_MODE,
        )


async def _handle_signup_cap(
    db_path: str,
    email: str,
    source: str,
    first_name: str | None = None,
) -> None:
    """Add the user to the waitlist, send a confirmation email, and raise 503.

    Called from the Google + email-PIN routes when create_web_user raises
    SignupCapReached. The waitlist insert is idempotent (UNIQUE(email)) so
    repeat attempts by the same user are harmless. The confirmation email
    is fire-and-forget - if Resend is down the signup flow still completes.
    """
    try:
        newly_added = await add_to_waitlist(
            db_path, email, source=source, first_name=first_name
        )
    except Exception:
        logger.warning("Waitlist insert failed for email_hash=%s", _hash_email(email), exc_info=True)
        newly_added = False
    # Observability: one log line per cap-hit so the operator can grep
    # for "signup_cap_reached" to see hit frequency + sources over time.
    logger.info(
        "signup_cap_reached source=%s cap=%d",
        source, BETA_SIGNUP_CAP,
    )
    if newly_added:
        import asyncio
        asyncio.create_task(
            _send_waitlist_confirmation_email(email, first_name)
        )
    raise HTTPException(
        status_code=503,
        detail={
            "error": "beta_full",
            "email": email,
            "cap": BETA_SIGNUP_CAP,
            "waitlist_added": True,
            "message": (
                "MacroShot is in beta. You've been added to the waitlist - "
                "we'll email you once we're ready to have you join."
            ),
        },
    )


async def _send_waitlist_confirmation_email(email: str, name: str | None) -> None:
    """Fire-and-forget confirmation email to a user who just joined the waitlist.

    Errors are logged, never raised - the /auth/waitlist response must not
    depend on Resend availability.
    """
    try:
        from src.web.email_templates import (
            SUBJECTS,
            waitlist_confirmation_html,
            waitlist_confirmation_text,
        )
        from src.web.email_scheduler import _send_email

        html = waitlist_confirmation_html(name or "")
        text = waitlist_confirmation_text(name or "")
        # Transactional: no unsubscribe header - confirms a user-initiated action.
        await _send_email(
            email, SUBJECTS["waitlist_confirmation"], html, text,
            is_transactional=True,
        )
    except Exception:
        logger.exception("Waitlist confirmation email failed for email_hash=%s", _hash_email(email))


@router.post("/waitlist", response_model=WaitlistJoinResponse)
@limiter.limit("5/hour")
async def join_waitlist(
    request: Request,
    body: WaitlistJoinRequest,
    db_path: DbPath,
):
    """Public endpoint for users to proactively join the beta waitlist.

    Idempotent: re-submitting the same email returns success but reports
    `already_subscribed=True`.  No auth required (the user has no account
    yet by definition).  Rate-limited at 5/hour per IP to prevent abuse.

    Always available regardless of APP_MODE / BETA_MODE - users can pre-
    subscribe even when the app is open or self-hosted; the row sits in
    the table for the operator to use as a marketing list.
    """
    try:
        newly_added = await add_to_waitlist(
            db_path,
            body.email,
            source="proactive_signup",
            first_name=body.first_name,
        )
    except Exception:
        logger.warning("Waitlist insert failed for email_hash=%s", _hash_email(body.email), exc_info=True)
        # Don't leak the failure to the user - pretend it worked.  Worst
        # case they think they're on the waitlist but aren't.  Better than
        # a 500 that exposes internal errors and DB schema.
        return WaitlistJoinResponse(
            ok=True,
            already_subscribed=False,
            message="You're on the waitlist. We'll email you when a spot opens.",
        )

    if newly_added:
        logger.info("Waitlist signup: email_hash=%s", _hash_email(body.email))
        msg = "You're on the waitlist. We'll email you when a spot opens."
    else:
        msg = "You're already on the waitlist. We'll email you when a spot opens."

    return WaitlistJoinResponse(
        ok=True,
        already_subscribed=not newly_added,
        message=msg,
    )


@router.post("/google", response_model=AuthResponse)
@limiter.limit("10/minute")
async def google_auth(request: Request, req: GoogleAuthRequest, response: Response, db_path: DbPath):
    """Exchange a Google ID token for a session JWT."""
    google_info = await verify_google_id_token(req.id_token)
    if not google_info:
        raise HTTPException(status_code=400, detail="Invalid Google token or Google auth not configured")

    # Normalize at boundary - same invariant as the email-PIN schemas. Google
    # already returns lowercase, but defensive lowercase + strip costs nothing.
    email = (google_info["email"] or "").strip().lower()
    _enforce_staging_allowlist(email, method="google")
    google_sub = google_info["sub"]
    full_name = google_info.get("name", "")
    g_picture = google_info.get("picture", "") or None
    name_parts = full_name.strip().split(None, 1) if full_name else []
    g_first = name_parts[0] if name_parts else None
    g_last = name_parts[1] if len(name_parts) > 1 else None

    # Look up by Google sub first, then by email
    user = await get_web_user_by_google_sub(db_path, google_sub)
    if not user:
        user = await get_web_user_by_email(db_path, email)
    is_new_user = False
    if not user:
        try:
            user_id = await create_web_user(
                db_path, email, google_sub=google_sub,
                first_name=g_first, last_name=g_last, avatar_url=g_picture,
            )
        except SignupCapReached:
            await _handle_signup_cap(
                db_path, email, source="google", first_name=g_first,
            )
            return  # _handle_signup_cap always raises, but guard against refactors
        # Seed subscription row (beta → Pro, non-beta hosted → trial, self → no-op)
        await _provision_subscription(db_path, user_id)
        user = {"user_id": user_id, "email": email, "username": email, "first_name": g_first}
        is_new_user = True
        # Fire-and-forget welcome email (don't block the auth response)
        import asyncio
        asyncio.create_task(_send_welcome_email(db_path, user_id, email, g_first))
    else:
        # Link google_sub if found by email but not yet linked
        if not user.get("google_sub") and google_sub:
            from src.db_pool import get_db
            async with get_db(db_path) as db:
                await db.execute(
                    "UPDATE web_auth SET google_sub = ? WHERE user_id = ? AND google_sub IS NULL",
                    (google_sub, user["user_id"]),
                )
                await db.commit()

        # Backfill name/avatar from Google if missing
        from src.db import set_user_profile
        updates = {}
        current_name = user.get("first_name") or ""
        if g_first and not current_name:
            updates["first_name"] = g_first
            updates["last_name"] = g_last
        if g_picture and not user.get("avatar_url"):
            updates["avatar_url"] = g_picture
        if updates:
            await set_user_profile(db_path, user["user_id"], **updates)

    token = create_jwt(user["user_id"], email)
    _set_session_cookie(response, token)
    logger.info("Login: user_id=%d method=google", user["user_id"])
    # Private telemetry: acquisition attribution - method + new-vs-returning
    await log_event(
        db_path, user["user_id"], "auth_signup" if is_new_user else "auth_login",
        metadata={"method": "google", "is_new_user": is_new_user},
    )
    return AuthResponse(user_id=user["user_id"], email=email, username=user.get("username"))


@router.post("/email/send-pin")
@limiter.limit("5/minute;10/hour")
async def send_email_pin(request: Request, req: EmailSendPinRequest, db_path: DbPath):
    """Send a 6-digit PIN to the given email address.

    The per-IP hourly cap (10/hour) is deliberate: combined with the per-email
    cap of 3/hour enforced by check_pin_rate_limit, it closes the enumeration
    channel where known emails rate-limit after 3 attempts but unknown emails
    never do. 10/hour is tight enough to frustrate a sprayer while leaving
    room for a shared household/WiFi NAT where 2–3 people might legitimately
    request PINs in one hour.
    """
    _enforce_staging_allowlist(req.email, method="email_pin_send")
    if not await check_pin_rate_limit(db_path, req.email):
        logger.warning("PIN rate limit: email_hash=%s", _hash_email(req.email))
        raise HTTPException(status_code=429, detail="Too many PIN requests. Try again in an hour.")

    # A11: global outbound-email circuit breaker. Caps total emails sent
    # across all users to GLOBAL_EMAIL_HOURLY_CAP/hour. If hit, return 503
    # without storing a PIN so we don't burn a slot in the per-email cap.
    if not await check_and_increment_global_email_cap(db_path):
        logger.warning("Global email circuit breaker tripped - refusing PIN send")
        raise HTTPException(status_code=503, detail="email service temporarily unavailable")

    pin = generate_pin()
    pin_hashed = hash_pin(pin)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

    await store_email_pin(db_path, req.email, pin_hashed, expires_at)

    sent = await send_pin_email(req.email, pin)
    if not sent:
        # A28: don't leak Resend's deliverability verdict - same response
        # whether the address was accepted or rejected. Internally log so
        # the operator still sees the failure, just not the user.
        logger.warning(
            "PIN email send failed (Resend) for email_hash=%s - returning generic 200",
            _hash_email(req.email),
        )

    return {"message": "PIN sent", "email": req.email}


@router.post("/email/verify-pin", response_model=AuthResponse)
@limiter.limit("10/minute")
async def verify_pin(request: Request, req: EmailVerifyPinRequest, response: Response, db_path: DbPath):
    """Verify the email PIN and issue a session JWT."""
    _enforce_staging_allowlist(req.email, method="email_pin_verify")
    # A4: per-email verify-attempt cap (mirrors check_pin_rate_limit). An
    # attacker who can keep requesting fresh PINs (1/hour now) could
    # otherwise spend the per-PIN attempt budget across many PINs in a row.
    if not await check_verify_rate_limit(db_path, req.email):
        logger.warning("PIN verify rate limit: email_hash=%s", _hash_email(req.email))
        raise HTTPException(status_code=429, detail="Too many verification attempts. Try again later.")
    # verify_email_pin takes the raw PIN - stored hashes are PBKDF2-salted,
    # so the function must see the raw value to re-derive with the same salt.
    valid = await verify_email_pin(db_path, req.email, req.pin)
    if not valid:
        logger.warning("PIN invalid: email_hash=%s", _hash_email(req.email))
        raise HTTPException(status_code=400, detail="Invalid or expired PIN")

    user = await get_web_user_by_email(db_path, req.email)
    is_new_user = False
    if not user:
        try:
            user_id = await create_web_user(
                db_path, req.email,
                first_name=req.first_name, last_name=req.last_name,
            )
        except SignupCapReached:
            await _handle_signup_cap(
                db_path, req.email, source="email_pin", first_name=req.first_name,
            )
            return  # _handle_signup_cap always raises, but guard against refactors
        # Seed subscription row (beta → Pro, non-beta hosted → trial, self → no-op)
        await _provision_subscription(db_path, user_id)
        user = {"user_id": user_id, "email": req.email, "username": req.email, "first_name": req.first_name}
        is_new_user = True
        # Fire-and-forget welcome email (don't block the auth response)
        import asyncio
        asyncio.create_task(_send_welcome_email(db_path, user_id, req.email, req.first_name))

    token = create_jwt(user["user_id"], req.email)
    _set_session_cookie(response, token)
    logger.info("Login: user_id=%d method=email_pin", user["user_id"])
    # Private telemetry: acquisition attribution - method + new-vs-returning
    await log_event(
        db_path, user["user_id"], "auth_signup" if is_new_user else "auth_login",
        metadata={"method": "email_pin", "is_new_user": is_new_user},
    )
    return AuthResponse(user_id=user["user_id"], email=req.email, username=user.get("username"))


@router.post("/refresh")
@limiter.limit("30/minute")
async def refresh_token(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: str | None = Cookie(default=None, alias="__Host-macro_session"),
    legacy_session: str | None = Cookie(default=None, alias="macro_session"),
):
    """Refresh the JWT session.

    A16: enforce a 30-day max session lifetime. The original-issued time
    (`iat`) is read from the current cookie's payload; if it's older than
    JWT_MAX_SESSION_DAYS, refuse to mint a new token so a stolen cookie
    can't grant indefinite access via repeated /refresh calls.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    from src.web.auth import JWT_MAX_SESSION_DAYS

    raw = session or legacy_session
    original_iat: int | float | None = None
    if raw:
        payload = decode_jwt(raw)
        iat = payload.get("iat") if payload else None
        if isinstance(iat, (int, float)):
            issued = _dt.fromtimestamp(iat, tz=_tz.utc)
            if _dt.now(_tz.utc) - issued > _td(days=JWT_MAX_SESSION_DAYS):
                raise HTTPException(
                    status_code=401,
                    detail="Session expired - please sign in again.",
                )
            # A16: preserve the ORIGINAL iat across refreshes so the
            # 30-day max-session cap is actually enforced. Without this,
            # create_jwt would reset iat to "now" on every refresh and
            # the cap above would never trigger (the iat read from the
            # current cookie would always be < 30 days old).
            original_iat = iat

    token = create_jwt(user["user_id"], user["email"], original_iat=original_iat)
    _set_session_cookie(response, token)
    return {"message": "Session refreshed"}


@router.post("/logout")
@limiter.limit("30/minute")
async def logout(
    request: Request,
    response: Response,
    db_path: DbPath,
    session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    legacy_session: str | None = Cookie(default=None, alias=LEGACY_SESSION_COOKIE_NAME),
):
    # Honor whichever cookie is present (host-prefixed preferred, legacy fallback).
    session = session or legacy_session
    """Clear the session cookie and wipe this user's push subscriptions.

    Wiping server-side push rows is a safety net: the client also calls
    unsubscribeFromPush() on the browser, but if that fails or is skipped
    (offline, iOS PWA quirks, different browser session), we still make
    sure the previous owner stops receiving reminders after logging out.
    """
    if session:
        payload = decode_jwt(session)
        if payload:
            try:
                await delete_push_subscriptions_for_user(db_path, int(payload["sub"]))
            except Exception:
                logger.exception("Failed to wipe push subs on logout")
            # Revoke the token server-side so a captured cookie can't be
            # replayed after the user logs out. Skip if jti is missing
            # (tokens issued before this change - they just expire naturally).
            jti = payload.get("jti")
            if jti:
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    exp_ts = payload.get("exp")
                    exp_str = (
                        _dt.fromtimestamp(exp_ts, tz=_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                        if isinstance(exp_ts, (int, float))
                        else _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
                    )
                    from src.db import revoke_jwt
                    await revoke_jwt(db_path, jti, int(payload["sub"]), exp_str)
                    logger.info("JWT revoked on logout: user=%s jti=%s", payload["sub"], jti[:8])
                except Exception:
                    logger.exception("Failed to revoke JWT on logout")
    # __Host- prefix REQUIRES Secure on every Set-Cookie or browsers ignore
    # it; without these flags the delete is dropped and the session cookie
    # stays in the browser even after /auth/logout returns 200.
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")
    response.delete_cookie(LEGACY_SESSION_COOKIE_NAME, path="/macro_app", httponly=True)
    return {"message": "Logged out"}


@router.get("/me", response_model=UserMeResponse)
async def get_me(user: CurrentUser, db_path: DbPath):
    """Get current user info."""
    targets = await get_user_target(db_path, user["user_id"])
    accepted_version = await get_tos_acceptance(db_path, user["user_id"])
    return UserMeResponse(
        user_id=user["user_id"],
        email=user["email"],
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        avatar_url=user.get("avatar_url"),
        google_linked=bool(user.get("google_sub")),
        has_targets=targets is not None,
        tos_accepted=accepted_version == CURRENT_TOS_VERSION,
    )


@router.post("/accept-tos")
@limiter.limit("10/minute")
async def accept_terms(request: Request, user: CurrentUser, db_path: DbPath):
    """Record that the user accepted the current TOS."""
    await accept_tos(db_path, user["user_id"])
    return {"accepted": True, "version": CURRENT_TOS_VERSION}


# A12: List-Unsubscribe one-click handler. Routed under /auth so it
# inherits the same prefix; no auth required - identity is verified by
# the HMAC token in the URL.
@router.api_route("/unsubscribe", methods=["GET", "POST"])
@limiter.limit("30/minute")
async def newsletter_unsubscribe(
    request: Request,
    db_path: DbPath,
):
    """Flip newsletter_opt_in=False for a user identified by HMAC token.

    HMAC = sha256(JWT_SECRET, f"unsub:{user_id}")[:32]. Constant-time
    compared against the `t` query param. Both GET (link click) and POST
    (List-Unsubscribe-Post one-click) are accepted.
    """
    import hashlib as _hashlib
    import hmac as _hmac

    from src.db import set_user_prefs
    from src.web.auth import JWT_SECRET

    user_id_raw = request.query_params.get("u", "")
    token = request.query_params.get("t", "")
    if not user_id_raw or not token:
        raise HTTPException(status_code=400, detail="Missing parameters")
    try:
        user_id = int(user_id_raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user id")

    expected = _hmac.new(
        JWT_SECRET.encode(), f"unsub:{user_id}".encode(), _hashlib.sha256,
    ).hexdigest()[:32]
    if not _hmac.compare_digest(expected, token):
        raise HTTPException(status_code=403, detail="Invalid token")

    try:
        await set_user_prefs(db_path, user_id, newsletter_opt_in=0)
    except Exception:
        logger.exception("Unsubscribe failed for user_id=%d", user_id)
    return {"ok": True, "message": "You're unsubscribed."}
