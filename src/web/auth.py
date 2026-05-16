"""JWT authentication + Google OAuth + Email PIN verification."""

import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

logger = logging.getLogger("macro_app")

# --- Configuration ---
_DEV_SECRET = "dev-secret-change-in-production"
JWT_SECRET = os.environ.get("JWT_SECRET", _DEV_SECRET)

# Production indicators: any of these env vars being present means we're
# running in a real (production / staging) deployment, not local dev.
_PRODUCTION_INDICATORS = (
    "RESEND_API_KEY", "DOMAIN", "GOOGLE_CLIENT_ID", "BASE_URL",
)
_APP_ENV = os.environ.get("APP_ENV", "").strip().lower()
_PRODUCTION_LIKE = (
    _APP_ENV in {"production", "staging"}
    or any(os.environ.get(k) for k in _PRODUCTION_INDICATORS)
)

if JWT_SECRET == _DEV_SECRET:
    if _PRODUCTION_LIKE:
        raise RuntimeError(
            "FATAL: JWT_SECRET env var must be set in production/staging. "
            "Refusing to start with insecure default."
        )
    logger.warning("JWT_SECRET not set - using insecure default. OK for local dev only.")
elif _PRODUCTION_LIKE and len(JWT_SECRET) < 32:
    # Even a custom secret is unsafe if too short. 32 bytes is the minimum
    # for HMAC-SHA256 (key length should match digest size).
    raise RuntimeError(
        f"FATAL: JWT_SECRET must be at least 32 characters in production/staging "
        f"(got {len(JWT_SECRET)}). Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
    )
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_DAYS = 7
# Maximum lifetime of a JWT chain (issued + refresh-renewed) before the user
# must re-login. Enforced in /auth/refresh: if `iat` (original-issued time)
# is older than this, refresh is refused.
JWT_MAX_SESSION_DAYS = 30
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "")
APP_URL = os.environ.get("APP_URL", "")
# Human-readable host text for email footers - just the hostname from
# APP_URL (keeps the subdomain, drops the scheme + path). Falls back to
# "MacroShot" so the PIN email still has a sensible link label when the
# operator hasn't configured APP_URL yet.
_APP_DOMAIN_TEXT = (
    APP_URL.replace("https://", "").replace("http://", "").split("/")[0]
    if APP_URL else "MacroShot"
)


