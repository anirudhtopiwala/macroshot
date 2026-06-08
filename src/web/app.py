"""FastAPI app factory: mounts API routes + serves React SPA static files."""

import asyncio
import logging
import os
import time

from dotenv import load_dotenv

# Load env before anything else
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"))

# Configure logging before anything else imports a logger. Uvicorn sets up
# its own handlers for `uvicorn*` loggers but never touches root, so without
# this our `logging.getLogger(...)` calls silently drop INFO/DEBUG. Go to
# stderr so journalctl picks it up; skip asctime because journalctl already
# stamps each line.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
# Keep noisy third-party libraries at WARNING so INFO output stays useful.
for _noisy in (
    "httpx",
    "httpcore",
    "urllib3",
    "google",
    "google.auth",
    "google.api_core",
    "asyncio",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Sentry error monitoring - initialise before app creation so all exceptions are captured
import sentry_sdk

_SENTRY_DSN = os.getenv("SENTRY_BACKEND_DSN", "")
if _SENTRY_DSN:
    def _scrub_sentry_event(event, _hint):
        """Strip request bodies, query params, and PII from Sentry events.

        send_default_pii=False handles IP/cookies/headers, but Sentry can
        still attach the request body for crashes inside route handlers
        (e.g. an exception in /auth/email/verify-pin would otherwise send
        the raw PIN to Sentry).  We aggressively drop request data so the
        only thing transmitted is the exception + stack trace.
        """
        try:
            req = event.get("request") or {}
            # Drop the entire body - there's no scenario where we want it.
            req.pop("data", None)
            req.pop("query_string", None)
            # Strip cookies / headers defensively in case sendDefaultPii flips.
            req.pop("cookies", None)
            req.pop("headers", None)
            event["request"] = req
            # Also scrub user object - only id is useful for grouping.
            user = event.get("user") or {}
            event["user"] = {"id": user.get("id")} if user.get("id") else {}
            # Breadcrumbs capture logs, HTTP calls, and state changes - any of
            # which can include meal text, API response bodies, or user_ids.
            # Keep only the timestamp/category/level/type so we still know
            # WHAT happened around the crash, not what the user typed.
            breadcrumbs = (event.get("breadcrumbs") or {}).get("values") or []
            sanitized: list[dict] = []
            for bc in breadcrumbs:
                sanitized.append({
                    "timestamp": bc.get("timestamp"),
                    "category": bc.get("category"),
                    "level": bc.get("level"),
                    "type": bc.get("type"),
                    # Keep a truncated, PII-free "message" hint.
                    "message": (bc.get("message") or "")[:120],
                })
            if breadcrumbs:
                event["breadcrumbs"] = {"values": sanitized}
            # Local variables in stack frames often carry request bodies /
            # DB rows by identifier. Drop them - we keep filename/line/func.
            for ex in (event.get("exception") or {}).get("values") or []:
                for frame in (ex.get("stacktrace") or {}).get("frames") or []:
                    frame.pop("vars", None)
        except Exception:
            pass
        return event

    def _resolve_release() -> str:
        """Best-effort release identifier matching the frontend's __APP_VERSION__.

        Prefers APP_VERSION env (set by deploy command) so both SDKs can
        stay in lockstep. Falls back to `git rev-parse` when unset so a
        manual restart still tags issues with a real commit sha, not 'dev'.
        Release IDs are what let Sentry auto-resolve issues when a new
        build ships, and flag regressions when an old error reappears.
        """
        v = os.environ.get("APP_VERSION", "").strip()
        if not v:
            try:
                import subprocess
                root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                v = subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=root, stderr=subprocess.DEVNULL, timeout=2,
                ).decode().strip()
            except Exception:
                v = ""
        return f"macroshot@{v}" if v else "macroshot@dev"

    sentry_sdk.init(
        dsn=_SENTRY_DSN,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        environment=os.getenv("SENTRY_ENV", "production"),
        release=_resolve_release(),
        # PII safety: do NOT send IP addresses, cookies, headers, or other
        # personal data with errors. We only want stack traces and breadcrumbs.
        send_default_pii=False,
        before_send=_scrub_sentry_event,
    )