def create_jwt(
    user_id: int,
    email: str | None = None,  # noqa: ARG001
    original_iat: int | float | None = None,
) -> str:
    """Create a signed JWT token with a unique `jti` so it can be revoked.

    A30: `email` is accepted for backwards-compatible signature but no
    longer embedded in the payload. Callers always re-load the user from
    the DB via the `sub` claim, so the dead claim only widened the trust
    surface (a future bug consuming `payload["email"]` could trust a
    stale value after an admin email-rewrite migration).

    A16: when refreshing, callers MUST pass `original_iat` (the `iat`
    claim from the previous token) so the original-issued time is
    preserved across the refresh chain. Without this, every /refresh
    would reset `iat`, defeating the JWT_MAX_SESSION_DAYS cap and
    letting a stolen cookie be refreshed indefinitely. New logins
    (Google / PIN verify) leave `original_iat` as None so iat is
    set to "now".
    """
    now = datetime.now(timezone.utc)
    if original_iat is not None and isinstance(original_iat, (int, float)):
        iat_value = datetime.fromtimestamp(float(original_iat), tz=timezone.utc)
    else:
        iat_value = now
    payload = {
        "sub": str(user_id),
        "exp": now + timedelta(days=JWT_EXPIRY_DAYS),
        "iat": iat_value,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_jwt(token: str) -> dict | None:
    """Decode and verify a JWT token. Returns payload or None if invalid."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# PIN hashing - PBKDF2-HMAC-SHA256 with a random per-pin salt so a leaked
# `email_pins` table can't be brute-forced against the 1M-key 6-digit space
# with a precomputed SHA-256 table.  Self-contained format keeps one column
# in DB: `iterations$salt_hex$hash_hex`.  Iteration count is tuned low
# enough to stay under ~50ms per login on the e2-micro VM.
_PBKDF2_ITERATIONS = 100_000
_PBKDF2_SALT_BYTES = 16


def hash_pin(pin: str) -> str:
    """Hash a PIN with PBKDF2-HMAC-SHA256 + fresh random salt."""
    salt = secrets.token_bytes(_PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_pin_hash(pin: str, stored: str) -> bool:
    """Constant-time check of a raw PIN against a hash_pin() output.

    Falls back to legacy SHA-256 (no `$` separators) so PINs issued
    right before the hashing upgrade deployed can still verify. Those
    rows expire within 10 minutes, so this branch is short-lived.
    """
    import hmac as _hmac
    if not stored:
        return False
    if "$" in stored:
        try:
            iters_s, salt_hex, hash_hex = stored.split("$", 2)
            iterations = int(iters_s)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except (ValueError, AttributeError):
            return False
        computed = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations)
        return _hmac.compare_digest(computed, expected)
    # Legacy SHA-256 path (temporary - in-flight PINs from before this upgrade).
    legacy = hashlib.sha256(pin.encode()).hexdigest()
    return _hmac.compare_digest(legacy, stored)


def generate_pin() -> str:
    """Generate a 6-digit PIN."""
    return f"{secrets.randbelow(1000000):06d}"


async def verify_google_id_token(token: str) -> dict | None:
    """Verify a Google ID token and return user info."""
    import asyncio

    if not GOOGLE_CLIENT_ID:
        logger.warning("GOOGLE_CLIENT_ID not configured, Google auth disabled")
        return None

    from google.auth.transport import requests
    from google.oauth2 import id_token as google_id_token

    try:
        # Run blocking I/O in a thread to avoid blocking the event loop
        idinfo = await asyncio.to_thread(
            google_id_token.verify_oauth2_token,
            token, requests.Request(), GOOGLE_CLIENT_ID,
        )
        # SECURITY: refuse tokens whose email isn't verified by Google.
        # Without this, an attacker could provision a Workspace account
        # claiming any address (those tokens can carry
        # email_verified=false) and call /auth/google to take over an
        # existing email-PIN account that uses the same address.
        # Consumer Gmail tokens always carry email_verified=true; a
        # missing/false claim is the suspicious case we reject.
        if not idinfo.get("email_verified"):
            logger.warning("Google ID token rejected: email_verified is not true")
            return None
        if not idinfo.get("email"):
            logger.warning("Google ID token rejected: missing email claim")
            return None
        return {
            "email": idinfo["email"],
            "sub": idinfo["sub"],
            "name": idinfo.get("name", ""),
            "picture": idinfo.get("picture", ""),
        }
    except ValueError:
        logger.warning("Invalid Google ID token")
        return None


def _render_pin_email_html(pin: str) -> str:
    """Render the branded PIN email.

    Inline styles only - email clients strip <style> and external CSS.
    Table-based layout for Outlook/older clients. Max width ~560px for
    mobile. Dark-on-light works in every client; we don't try to honor
    dark-mode media queries because support is inconsistent.
    """
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your MacroShot verification code</title>
</head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#0f172a;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">
Your MacroShot code is {pin}. Expires in 10 minutes.
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f5f7;padding:32px 16px;">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:16px;border:1px solid #e5e7eb;box-shadow:0 1px 3px rgba(15,23,42,0.04);overflow:hidden;">
<tr><td style="padding:32px 40px 8px;text-align:center;">
<a href="{APP_URL}" style="text-decoration:none;color:#0f172a;">
<img src="{APP_URL}icons/icon-192.png"
     width="64" height="64" alt="MacroShot"
     style="display:block;margin:0 auto 12px;width:64px;height:64px;border:0;border-radius:14px;outline:none;">
<div style="font-size:22px;font-weight:700;letter-spacing:0.3px;color:#0f172a;">
MacroShot
</div>
</a>
</td></tr>
<tr><td style="padding:24px 40px 8px;text-align:center;">
<h1 style="margin:0;font-size:20px;font-weight:600;color:#0f172a;line-height:1.3;">
Your verification code
</h1>
<p style="margin:12px 0 0;font-size:15px;line-height:1.5;color:#475569;">
Enter this code in MacroShot to finish signing in.
</p>
</td></tr>
<tr><td style="padding:24px 40px;text-align:center;">
<div style="display:inline-block;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:12px;padding:18px 32px;">
<div style="font-family:'SF Mono',Menlo,Consolas,'Liberation Mono',monospace;font-size:34px;font-weight:700;letter-spacing:8px;color:#065f46;">{pin}</div>
</div>
<p style="margin:16px 0 0;font-size:13px;color:#64748b;">
This code expires in <strong style="color:#0f172a;">10 minutes</strong>.
</p>
</td></tr>
<tr><td style="padding:8px 40px 32px;">
<p style="margin:0;font-size:13px;line-height:1.6;color:#64748b;text-align:center;">
Didn't request this code? You can safely ignore this email - someone may have typed your address by mistake.
</p>
</td></tr>
<tr><td style="padding:20px 40px;background:#f8fafc;border-top:1px solid #e5e7eb;text-align:center;">
<p style="margin:0;font-size:12px;line-height:1.6;color:#94a3b8;">
MacroShot &middot; AI-powered meal tracking<br>
<a href="{APP_URL}" style="color:#10b981;text-decoration:none;">{_APP_DOMAIN_TEXT}</a>
</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""


def _render_pin_email_text(pin: str) -> str:
    """Plain-text alternative - improves deliverability (lower spam score)."""
    return (
        f"Your MacroShot verification code is: {pin}\n\n"
        f"Enter this code in MacroShot to finish signing in.\n"
        f"This code expires in 10 minutes.\n\n"
        f"Didn't request this code? You can safely ignore this email.\n\n"
        f"- MacroShot · {APP_URL}\n"
    )


async def send_pin_email(email: str, pin: str) -> bool:
    """Send a PIN via email using Resend API.

    Retries up to 3 times with exponential backoff (0.4s, 1.2s) on
    transient failures - Resend occasionally returns 5xx or times out,
    and we don't want to strand a user at the login screen.
    """
    if not RESEND_API_KEY or not RESEND_FROM_EMAIL:
        logger.warning(
            "Email disabled - missing %s",
            "RESEND_API_KEY" if not RESEND_API_KEY else "RESEND_FROM_EMAIL",
        )
        # SECURITY: never log raw PINs to the standard logger.  Even debug
        # logs can be tailed in production / shared destinations.  Print to
        # stderr only when DEBUG_SHOW_PINS=1 is explicitly set in the
        # local-dev shell.  This block is unreachable in production because
        # the JWT secret check above already requires RESEND_API_KEY to be
        # set when starting the server.
        if os.environ.get("DEBUG_SHOW_PINS") == "1":
            import sys
            print(f"[DEBUG_SHOW_PINS] PIN for {email}: {pin}", file=sys.stderr, flush=True)
        return True  # Succeed in dev mode so the flow works

    import asyncio

    import resend

    if not getattr(resend, '_api_key_set', False):
        resend.api_key = RESEND_API_KEY
        resend._api_key_set = True  # type: ignore[attr-defined]

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": email,
        "subject": "Your MacroShot verification code",
        "html": _render_pin_email_html(pin),
        "text": _render_pin_email_text(pin),
    }

    max_attempts = 3
    backoff_s = [0.4, 1.2]  # delays between attempts 1→2 and 2→3
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            # Per-attempt timeout: resend.Emails.send is a sync HTTP call in a
            # thread; without wait_for, a stalled Resend endpoint would hang
            # the entire PIN login flow.
            await asyncio.wait_for(
                asyncio.to_thread(resend.Emails.send, payload), timeout=5.0
            )
            if attempt > 1:
                logger.info("Resend PIN email succeeded on attempt %d", attempt)
            return True
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                delay = backoff_s[attempt - 1]
                logger.warning(
                    "Resend PIN email failed (attempt %d/%d): %s - retrying in %.1fs",
                    attempt, max_attempts, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
            else:
                logger.exception(
                    "Resend PIN email failed after %d attempts", max_attempts
                )

    # Exhausted retries - signal failure so the route can return a clear error.
    _ = last_exc
    return False