# SECURITY: Pillow decompression-bomb protection.
# A small (~50 KB) JPEG/PNG can declare a 100,000 x 100,000 canvas and
# decompress to ~40 GB of pixel data, OOMing the e2-micro VM (1 GB RAM)
# trivially.  Set the global cap to 25 megapixels (~5000x5000) which is
# plenty for any phone camera and rejects bombs with DecompressionBombError.
# Set this BEFORE any route imports PIL.
try:
    from PIL import Image as _PILImage
    # B20: lowered from 25 MP to 12 MP. 12 MP covers iPhone Pro main-camera
    # output (4032×3024) and rejects oversized inputs that would otherwise
    # decompress to ~150 MB+ of pixel data on a 1 GB VM. Combined with the
    # _REENCODE_SEM cap in routes/meals.py this bounds peak RSS during
    # bursts of concurrent uploads.
    _PILImage.MAX_IMAGE_PIXELS = 12_000_000
except Exception:
    pass

from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import Cookie, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.db import init_db
from src.db_pool import init_pool, close_pool
from src.web.deps import DB_PATH
from src.web.routes import achievements, admin, aliases, auth, barcode, chat, dashboard, events, fitbit, guest, meals, memory, oura, settings, strava, subscription, targets, weight, workouts

logger = logging.getLogger("macro_app")

# Base path configuration
BASE_PATH = os.environ.get("BASE_PATH", "/macro_app")
DOMAIN = os.environ.get("DOMAIN", "")
BASE_URL = os.environ.get("BASE_URL", f"https://{DOMAIN}{BASE_PATH}" if DOMAIN else "")
# APP_URL must be explicit - we don't derive it from DOMAIN because the
# app is commonly served on a subdomain ("macro.example.com") while
# DOMAIN holds the apex ("example.com"). Guessing would send legal pages
# and emails to the wrong host.
APP_URL = os.environ.get("APP_URL", "").strip()

# Paths
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
FRONTEND_DIST = os.path.join(PROJECT_ROOT, "web", "dist")
IMAGE_DIR = os.path.join(PROJECT_ROOT, "data", "images")

def _sd_notify(message: str) -> None:
    """Send a state notification to systemd via $NOTIFY_SOCKET (no-op if unset).

    We talk to the socket directly so the app stays dependency-free —
    `systemd-python` would pull in a C extension just to send a UDP datagram.
    """
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return
    # Abstract Unix sockets: leading '@' becomes a NUL byte.
    if sock_path.startswith("@"):
        sock_path = "\0" + sock_path[1:]
    import socket as _socket
    try:
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_DGRAM) as s:
            s.sendto(message.encode(), sock_path)
    except OSError as exc:
        logger.debug("sd_notify failed: %s", exc)


async def _watchdog_loop(health_url: str, interval_s: float) -> None:
    """Periodically self-probe /api/health; notify systemd only on success.

    A pure asyncio liveness check (e.g. just `asyncio.sleep`) wouldn't catch
    the failure mode we hit on 2026-05-05, where the asyncio loop was
    nominally alive but the request handler was wedged. Hitting our own
    /api/health over the loopback exercises the full path: uvicorn → ASGI
    → DB pool. If two pings in a row fail to fire `WATCHDOG=1`, systemd's
    `WatchdogSec` deadline elapses and the unit is killed and restarted.
    """
    import httpx
    # Connect timeout is short; total timeout matches the deadline budget.
    timeout = httpx.Timeout(5.0, connect=2.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while True:
            try:
                r = await client.get(health_url)
                if r.status_code == 200:
                    _sd_notify("WATCHDOG=1")
                else:
                    logger.warning("Watchdog probe got HTTP %d", r.status_code)
            except Exception as exc:
                logger.warning("Watchdog probe failed: %s", exc)
            await asyncio.sleep(interval_s)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup, start background push scheduler."""
    # B-RL: assert single-worker uvicorn. The in-memory SlowAPI rate
    # limiter, the budget_gate spend cache, the _GEMINI_SEM in
    # src/gemini.py, and the _GCS_UPLOAD_SEM / _REENCODE_SEM caps in
    # routes/meals.py all assume a single process. Multi-worker uvicorn
    # multiplies effective limits by the worker count and silently
    # breaks the global cost cap. systemd unit deliberately omits
    # --workers; this assertion is defense-in-depth so an accidental
    # `uvicorn ... --workers 2` would refuse to start instead of
    # quietly serving traffic with broken rate limits.
    # Check both uvicorn's own env (UVICORN_WORKERS) and the standard
    # PaaS / gunicorn convention (WEB_CONCURRENCY). One of these will
    # catch most worker-count overrides; the CLI flag --workers is not
    # observable from inside the process, so the systemd unit is the
    # authoritative source — this assertion is belt-and-suspenders.
    for _w_var in ("UVICORN_WORKERS", "WEB_CONCURRENCY"):
        _w_env = os.environ.get(_w_var, "").strip()
        if _w_env and _w_env != "1":
            raise RuntimeError(
                "MacroShot requires a single uvicorn worker. "
                "In-memory rate limits, the budget gate, and Gemini "
                "concurrency caps assume one process. "
                f"Got {_w_var}={_w_env!r}; remove --workers or set =1."
            )
    logger.info("Initializing database at %s", DB_PATH)
    await init_db(DB_PATH)
    await init_pool(DB_PATH, size=4)
    os.makedirs(IMAGE_DIR, exist_ok=True)

    # Tell systemd we're ready (Type=notify). Must come after init_db so the
    # readiness signal isn't a lie. If NOTIFY_SOCKET is unset (local dev),
    # this is a no-op.
    _sd_notify("READY=1")

    # Spin up the watchdog only if systemd configured one (WATCHDOG_USEC is
    # set by `WatchdogSec=` in the unit). Ping at half the deadline so a
    # single missed tick doesn't kill us.
    watchdog_task: asyncio.Task | None = None
    _wd_usec = os.environ.get("WATCHDOG_USEC")
    if _wd_usec:
        try:
            interval_s = max(2.0, int(_wd_usec) / 1_000_000.0 / 2.0)
            # APP_PORT comes from the systemd unit (prod 8000, staging 8001).
            # Defaults to 8000 so local dev with WatchdogSec= still works.
            _port = os.environ.get("APP_PORT", "8000")
            health_url = f"http://127.0.0.1:{_port}{BASE_PATH}/api/health"
            watchdog_task = asyncio.create_task(_watchdog_loop(health_url, interval_s))
            logger.info("Watchdog enabled: probing %s every %.1fs", health_url, interval_s)
        except (TypeError, ValueError):
            logger.warning("Invalid WATCHDOG_USEC=%r; watchdog disabled", _wd_usec)

    from src.web.push_scheduler import push_reminder_loop
    push_task = asyncio.create_task(push_reminder_loop(DB_PATH))

    # Fitbit / Strava / Oura sync is production-only. Even though the nightly
    # exporter now scrubs integration tokens from staging, we still skip the
    # loop here as belt-and-suspenders: if a token ever leaks back into
    # staging, its refresh here would rotate the RT at the provider and
    # invalidate prod's copy (2026-04-17 & 2026-04-22 Fitbit incidents).
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        from src.web.activity_sync import activity_sync_loop
        activity_task = asyncio.create_task(activity_sync_loop(DB_PATH))
    else:
        activity_task = None
        logger.info("APP_ENV=staging - skipping activity sync loop")

    # Engagement emails are production-only. Staging shares prod's
    # real user list (via the nightly exporter), so running the scheduler
    # there would either double-send or require a separate suppression
    # list - not worth the complexity for a disposable environment.
    if os.environ.get("APP_ENV", "").strip().lower() != "staging":
        from src.web.email_scheduler import email_engagement_loop
        email_task = asyncio.create_task(email_engagement_loop(DB_PATH))
    else:
        email_task = None
        logger.info("APP_ENV=staging - skipping engagement email scheduler")

    async def session_cleanup_loop():
        """Periodically clean up stale meal sessions to prevent DB bloat."""
        from src.db import cleanup_stale_sessions
        while True:
            await asyncio.sleep(6 * 3600)  # Every 6 hours
            try:
                n = await cleanup_stale_sessions(DB_PATH, max_age_hours=24, image_dir=IMAGE_DIR)
                if n:
                    logger.info("Cleaned up %d stale meal sessions", n)
            except Exception:
                logger.exception("Session cleanup failed")

    cleanup_task = asyncio.create_task(session_cleanup_loop())

    async def events_cleanup_loop():
        """Periodically drop user_events older than 365 days.

        user_events is append-only telemetry - if left unbounded it grows
        indefinitely. 365 days matches the admin dashboard's max window.
        Runs once per day, on the same cadence as other retention tasks.
        """
        from src.db import cleanup_old_events
        # Initial delay so cleanup doesn't race with DB init on restart
        await asyncio.sleep(60 * 60)  # 1 hour after boot
        while True:
            try:
                n = await cleanup_old_events(DB_PATH, max_age_days=365)
                if n:
                    logger.info("Cleaned up %d old user_events rows", n)
            except Exception:
                logger.exception("Events cleanup failed")
            await asyncio.sleep(24 * 3600)  # daily

    events_task = asyncio.create_task(events_cleanup_loop())

    async def aux_prune_loop():
        """Daily pruning for small, append-only tables that would otherwise
        grow unbounded: revoked JWTs past their exp, and stripe_webhook_events
        older than 30 days (Stripe's retry window is ~3 days)."""
        from src.db import prune_expired_revocations, prune_stripe_webhook_events
        await asyncio.sleep(2 * 3600)  # 2 hours after boot - stagger from other cleanup loops
        while True:
            try:
                n_jwt = await prune_expired_revocations(DB_PATH)
                n_stripe = await prune_stripe_webhook_events(DB_PATH, max_age_days=30)
                if n_jwt or n_stripe:
                    logger.info(
                        "Pruned %d revoked_tokens, %d stripe_webhook_events",
                        n_jwt, n_stripe,
                    )
            except Exception:
                logger.exception("Aux prune loop failed")
            await asyncio.sleep(24 * 3600)

    aux_prune_task = asyncio.create_task(aux_prune_loop())

    yield

    _sd_notify("STOPPING=1")
    if watchdog_task is not None:
        watchdog_task.cancel()
        try:
            await watchdog_task
        except asyncio.CancelledError:
            pass
    push_task.cancel()
    if activity_task is not None:
        activity_task.cancel()
    if email_task is not None:
        email_task.cancel()
    cleanup_task.cancel()
    events_task.cancel()
    aux_prune_task.cancel()
    try:
        await push_task
    except asyncio.CancelledError:
        pass
    if activity_task is not None:
        try:
            await activity_task
        except asyncio.CancelledError:
            pass
    if email_task is not None:
        try:
            await email_task
        except asyncio.CancelledError:
            pass
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    try:
        await events_task
    except asyncio.CancelledError:
        pass
    try:
        await aux_prune_task
    except asyncio.CancelledError:
        pass
    await close_pool()


from src.web.rate_limit import limiter

# SECURITY: Hide the FastAPI auto-generated docs (swagger / redoc /
# openapi.json) in production.  They give attackers a full schema of
# every endpoint - useful reconnaissance.  Local dev still gets them.
# Production indicator: APP_ENV explicit dev/local AND no Sentry DSN.
# Fail-closed: an unset APP_ENV in a deployed environment leaves docs off.
APP_ENV = os.environ.get("APP_ENV", "").strip().lower()
_DOCS_ENABLED = APP_ENV in ("", "development", "dev", "local") and not bool(os.getenv("SENTRY_BACKEND_DSN", ""))
# Treat presence of Sentry DSN OR explicit production env as production.
IS_PROD = bool(os.getenv("SENTRY_BACKEND_DSN", "")) or APP_ENV == "production"
if IS_PROD:
    _DOCS_ENABLED = False

app = FastAPI(
    title="MacroShot API",
    version="1.0.0",
    docs_url=f"{BASE_PATH}/api/docs" if _DOCS_ENABLED else None,
    redoc_url=f"{BASE_PATH}/api/redoc" if _DOCS_ENABLED else None,
    openapi_url=f"{BASE_PATH}/api/openapi.json" if _DOCS_ENABLED else None,
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ──────────────────────────────────────────────────────────────────────
# Body-size cap (B4): refuse oversized requests before they hit any route.
# ASGI middleware so we can short-circuit on Content-Length without buffering.
# 60 MB ceiling matches MAX_IMAGES (5) × 10 MB plus modest multipart overhead.
# ──────────────────────────────────────────────────────────────────────
_GLOBAL_MAX_BODY_BYTES = 60_000_000


class BodySizeLimitMiddleware:
    """ASGI middleware that 413s requests whose body exceeds the cap.

    Two paths:
      • Content-Length present: short-circuit before reading the body.
      • Transfer-Encoding: chunked (or any request without a declared length):
        wrap `receive` so we accumulate the byte count across http.request
        events and abort once the cap is exceeded. Without this the global
        cap is trivially bypassed by any HTTP/1.1 client that elects to
        chunk-encode a multipart payload.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        cl = headers.get(b"content-length")
        if cl is not None:
            try:
                if int(cl) > self.max_bytes:
                    await send({
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b'{"detail":"Request body too large"}',
                    })
                    return
                # Content-Length declared and within cap — pass through;
                # downstream still streams the body once.
                await self.app(scope, receive, send)
                return
            except ValueError:
                # Fall through to streaming guard if header is malformed.
                pass

        # No (valid) Content-Length: count bytes as we forward chunks.
        # Once the cap is exceeded we stop forwarding and emit a 413 ourselves.
        max_bytes = self.max_bytes
        state = {"received": 0, "aborted": False}
        response_started = False

        async def counting_receive():
            if state["aborted"]:
                # After we've sent a 413, surface a disconnect so the app
                # tears down the request handler instead of waiting for more.
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                state["received"] += len(body)
                if state["received"] > max_bytes:
                    state["aborted"] = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            if state["aborted"] and not response_started:
                # Swallow the late response from the wrapped app — we already
                # sent the 413 below.
                return
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        finally:
            if state["aborted"] and not response_started:
                try:
                    await send({
                        "type": "http.response.start",
                        "status": 413,
                        "headers": [(b"content-type", b"application/json")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b'{"detail":"Request body too large"}',
                    })
                except Exception:
                    pass


app.add_middleware(BodySizeLimitMiddleware, max_bytes=_GLOBAL_MAX_BODY_BYTES)

# ──────────────────────────────────────────────────────────────────────
# B3: Gzip — skip API + auth paths to avoid BREACH-class compression
# oracles on authenticated responses. Static assets still benefit.
# ──────────────────────────────────────────────────────────────────────
from starlette.middleware.gzip import GZipMiddleware


class _APISkippingGZip(GZipMiddleware):
    """GZipMiddleware that disables compression on API + auth paths.

    BREACH attacks recover secrets from authenticated, gzip-compressed
    responses by varying attacker-controlled query parameters. The mitigation
    is to never compress responses that contain a session-bound secret.
    Strip Accept-Encoding before delegating so the parent middleware
    treats the request as if the client didn't request compression.
    """

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "") or ""
            if (
                path.startswith(f"{BASE_PATH}/api/")
                or path.startswith("/auth/")
                or path.startswith(f"{BASE_PATH}/auth/")
            ):
                # Strip Accept-Encoding so GZip doesn't kick in.
                new_headers = [
                    (k, v) for (k, v) in scope.get("headers") or []
                    if k.lower() != b"accept-encoding"
                ]
                scope = dict(scope)
                scope["headers"] = new_headers
        await super().__call__(scope, receive, send)


app.add_middleware(_APISkippingGZip, minimum_size=500)

# CORS - localhost origins are only allowed outside production so a dev
# server can't issue credentialed requests against the live domain.
# Fail-closed (B1): only add localhost when APP_ENV is explicitly dev/local
# AND we're not in production.
_cors_origins: list[str] = []
if DOMAIN:
    _cors_origins.append(f"https://{DOMAIN}")
_staging_origin = os.environ.get("STAGING_ORIGIN", "").strip()
if _staging_origin:
    _cors_origins.append(_staging_origin)
if APP_ENV in ("development", "dev", "local") and not IS_PROD:
    _cors_origins += ["http://localhost:5173", "http://localhost:8000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "X-App-Version",
        "Sentry-Trace",
        "Baggage",
    ],
)

# ──────────────────────────────────────────────────────────────────────
# B2: TrustedHostMiddleware — Host-header validation. Defends against
# Host-header injection (cache poisoning, password-reset link rewriting,
# routing bugs). Always allow loopback so health probes and watchdog work.
# ──────────────────────────────────────────────────────────────────────
_allowed_hosts: list[str] = ["127.0.0.1", "localhost"]
if DOMAIN:
    _allowed_hosts.append(DOMAIN)
if _staging_origin:
    try:
        _staging_host = urlparse(_staging_origin).hostname
        if _staging_host:
            _allowed_hosts.append(_staging_host)
    except Exception:
        pass
# In dev, allow common dev hosts so vite + uvicorn pass through.
if APP_ENV in ("development", "dev", "local") and not IS_PROD:
    _allowed_hosts.extend(["*.localhost", "0.0.0.0"])

app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# Security headers + Cache-Control for API responses
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        path = request.url.path
        # Security headers on all responses
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # B23: stricter Permissions-Policy — disable sensors and payment APIs
        # we don't use; allow camera for in-app meal photo capture.
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(self), geolocation=(), gyroscope=(), "
            "magnetometer=(), microphone=(), payment=(), usb=(), interest-cohort=()"
        )
        # CSP: allow self, Google Sign-In.
        # B27: theme-init script externalized → drop the sha256 from script-src.
        # B26 deferred for now: skeleton still uses inline `style=` attributes
        # for layout that would all need data-attribute classes; until that
        # refactor lands, keep 'unsafe-inline' for styles. Non-script inline
        # CSS is significantly lower-risk (no JS execution path).
        # B28: CSP report-uri; operator can override via SENTRY_CSP_REPORT_URI.
        _csp_report_uri = os.environ.get("SENTRY_CSP_REPORT_URI", "").strip()
        # B29: explicit object-src / form-action / manifest-src.
        # - object-src 'none' neutralizes legacy <object>/<embed>/<applet>
        #   Flash-style XSS. default-src 'self' would already cover this but
        #   the spec requires object-src to be set explicitly to take effect
        #   in some older browsers.
        # - form-action 'self' stops an injected <form action="https://evil/">
        #   from exfiltrating credentials on submit. CORS and frame-ancestors
        #   do not cover top-level form POSTs to a cross-origin target.
        # - manifest-src 'self' pins the PWA manifest origin so an XSS that
        #   injects a <link rel="manifest"> can't swap install metadata.
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'wasm-unsafe-eval' https://accounts.google.com https://static.cloudflareinsights.com; "
            "style-src 'self' 'unsafe-inline' https://accounts.google.com; "
            "font-src 'self' data:; "
            "img-src 'self' data: blob: https://*.googleusercontent.com https://images.openfoodfacts.org https://*.openfoodfacts.org https://*.fatsecret.com; "
            "connect-src 'self' https://accounts.google.com https://*.ingest.sentry.io https://*.ingest.us.sentry.io https://cloudflareinsights.com; "
            "frame-src https://accounts.google.com; "
            "frame-ancestors 'none'; "
            "form-action 'self'; "
            "object-src 'none'; "
            "manifest-src 'self'; "
            "worker-src 'self' blob:; "
            "base-uri 'self'"
        )
        if _csp_report_uri:
            csp += f"; report-uri {_csp_report_uri}"
        response.headers["Content-Security-Policy"] = csp
        # HSTS (only on HTTPS in production). B24: include preload directive.
        if DOMAIN and not path.startswith("/macro_app/api/docs"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        # Cache-Control: context-appropriate caching for Cloudflare + service worker
        if path.startswith(f"{BASE_PATH}/api/") and not path.endswith("/config"):
            # API responses: never cache
            response.headers["Cache-Control"] = "no-store, private"
        elif path.endswith("/sw.js") or path.endswith("/manifest.json"):
            # Service worker + manifest: always revalidate (critical for PWA updates)
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif "/assets/" in path:
            # Vite hashed bundles: cache forever (filename changes on rebuild)
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# CSRF defense-in-depth: require X-Requested-With header on state-changing API requests.
# Cross-site forms/redirects cannot set custom headers - only same-origin JS can.
class CSRFMiddleware(BaseHTTPMiddleware):
    # Exact full paths exempt from CSRF.  We previously used endswith(), which
    # would silently exempt any future route ending in one of these literal
    # suffixes - a landmine.  Use full paths anchored to BASE_PATH so future
    # additions are explicit.
    _EXEMPT_PATHS = {
        f"{BASE_PATH}/api/v1/subscription/webhook",
        f"{BASE_PATH}/api/v1/strava/webhook",
        f"{BASE_PATH}/api/v1/fitbit/webhook",
        f"{BASE_PATH}/api/v1/oura/webhook",
        # /auth/google stays exempt: it carries its own anti-CSRF guard via
        # the Google ID token (a forged cross-site call has no way to mint
        # a valid token signed by Google for the configured client_id).
        f"{BASE_PATH}/api/v1/auth/google",
        # A12 list-unsubscribe one-click POST - HMAC-tied URL, no cookies.
        f"{BASE_PATH}/api/v1/auth/unsubscribe",
    }

    async def dispatch(self, request: StarletteRequest, call_next):
        if request.method in ("POST", "PUT", "DELETE"):
            path = request.url.path
            if path.startswith(f"{BASE_PATH}/api/") and path not in self._EXEMPT_PATHS:
                if request.headers.get("X-Requested-With") != "MacroApp":
                    return JSONResponse(status_code=403, content={"detail": "Missing or invalid X-Requested-With header"})
        return await call_next(request)

app.add_middleware(CSRFMiddleware)

# Request timing - log slow API requests
class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        path = request.url.path
        if path.startswith(f"{BASE_PATH}/api/"):
            method = request.method
            status = response.status_code
            if duration_ms > 5000:
                logger.warning("Slow request: %s %s %d %.0fms", method, path, status, duration_ms)
            elif duration_ms > 1000:
                logger.info("Request: %s %s %d %.0fms", method, path, status, duration_ms)
        return response

app.add_middleware(RequestTimingMiddleware)

# Mount API routes
api_prefix = f"{BASE_PATH}/api/v1"
app.include_router(auth.router, prefix=api_prefix)
app.include_router(meals.router, prefix=api_prefix)
app.include_router(barcode.router, prefix=api_prefix)
app.include_router(chat.router, prefix=api_prefix)
app.include_router(dashboard.router, prefix=api_prefix)
app.include_router(aliases.router, prefix=api_prefix)
app.include_router(settings.router, prefix=api_prefix)
app.include_router(targets.router, prefix=api_prefix)
app.include_router(weight.router, prefix=api_prefix)
app.include_router(subscription.router, prefix=api_prefix)
app.include_router(strava.router, prefix=api_prefix)
app.include_router(fitbit.router, prefix=api_prefix)
app.include_router(oura.router, prefix=api_prefix)
app.include_router(workouts.router, prefix=api_prefix)
app.include_router(achievements.router, prefix=api_prefix)
app.include_router(memory.router, prefix=api_prefix)
app.include_router(events.router, prefix=api_prefix)
app.include_router(guest.router, prefix=api_prefix)
# Admin router excluded from OpenAPI schema - defense-in-depth so an
# enumeration of public docs (when enabled) does not reveal admin paths.
app.include_router(admin.router, prefix=api_prefix, include_in_schema=False)


# Health check - pings the DB so load balancers and uptime monitors
# see 503 (not 200) if SQLite is locked, corrupt, or the file is missing.
@app.api_route(f"{BASE_PATH}/api/health", methods=["GET", "HEAD"])
@limiter.limit("60/minute")
async def health(request: Request):
    from src.db_pool import get_db

    async def _ping() -> None:
        async with get_db(DB_PATH) as db:
            await (await db.execute("SELECT 1")).fetchone()

    try:
        await asyncio.wait_for(_ping(), timeout=2.0)
    except Exception as e:
        logger.warning("health check failed: %s", e)
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "service": "macro_web", "reason": "db_unreachable"},
        )
    return {"status": "ok", "service": "macro_web"}


# Public config for frontend (e.g. Google Client ID, operator identity)
@app.get(f"{BASE_PATH}/api/config")
async def frontend_config():
    from src.web.push import get_vapid_public_key
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
    return {
        "google_client_id": google_client_id if google_client_id else None,
        "vapid_public_key": get_vapid_public_key() or None,
        "operator_name": os.environ.get("OPERATOR_NAME", "") or None,
        "operator_first_name": os.environ.get("OPERATOR_FIRST_NAME", "") or None,
        "contact_email": os.environ.get("CONTACT_EMAIL", "") or None,
        "github_url": os.environ.get("GITHUB_URL", "") or None,
        "sponsor_url": os.environ.get("SPONSOR_URL", "") or None,
        "donate_url": os.environ.get("DONATE_URL", "") or None,
        "app_url": APP_URL or None,
        "domain": DOMAIN or None,
    }


# Serve meal images - require auth cookie
@app.get(f"{BASE_PATH}/api/v1/images/{{path:path}}")
async def serve_image(
    path: str,
    host_session: str = Cookie(default=None, alias="__Host-macro_session"),
    macro_session: str = Cookie(default=None),
):
    """Serve meal images from data/images/. Requires valid session."""
    from src.web.auth import decode_jwt
    raw = host_session or macro_session
    payload = decode_jwt(raw) if raw else None
    if not payload:
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
    jti = payload.get("jti")
    if jti:
        from src.db import is_jwt_revoked
        if await is_jwt_revoked(DB_PATH, jti):
            return JSONResponse(status_code=401, content={"detail": "Session revoked"})
    # Resolve real path first to prevent traversal attacks
    file_path = os.path.join(IMAGE_DIR, path)
    real = os.path.realpath(file_path)
    if not real.startswith(os.path.realpath(IMAGE_DIR)):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    # Verify the authenticated user owns the requested image (check resolved path)
    try:
        rel = os.path.relpath(real, os.path.realpath(IMAGE_DIR))
    except ValueError:
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    path_user_id = rel.split(os.sep)[0] if os.sep in rel else ""
    if path_user_id != payload.get("sub"):
        return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    _cache_headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    # Serve from local disk if available
    if os.path.isfile(real):
        return FileResponse(real, headers=_cache_headers)
    # Fallback: fetch from GCS private bucket
    from src.gcs import gcs_download
    data = await gcs_download(path)
    if data is None:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    # Determine content type from extension
    ext = os.path.splitext(path)[1].lower()
    ct = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "application/octet-stream")
    from fastapi.responses import Response
    return Response(content=data, media_type=ct, headers=_cache_headers)


# Serve React SPA static files
_ASSETS_DIR = os.path.join(FRONTEND_DIST, "assets")
if os.path.isdir(FRONTEND_DIST) and os.path.isdir(_ASSETS_DIR):
    # Mount static assets (JS, CSS, etc.). Pre-flag config_checked=True so
    # Starlette's lazy "first-request" stat() of the directory (staticfiles.py
    # line ~94) is skipped: we already validated existence above. Without this,
    # an asset request that happens to land while vite is clearing dist/assets
    # mid-deploy would raise RuntimeError → 500 instead of resolving to 404
    # once vite finishes writing. Saw this fire on a real prod deploy
    # (2026-05-05 23:56 UTC).
    _static_assets = StaticFiles(directory=_ASSETS_DIR)
    _static_assets.config_checked = True  # type: ignore[attr-defined]
    app.mount(
        f"{BASE_PATH}/assets",
        _static_assets,
        name="static-assets",
    )

    # B21: paths we never serve from the SPA catch-all even if a stale file
    # is present in dist/. reset.html is a recovery tool that wipes local
    # state — it must not be reachable as a static asset to avoid hostile
    # links logging users out / clearing their device data.
    _SPA_BLOCKED_PATHS = {"reset.html"}

    # SPA catch-all: serve index.html for all non-API routes
    @app.get(f"{BASE_PATH}/{{path:path}}")
    async def serve_spa(path: str):
        """Serve the React SPA index.html for all non-API paths."""
        # Normalize path to defeat blocklist bypasses like `./reset.html`,
        # `subdir/../reset.html`, or `/reset.html`. Membership-by-literal is
        # not enough — we have to compare on the basename of the resolved
        # file (which is what gets served).
        norm = os.path.normpath(path).lstrip("./").lstrip("/") if path else path
        if norm in _SPA_BLOCKED_PATHS or os.path.basename(norm) in _SPA_BLOCKED_PATHS:
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        # If path points to a real file in dist, serve it (with path traversal check)
        file_path = os.path.join(FRONTEND_DIST, path)
        if path and os.path.isfile(file_path):
            real = os.path.realpath(file_path)
            if not real.startswith(os.path.realpath(FRONTEND_DIST)):
                return JSONResponse(status_code=403, content={"detail": "Forbidden"})
            # Defense-in-depth: also block by realpath basename in case the
            # normalize step above missed a clever encoding.
            if os.path.basename(real) in _SPA_BLOCKED_PATHS:
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            # sw.js must never be cached by the browser
            if path == "sw.js":
                return FileResponse(file_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
            return FileResponse(file_path)
        # SPA routing - serve index.html with no-cache to prevent stale HTML after deploys
        _NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
        index_path = os.path.join(FRONTEND_DIST, "index.html")
        if not os.path.isfile(index_path):
            return JSONResponse(status_code=503, content={"detail": "App is restarting, please retry in a few seconds."})
        return FileResponse(index_path, headers=_NO_CACHE)

    @app.get(BASE_PATH)
    async def serve_spa_root():
        """Serve SPA root."""
        index_path = os.path.join(FRONTEND_DIST, "index.html")
        if not os.path.isfile(index_path):
            return JSONResponse(status_code=503, content={"detail": "App is restarting, please retry in a few seconds."})
        return FileResponse(index_path,
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get(f"{BASE_PATH}/")
    async def serve_spa_root_slash():
        """Serve SPA root (trailing slash)."""
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"),
                            headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
else:
    @app.get(f"{BASE_PATH}/{{path:path}}")
    async def no_frontend(path: str):
        return JSONResponse(
            status_code=200,
            content={"message": "Frontend not built yet. Run: cd web && npm run build"},
        )

    @app.get(BASE_PATH)
    async def no_frontend_root():
        return JSONResponse(
            status_code=200,
            content={"message": "Frontend not built yet. Run: cd web && npm run build"},
        )
