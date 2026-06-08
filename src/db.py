"""Async SQLite layer - all reads and writes for every table.

Uses a connection pool (src/db_pool.py) when available for reuse and
pre-applied PRAGMAs.  Falls back to per-call connections in tests / subprocesses.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from datetime import date, timedelta

import aiosqlite

from src.db_pool import get_db

logger = logging.getLogger("macro_app")


# B29: shared helper for dynamic UPDATE … SET clauses. Asserts every
# column name matches a strict SQL identifier regex AND is in the caller's
# allow-list - defense-in-depth so any future caller that accidentally
# forwards user-controlled keys can't introduce SQL injection through the
# `f"… SET {set_clause} …"` pattern.
_IDENT_RE = re.compile(r"[a-z_][a-z0-9_]*")


def _safe_set_clause(allowed: frozenset[str], kw: dict) -> tuple[str, list]:
    """Build a `<col> = ?, …` clause + matching params from kw.

    Drops keys that are None or not in `allowed`. Raises ValueError if any
    surviving key fails the identifier regex (defense-in-depth - should be
    impossible if `allowed` only contains literal column names).
    """
    safe = {k: v for k, v in kw.items() if k in allowed and v is not None}
    for k in safe:
        if not _IDENT_RE.fullmatch(k):
            raise ValueError(f"unsafe column identifier: {k!r}")
    if not safe:
        return "", []
    set_clause = ", ".join(f"{k} = ?" for k in safe)
    return set_clause, list(safe.values())


# ── Token encryption (MultiFernet, OAUTH_TOKEN_KEY preferred) ───────────
_fernet_instance = None


def _derive_fernet_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from an arbitrary secret string."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _get_fernet():
    """Lazy-init MultiFernet for OAuth token encryption.

    Prefers OAUTH_TOKEN_KEY (a comma-separated list of keys; first = primary
    used for new encrypts, rest are tried for decrypts to support rotation).
    Falls back to deriving a key from JWT_SECRET with a deprecation warning -
    sharing keys between auth and OAuth-token storage is a footgun (a JWT
    secret leak forces a password-equivalent rotation across both surfaces).

    Migration / rotation pattern (comma-separated multi-key behavior):
        OAUTH_TOKEN_KEY="<new_primary>,<old_secondary>"
    On encrypt, MultiFernet uses the FIRST key. On decrypt, it tries each
    key in order and returns the first successful result. To migrate from
    the legacy JWT_SECRET-derived key, set the new dedicated key as the
    primary and the old JWT_SECRET as a secondary (so existing rows still
    decrypt). Drop the secondary after one OAuth-refresh cycle per user
    (each refresh re-encrypts with the new primary, retiring legacy
    ciphertext). Forgetting the secondary on first rollout BRICKS every
    existing OAuth row - they cannot be decrypted without the old key.
    """
    global _fernet_instance
    if _fernet_instance is not None:
        return _fernet_instance
    from cryptography.fernet import Fernet, MultiFernet

    raw = os.environ.get("OAUTH_TOKEN_KEY", "").strip()
    if raw:
        # Multiple keys allowed for rotation: first is primary, rest are
        # secondary and used only for decryption.
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        keys = [Fernet(_derive_fernet_key(p)) for p in parts]
        _fernet_instance = MultiFernet(keys) if len(keys) > 1 else keys[0]
        return _fernet_instance

    # Legacy fallback: derive from JWT_SECRET. Emit a deprecation warning so
    # the operator knows to set OAUTH_TOKEN_KEY before the next launch.
    secret = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
    logger.warning(
        "OAUTH_TOKEN_KEY not set - deriving OAuth-token encryption key from "
        "JWT_SECRET. This couples two unrelated security surfaces; set "
        "OAUTH_TOKEN_KEY (and rotate it independently) before launch."
    )
    _fernet_instance = Fernet(_derive_fernet_key(secret))
    return _fernet_instance


def _encrypt_token(plaintext: str) -> str:
    """Encrypt an OAuth token for storage."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt_token(ciphertext: str) -> str:
    """Decrypt an OAuth token from storage.

    On decryption failure we log loudly and raise.  Silently returning the
    ciphertext (the previous behavior) was a footgun: a future JWT_SECRET
    rotation would silently break every integration AND in the worst case
    cause the ciphertext to be sent to Strava/Fitbit as a bearer token.

    Legacy plaintext rows from before encryption was deployed are detected
    by their format (Fernet ciphertext starts with a base64-urlsafe blob
    that's much longer than any real OAuth token).  If you have legacy
    plaintext rows, you should run a one-time migration to re-encrypt them
    rather than relying on the previous silent fallback.
    """
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception as e:
        logger.error(
            "Failed to decrypt OAuth token (likely a key rotation or "
            "corrupted row). Length=%d. The integration will fail until "
            "the user re-authenticates.",
            len(ciphertext),
        )
        # Re-raise so callers see the failure rather than silently using
        # ciphertext as a bearer token.
        raise RuntimeError("OAuth token decryption failed") from e

# ── Email normalization helpers ─────────────────────────────────
#
# Two distinct normalization tiers:
#
#   normalize_email(): strip + lowercase. The canonical "stored form" used
#       in web_auth.email, email_pins.email, etc.  Different from the raw
#       address the user typed (which can vary in case).
#
#   canonical_email(): provider-aware aliasing reduction. Gmail treats
#       dots in the local part as cosmetic (foo.bar@gmail.com == foobar@gmail.com)
#       and supports +suffix tagging (foo+anything@gmail.com == foo@gmail.com),
#       and googlemail.com is an alias for gmail.com.  We collapse all of
#       these to a single canonical form and key trial-farming detection
#       (UNIQUE on web_auth.email_canonical) off it.  For non-Gmail
#       providers we simply use the lowercased + stripped address.


def normalize_email(email: str | None) -> str:
    """Lowercase and strip an email address. Returns "" for None."""
    return (email or "").strip().lower()


def canonical_email(email: str | None) -> str:
    """Return a canonical form for trial-abuse detection.

    Gmail-specific reductions:
      * dots in the local part are removed
      * everything after a `+` in the local part is dropped
      * googlemail.com is normalized to gmail.com

    Every other provider just gets the normalize_email() form back.
    """
    e = normalize_email(email)
    if "@" not in e:
        return e
    local, _, domain = e.partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        local = local.split("+", 1)[0].replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}"


_DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meal_logs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    logged_at        TEXT NOT NULL,
    item_name        TEXT NOT NULL,
    meal_description TEXT DEFAULT '',
    user_input       TEXT DEFAULT '',
    calories         REAL NOT NULL,
    protein          REAL NOT NULL,
    carbs            REAL NOT NULL,
    fat              REAL NOT NULL,
    source           TEXT DEFAULT '',
    meal_type        TEXT DEFAULT '',
    items_json       TEXT DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS user_targets (
    user_id    INTEGER PRIMARY KEY,
    calories   REAL NOT NULL,
    protein    REAL NOT NULL,
    carbs      REAL NOT NULL,
    fat        REAL NOT NULL,
    set_by     TEXT NOT NULL DEFAULT 'auto',
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id           INTEGER PRIMARY KEY,
    timezone          TEXT    NOT NULL DEFAULT 'America/Los_Angeles',
    breakfast_hour    INTEGER NOT NULL DEFAULT 8,
    lunch_hour        INTEGER NOT NULL DEFAULT 11,
    snack_hour        INTEGER NOT NULL DEFAULT 15,
    dinner_hour       INTEGER NOT NULL DEFAULT 19,
    streak_alert_hour INTEGER NOT NULL DEFAULT 22,
    reminders_on      INTEGER NOT NULL DEFAULT 1,
    meals_public      INTEGER NOT NULL DEFAULT 0,
    units_system      TEXT    NOT NULL DEFAULT 'metric',
    gamification      TEXT    NOT NULL DEFAULT 'full',
    newsletter_opt_in INTEGER NOT NULL DEFAULT 1,
    ai_web_search_enabled INTEGER NOT NULL DEFAULT 0,
    notif_show_macros INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS gemini_calls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at     TEXT    NOT NULL,
    call_type     TEXT    NOT NULL DEFAULT '',
    user_id       INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    has_image     INTEGER NOT NULL DEFAULT 0,
    web_searches  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fatsecret_cache (
    query_key  TEXT PRIMARY KEY,
    calories   REAL NOT NULL,
    protein    REAL NOT NULL,
    carbs      REAL NOT NULL,
    fat        REAL NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fatsecret_comparison (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id               INTEGER NOT NULL,
    meal_log_id           INTEGER,
    item_name             TEXT NOT NULL,
    weight_g              REAL,
    gemini_cal_100g       REAL,
    gemini_protein_100g   REAL,
    gemini_carbs_100g     REAL,
    gemini_fat_100g       REAL,
    fatsecret_cal_100g    REAL,
    fatsecret_protein_100g REAL,
    fatsecret_carbs_100g  REAL,
    fatsecret_fat_100g    REAL,
    logged_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meal_logs_user_id     ON meal_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_meal_logs_logged_at   ON meal_logs(logged_at);
CREATE INDEX IF NOT EXISTS idx_meal_logs_user_logged ON meal_logs(user_id, logged_at);
CREATE INDEX IF NOT EXISTS idx_gemini_calls_at      ON gemini_calls(called_at);
CREATE INDEX IF NOT EXISTS idx_fatsecret_cmp_user   ON fatsecret_comparison(user_id);

CREATE TABLE IF NOT EXISTS web_auth (
    user_id         INTEGER PRIMARY KEY,
    email           TEXT UNIQUE NOT NULL,
    google_sub      TEXT UNIQUE,
    -- Provider-aware canonical email used to block trial farming via Gmail
    -- dot/plus aliasing (foo.bar+x@gmail.com == foobar@gmail.com). Populated
    -- by canonical_email() at insert time. UNIQUE INDEX is created below.
    email_canonical TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS email_pins (
    email         TEXT NOT NULL,
    pin_hash      TEXT NOT NULL,
    attempts      INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS revoked_tokens (
    jti           TEXT PRIMARY KEY,
    user_id       INTEGER,
    revoked_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revoked_tokens_expires ON revoked_tokens(expires_at);

CREATE TABLE IF NOT EXISTS email_pin_failures (
    email      TEXT NOT NULL,
    failed_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_pin_failures ON email_pin_failures(email, failed_at);

CREATE TABLE IF NOT EXISTS meal_sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    images_json   TEXT DEFAULT '[]',
    conversation  TEXT DEFAULT '[]',
    nutrition     TEXT DEFAULT '',
    meal_type     TEXT DEFAULT '',
    status        TEXT DEFAULT 'pending',
    user_input    TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    endpoint   TEXT NOT NULL,
    p256dh     TEXT NOT NULL,
    auth       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, endpoint)
);

CREATE TABLE IF NOT EXISTS meal_aliases (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    alias_name       TEXT    NOT NULL,
    item_name        TEXT    NOT NULL,
    meal_description TEXT    DEFAULT '',
    calories         REAL    NOT NULL,
    protein          REAL    NOT NULL,
    carbs            REAL    NOT NULL,
    fat              REAL    NOT NULL,
    items_json       TEXT    DEFAULT '[]',
    created_at       TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, alias_name)
);

CREATE TABLE IF NOT EXISTS weight_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    logged_at  TEXT NOT NULL,
    weight_kg  REAL NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_weight_logs_user ON weight_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_weight_logs_user_date ON weight_logs(user_id, logged_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_auth_email ON web_auth(email);
CREATE INDEX IF NOT EXISTS idx_web_auth_google ON web_auth(google_sub);
-- NOTE: idx_web_auth_email_canonical (UNIQUE on web_auth.email_canonical) is
-- intentionally NOT created here. The email_canonical column is added by
-- versioned migration v3, and the UNIQUE index is created in v12 (after the
-- _migrate_email_normalization backfill runs). Creating it in _DDL on a fresh
-- install would race with v3 on existing DBs (no such column) - instead the
-- v12 migration is the single source of truth for both fresh and existing DBs.
CREATE INDEX IF NOT EXISTS idx_meal_sessions_user ON meal_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_email_pins_email ON email_pins(email);
CREATE INDEX IF NOT EXISTS idx_push_sub_user ON push_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_meal_aliases_user ON meal_aliases(user_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    title      TEXT DEFAULT '',
    conversation TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                INTEGER NOT NULL UNIQUE,
    plan                   TEXT NOT NULL DEFAULT 'free',
    status                 TEXT NOT NULL DEFAULT 'active',
    stripe_customer_id     TEXT DEFAULT '',
    stripe_subscription_id TEXT DEFAULT '',
    trial_ends_at          TEXT,
    trial_used             INTEGER NOT NULL DEFAULT 0,
    is_og                  INTEGER NOT NULL DEFAULT 0,
    current_period_start   TEXT,
    current_period_end     TEXT,
    cancelled_at           TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id);

CREATE TABLE IF NOT EXISTS usage_tracking (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    usage_type  TEXT NOT NULL,
    period      TEXT NOT NULL,
    used_count  INTEGER NOT NULL DEFAULT 0,
    limit_count INTEGER NOT NULL DEFAULT 5,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, usage_type, period)
);

CREATE INDEX IF NOT EXISTS idx_usage_tracking_user ON usage_tracking(user_id, usage_type, period);

CREATE TABLE IF NOT EXISTS strava_tokens (
    user_id           INTEGER PRIMARY KEY,
    strava_athlete_id INTEGER,
    access_token      TEXT NOT NULL,
    refresh_token     TEXT NOT NULL,
    expires_at        INTEGER NOT NULL,
    scope             TEXT DEFAULT '',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS workout_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    source          TEXT NOT NULL,
    external_id     TEXT DEFAULT '',
    activity_type   TEXT NOT NULL,
    name            TEXT DEFAULT '',
    started_at      TEXT NOT NULL,
    duration_sec    INTEGER NOT NULL,
    calories_burned REAL DEFAULT 0,
    distance_m      REAL DEFAULT 0,
    avg_heart_rate  REAL DEFAULT 0,
    logged_at       TEXT NOT NULL,
    raw_json        TEXT DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_workout_logs_user ON workout_logs(user_id, logged_at);

CREATE TABLE IF NOT EXISTS fitbit_tokens (
    user_id         INTEGER PRIMARY KEY,
    fitbit_user_id  TEXT NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    scope           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS fitbit_oauth_state (
    state               TEXT PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    code_verifier       TEXT NOT NULL,
    -- A6: sha256 hash of an HttpOnly cookie set at /connect. The /callback
    -- requires the cookie to round-trip and match this hash, defeating
    -- account-linking CSRF (an attacker-initiated /connect that lures the
    -- victim to /callback would not carry the victim's cookie).
    session_token_hash  TEXT,
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fitbit_activity (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    date                TEXT NOT NULL,
    calories_out        REAL DEFAULT 0,
    activity_calories   REAL DEFAULT 0,
    calories_bmr        REAL DEFAULT 0,
    steps               INTEGER DEFAULT 0,
    fairly_active_min   INTEGER DEFAULT 0,
    very_active_min     INTEGER DEFAULT 0,
    resting_heart_rate  INTEGER DEFAULT 0,
    fetched_at          TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, date)
);

CREATE TABLE IF NOT EXISTS strava_oauth_state (
    state               TEXT PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    session_token_hash  TEXT,  -- A6: see fitbit_oauth_state for rationale
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oura_tokens (
    user_id         INTEGER PRIMARY KEY,
    oura_user_id    TEXT NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    scope           TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS oura_oauth_state (
    state               TEXT PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    session_token_hash  TEXT,  -- A6: see fitbit_oauth_state for rationale
    -- A21: PKCE code_verifier so Oura mirrors Fitbit's S256 flow.
    code_verifier       TEXT,
    created_at          TEXT NOT NULL,
    expires_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oura_activity (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    date                TEXT NOT NULL,
    calories_out        REAL DEFAULT 0,
    activity_calories   REAL DEFAULT 0,
    steps               INTEGER DEFAULT 0,
    fairly_active_min   INTEGER DEFAULT 0,
    very_active_min     INTEGER DEFAULT 0,
    resting_heart_rate  INTEGER DEFAULT 0,
    fetched_at          TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, date)
);

CREATE INDEX IF NOT EXISTS idx_fitbit_activity_user ON fitbit_activity(user_id, date);
CREATE INDEX IF NOT EXISTS idx_fitbit_oauth_state_exp ON fitbit_oauth_state(expires_at);
CREATE INDEX IF NOT EXISTS idx_strava_oauth_state_exp ON strava_oauth_state(expires_at);
CREATE INDEX IF NOT EXISTS idx_oura_activity_user ON oura_activity(user_id, date);
CREATE INDEX IF NOT EXISTS idx_oura_oauth_state_exp ON oura_oauth_state(expires_at);

CREATE TABLE IF NOT EXISTS tos_acceptances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    tos_version     TEXT NOT NULL,
    accepted_at     TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_tos_user ON tos_acceptances(user_id);

-- Badge / achievement system
CREATE TABLE IF NOT EXISTS badge_earned (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    badge_id   TEXT NOT NULL,
    tier       INTEGER NOT NULL DEFAULT 0,
    earned_at  TEXT NOT NULL,
    seen       INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE(user_id, badge_id)
);

CREATE INDEX IF NOT EXISTS idx_badge_earned_user ON badge_earned(user_id);

CREATE TABLE IF NOT EXISTS streak_shields (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    earned_at    TEXT NOT NULL,
    used_at      TEXT,
    bridged_date TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_streak_shields_user ON streak_shields(user_id);

CREATE TABLE IF NOT EXISTS barcode_cache (
    barcode          TEXT PRIMARY KEY,
    product_name     TEXT NOT NULL,
    brand            TEXT DEFAULT '',
    serving_size_g   REAL,
    serving_size_unit TEXT NOT NULL DEFAULT 'g',
    serving_label    TEXT DEFAULT '',
    calories         REAL NOT NULL,
    protein          REAL NOT NULL,
    carbs            REAL NOT NULL,
    fat              REAL NOT NULL,
    cal_per_100g     REAL,
    protein_per_100g REAL,
    carbs_per_100g   REAL,
    fat_per_100g     REAL,
    source           TEXT NOT NULL,
    raw_json         TEXT DEFAULT '',
    image_url        TEXT DEFAULT '',
    not_found        INTEGER NOT NULL DEFAULT 0,
    fetched_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_barcode_cache_fetched ON barcode_cache(fetched_at);

-- Per-user barcode corrections: remember what the user accepted last time
-- they scanned this barcode, so future scans of the same product use their
-- values instead of the (often wrong) Open Food Facts data. Written from
-- src/services.py:accept_meal when an accepted barcode meal differs from
-- the original backend response by more than the threshold.
CREATE TABLE IF NOT EXISTS barcode_corrections (
    user_id          INTEGER NOT NULL,
    barcode          TEXT    NOT NULL,
    product_name     TEXT    NOT NULL,
    brand            TEXT    DEFAULT '',
    calories         REAL    NOT NULL,
    protein          REAL    NOT NULL,
    carbs            REAL    NOT NULL,
    fat              REAL    NOT NULL,
    serving_size_g   REAL,
    serving_size_unit TEXT   NOT NULL DEFAULT 'g',
    serving_label    TEXT    DEFAULT '',
    corrected_at     TEXT    NOT NULL,
    PRIMARY KEY (user_id, barcode),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_barcode_corrections_user ON barcode_corrections(user_id);

CREATE TABLE IF NOT EXISTS feedback (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    feedback_type TEXT NOT NULL,
    message       TEXT NOT NULL,
    page          TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback(user_id);

CREATE TABLE IF NOT EXISTS meal_feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    meal_id    INTEGER NOT NULL,
    rating     INTEGER NOT NULL,
    comment    TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (meal_id) REFERENCES meal_logs(id) ON DELETE CASCADE,
    UNIQUE(user_id, meal_id)
);

CREATE INDEX IF NOT EXISTS idx_meal_feedback_user ON meal_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_meal_feedback_meal ON meal_feedback(meal_id);
-- Admin metrics filter by created_at + rating; the composite covers both
-- the date-window scan and the rating="down" refinement.
CREATE INDEX IF NOT EXISTS idx_meal_feedback_rating_created
    ON meal_feedback(rating, created_at DESC);

CREATE TABLE IF NOT EXISTS user_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    event_type    TEXT NOT NULL,
    metadata_json TEXT DEFAULT '',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_events_user_type_ts
    ON user_events(user_id, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_events_type_ts
    ON user_events(event_type, created_at DESC);

-- Beta signup cap waitlist: emails that attempted to sign up after the
-- BETA_SIGNUP_CAP was reached. One row per email (UNIQUE); `invited_at`
-- is populated manually when the operator decides to let the user in.
CREATE TABLE IF NOT EXISTS waitlist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    email        TEXT UNIQUE NOT NULL,
    source       TEXT DEFAULT '',
    first_name   TEXT,
    referrer     TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    invited_at   TEXT,
    notes        TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_waitlist_created ON waitlist(created_at);

-- Email engagement: tracks which onboarding/re-engagement emails have been
-- sent to each user.  The scheduler queries this to decide what's next.
CREATE TABLE IF NOT EXISTS email_engagement (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    email_key  TEXT    NOT NULL,
    sent_at    TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_email_engagement_user ON email_engagement(user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_engagement_user_key ON email_engagement(user_id, email_key);

-- Stripe webhook idempotency: one row per delivered event_id. Inserted
-- only after a handler runs successfully, so a mid-handler crash does
-- *not* mark the event processed - Stripe's retry will run the handler
-- again. Older rows can be pruned (30 days is ample for Stripe's retry
-- window) by a periodic job; not critical since each row is tiny.
CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    processed_at  TEXT NOT NULL
);

-- Global outbound-email circuit breaker: one row per UTC hour bucket
-- (key = "YYYY-MM-DD HH"). Caps total emails sent across all users at
-- GLOBAL_EMAIL_HOURLY_CAP / hour to prevent a mailbomb-relay attack that
-- conscripts MacroShot to send to attacker-supplied addresses.
CREATE TABLE IF NOT EXISTS email_send_global_count (
    hour_bucket TEXT PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 0
);

-- Coach memory: durable per-user facts the AI coach uses across sessions.
-- Writes always flow through the pending-action confirm path (same as
-- log_weight, set_targets) so the user is in the loop on every save.
-- text_embedding fills in fire-and-forget after each insert/edit.
CREATE TABLE IF NOT EXISTS user_memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    kind            TEXT    NOT NULL,
    text            TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    text_embedding  BLOB,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_memories_user_kind ON user_memories(user_id, kind);
"""


_MIGRATIONS = [
    "ALTER TABLE users ADD COLUMN age INTEGER",
    "ALTER TABLE users ADD COLUMN height_cm REAL",
    "ALTER TABLE users ADD COLUMN weight_kg REAL",
    "ALTER TABLE users ADD COLUMN sex TEXT",
    "ALTER TABLE user_prefs ADD COLUMN meals_public INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE meal_logs ADD COLUMN meal_type TEXT DEFAULT ''",
    "ALTER TABLE meal_logs ADD COLUMN items_json TEXT DEFAULT ''",
    "ALTER TABLE meal_logs ADD COLUMN image_path TEXT DEFAULT ''",
    "ALTER TABLE users ADD COLUMN last_name TEXT",
    "ALTER TABLE users ADD COLUMN avatar_url TEXT",
    "ALTER TABLE users ADD COLUMN weight_goal_kg REAL",
    "ALTER TABLE user_prefs ADD COLUMN units_system TEXT DEFAULT 'metric'",
    "ALTER TABLE users ADD COLUMN activity_level TEXT",
    "ALTER TABLE users ADD COLUMN workouts_per_week INTEGER",
    "ALTER TABLE users ADD COLUMN weight_change_rate_kg REAL",
    "ALTER TABLE users ADD COLUMN goal TEXT",
    "ALTER TABLE meal_aliases ADD COLUMN sort_order INTEGER DEFAULT 0",
    # Gamification
    "ALTER TABLE user_prefs ADD COLUMN gamification TEXT DEFAULT 'full'",
    "ALTER TABLE meal_logs ADD COLUMN correction_count INTEGER DEFAULT 0",
    "ALTER TABLE users ADD COLUMN target_set_count INTEGER DEFAULT 0",
    "ALTER TABLE barcode_cache ADD COLUMN image_url TEXT DEFAULT ''",
    "ALTER TABLE barcode_cache ADD COLUMN not_found INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE streak_shields ADD COLUMN bridged_date TEXT",
    # Backfill bridged_date from used_at for legacy shields consumed before the column existed
    "UPDATE streak_shields SET bridged_date = used_at WHERE used_at IS NOT NULL AND bridged_date IS NULL",
    # Exercise calorie adjustment settings
    "ALTER TABLE user_prefs ADD COLUMN exercise_adjustment_on INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE user_prefs ADD COLUMN exercise_eat_back_pct REAL NOT NULL DEFAULT 0.75",
    # Rename meals_first_bite -> meals_meal_machine
    "UPDATE badge_earned SET badge_id = 'meals_meal_machine' WHERE badge_id = 'meals_first_bite'",
    # USDA FoodData Central cache
    "CREATE TABLE IF NOT EXISTS usda_fdc_cache (query_key TEXT PRIMARY KEY, calories REAL, protein REAL, carbs REAL, fat REAL, fdc_id INTEGER, data_type TEXT DEFAULT '', fetched_at TEXT NOT NULL)",
    # Per-user barcode corrections (see DDL above for schema)
    (
        "CREATE TABLE IF NOT EXISTS barcode_corrections ("
        "user_id INTEGER NOT NULL, barcode TEXT NOT NULL, "
        "product_name TEXT NOT NULL, brand TEXT DEFAULT '', "
        "calories REAL NOT NULL, protein REAL NOT NULL, "
        "carbs REAL NOT NULL, fat REAL NOT NULL, "
        "serving_size_g REAL, "
        "serving_size_unit TEXT NOT NULL DEFAULT 'g', "
        "serving_label TEXT DEFAULT '', "
        "corrected_at TEXT NOT NULL, "
        "PRIMARY KEY (user_id, barcode))"
    ),
    "CREATE INDEX IF NOT EXISTS idx_barcode_corrections_user ON barcode_corrections(user_id)",
    # meal_sessions: stash the original backend response so accept can
    # compare final vs original and save barcode corrections.
    "ALTER TABLE meal_sessions ADD COLUMN original_nutrition TEXT DEFAULT ''",
    # meal_sessions: remember which barcode a session came from (empty for
    # photo/text analyses) so we know when to consider saving a correction.
    "ALTER TABLE meal_sessions ADD COLUMN barcode TEXT DEFAULT ''",
    # Beta release: OG ("founding") flag on subscriptions. OG users share
    # the same daily/per-meal caps as regular Pro users but never require
    # renewal - they're grandfathered Pro forever, even when BETA_MODE flips
    # off and paying users start flowing through Stripe checkout.
    "ALTER TABLE subscriptions ADD COLUMN is_og INTEGER NOT NULL DEFAULT 0",
    # Beta release: newsletter opt-in (defaults to 1 so existing signups
    # auto-receive product updates until they opt out from Settings).
    "ALTER TABLE user_prefs ADD COLUMN newsletter_opt_in INTEGER NOT NULL DEFAULT 1",
    # Email engagement tracking (onboarding drip + re-engagement)
    (
        "CREATE TABLE IF NOT EXISTS email_engagement ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "email_key TEXT NOT NULL, "
        "sent_at TEXT NOT NULL, "
        "FOREIGN KEY (user_id) REFERENCES users(user_id))"
    ),
    "CREATE INDEX IF NOT EXISTS idx_email_engagement_user ON email_engagement(user_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_engagement_user_key ON email_engagement(user_id, email_key)",
    # Snapshot of the analysis that produced this meal, captured at accept
    # time (while meal_sessions is still populated). Stored on meal_logs so
    # admins can reproduce a thumbs-down meal's Gemini analysis after the
    # session row has been marked accepted. Contains: mode, image_path,
    # barcode, original_nutrition, final_nutrition, conversation turns,
    # session_id, created_at. Path-only - never the raw image bytes.
    "ALTER TABLE meal_logs ADD COLUMN analysis_snapshot_json TEXT DEFAULT ''",
    # The raw user-typed text that accompanied an image+text (or text-only)
    # analysis. Distinct from meal_description, which Gemini generates.
    # Preserved so the eval harness can replay production invocations
    # faithfully against candidate models.
    "ALTER TABLE meal_logs ADD COLUMN user_input TEXT DEFAULT ''",
    "ALTER TABLE meal_sessions ADD COLUMN user_input TEXT DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS idx_meal_feedback_rating_created "
    "ON meal_feedback(rating, created_at DESC)",
    # Push reminder idempotency: one row per (user, tag). Prevents a rare
    # double-fire when the service restarts inside the same hour as a tick
    # (e.g., deploy mid-hour) and the next tick happens to re-enter the
    # same meal_hour window. Rows are tiny - safe to keep 7 days and prune.
    (
        "CREATE TABLE IF NOT EXISTS reminder_sent ("
        "user_id INTEGER NOT NULL, tag TEXT NOT NULL, "
        "sent_at TEXT NOT NULL, PRIMARY KEY (user_id, tag))"
    ),
    # Barcode serving unit: 'g' (mass) or 'ml' (volume). serving_size_g is
    # kept for backwards compatibility as the numeric amount - read alongside
    # this column to decide whether the number means grams or millilitres.
    "ALTER TABLE barcode_cache ADD COLUMN serving_size_unit TEXT NOT NULL DEFAULT 'g'",
    "ALTER TABLE barcode_corrections ADD COLUMN serving_size_unit TEXT NOT NULL DEFAULT 'g'",
]


# Declarative versioned migrations. Add a new entry here for every schema
# change - the runner in init_db executes every entry whose version is
# greater than the DB's current schema_version, then bumps the version.
#
# Rules:
#   - Versions must be strictly increasing and start at 2 (v1 = _MIGRATIONS above).
#   - Every DDL change in a fresh install (in _DDL) should have a matching
#     entry here so existing DBs get the same column/table.
#   - Entries are idempotent via the duplicate-column/already-exists guard,
#     so a fresh-install DDL that already has the column is safe.
#
# DO NOT append to _MIGRATIONS for new changes - that list is frozen at v1
# and is not replayed on DBs already at schema_version>=1.
_VERSIONED_MIGRATIONS: list[tuple[int, str]] = [
    # v2: fitbit_activity.calories_bmr for Apple-Health-style net active
    # calorie calc (caloriesOut - caloriesBMR). See workouts.py
    # _fitbit_net_active_cals.
    (2, "ALTER TABLE fitbit_activity ADD COLUMN calories_bmr REAL DEFAULT 0"),
    # v3: email normalization (A3/A5). Adds email_canonical column.
    # The data-rewrite (lowercase + canonical-email backfill) happens in
    # `_migrate_email_normalization` (run from init_db immediately after
    # this version completes) because it requires Python logic and
    # conflict-aware row collapsing.
    (3, "ALTER TABLE web_auth ADD COLUMN email_canonical TEXT"),
    # v4-v6: UNIQUE indexes on external OAuth user IDs (A7). Each gets its
    # own version slot so the strictly-increasing invariant holds. Idempotent
    # via "IF NOT EXISTS" so reruns are no-ops. If existing data has dupes
    # the index creation fails and is logged; operator must resolve.
    (4, "CREATE UNIQUE INDEX IF NOT EXISTS idx_fitbit_tokens_fitbit_user_id ON fitbit_tokens(fitbit_user_id)"),
    (5, "CREATE UNIQUE INDEX IF NOT EXISTS idx_strava_tokens_athlete_id ON strava_tokens(strava_athlete_id)"),
    (6, "CREATE UNIQUE INDEX IF NOT EXISTS idx_oura_tokens_oura_user_id ON oura_tokens(oura_user_id)"),
    # v7: email-send circuit-breaker counter (A11).
    (7, (
        "CREATE TABLE IF NOT EXISTS email_send_global_count ("
        "hour_bucket TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0)"
    )),
    # v8-v11: A6 session-binding for OAuth state, A21 PKCE for Oura.
    (8, "ALTER TABLE fitbit_oauth_state ADD COLUMN session_token_hash TEXT"),
    (9, "ALTER TABLE strava_oauth_state ADD COLUMN session_token_hash TEXT"),
    (10, "ALTER TABLE oura_oauth_state ADD COLUMN session_token_hash TEXT"),
    (11, "ALTER TABLE oura_oauth_state ADD COLUMN code_verifier TEXT"),
    # v12: UNIQUE index on web_auth.email_canonical. MUST run after v3 (which
    # adds the column) and after _migrate_email_normalization backfills the
    # values. Previously this lived in _DDL, which crashed init_db on existing
    # DBs because _DDL runs BEFORE versioned migrations.
    (12, "CREATE UNIQUE INDEX IF NOT EXISTS idx_web_auth_email_canonical ON web_auth(email_canonical) WHERE email_canonical IS NOT NULL"),
    # v13/v14: user_prefs columns for AI web-search grounding and "show macros
    # in push notifications". Defaults to 0 (off) so existing rows opt in
    # explicitly via Settings - see src/web/schemas.py PrefsRequest.
    (13, "ALTER TABLE user_prefs ADD COLUMN ai_web_search_enabled INTEGER NOT NULL DEFAULT 0"),
    (14, "ALTER TABLE user_prefs ADD COLUMN notif_show_macros INTEGER NOT NULL DEFAULT 0"),
    # v15: semantic meal lookup. Stores a Gemini text-embedding (float32 BLOB)
    # of "{item_name}. {meal_description}" so search_meals_semantic can do
    # cosine top-K against the user's meal history. NULL = not yet embedded;
    # filled in fire-and-forget after each log_meal and by the offline
    # backfill script (scripts/backfill_meal_embeddings.py).
    (15, "ALTER TABLE meal_logs ADD COLUMN name_embedding BLOB"),
    # v16/v17: AI-coach long-term memory. user_memories holds per-user durable
    # facts the coach reads on every chat turn (allergies, restrictions,
    # preferences, notes). Writes always go through the pending-action confirm
    # path (remember_fact / forget_fact tools) - the user must approve every
    # save. text_embedding ships in V1 write-only (mirrors meal_logs.name_embedding
    # at v15); retrieval lands in V2 once a user crosses ~80 memories.
    (16, (
        "CREATE TABLE IF NOT EXISTS user_memories ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "kind TEXT NOT NULL, "
        "text TEXT NOT NULL, "
        "source TEXT NOT NULL, "
        "text_embedding BLOB, "
        "created_at TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, "
        "FOREIGN KEY (user_id) REFERENCES users(user_id))"
    )),
    (17, "CREATE INDEX IF NOT EXISTS idx_user_memories_user_kind ON user_memories(user_id, kind)"),
    # v18/v19: notification quiet hours. Window is interpreted as
    # [quiet_hours_start, quiet_hours_end) wrapping at midnight when
    # start > end. Defaults (23 -> 7) silence follow-up meal reminders
    # that would otherwise wrap into the late night, without affecting
    # primary meal reminders or the streak-alert hour.
    (18, "ALTER TABLE user_prefs ADD COLUMN quiet_hours_start INTEGER NOT NULL DEFAULT 23"),
    (19, "ALTER TABLE user_prefs ADD COLUMN quiet_hours_end INTEGER NOT NULL DEFAULT 7"),
    # v20: one-shot flag for guest-mode meal import on signup. NULL = the
    # user has never imported their pre-signup guest meals; a UTC ISO
    # timestamp means they already did. Gate in /meals/import-guest stops
    # the endpoint from being re-runnable (which would let an attacker
    # spray fake high-cal entries past the per-call 30-cap by replaying).
    (20, "ALTER TABLE users ADD COLUMN guest_meals_imported_at TEXT"),
]


async def _migrate_email_normalization(db) -> None:
    """Lowercase email columns in-place + backfill email_canonical.

    Idempotent: only rewrites rows whose stored email != lowercased form,
    and only fills email_canonical where it is NULL/empty.

    Conflict handling: if two rows in web_auth differ only by case (e.g.,
    `Alice@x.com` and `alice@x.com`), the lowercase rewrite would hit the
    UNIQUE constraint. We log and skip those - the operator must
    deduplicate by hand before the constraint can be enforced. The
    canonical-uniqueness invariant is added at the v3 schema step but
    creating the index will fail if dupes exist; we tolerate that here
    and log so the operator notices.
    """
    db.row_factory = aiosqlite.Row

    # 1. Lowercase web_auth.email
    rows = await (await db.execute(
        "SELECT user_id, email FROM web_auth WHERE email != LOWER(email)"
    )).fetchall()
    for r in rows:
        new_email = normalize_email(r["email"])
        try:
            await db.execute(
                "UPDATE web_auth SET email = ? WHERE user_id = ?",
                (new_email, r["user_id"]),
            )
        except Exception as exc:
            logger.warning(
                "Email normalization skipped for user_id=%d (likely UNIQUE conflict): %s",
                r["user_id"], exc,
            )

    # 2. Lowercase email_pins.email
    try:
        await db.execute(
            "UPDATE email_pins SET email = LOWER(email) WHERE email != LOWER(email)"
        )
    except Exception as exc:
        logger.warning("email_pins lowercase failed: %s", exc)

    # 3. Lowercase email_pin_failures.email
    try:
        await db.execute(
            "UPDATE email_pin_failures SET email = LOWER(email) WHERE email != LOWER(email)"
        )
    except Exception as exc:
        logger.warning("email_pin_failures lowercase failed: %s", exc)

    # 4. Lowercase waitlist.email if the table exists
    try:
        await db.execute(
            "UPDATE waitlist SET email = LOWER(email) WHERE email != LOWER(email)"
        )
    except Exception:
        pass  # Older DBs may not have the table

    # 5. Backfill email_canonical for any web_auth rows missing it.
    rows = await (await db.execute(
        "SELECT user_id, email FROM web_auth "
        "WHERE email_canonical IS NULL OR email_canonical = ''"
    )).fetchall()
    for r in rows:
        canon = canonical_email(r["email"])
        try:
            await db.execute(
                "UPDATE web_auth SET email_canonical = ? WHERE user_id = ?",
                (canon, r["user_id"]),
            )
        except Exception as exc:
            logger.warning(
                "email_canonical backfill skipped for user_id=%d: %s",
                r["user_id"], exc,
            )

    await db.commit()
    if rows:
        logger.info("email_canonical backfilled for %d row(s)", len(rows))


async def _migrate_fk_cascades(db) -> None:
    """Rebuild `meal_feedback` with ON DELETE CASCADE on meal_id.

    SQLite cannot `ALTER TABLE ADD FOREIGN KEY` - the whole table must be
    recreated. Idempotent: skips the rebuild if CASCADE is already there.

    PRAGMA foreign_keys is toggled via individual `execute()` calls
    outside of any transaction, because the PRAGMA is a no-op inside an
    open transaction - the SQLite docs require it to be set at the
    connection level before BEGIN. A previous version of this function
    embedded the PRAGMA calls inside an `executescript` which was at
    best ambiguous about whether FKs were really disabled during the
    DROP/RENAME.
    """
    row = await (await db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='meal_feedback'"
    )).fetchone()
    sql = (row[0] or "") if row else ""
    if not sql or "ON DELETE CASCADE" in sql:
        return

    # Clean up any orphan _new table from a prior aborted migration so
    # the CREATE below doesn't fail on "table already exists".
    await db.execute("DROP TABLE IF EXISTS meal_feedback_new")
    await db.commit()

    await db.execute("PRAGMA foreign_keys = OFF")
    try:
        await db.execute("BEGIN")
        await db.execute(
            """
            CREATE TABLE meal_feedback_new (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                meal_id    INTEGER NOT NULL,
                rating     INTEGER NOT NULL,
                comment    TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (meal_id) REFERENCES meal_logs(id) ON DELETE CASCADE,
                UNIQUE(user_id, meal_id)
            )
            """
        )
        await db.execute(
            "INSERT INTO meal_feedback_new (id, user_id, meal_id, rating, comment, created_at) "
            "SELECT id, user_id, meal_id, rating, comment, created_at FROM meal_feedback"
        )
        await db.execute("DROP TABLE meal_feedback")
        await db.execute("ALTER TABLE meal_feedback_new RENAME TO meal_feedback")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_meal_feedback_user ON meal_feedback(user_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_meal_feedback_meal ON meal_feedback(meal_id)")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_meal_feedback_rating_created "
            "ON meal_feedback(rating, created_at DESC)"
        )
        await db.execute("COMMIT")
        logger.info("Migrated meal_feedback to include ON DELETE CASCADE")
    except Exception:
        try:
            await db.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        await db.execute("PRAGMA foreign_keys = ON")


# Schema version auto-derived from _VERSIONED_MIGRATIONS. Version 1 is
# the historical _MIGRATIONS list (frozen, 2026-04-19). Every entry in
# _VERSIONED_MIGRATIONS contributes a new version >= 2.
_TARGET_SCHEMA_VERSION = (
    max((v for v, _ in _VERSIONED_MIGRATIONS), default=1) if _VERSIONED_MIGRATIONS else 1
)


async def _get_schema_version(db) -> int:
    """Return the current schema_version, or 0 if the table doesn't exist yet."""
    try:
        row = await (await db.execute("SELECT version FROM schema_version LIMIT 1")).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


async def _set_schema_version(db, version: int) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
    )
    await db.execute("DELETE FROM schema_version")
    await db.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


async def init_db(db_path: str) -> None:
    """Create tables, run migrations. Called once at startup BEFORE pool init.

    Uses a direct connection because the pool doesn't exist yet.
    PRAGMAs are applied here for the DDL connection; the pool applies them
    on each pooled connection separately.

    Migration versioning: schema_version tracks which batch of migrations
    has run. Existing idempotent guards (`duplicate column` swallowing)
    stay in place as a safety net for dev DBs that predate the table, but
    prod restarts now skip the 50+ ALTER attempts once the table reports
    the current version.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute("PRAGMA synchronous = NORMAL")
        await db.executescript(_DDL)
        await db.commit()

        current = await _get_schema_version(db)
        if current < 1:
            for migration in _MIGRATIONS:
                try:
                    await db.execute(migration)
                    await db.commit()
                except Exception as exc:
                    if "duplicate column" in str(exc).lower() or "already exists" in str(exc).lower():
                        pass  # Column already exists - idempotent no-op
                    else:
                        logger.warning("Migration failed: %s - %s", migration.strip()[:80], exc)
            try:
                await _migrate_fk_cascades(db)
            except Exception:
                logger.exception("FK cascade migration failed")
            await _set_schema_version(db, 1)
            await db.commit()

        # Declarative versioned migrations (v2+). Apply every pending step,
        # bumping schema_version after each so a mid-run crash resumes from
        # the right spot on next boot.
        ran_email_normalization = False
        for version, sql in sorted(_VERSIONED_MIGRATIONS):
            if current >= version:
                continue
            # Track whether this migration succeeded. We must NOT bump
            # schema_version on a UNIQUE-INDEX violation because the operator
            # still needs to deduplicate and re-run; bumping locks the index
            # out forever (next boot's `current >= version` skips it).
            should_bump = True
            try:
                await db.execute(sql)
                await db.commit()
            except Exception as exc:
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    pass  # Fresh-install DDL already has it - idempotent no-op
                else:
                    # CREATE UNIQUE INDEX fails (without "already exists") only
                    # when existing rows violate the constraint. v4-v6 install
                    # the per-source UNIQUE indexes that back ExternalAccount-
                    # AlreadyLinked - if they don't apply, the application-
                    # layer pre-flight check is the only remaining defense.
                    is_unique_idx = "unique" in sql.lower() and "index" in sql.lower()
                    if is_unique_idx:
                        # Do NOT bump schema_version: operator must dedupe and
                        # we need to re-attempt this migration on next boot.
                        should_bump = False
                        logger.error(
                            "Migration v%d (UNIQUE index) FAILED, likely existing "
                            "duplicate rows: %s - %s. Resolve duplicates before "
                            "launch; schema_version stays at v%d so this "
                            "migration retries on next boot. The application-"
                            "layer linked-account guard is the only protection "
                            "until this index is created.",
                            version, sql[:120], exc, version - 1,
                        )
                    else:
                        logger.warning("Migration v%d failed: %s - %s", version, sql[:80], exc)
            # v3 needs a Python-side data rewrite (lowercase + canonical-email
            # backfill) AFTER the column is added but BEFORE the unique index
            # can be created. Run once per upgrade pass.
            if version == 3 and not ran_email_normalization:
                try:
                    await _migrate_email_normalization(db)
                except Exception:
                    logger.exception("Email normalization migration failed")
                ran_email_normalization = True
            if should_bump:
                await _set_schema_version(db, version)
                await db.commit()
            else:
                # Stop the loop: subsequent migrations may depend on this one.
                break

    logger.info("SQLite DB initialised at %s (schema_version=%d)", db_path, _TARGET_SCHEMA_VERSION)


async def ensure_user(
    db_path: str,
    user_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    """INSERT the user if not present, then UPDATE mutable fields."""
    from datetime import datetime, timezone

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, registered_at) VALUES (?, ?, ?, ?)",
            (user_id, username or "", first_name or "", now_str),
        )
        await db.execute(
            "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
            (username or "", first_name or "", user_id),
        )
        await db.commit()


async def get_user_profile(db_path: str, user_id: int) -> dict:
    """Return {first_name, last_name, age, height_cm, weight_kg, sex} for the user (fields may be None)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT first_name, last_name, age, height_cm, weight_kg, sex, weight_goal_kg, activity_level, workouts_per_week, weight_change_rate_kg, goal FROM users WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return {"first_name": None, "last_name": None, "age": None, "height_cm": None, "weight_kg": None, "sex": None, "weight_goal_kg": None, "activity_level": None, "workouts_per_week": None, "weight_change_rate_kg": None, "goal": None}
    return {
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "age": row["age"],
        "height_cm": row["height_cm"],
        "weight_kg": row["weight_kg"],
        "sex": row["sex"],
        "weight_goal_kg": row["weight_goal_kg"],
        "activity_level": row["activity_level"],
        "workouts_per_week": row["workouts_per_week"],
        "weight_change_rate_kg": row["weight_change_rate_kg"],
        "goal": row["goal"],
    }


async def set_user_profile(db_path: str, user_id: int, **kwargs) -> None:
    """Update user profile fields. Only updates provided non-None values."""
    allowed = {"first_name", "last_name", "avatar_url", "age", "height_cm", "weight_kg", "sex", "weight_goal_kg", "activity_level", "workouts_per_week", "weight_change_rate_kg", "goal"}
    safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not safe_kwargs:
        return
    set_clause = ", ".join(f"{k} = ?" for k in safe_kwargs)
    values = list(safe_kwargs.values()) + [user_id]
    async with get_db(db_path) as db:
        await db.execute(
            f"UPDATE users SET {set_clause} WHERE user_id = ?",
            values,
        )
        await db.commit()


async def log_meal(
    db_path: str,
    user_id: int,
    logged_at: str,
    item_name: str,
    meal_description: str,
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    source: str,
    meal_type: str = "",
    items_json: str = "",
    image_path: str = "",
    analysis_snapshot_json: str = "",
    user_input: str = "",
) -> int:
    """Append a meal row to meal_logs. Returns the new row id.

    Schedules a fire-and-forget embedding of the meal name + description
    for semantic lookup (search_meals_semantic). The hook never blocks the
    caller and never raises; on missing API key or transient failure the
    row simply has a NULL name_embedding until the backfill script runs.
    """
    async with get_db(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO meal_logs
                (user_id, logged_at, item_name, meal_description, user_input,
                 calories, protein, carbs, fat, source, meal_type, items_json, image_path,
                 analysis_snapshot_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, logged_at, item_name, meal_description, user_input,
             calories, protein, carbs, fat, source, meal_type, items_json, image_path,
             analysis_snapshot_json),
        )
        await db.commit()
        meal_log_id = cursor.lastrowid

    try:
        from src.embeddings import schedule_embed_for_meal
        schedule_embed_for_meal(db_path, meal_log_id, item_name, meal_description)
    except Exception:
        logger.exception("schedule_embed_for_meal failed (non-fatal)")
    return meal_log_id


async def set_user_target(
    db_path: str,
    user_id: int,
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    set_by: str = "auto",
) -> None:
    """Upsert daily macro targets for a user."""
    from datetime import datetime, timezone

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO user_targets
                (user_id, calories, protein, carbs, fat, set_by, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, calories, protein, carbs, fat, set_by, now_str),
        )
        await db.commit()


async def get_user_target(db_path: str, user_id: int) -> dict | None:
    """Return {calories, protein, carbs, fat, set_by} or None if not set."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT calories, protein, carbs, fat, set_by FROM user_targets WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return {
        "calories": float(row["calories"]),
        "protein": float(row["protein"]),
        "carbs": float(row["carbs"]),
        "fat": float(row["fat"]),
        "set_by": row["set_by"],
    }


async def get_today_totals(db_path: str, user_id: int, today_str: str) -> dict:
    """Return {calories, protein, carbs, fat, meal_count} summed for today_str. Zeros if no rows."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT
                    COALESCE(SUM(calories), 0) AS sc,
                    COALESCE(SUM(protein),  0) AS sp,
                    COALESCE(SUM(carbs),    0) AS sw,
                    COALESCE(SUM(fat),      0) AS sf,
                    COUNT(*)                   AS cnt
                FROM meal_logs
                WHERE user_id = ? AND logged_at >= ? AND logged_at < ?
                """,
                (user_id, today_str, (date.fromisoformat(today_str) + timedelta(days=1)).isoformat()),
            )
        ).fetchone()
    return {
        "calories": float(row["sc"]),
        "protein": float(row["sp"]),
        "carbs": float(row["sw"]),
        "fat": float(row["sf"]),
        "meal_count": int(row["cnt"]),
    }


async def get_meals_for_date(db_path: str, user_id: int, date_str: str) -> list[dict]:
    """Get all meals logged on a specific date (YYYY-MM-DD)."""
    next_day = (date.fromisoformat(date_str) + timedelta(days=1)).isoformat()
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT item_name, meal_description, calories, protein, carbs, fat,
                       meal_type, items_json, logged_at
                FROM meal_logs
                WHERE user_id = ? AND logged_at >= ? AND logged_at < ?
                ORDER BY logged_at
                """,
                (user_id, date_str, next_day),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def get_period_totals(db_path: str, user_id: int, period: str, today_str: str):
    """Return TotalsResult for day/week/month using SQLite.

    today_str must be a YYYY-MM-DD date string in the user's local timezone.
    """
    from src.models import TotalsResult
    from datetime import date, timedelta

    today = date.fromisoformat(today_str)
    next_day = (today + timedelta(days=1)).isoformat()
    if period == "week":
        cutoff = (today - timedelta(days=6)).isoformat()
    elif period == "month":
        cutoff = (today - timedelta(days=29)).isoformat()
    else:
        period = "day"

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        if period == "day":
            row = await (
                await db.execute(
                    """
                    SELECT
                        COALESCE(SUM(calories), 0) AS sc,
                        COALESCE(SUM(protein),  0) AS sp,
                        COALESCE(SUM(carbs),    0) AS sw,
                        COALESCE(SUM(fat),      0) AS sf,
                        COUNT(*)                   AS cnt
                    FROM meal_logs
                    WHERE user_id = ? AND logged_at >= ? AND logged_at < ?
                    """,
                    (user_id, today_str, next_day),
                )
            ).fetchone()
            avg_cal = avg_pro = avg_carb = avg_f = None
        else:
            row = await (
                await db.execute(
                    """
                    SELECT
                        COALESCE(SUM(calories), 0)                    AS sc,
                        COALESCE(SUM(protein),  0)                    AS sp,
                        COALESCE(SUM(carbs),    0)                    AS sw,
                        COALESCE(SUM(fat),      0)                    AS sf,
                        COUNT(*)                                       AS cnt,
                        COUNT(DISTINCT substr(logged_at, 1, 10))      AS days
                    FROM meal_logs
                    WHERE user_id = ? AND logged_at >= ? AND logged_at < ?
                    """,
                    (user_id, cutoff, next_day),
                )
            ).fetchone()
            meal_count = int(row["cnt"])
            days_logged = int(row["days"] or 1)
            if meal_count > 0:
                avg_cal = float(row["sc"]) / days_logged
                avg_pro = float(row["sp"]) / days_logged
                avg_carb = float(row["sw"]) / days_logged
                avg_f = float(row["sf"]) / days_logged
            else:
                avg_cal = avg_pro = avg_carb = avg_f = None

    meal_count = int(row["cnt"])
    return TotalsResult(
        period=period,
        total_calories=float(row["sc"]),
        total_protein=float(row["sp"]),
        total_carbs=float(row["sw"]),
        total_fat=float(row["sf"]),
        meal_count=meal_count,
        avg_calories_per_day=avg_cal,
        avg_protein_per_day=avg_pro if period != "day" else None,
        avg_carbs_per_day=avg_carb if period != "day" else None,
        avg_fat_per_day=avg_f if period != "day" else None,
    )


async def get_meals_in_range(db_path: str, user_id: int, start_date: str, end_date: str) -> list[dict]:
    """Return meals logged between start_date and end_date (inclusive) ordered by time desc."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT id, logged_at, item_name, meal_description, calories, protein, carbs, fat, meal_type, items_json, image_path
                FROM meal_logs
                WHERE user_id = ? AND logged_at >= ? AND logged_at < ?
                ORDER BY id DESC
                """,
                (user_id, start_date, (date.fromisoformat(end_date) + timedelta(days=1)).isoformat()),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def get_meals_for_day(db_path: str, user_id: int, today_str: str, *, limit: int | None = None) -> list[dict]:
    """Return each meal logged on today_str ordered by time."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = """
                SELECT id, logged_at, item_name, meal_description, calories, protein, carbs, fat, meal_type, items_json, image_path
                FROM meal_logs
                WHERE user_id = ? AND logged_at LIKE ?
                ORDER BY id DESC
                """
        if limit:
            query += f" LIMIT {int(limit)}"
        rows = await (
            await db.execute(query, (user_id, f"{today_str}%"))
        ).fetchall()
    return [dict(r) for r in rows]


async def get_weekly_avg(db_path: str, user_id: int, today_str: str | None = None) -> dict | None:
    """Return {calories, protein, carbs, fat} averaged over days actually logged in the last 7 days."""
    today = date.fromisoformat(today_str) if today_str else date.today()
    week_ago_str = (today - timedelta(days=6)).isoformat()
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT
                    SUM(calories) AS sc,
                    SUM(protein)  AS sp,
                    SUM(carbs)    AS sw,
                    SUM(fat)      AS sf,
                    COUNT(*)      AS cnt,
                    COUNT(DISTINCT substr(logged_at, 1, 10)) AS days
                FROM meal_logs
                WHERE user_id = ? AND logged_at >= ?
                """,
                (user_id, week_ago_str),
            )
        ).fetchone()
    if not row or int(row["cnt"]) == 0:
        return None
    days = int(row["days"]) or 1
    return {
        "calories": float(row["sc"] or 0) / days,
        "protein": float(row["sp"] or 0) / days,
        "carbs": float(row["sw"] or 0) / days,
        "fat": float(row["sf"] or 0) / days,
    }


def _calculate_streak(
    sorted_dates: list[str],
    today_str: str,
    shielded_dates: set[str] | None = None,
) -> int:
    """Return the current logging streak in days.

    Anchor = today if today has a log (or shield), else yesterday if yesterday
    has a log (or shield), else 0. Walks backwards from anchor counting
    consecutive days that have either a meal log or a used shield.
    """
    date_set = set(sorted_dates)
    all_valid = date_set | (shielded_dates or set())
    today = date.fromisoformat(today_str)

    if today_str in all_valid:
        anchor = today
    else:
        yesterday_str = (today - timedelta(days=1)).isoformat()
        if yesterday_str in all_valid:
            anchor = today - timedelta(days=1)
        else:
            return 0

    streak = 0
    current = anchor
    while current.isoformat() in all_valid:
        streak += 1
        current -= timedelta(days=1)
    return streak


async def get_community_stats(db_path: str, today_str: str | None = None) -> dict:
    """Return aggregate community statistics."""
    from datetime import date, timedelta

    today = date.fromisoformat(today_str) if today_str else date.today()
    today_str = today.isoformat()
    week_ago_str = (today - timedelta(days=6)).isoformat()

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        row = await (await db.execute("SELECT COUNT(*) AS c FROM meal_logs")).fetchone()
        total_meals: int = row["c"] if row else 0

        row = await (await db.execute("SELECT COUNT(*) AS c FROM users")).fetchone()
        total_users: int = row["c"] if row else 0

        row = await (
            await db.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM meal_logs WHERE logged_at LIKE ?",
                (f"{today_str}%",),
            )
        ).fetchone()
        active_today: int = row["c"] if row else 0

        row = await (
            await db.execute(
                "SELECT COUNT(DISTINCT user_id) AS c FROM meal_logs WHERE logged_at >= ?",
                (week_ago_str,),
            )
        ).fetchone()
        active_this_week: int = row["c"] if row else 0

        top_rows = await (
            await db.execute(
                """
                SELECT item_name, COUNT(*) AS cnt
                FROM meal_logs
                GROUP BY item_name
                ORDER BY cnt DESC
                LIMIT 5
                """
            )
        ).fetchall()
        top_foods: list[tuple[str, int]] = [(r["item_name"], r["cnt"]) for r in top_rows]

        agg = await (
            await db.execute(
                "SELECT AVG(calories) AS ac, AVG(protein) AS ap, AVG(carbs) AS aw, AVG(fat) AS af FROM meal_logs"
            )
        ).fetchone()
        avg_calories = float(agg["ac"] or 0)
        avg_protein = float(agg["ap"] or 0)
        avg_carbs = float(agg["aw"] or 0)
        avg_fat = float(agg["af"] or 0)

    return {
        "total_meals": total_meals,
        "total_users": total_users,
        "active_today": active_today,
        "active_this_week": active_this_week,
        "top_foods": top_foods,
        "avg_calories": avg_calories,
        "avg_protein": avg_protein,
        "avg_carbs": avg_carbs,
        "avg_fat": avg_fat,
    }


async def get_last_meal(db_path: str, user_id: int) -> dict | None:
    """Return the most recently logged meal row or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, logged_at, item_name, meal_description, calories, protein, carbs, fat, meal_type, items_json "
                "FROM meal_logs WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "logged_at": row["logged_at"],
        "item_name": row["item_name"],
        "meal_description": row["meal_description"] or "",
        "calories": float(row["calories"]),
        "protein": float(row["protein"]),
        "carbs": float(row["carbs"]),
        "fat": float(row["fat"]),
        "meal_type": row["meal_type"] or "",
        "items_json": row["items_json"] or "",
    }


async def has_meal_in_window(
    db_path: str, user_id: int, date_str: str, start_hour: int, end_hour: int
) -> bool:
    """Return True if any meal was logged for user_id on date_str with hour in [start_hour, end_hour)."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                """
                SELECT 1 FROM meal_logs
                WHERE user_id = ?
                  AND logged_at LIKE ?
                  AND CAST(substr(logged_at, 12, 2) AS INTEGER) >= ?
                  AND CAST(substr(logged_at, 12, 2) AS INTEGER) < ?
                LIMIT 1
                """,
                (user_id, f"{date_str}%", start_hour, end_hour),
            )
        ).fetchone()
    return row is not None


async def get_meal_window_pattern(
    db_path: str, user_id: int, today_str: str, lookback_days: int = 14
) -> dict:
    """Count distinct days the user logged a meal in each meal-time window
    over the recent past. Used to detect meals the user consistently skips
    (e.g., never logs breakfast) so push reminders for those windows can be
    suppressed.

    Returns:
        {
          "total_days": <distinct active days in lookback window>,
          "breakfast_days": <distinct days with hour 0-10>,
          "lunch_days":     <distinct days with hour 11-15>,
          "snack_days":     <distinct days with hour 16-19>,
          "dinner_days":    <distinct days with hour 20-23>,
        }

    Hour buckets match _MEAL_WINDOWS in push_scheduler. Uses local-naive
    substr on logged_at, matching the convention in has_meal_in_window.
    """
    from datetime import date as _date, timedelta
    try:
        cutoff = (_date.fromisoformat(today_str) - timedelta(days=lookback_days)).isoformat()
    except ValueError:
        return {"total_days": 0, "breakfast_days": 0, "lunch_days": 0, "snack_days": 0, "dinner_days": 0}
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT
                  COUNT(DISTINCT substr(logged_at, 1, 10)) AS total_days,
                  COUNT(DISTINCT CASE WHEN CAST(substr(logged_at, 12, 2) AS INTEGER) < 11
                                       THEN substr(logged_at, 1, 10) END) AS breakfast_days,
                  COUNT(DISTINCT CASE WHEN CAST(substr(logged_at, 12, 2) AS INTEGER) BETWEEN 11 AND 15
                                       THEN substr(logged_at, 1, 10) END) AS lunch_days,
                  COUNT(DISTINCT CASE WHEN CAST(substr(logged_at, 12, 2) AS INTEGER) BETWEEN 16 AND 19
                                       THEN substr(logged_at, 1, 10) END) AS snack_days,
                  COUNT(DISTINCT CASE WHEN CAST(substr(logged_at, 12, 2) AS INTEGER) BETWEEN 20 AND 23
                                       THEN substr(logged_at, 1, 10) END) AS dinner_days
                FROM meal_logs
                WHERE user_id = ?
                  AND substr(logged_at, 1, 10) >= ?
                  AND substr(logged_at, 1, 10) < ?
                """,
                (user_id, cutoff, today_str),
            )
        ).fetchone()
    if row is None:
        return {"total_days": 0, "breakfast_days": 0, "lunch_days": 0, "snack_days": 0, "dinner_days": 0}
    return {
        "total_days": int(row["total_days"] or 0),
        "breakfast_days": int(row["breakfast_days"] or 0),
        "lunch_days": int(row["lunch_days"] or 0),
        "snack_days": int(row["snack_days"] or 0),
        "dinner_days": int(row["dinner_days"] or 0),
    }


async def delete_meal(db_path: str, meal_id: int, user_id: int | None = None) -> None:
    """Delete a meal row by id. If user_id is provided, enforces ownership."""
    async with get_db(db_path) as db:
        if user_id is not None:
            await db.execute("DELETE FROM meal_logs WHERE id = ? AND user_id = ?", (meal_id, user_id))
        else:
            await db.execute("DELETE FROM meal_logs WHERE id = ?", (meal_id,))
        await db.commit()


async def update_meal(
    db_path: str,
    meal_id: int,
    user_id: int,
    item_name: str,
    meal_description: str,
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    items_json: str = "",
    meal_type: str | None = None,
) -> None:
    """Update an existing meal_logs row by id. Enforces user ownership."""
    async with get_db(db_path) as db:
        if meal_type:
            await db.execute(
                """
                UPDATE meal_logs
                SET item_name = ?, meal_description = ?,
                    calories = ?, protein = ?, carbs = ?, fat = ?,
                    items_json = ?, meal_type = ?
                WHERE id = ? AND user_id = ?
                """,
                (item_name, meal_description, calories, protein, carbs, fat, items_json, meal_type, meal_id, user_id),
            )
        else:
            await db.execute(
                """
                UPDATE meal_logs
                SET item_name = ?, meal_description = ?,
                    calories = ?, protein = ?, carbs = ?, fat = ?,
                    items_json = ?
                WHERE id = ? AND user_id = ?
                """,
                (item_name, meal_description, calories, protein, carbs, fat, items_json, meal_id, user_id),
            )
        await db.commit()


async def get_meal_by_id(db_path: str, user_id: int, meal_id: int) -> dict | None:
    """Return a full meal row by id for the given user, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, logged_at, item_name, meal_description, calories, protein, carbs, fat, meal_type, items_json, image_path "
                "FROM meal_logs WHERE id = ? AND user_id = ?",
                (meal_id, user_id),
            )
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "logged_at": row["logged_at"],
        "item_name": row["item_name"],
        "meal_description": row["meal_description"] or "",
        "calories": float(row["calories"]),
        "protein": float(row["protein"]),
        "carbs": float(row["carbs"]),
        "fat": float(row["fat"]),
        "meal_type": row["meal_type"] or "",
        "items_json": row["items_json"] or "",
        "image_path": row["image_path"] or "",
    }


async def get_last_meal_by_type(
    db_path: str, user_id: int, date_str: str, meal_type: str
) -> dict | None:
    """Return the most recent meal of a given meal_type on date_str, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, logged_at, item_name, meal_description, calories, protein, carbs, fat, meal_type, items_json "
                "FROM meal_logs WHERE user_id = ? AND logged_at LIKE ? AND meal_type = ? "
                "ORDER BY id DESC LIMIT 1",
                (user_id, f"{date_str}%", meal_type),
            )
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "logged_at": row["logged_at"],
        "item_name": row["item_name"],
        "meal_description": row["meal_description"] or "",
        "calories": float(row["calories"]),
        "protein": float(row["protein"]),
        "carbs": float(row["carbs"]),
        "fat": float(row["fat"]),
        "meal_type": row["meal_type"] or "",
        "items_json": row["items_json"] or "",
    }


async def get_daily_totals_7days(db_path: str, user_id: int, num_days: int = 7, today_str: str | None = None) -> list[dict]:
    """Return per-day totals for the last N days, ascending, filling zeros."""
    today = date.fromisoformat(today_str) if today_str else date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(num_days - 1, -1, -1)]
    cutoff = dates[0]

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT substr(logged_at,1,10) AS d,
                       SUM(calories) AS sc,
                       SUM(protein)  AS sp,
                       SUM(carbs)    AS sw,
                       SUM(fat)      AS sf,
                       COUNT(*)      AS cnt
                FROM meal_logs
                WHERE user_id = ? AND logged_at >= ?
                GROUP BY d
                """,
                (user_id, cutoff),
            )
        ).fetchall()

    data_by_date = {
        r["d"]: {
            "date": r["d"],
            "calories": float(r["sc"] or 0),
            "protein": float(r["sp"] or 0),
            "carbs": float(r["sw"] or 0),
            "fat": float(r["sf"] or 0),
            "meal_count": int(r["cnt"]),
        }
        for r in rows
    }
    return [
        data_by_date.get(d, {"date": d, "calories": 0.0, "protein": 0.0, "carbs": 0.0, "fat": 0.0, "meal_count": 0})
        for d in dates
    ]


_PREFS_DEFAULTS = {
    "timezone": "America/Los_Angeles",
    "breakfast_hour": 8,
    "lunch_hour": 11,
    "snack_hour": 15,
    "dinner_hour": 19,
    "streak_alert_hour": 22,
    "reminders_on": 1,
    "meals_public": 0,
    "units_system": "metric",
    "gamification": "full",
    "exercise_adjustment_on": 1,
    "exercise_eat_back_pct": 0.75,
    "ai_web_search_enabled": 0,
    "notif_show_macros": 0,
    "quiet_hours_start": 23,
    "quiet_hours_end": 7,
}


async def get_user_prefs(db_path: str, user_id: int) -> dict:
    """Return user_prefs dict, falling back to defaults if no row exists."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT timezone, breakfast_hour, lunch_hour, snack_hour, "
                "dinner_hour, streak_alert_hour, reminders_on, meals_public, "
                "units_system, gamification, exercise_adjustment_on, exercise_eat_back_pct, "
                "ai_web_search_enabled, notif_show_macros, "
                "quiet_hours_start, quiet_hours_end "
                "FROM user_prefs WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return dict(_PREFS_DEFAULTS)
    keys = row.keys()
    return {
        "timezone": row["timezone"],
        "breakfast_hour": int(row["breakfast_hour"]),
        "lunch_hour": int(row["lunch_hour"]),
        "snack_hour": int(row["snack_hour"]),
        "dinner_hour": int(row["dinner_hour"]),
        "streak_alert_hour": int(row["streak_alert_hour"]),
        "reminders_on": int(row["reminders_on"]),
        "meals_public": int(row["meals_public"]),
        "units_system": row["units_system"] or "metric",
        "gamification": row["gamification"] if "gamification" in keys else "full",
        "exercise_adjustment_on": int(row["exercise_adjustment_on"]) if "exercise_adjustment_on" in keys else 1,
        "exercise_eat_back_pct": float(row["exercise_eat_back_pct"]) if "exercise_eat_back_pct" in keys else 0.75,
        "ai_web_search_enabled": int(row["ai_web_search_enabled"]) if "ai_web_search_enabled" in keys else 0,
        "notif_show_macros": int(row["notif_show_macros"]) if "notif_show_macros" in keys else 0,
        "quiet_hours_start": int(row["quiet_hours_start"]) if "quiet_hours_start" in keys else 23,
        "quiet_hours_end": int(row["quiet_hours_end"]) if "quiet_hours_end" in keys else 7,
    }


async def set_user_prefs(db_path: str, user_id: int, **kwargs) -> None:
    """Upsert user_prefs, then UPDATE only the provided kwargs keys."""
    from datetime import datetime, timezone as _tz

    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    defaults = _PREFS_DEFAULTS
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO user_prefs
                (user_id, timezone, breakfast_hour, lunch_hour, snack_hour,
                 dinner_hour, streak_alert_hour, reminders_on, meals_public, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                defaults["timezone"],
                defaults["breakfast_hour"],
                defaults["lunch_hour"],
                defaults["snack_hour"],
                defaults["dinner_hour"],
                defaults["streak_alert_hour"],
                defaults["reminders_on"],
                defaults["meals_public"],
                now_str,
            ),
        )
        if kwargs:
            allowed = set(defaults.keys())
            safe_kwargs = {k: v for k, v in kwargs.items() if k in allowed}
            if safe_kwargs:
                set_clause = ", ".join(f"{k} = ?" for k in safe_kwargs)
                values = list(safe_kwargs.values()) + [now_str, user_id]
                await db.execute(
                    f"UPDATE user_prefs SET {set_clause}, updated_at = ? WHERE user_id = ?",
                    values,
                )
        await db.commit()


async def get_user_stats(db_path: str, user_id: int, today_str: str | None = None) -> dict:
    """Return per-user statistics using a single connection and minimal queries."""
    from datetime import date, timedelta

    today = date.fromisoformat(today_str) if today_str else date.today()
    today_str = today.isoformat()
    week_ago_str = (today - timedelta(days=6)).isoformat()

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Single query: total meals, all-time macro sums, weekly sums, most logged
        agg = await (
            await db.execute(
                """
                SELECT
                    COUNT(*) AS total_meals,
                    SUM(protein) AS sp, SUM(carbs) AS sc, SUM(fat) AS sf,
                    SUM(CASE WHEN logged_at >= ? THEN calories ELSE 0 END) AS wk_cal,
                    SUM(CASE WHEN logged_at >= ? THEN protein ELSE 0 END) AS wk_pro,
                    SUM(CASE WHEN logged_at >= ? THEN carbs ELSE 0 END) AS wk_carb,
                    SUM(CASE WHEN logged_at >= ? THEN fat ELSE 0 END) AS wk_fat,
                    COUNT(DISTINCT CASE WHEN logged_at >= ? THEN substr(logged_at, 1, 10) END) AS wk_days
                FROM meal_logs WHERE user_id = ?
                """,
                (week_ago_str, week_ago_str, week_ago_str, week_ago_str, week_ago_str, user_id),
            )
        ).fetchone()

        total_meals = int(agg["total_meals"] or 0)
        if total_meals == 0:
            return {
                "total_meals": 0,
                "streak_days": 0,
                "avg_daily_calories_week": 0,
                "avg_protein_week": 0,
                "avg_carbs_week": 0,
                "avg_fat_week": 0,
                "most_logged_meal": None,
                "macro_split": {"pct_protein": 0, "pct_carbs": 0, "pct_fat": 0},
            }

        # Most logged meal (cheap group-by, already indexed)
        top = await (
            await db.execute(
                "SELECT item_name, COUNT(*) AS cnt FROM meal_logs WHERE user_id = ? GROUP BY item_name ORDER BY cnt DESC LIMIT 1",
                (user_id,),
            )
        ).fetchone()

        # Streak: distinct dates in last 90 days (sufficient for any realistic streak)
        streak_cutoff = (today - timedelta(days=90)).isoformat()
        date_rows = await (
            await db.execute(
                "SELECT DISTINCT substr(logged_at, 1, 10) AS d FROM meal_logs WHERE user_id = ? AND logged_at >= ? ORDER BY d",
                (user_id, streak_cutoff),
            )
        ).fetchall()

        # Shield dates for streak calculation (shields bridge missed days)
        shield_rows = await (
            await db.execute(
                "SELECT bridged_date FROM streak_shields WHERE user_id = ? AND bridged_date IS NOT NULL",
                (user_id,),
            )
        ).fetchall()

    sorted_dates = [r["d"] for r in date_rows]
    shielded_dates = {r["bridged_date"] for r in shield_rows if r["bridged_date"]}
    streak_days = _calculate_streak(sorted_dates, today_str, shielded_dates)

    week_days = max(int(agg["wk_days"] or 1), 1)
    avg_daily_calories_week = float(agg["wk_cal"] or 0) / week_days
    avg_protein_week = float(agg["wk_pro"] or 0) / week_days
    avg_carbs_week = float(agg["wk_carb"] or 0) / week_days
    avg_fat_week = float(agg["wk_fat"] or 0) / week_days

    most_logged_meal = (top["item_name"], top["cnt"]) if top else None

    total_protein_kcal = float(agg["sp"] or 0) * 4
    total_carbs_kcal = float(agg["sc"] or 0) * 4
    total_fat_kcal = float(agg["sf"] or 0) * 9
    total_macro_kcal = total_protein_kcal + total_carbs_kcal + total_fat_kcal
    if total_macro_kcal > 0:
        pct_protein = total_protein_kcal / total_macro_kcal * 100
        pct_carbs = total_carbs_kcal / total_macro_kcal * 100
        pct_fat = total_fat_kcal / total_macro_kcal * 100
    else:
        pct_protein = pct_carbs = pct_fat = 0.0

    return {
        "total_meals": total_meals,
        "streak_days": streak_days,
        "avg_daily_calories_week": avg_daily_calories_week,
        "avg_protein_week": avg_protein_week,
        "avg_carbs_week": avg_carbs_week,
        "avg_fat_week": avg_fat_week,
        "most_logged_meal": most_logged_meal,
        "macro_split": {
            "pct_protein": pct_protein,
            "pct_carbs": pct_carbs,
            "pct_fat": pct_fat,
        },
    }


async def sync_user_meals(db_path: str, user_id: int, meals: list[dict]) -> int:
    """Replace all meal_logs for a user with the provided list (sourced from Sheets).
    Returns the number of rows inserted.
    """
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM meal_logs WHERE user_id = ?", (user_id,))
        count = 0
        failed = 0
        for m in meals:
            try:
                await db.execute(
                    """
                    INSERT INTO meal_logs
                        (user_id, logged_at, item_name, meal_description,
                         calories, protein, carbs, fat, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        m["logged_at"],
                        m["item_name"],
                        m.get("meal_description", ""),
                        m["calories"],
                        m["protein"],
                        m["carbs"],
                        m["fat"],
                        m.get("source", ""),
                    ),
                )
                count += 1
            except Exception:
                failed += 1
                continue
        await db.commit()
    if failed:
        logger.warning("sync_user_meals: %d/%d rows failed to insert for user_id=%s", failed, len(meals), user_id)
    return count


async def get_recent_meals(db_path: str, user_id: int, limit: int = 20) -> list[dict]:
    """Return the last N meals for a user ordered by logged_at DESC."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT logged_at, item_name, meal_description, calories, protein, carbs, fat, meal_type
                FROM meal_logs
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def log_gemini_call(
    db_path: str,
    call_type: str,
    user_id: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    has_image: bool = False,
    web_searches: int = 0,
) -> None:
    """Record a single Gemini API call for cost tracking."""
    from datetime import datetime, timezone as _tz

    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO gemini_calls
                (called_at, call_type, user_id, input_tokens, output_tokens, has_image, web_searches)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (now_str, call_type, user_id, input_tokens, output_tokens, int(has_image), web_searches),
        )
        await db.commit()


async def get_gemini_stats(db_path: str) -> dict:
    """Return Gemini API usage stats for today, this week, and this month.

    Timestamps are stored in UTC by log_gemini_call, so all date comparisons
    here use UTC dates to avoid mismatches when the server runs in a non-UTC timezone.
    """
    from datetime import datetime, timedelta, timezone as _tz

    utc_today = datetime.now(_tz.utc).date()
    today_str = utc_today.isoformat()
    week_ago_str = (utc_today - timedelta(days=6)).isoformat()
    month_start_str = utc_today.replace(day=1).isoformat()

    agg_sql = """
        SELECT COUNT(*) AS calls,
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(web_searches), 0) AS web_searches,
               COALESCE(SUM(CASE WHEN has_image THEN 1 ELSE 0 END), 0) AS image_calls
        FROM gemini_calls
    """

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        today_row = await (await db.execute(
            f"{agg_sql} WHERE called_at LIKE ?", (f"{today_str}%",)
        )).fetchone()

        week_row = await (await db.execute(
            f"{agg_sql} WHERE substr(called_at,1,10) >= ?", (week_ago_str,)
        )).fetchone()

        month_row = await (await db.execute(
            f"{agg_sql} WHERE substr(called_at,1,10) >= ?", (month_start_str,)
        )).fetchone()

        all_row = await (await db.execute(agg_sql)).fetchone()

    def _to_dict(row):
        return dict(row) if row else {"calls": 0, "input_tokens": 0, "output_tokens": 0, "web_searches": 0, "image_calls": 0}

    return {
        "today": _to_dict(today_row),
        "week": _to_dict(week_row),
        "month": _to_dict(month_row),
        "all_time": _to_dict(all_row),
    }


async def log_fatsecret_comparison(
    db_path: str,
    user_id: int,
    meal_log_id: int | None,
    items: list,
    logged_at: str,
) -> None:
    """Insert one row per FoodItem that has gemini_per_100g or fatsecret_per_100g."""
    rows_to_insert = []
    for item in items:
        g = item.gemini_per_100g or {}
        f = item.fatsecret_per_100g or {}
        if not g and not f:
            continue
        rows_to_insert.append((
            user_id,
            meal_log_id,
            item.name,
            item.weight_g,
            g.get("calories"), g.get("protein"), g.get("carbs"), g.get("fat"),
            f.get("calories"), f.get("protein"), f.get("carbs"), f.get("fat"),
            logged_at,
        ))
    if not rows_to_insert:
        return
    async with get_db(db_path) as db:
        await db.executemany(
            """
            INSERT INTO fatsecret_comparison
                (user_id, meal_log_id, item_name, weight_g,
                 gemini_cal_100g, gemini_protein_100g, gemini_carbs_100g, gemini_fat_100g,
                 fatsecret_cal_100g, fatsecret_protein_100g, fatsecret_carbs_100g, fatsecret_fat_100g,
                 logged_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows_to_insert,
        )
        await db.commit()


async def create_web_user(
    db_path: str,
    email: str,
    google_sub: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    avatar_url: str | None = None,
) -> int:
    """Create a new user + web_auth entry. Returns the new user_id.

    Enforces the beta signup cap when APP_MODE=hosted + BETA_MODE=on: if
    `web_auth` already has BETA_SIGNUP_CAP rows, raises SignupCapReached
    so the caller can add the email to the waitlist and return a friendly
    error to the client.

    Concurrency: the check + INSERTs run inside a `BEGIN IMMEDIATE`
    transaction so SQLite serialises the write lock. Two racing signups
    on different pool connections cannot both pass the COUNT check - the
    second blocks on the write lock, and when it wakes the COUNT reflects
    the first signup's insert. Any future refactor that removes the
    explicit BEGIN IMMEDIATE re-introduces the TOCTOU race.
    """
    from datetime import datetime, timezone as _tz

    # Lazy import to avoid circular (web.constants imports nothing from db.py,
    # but keeping the hot db.py import path free of web.* imports is a good
    # guardrail for any future refactor that moves constants around).
    from src.web.constants import APP_MODE, BETA_MODE, BETA_SIGNUP_CAP

    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    # Defense-in-depth: schemas should already lowercase, but mirror it here
    # so direct callers (tests, scripts) can't bypass the invariant.
    email = normalize_email(email)
    email_canon = canonical_email(email)
    display_name = first_name or email.split("@")[0]
    async with get_db(db_path) as db:
        # BEGIN IMMEDIATE grabs the RESERVED write lock up-front so the
        # cap check races fairly against other signup connections. Without
        # it, SQLite's default deferred transaction would let two readers
        # both see count=199 before either upgrades to a writer.
        await db.execute("BEGIN IMMEDIATE")
        try:
            if APP_MODE == "hosted" and BETA_MODE:
                row = await (
                    await db.execute("SELECT COUNT(*) FROM web_auth")
                ).fetchone()
                current = int(row[0]) if row else 0
                if current >= BETA_SIGNUP_CAP:
                    # ROLLBACK explicitly so the next caller on this
                    # pooled connection doesn't inherit an open txn.
                    await db.execute("ROLLBACK")
                    raise SignupCapReached(email, BETA_SIGNUP_CAP)

            cursor = await db.execute(
                "INSERT INTO users (username, first_name, last_name, avatar_url, registered_at) VALUES (?, ?, ?, ?, ?)",
                (email, display_name, last_name, avatar_url, now_str),
            )
            user_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO web_auth (user_id, email, email_canonical, google_sub, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, email, email_canon, google_sub, now_str),
            )
            await db.commit()
        except SignupCapReached:
            raise
        except Exception:
            # Rollback on any other failure so the pooled connection is
            # left in a clean state for the next borrower.
            try:
                await db.execute("ROLLBACK")
            except Exception:
                pass
            raise
    return user_id


async def get_web_user_by_email(db_path: str, email: str) -> dict | None:
    """Look up web_auth + users by email.

    Tries an exact (lowercased) match first, then falls back to the
    canonical form so two visually-distinct Gmail aliases that resolve to
    the same canonical address resolve to one account.
    """
    email = normalize_email(email)
    canon = canonical_email(email)
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT wa.user_id, wa.email, wa.google_sub, wa.created_at, u.username, u.first_name, u.last_name, u.avatar_url "
            "FROM web_auth wa JOIN users u ON wa.user_id = u.user_id WHERE wa.email = ?",
            (email,),
        )).fetchone()
        if not row and canon != email:
            row = await (await db.execute(
                "SELECT wa.user_id, wa.email, wa.google_sub, wa.created_at, u.username, u.first_name, u.last_name, u.avatar_url "
                "FROM web_auth wa JOIN users u ON wa.user_id = u.user_id WHERE wa.email_canonical = ?",
                (canon,),
            )).fetchone()
    if not row:
        return None
    return dict(row)


async def get_web_user_by_google_sub(db_path: str, google_sub: str) -> dict | None:
    """Look up web_auth + users by Google subject ID."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT wa.user_id, wa.email, wa.google_sub, wa.created_at, u.username, u.first_name, u.last_name, u.avatar_url "
            "FROM web_auth wa JOIN users u ON wa.user_id = u.user_id WHERE wa.google_sub = ?",
            (google_sub,),
        )).fetchone()
    if not row:
        return None
    return dict(row)


async def get_guest_imported_at(db_path: str, user_id: int) -> str | None:
    """Return the timestamp the user imported pre-signup guest meals, or None."""
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT guest_meals_imported_at FROM users WHERE user_id = ?",
            (user_id,),
        )).fetchone()
    return row[0] if row and row[0] else None


async def mark_guest_imported(db_path: str, user_id: int) -> bool:
    """Atomically flip users.guest_meals_imported_at if still NULL.

    Returns True if this call won the race (and may proceed to insert
    meals), False if a concurrent /meals/import-guest already ran for
    this user. UPDATE … WHERE … IS NULL gives us the atomicity needed
    for "once-per-user" without an explicit transaction.
    """
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        cur = await db.execute(
            "UPDATE users SET guest_meals_imported_at = ? "
            "WHERE user_id = ? AND guest_meals_imported_at IS NULL",
            (now_str, user_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_web_user_by_id(db_path: str, user_id: int) -> dict | None:
    """Look up web_auth + users by user_id."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT wa.user_id, wa.email, wa.google_sub, wa.created_at, u.username, u.first_name, u.last_name, u.avatar_url "
            "FROM web_auth wa JOIN users u ON wa.user_id = u.user_id WHERE wa.user_id = ?",
            (user_id,),
        )).fetchone()
    if not row:
        return None
    return dict(row)


async def store_email_pin(db_path: str, email: str, pin_hash: str, expires_at: str) -> None:
    """Store a hashed email PIN. Deletes only expired PINs (keeps recent ones for rate limiting)."""
    from datetime import datetime, timezone as _tz
    email = normalize_email(email)
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        # Only delete expired PINs, keep recent ones for rate limiting
        await db.execute("DELETE FROM email_pins WHERE email = ? AND expires_at < ?", (email, now_str))
        await db.execute(
            "INSERT INTO email_pins (email, pin_hash, attempts, created_at, expires_at) VALUES (?, ?, 0, ?, ?)",
            (email, pin_hash, now_str, expires_at),
        )
        await db.commit()


PIN_FAILURES_PER_HOUR = 10  # block further verification after this many failures


async def is_email_pin_locked(db_path: str, email: str) -> bool:
    """Return True if the email has hit the per-email PIN-brute-force cap.

    Caps total failed verification attempts at 10/hour across all PINs
    for the email. Closes the hole where the per-PIN cap (5) * new PINs
    allowed per hour (3) would otherwise permit 15 tries/hour against
    the 1M-key 6-digit PIN space.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    email = normalize_email(email)
    one_hour_ago = (_dt.now(_tz.utc) - _td(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM email_pin_failures WHERE email = ? AND failed_at > ?",
                (email, one_hour_ago),
            )
        ).fetchone()
        return (row[0] if row else 0) >= PIN_FAILURES_PER_HOUR


async def verify_email_pin(db_path: str, email: str, raw_pin: str) -> bool:
    """Verify an email PIN. Returns True if valid. Increments attempts counter.

    Takes the raw user-submitted PIN (not a pre-hashed value) because
    stored hashes are salted - only verify_pin_hash can decide equality.

    Uses atomic UPDATE with WHERE attempts < 5 to prevent TOCTOU race on
    the brute-force attempt counter. Also enforces a per-email lockout
    (see `is_email_pin_locked`) so attackers can't trade fresh PINs for
    additional attempts.
    """
    from datetime import datetime, timezone as _tz, timedelta as _td
    from src.web.auth import verify_pin_hash
    email = normalize_email(email)
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    one_hour_ago = (datetime.now(_tz.utc) - _td(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Per-email lockout check (global across all PINs for this email).
        fail_row = await (await db.execute(
            "SELECT COUNT(*) AS c FROM email_pin_failures WHERE email = ? AND failed_at > ?",
            (email, one_hour_ago),
        )).fetchone()
        if fail_row and fail_row["c"] >= PIN_FAILURES_PER_HOUR:
            logger.warning(
                "PIN verification blocked for %s: %d failures in the last hour (cap %d)",
                email, fail_row["c"], PIN_FAILURES_PER_HOUR,
            )
            return False

        row = await (await db.execute(
            "SELECT pin_hash, attempts, expires_at FROM email_pins WHERE email = ? ORDER BY created_at DESC LIMIT 1",
            (email,),
        )).fetchone()
        if not row:
            await db.execute(
                "INSERT INTO email_pin_failures (email, failed_at) VALUES (?, ?)",
                (email, now_str),
            )
            await db.commit()
            return False
        if row["expires_at"] < now_str:
            await db.execute(
                "INSERT INTO email_pin_failures (email, failed_at) VALUES (?, ?)",
                (email, now_str),
            )
            await db.commit()
            return False
        if row["attempts"] >= 5:
            await db.execute(
                "INSERT INTO email_pin_failures (email, failed_at) VALUES (?, ?)",
                (email, now_str),
            )
            await db.commit()
            return False
        # verify_pin_hash handles both the new PBKDF2 format and legacy
        # SHA-256 values stored before the hashing upgrade, and uses
        # hmac.compare_digest internally for timing-side-channel safety.
        if not verify_pin_hash(raw_pin, row["pin_hash"]):
            # Atomic increment with guard: only increment if still under limit
            cursor = await db.execute(
                "UPDATE email_pins SET attempts = attempts + 1 "
                "WHERE email = ? AND pin_hash = ? AND attempts < 5",
                (email, row["pin_hash"]),
            )
            await db.execute(
                "INSERT INTO email_pin_failures (email, failed_at) VALUES (?, ?)",
                (email, now_str),
            )
            await db.commit()
            if cursor.rowcount == 0:
                # Another concurrent request already hit the limit
                return False
            return False
        # Correct PIN - delete it so it can't be reused, and clear the
        # email's failure history (legitimate user logged in).
        await db.execute(
            "DELETE FROM email_pins WHERE email = ? AND pin_hash = ?",
            (email, row["pin_hash"]),
        )
        await db.execute("DELETE FROM email_pin_failures WHERE email = ?", (email,))
        await db.commit()
        return True


PIN_REQUESTS_PER_HOUR = 1


async def check_pin_rate_limit(db_path: str, email: str) -> bool:
    """Return True if the email can request a new PIN.

    Two gates in one: max PIN_REQUESTS_PER_HOUR new PINs per hour, AND
    not inside a brute-force lockout (>= PIN_FAILURES_PER_HOUR failed
    verification attempts in the last hour). Both are per-email.

    Tightened from 3/hour to 1/hour on 2026-05-05 as a mailbomb-relay
    countermeasure (A11) - the previous cap let an attacker conscript
    MacroShot to send 3 emails/hour to any address they typed.
    """
    from datetime import datetime, timezone as _tz, timedelta
    email = normalize_email(email)
    canon = canonical_email(email)
    one_hour_ago = (datetime.now(_tz.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM email_pins WHERE email IN (?, ?) AND created_at > ?",
            (email, canon, one_hour_ago),
        )).fetchone()
        if (row[0] if row else 0) >= PIN_REQUESTS_PER_HOUR:
            return False
        fail_row = await (await db.execute(
            "SELECT COUNT(*) FROM email_pin_failures WHERE email IN (?, ?) AND failed_at > ?",
            (email, canon, one_hour_ago),
        )).fetchone()
        if (fail_row[0] if fail_row else 0) >= PIN_FAILURES_PER_HOUR:
            return False
    return True


# ── A4: per-email verify-attempt cap (mirrors check_pin_rate_limit) ──

# Max failed PIN-verifications counted within the rolling
# VERIFY_LOCKOUT_MINUTES window before further attempts are rejected. The
# previous name "_PER_HOUR" was misleading because the actual rolling
# window is 30 minutes (VERIFY_LOCKOUT_MINUTES), not one hour.
VERIFY_FAILURES_BEFORE_LOCKOUT = 10
VERIFY_LOCKOUT_MINUTES = 30


async def check_verify_rate_limit(db_path: str, email: str) -> bool:
    """Return True if the email can attempt PIN verification.

    Caps failed verifications at VERIFY_FAILURES_BEFORE_LOCKOUT within a
    rolling VERIFY_LOCKOUT_MINUTES window. This is a second gate alongside
    the per-PIN attempts counter; without it an attacker could rotate
    fresh PINs to keep a per-PIN budget alive.
    """
    from datetime import datetime, timezone as _tz, timedelta
    email = normalize_email(email)
    canon = canonical_email(email)
    cutoff = (
        datetime.now(_tz.utc) - timedelta(minutes=VERIFY_LOCKOUT_MINUTES)
    ).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM email_pin_failures "
            "WHERE email IN (?, ?) AND failed_at > ?",
            (email, canon, cutoff),
        )).fetchone()
        return (row[0] if row else 0) < VERIFY_FAILURES_BEFORE_LOCKOUT


# ── A11: global outbound-email circuit breaker ─────────────────────

GLOBAL_EMAIL_HOURLY_CAP = 1000


async def check_and_increment_global_email_cap(db_path: str) -> bool:
    """Return True and increment the bucket if under the global hourly cap.

    Atomic INSERT-then-conditional-UPDATE pattern: the bucket key is the
    current hour (UTC), so each new hour gets a fresh row. Multiple
    concurrent senders race on the UPDATE - SQLite serializes writes so
    the race is benign.
    """
    from datetime import datetime, timezone as _tz
    bucket = datetime.now(_tz.utc).strftime("%Y-%m-%d %H")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO email_send_global_count (hour_bucket, count) VALUES (?, 0)",
            (bucket,),
        )
        # Conditional update ensures we never exceed the cap.
        cur = await db.execute(
            "UPDATE email_send_global_count SET count = count + 1 "
            "WHERE hour_bucket = ? AND count < ?",
            (bucket, GLOBAL_EMAIL_HOURLY_CAP),
        )
        await db.commit()
        return (cur.rowcount or 0) > 0


async def revoke_jwt(db_path: str, jti: str, user_id: int | None, expires_at: str) -> None:
    """Mark a JWT as revoked by its jti.

    expires_at is the JWT's `exp` claim as an ISO/SQL datetime string - we
    keep the row until then so expired tokens can be pruned; after that
    the signature check alone is sufficient.
    """
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO revoked_tokens (jti, user_id, revoked_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (jti, user_id, now, expires_at),
        )
        await db.commit()


async def is_jwt_revoked(db_path: str, jti: str) -> bool:
    async with get_db(db_path) as db:
        row = await (
            await db.execute("SELECT 1 FROM revoked_tokens WHERE jti = ? LIMIT 1", (jti,))
        ).fetchone()
        return row is not None


async def prune_expired_revocations(db_path: str) -> int:
    """Delete revoked_tokens rows whose underlying JWT has already expired.

    Called periodically (push scheduler, etc.) so the table doesn't grow
    unbounded. Returns number of rows deleted.
    """
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM revoked_tokens WHERE expires_at < ?", (now,),
        )
        await db.commit()
        return cursor.rowcount or 0


async def prune_stripe_webhook_events(db_path: str, max_age_days: int = 30) -> int:
    """Delete stripe_webhook_events rows older than max_age_days.

    Stripe retries failed webhooks on its own schedule (max ~3 days), so
    30 days is ample for idempotency while keeping the table bounded.
    Without this the table grows one row per delivered event forever.
    Returns number of rows deleted.
    """
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    cutoff = (_dt.now(_tz.utc) - _td(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM stripe_webhook_events WHERE processed_at < ?", (cutoff,),
        )
        await db.commit()
        return cursor.rowcount or 0


async def create_meal_session(db_path: str, session_id: str, user_id: int, images_json: str = "[]",
                               conversation: str = "[]", nutrition: str = "", meal_type: str = "",
                               original_nutrition: str = "", barcode: str = "",
                               user_input: str = "") -> None:
    """Create a new meal analysis session.

    original_nutrition: JSON snapshot of the nutrition returned by the
    backend at session-creation time. Used by accept_meal to compare the
    final (possibly edited) nutrition against the original so we can save
    barcode corrections for future scans.
    barcode: the scanned barcode if this session came from a barcode lookup,
    empty string otherwise.
    user_input: the raw user-typed text that accompanied the analysis
    (for image+text and text-only meals). Distinct from the Gemini-generated
    meal_description, preserved so the eval harness can replay production
    invocations faithfully.
    """
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO meal_sessions (session_id, user_id, images_json, conversation, nutrition, meal_type, original_nutrition, barcode, user_input, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (session_id, user_id, images_json, conversation, nutrition, meal_type, original_nutrition, barcode, user_input, now_str, now_str),
        )
        await db.commit()


async def get_meal_session(db_path: str, session_id: str, user_id: int | None = None) -> dict | None:
    """Get a meal session. If user_id provided, enforce ownership."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        if user_id is not None:
            row = await (await db.execute(
                "SELECT * FROM meal_sessions WHERE session_id = ? AND user_id = ?",
                (session_id, user_id),
            )).fetchone()
        else:
            row = await (await db.execute(
                "SELECT * FROM meal_sessions WHERE session_id = ?", (session_id,),
            )).fetchone()
    if not row:
        return None
    return dict(row)


async def try_claim_meal_session(db_path: str, session_id: str, user_id: int) -> bool:
    """Atomically transition a pending meal session to 'accepted'.

    Returns True if this caller won the race and may proceed to log_meal.
    Returns False if the session was already accepted, cancelled, or missing
    - meaning a concurrent accept (or correction) got there first. This is
    the single atomic step that makes accept_meal safe against double-logging.

    NULL status is treated as 'pending' because older rows predate the
    defaulted column.
    """
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "UPDATE meal_sessions SET status = 'accepted', updated_at = ? "
            "WHERE session_id = ? AND user_id = ? "
            "AND (status = 'pending' OR status IS NULL)",
            (__import__("datetime").datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
             session_id, user_id),
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0


async def update_meal_session(
    db_path: str, session_id: str, user_id: int | None = None, **kwargs
) -> None:
    """Update meal session fields.

    SECURITY: pass ``user_id`` whenever it is known so the WHERE clause
    enforces ownership at the SQL level.  Existing callers all check
    ownership upstream via ``get_meal_session(db, session_id, user_id)``,
    but defense-in-depth at the SQL boundary protects against future
    refactors that forget the check.
    """
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    allowed = {"images_json", "conversation", "nutrition", "meal_type", "status"}
    safe = {k: v for k, v in kwargs.items() if k in allowed}
    safe["updated_at"] = now_str
    set_clause = ", ".join(f"{k} = ?" for k in safe)
    values = list(safe.values()) + [session_id]
    where_clause = "session_id = ?"
    if user_id is not None:
        where_clause += " AND user_id = ?"
        values.append(user_id)
    async with get_db(db_path) as db:
        await db.execute(
            f"UPDATE meal_sessions SET {set_clause} WHERE {where_clause}",
            values,
        )
        await db.commit()


async def cleanup_stale_sessions(
    db_path: str, max_age_hours: int = 1, image_dir: str | None = None
) -> int:
    """Delete stale meal sessions (pending + cancelled) and their images. Returns count deleted."""
    import shutil
    from datetime import datetime, timezone as _tz, timedelta

    cutoff = (datetime.now(_tz.utc) - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Fetch sessions to delete so we can clean up their images
        rows = await (await db.execute(
            "SELECT session_id, user_id FROM meal_sessions "
            "WHERE status IN ('pending', 'cancelled') AND updated_at < ?",
            (cutoff,),
        )).fetchall()

        if not rows:
            return 0

        # Clean up image directories
        if image_dir:
            import os
            for row in rows:
                session_img_dir = os.path.join(image_dir, str(row["user_id"]), row["session_id"])
                if os.path.isdir(session_img_dir) and os.path.realpath(session_img_dir).startswith(os.path.realpath(image_dir)):
                    shutil.rmtree(session_img_dir, ignore_errors=True)

        # Delete DB rows
        await db.execute(
            "DELETE FROM meal_sessions "
            "WHERE status IN ('pending', 'cancelled') AND updated_at < ?",
            (cutoff,),
        )
        await db.commit()
        return len(rows)


async def get_meals_paginated(db_path: str, user_id: int, limit: int = 20, offset: int = 0,
                               date_filter: str | None = None, meal_type: str | None = None) -> list[dict]:
    """Get meals with pagination and optional filters."""
    conditions = ["user_id = ?"]
    params: list = [user_id]
    if date_filter:
        conditions.append("logged_at LIKE ?")
        params.append(f"{date_filter}%")
    if meal_type:
        conditions.append("meal_type = ?")
        params.append(meal_type)
    where = " AND ".join(conditions)
    params.extend([limit, offset])
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            f"SELECT id, logged_at, item_name, meal_description, calories, protein, carbs, fat, "
            f"meal_type, items_json, image_path FROM meal_logs WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params,
        )).fetchall()
    return [dict(r) for r in rows]


async def get_recent_unique_meals(db_path: str, user_id: int, limit: int = 8) -> list[dict]:
    """Get the most recent unique meals (deduped by description), for quick re-logging."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT id, item_name, meal_description, calories, protein, carbs, fat, meal_type, logged_at
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY CASE WHEN meal_description != '' THEN meal_description ELSE item_name END
                    ORDER BY id DESC
                ) AS rn
                FROM meal_logs
                WHERE user_id = ?
            )
            WHERE rn = 1
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )).fetchall()
    return [dict(r) for r in rows]


# ── Meal aliases (saved meals) ──────────────────────────────────────

async def get_all_aliases(db_path: str, user_id: int) -> list[dict]:
    """Return all aliases for a user."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT alias_name, item_name, meal_description, calories, protein, carbs, fat, items_json, sort_order "
            "FROM meal_aliases WHERE user_id = ? ORDER BY sort_order, alias_name",
            (user_id,),
        )).fetchall()
    return [dict(r) for r in rows]


async def count_aliases(db_path: str, user_id: int) -> int:
    """Return the number of aliases for a user (faster than get_all_aliases)."""
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM meal_aliases WHERE user_id = ?", (user_id,),
        )).fetchone()
    return row[0] if row else 0


async def save_alias(
    db_path: str, user_id: int, alias_name: str,
    item_name: str, meal_description: str,
    calories: float, protein: float, carbs: float, fat: float,
    items_json: str = "[]",
) -> None:
    """Upsert a meal alias (case-insensitive name)."""
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            """INSERT INTO meal_aliases (user_id, alias_name, item_name, meal_description,
                   calories, protein, carbs, fat, items_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, alias_name) DO UPDATE SET
                   item_name=excluded.item_name, meal_description=excluded.meal_description,
                   calories=excluded.calories, protein=excluded.protein,
                   carbs=excluded.carbs, fat=excluded.fat,
                   items_json=excluded.items_json""",
            (user_id, alias_name.strip().lower(), item_name, meal_description,
             calories, protein, carbs, fat, items_json, now_str),
        )
        await db.commit()


async def reorder_aliases(db_path: str, user_id: int, names: list[str]) -> None:
    """Update sort_order for aliases based on the provided name list."""
    async with get_db(db_path) as db:
        for i, name in enumerate(names):
            await db.execute(
                "UPDATE meal_aliases SET sort_order = ? WHERE user_id = ? AND alias_name = ?",
                (i, user_id, name.strip().lower()),
            )
        await db.commit()


async def delete_alias(db_path: str, user_id: int, alias_name: str) -> bool:
    """Delete a meal alias. Returns True if a row was deleted."""
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM meal_aliases WHERE user_id = ? AND alias_name = ?",
            (user_id, alias_name.strip().lower()),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_alias_by_name(db_path: str, user_id: int, alias_name: str) -> dict | None:
    """Get a single alias by name (case-insensitive)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT alias_name, item_name, meal_description, calories, protein, carbs, fat, items_json "
            "FROM meal_aliases WHERE user_id = ? AND alias_name = ?",
            (user_id, alias_name.strip().lower()),
        )).fetchone()
    return dict(row) if row else None


# ── Coach memory (user_memories) ────────────────────────────────────

# Allowed `kind` values. Hard constraints (allergy, restriction) get
# emphasized in the seed-context block; preference and note are soft signals.
# `goal` is intentionally absent - users.goal / users.weight_goal_kg already
# track that and we don't want two sources of truth.
USER_MEMORY_KINDS = frozenset({"allergy", "restriction", "preference", "note"})
USER_MEMORY_SOURCES = frozenset({"user", "coach_suggested"})
_USER_MEMORY_UPDATE_COLS = frozenset({"kind", "text"})

# Defense-in-depth cap. The system prompt tells the coach to skip one-off
# statements, but the model can ignore. Without a hard cap, a misbehaving
# turn could spam confirmation cards every message and bloat the seed
# context indefinitely. 100 is well above any realistic user need (a busy
# household with multiple allergies + restrictions + recipe preferences
# typically lands well under 30) but small enough that a runaway
# remember_fact loop hits the wall fast.
MAX_USER_MEMORIES = 100


async def count_user_memories(db_path: str, user_id: int) -> int:
    """Return the number of memories stored for a user."""
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM user_memories WHERE user_id = ?", (user_id,),
        )).fetchone()
    return row[0] if row else 0


async def add_user_memory(
    db_path: str, user_id: int, kind: str, text: str, source: str = "user",
) -> int:
    """Insert a memory and return its new id.

    Caller is expected to validate kind/text shape (the MCP tool layer does
    this) - we do a defensive enum check anyway in case a new caller shows up.
    """
    if kind not in USER_MEMORY_KINDS:
        raise ValueError(f"invalid memory kind: {kind!r}")
    if source not in USER_MEMORY_SOURCES:
        raise ValueError(f"invalid memory source: {source!r}")
    text = text.strip()
    if not text:
        raise ValueError("memory text cannot be empty")

    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO user_memories (user_id, kind, text, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, kind, text, source, now_str, now_str),
        )
        await db.commit()
        return cursor.lastrowid


async def get_user_memories(db_path: str, user_id: int) -> list[dict]:
    """Return all memories for a user, ordered by kind then creation time.

    `id` is the final tiebreaker so equal-second timestamps don't jitter
    the ordering across reads - matters because the seed-context block
    feeds Gemini's prompt cache and a stable byte-prefix maximizes hits.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT id, kind, text, source, created_at, updated_at "
            "FROM user_memories WHERE user_id = ? "
            "ORDER BY kind, created_at, id",
            (user_id,),
        )).fetchall()
    return [dict(r) for r in rows]


async def get_user_memory_by_id(
    db_path: str, user_id: int, memory_id: int,
) -> dict | None:
    """Look up a single memory scoped to user_id (returns None if not owned)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT id, kind, text, source, created_at, updated_at "
            "FROM user_memories WHERE user_id = ? AND id = ?",
            (user_id, memory_id),
        )).fetchone()
    return dict(row) if row else None


async def update_user_memory(
    db_path: str, user_id: int, memory_id: int, **fields,
) -> bool:
    """Update a memory's mutable fields. Returns True if a row was changed.

    Only `kind` and `text` are updatable - everything else (id, user_id,
    source, created_at) is immutable. Embedding refresh is the caller's
    responsibility (schedule_embed_for_memory) when text changes.

    A None value for any updatable field is treated as "don't update that
    column" - matches the partial-update semantics of the REST PUT
    endpoint. An empty `fields` dict (or one filtered down to nothing)
    raises ValueError so callers can't mistake a no-op for a 404.
    """
    if "kind" in fields:
        if fields["kind"] is None:
            del fields["kind"]
        elif fields["kind"] not in USER_MEMORY_KINDS:
            raise ValueError(f"invalid memory kind: {fields['kind']!r}")
    if "text" in fields:
        if fields["text"] is None:
            del fields["text"]
        else:
            fields["text"] = fields["text"].strip()
            if not fields["text"]:
                raise ValueError("memory text cannot be empty")

    set_clause, params = _safe_set_clause(_USER_MEMORY_UPDATE_COLS, fields)
    if not set_clause:
        raise ValueError("no updatable fields provided")

    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    params.append(now_str)
    params.extend([user_id, memory_id])
    async with get_db(db_path) as db:
        cursor = await db.execute(
            f"UPDATE user_memories SET {set_clause}, updated_at = ? "
            "WHERE user_id = ? AND id = ?",
            params,
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_user_memory(db_path: str, user_id: int, memory_id: int) -> bool:
    """Delete a memory scoped to user_id. Returns True if a row was deleted."""
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM user_memories WHERE user_id = ? AND id = ?",
            (user_id, memory_id),
        )
        await db.commit()
        return cursor.rowcount > 0


# ── Marker-keyed memory upsert (for auto-managed coach memories) ─────
#
# Some memories are "slots" - we want at most one current value at a time
# and overwriting it on update beats appending stale rows. The text-marker
# scheme avoids a schema migration: we encode the slot identity in a stable
# prefix of the text (e.g. "Targets baseline: ..."), find any prior row for
# the same user with that prefix, delete it, and insert the new value.
#
# Callers must:
#   1. Use a unique, stable marker substring (recommend a colon-suffixed
#      label that no other memory text would naturally start with).
#   2. Set source="coach_suggested" so user-authored memories with
#      coincidentally-similar text aren't clobbered (the LIKE is scoped
#      by source).
#   3. Schedule the embedding refresh on the returned new id (use
#      `schedule_embed_for_memory` from src.embeddings).

async def upsert_marker_memory(
    db_path: str,
    user_id: int,
    kind: str,
    text: str,
    marker: str,
    source: str = "coach_suggested",
) -> int:
    """Replace any prior memory matching the marker prefix, then insert text.

    Returns the new row id. Use the returned id with
    `schedule_embed_for_memory(db_path, id, text)` so the embedding catches
    up. Marker matching is `source = ? AND text LIKE marker || '%'`, so the
    marker MUST be the literal prefix of `text` (we assert it here in case
    the caller forgets and builds `text` without it).

    Atomic w.r.t. the source/marker combination: the delete and insert run
    in one transaction, so a concurrent read on a re-entrant accept never
    observes "neither row present" or "both rows present."
    """
    if kind not in USER_MEMORY_KINDS:
        raise ValueError(f"invalid memory kind: {kind!r}")
    if source not in USER_MEMORY_SOURCES:
        raise ValueError(f"invalid memory source: {source!r}")
    text = text.strip()
    if not text:
        raise ValueError("memory text cannot be empty")
    if not marker or not text.startswith(marker):
        raise ValueError("text must start with marker for upsert_marker_memory to find it later")

    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    like_pattern = marker + "%"
    async with get_db(db_path) as db:
        await db.execute(
            "DELETE FROM user_memories WHERE user_id = ? AND source = ? AND text LIKE ?",
            (user_id, source, like_pattern),
        )
        cursor = await db.execute(
            "INSERT INTO user_memories (user_id, kind, text, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, kind, text, source, now_str, now_str),
        )
        await db.commit()
        return cursor.lastrowid


# Marker prefixes for slots that the backend manages automatically.
# Keep these distinctive enough that user-authored text won't collide.
ONBOARDING_TARGETS_MARKER = "Targets baseline:"
GOAL_CONTEXT_MARKER = "Goal context:"


def build_targets_baseline_text(
    calories: int | float,
    protein: int | float,
    carbs: int | float,
    fat: int | float,
    goal: str | None = None,
    activity_level: str | None = None,
) -> str:
    """Format the canonical 'Targets baseline:' memory body. ~95 chars typical.

    Goal/activity are appended only when present so a user who skipped
    those fields still gets a clean memory string. Stays under the 200-char
    user_memories text cap with plenty of headroom.
    """
    parts = [
        f"{ONBOARDING_TARGETS_MARKER} {int(round(calories))} cal, "
        f"{int(round(protein))}g protein, {int(round(carbs))}g carbs, {int(round(fat))}g fat."
    ]
    if goal:
        # Friendly form: "lose_weight" → "lose weight"
        parts.append(f"Goal: {goal.replace('_', ' ')}.")
    if activity_level:
        parts.append(f"Activity: {activity_level.replace('_', ' ')}.")
    return " ".join(parts)


# ── Push subscriptions ──────────────────────────────────────────────

async def save_push_subscription(db_path: str, user_id: int, endpoint: str, p256dh: str, auth: str) -> None:
    """Upsert a push subscription, transferring ownership of the endpoint to user_id.

    An endpoint is physical-device-scoped, so it must belong to exactly one user.
    Any prior row with the same endpoint (under any user) is deleted first - this
    is how we clean up after a user logs out on a shared device and another user
    logs in and re-subscribes.
    """
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))
        await db.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, endpoint, p256dh, auth, now_str),
        )
        await db.commit()


async def delete_push_subscriptions_for_user(db_path: str, user_id: int) -> int:
    """Delete all push subscriptions for a user (used on logout as a safety net)."""
    async with get_db(db_path) as db:
        cur = await db.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
        await db.commit()
        return cur.rowcount or 0


async def delete_push_subscription(db_path: str, user_id: int, endpoint: str) -> None:
    """Remove a push subscription."""
    async with get_db(db_path) as db:
        await db.execute(
            "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
            (user_id, endpoint),
        )
        await db.commit()


async def get_push_subscriptions(db_path: str, user_id: int) -> list[dict]:
    """Return all push subscriptions for a user."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = ?",
            (user_id,),
        )).fetchall()
    return [dict(r) for r in rows]


async def get_all_push_subscriptions(db_path: str) -> list[dict]:
    """Return all push subscriptions (for scheduler)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT user_id, endpoint, p256dh, auth FROM push_subscriptions"
        )).fetchall()
    return [dict(r) for r in rows]


async def try_mark_reminder_sent(db_path: str, user_id: int, tag: str) -> bool:
    """Record that a reminder with this tag was sent to this user.

    Returns True if this is the first time (caller should send the push),
    False if the tag was already recorded (caller should skip - another
    tick already handled it, e.g., after a mid-hour service restart).
    """
    from datetime import datetime, timezone as tz

    now = datetime.now(tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO reminder_sent (user_id, tag, sent_at) VALUES (?, ?, ?)",
            (user_id, tag, now),
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_reminder_sent_row(db_path: str, user_id: int, tag: str) -> None:
    """Roll back a reminder_sent claim - used when the actual push send failed
    transiently (no devices got it) so the next dispatch can retry."""
    async with get_db(db_path) as db:
        await db.execute(
            "DELETE FROM reminder_sent WHERE user_id = ? AND tag = ?",
            (user_id, tag),
        )
        await db.commit()


async def purge_old_reminder_sent(db_path: str, keep_days: int = 7) -> int:
    """Delete reminder_sent rows older than keep_days. Returns rows deleted."""
    async with get_db(db_path) as db:
        cur = await db.execute(
            "DELETE FROM reminder_sent WHERE sent_at < datetime('now', ?)",
            (f"-{keep_days} days",),
        )
        await db.commit()
        return cur.rowcount


# ── Email engagement ──────────────────────────────────────────


async def record_email_sent(db_path: str, user_id: int, email_key: str) -> None:
    """Record that an engagement email was sent. Idempotent (INSERT OR IGNORE)."""
    from datetime import datetime, timezone as tz

    now = datetime.now(tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO email_engagement (user_id, email_key, sent_at) VALUES (?, ?, ?)",
            (user_id, email_key, now),
        )
        await db.commit()


async def get_emails_sent(db_path: str, user_id: int) -> set[str]:
    """Return the set of email_keys already sent to this user."""
    async with get_db(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT email_key FROM email_engagement WHERE user_id = ?",
                (user_id,),
            )
        ).fetchall()
    return {r[0] for r in rows}


async def get_engagement_eligible_users(db_path: str) -> list[dict]:
    """Return users eligible for engagement emails (opted in, with email).

    Returns user_id, email, first_name, signed_up_at, timezone, newsletter_opt_in.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT
                    wa.user_id,
                    wa.email,
                    wa.created_at AS signed_up_at,
                    u.first_name,
                    COALESCE(p.timezone, 'America/Los_Angeles') AS timezone,
                    COALESCE(p.newsletter_opt_in, 1) AS newsletter_opt_in
                FROM web_auth wa
                JOIN users u ON u.user_id = wa.user_id
                LEFT JOIN user_prefs p ON p.user_id = wa.user_id
                WHERE COALESCE(p.newsletter_opt_in, 1) = 1
                """
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def count_user_meals(db_path: str, user_id: int) -> int:
    """Return total meal count for a user."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM meal_logs WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def count_user_meal_images_with_path(db_path: str, user_id: int) -> int:
    """Count this user's meal_logs rows that have a non-empty image_path.

    Used by routes/meals.py to enforce a per-user lifetime quota on stored
    meal images (B17).
    """
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                """SELECT COUNT(*) FROM meal_logs
                   WHERE user_id = ?
                     AND image_path IS NOT NULL
                     AND image_path <> ''""",
                (user_id,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def get_last_meal_date(db_path: str, user_id: int) -> str | None:
    """Return the date string (YYYY-MM-DD) of the user's last meal, or None."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT MAX(substr(logged_at, 1, 10)) FROM meal_logs WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    return row[0] if row and row[0] else None


# ── Feedback ───────────────────────────────────────────────────


async def insert_feedback(
    db_path: str,
    user_id: int,
    feedback_type: str,
    message: str,
    page: str = "",
) -> int:
    """Insert a user feedback record. Returns the new row id."""
    from datetime import datetime, timezone as tz

    now = datetime.now(tz.utc).isoformat()
    async with get_db(db_path) as db:
        cur = await db.execute(
            "INSERT INTO feedback (user_id, feedback_type, message, page, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, feedback_type, message, page, now),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def insert_meal_feedback(
    db_path: str,
    user_id: int,
    meal_id: int,
    rating: int,
    comment: str = "",
) -> int:
    """Insert or update an accuracy feedback record for a meal. Returns the row id."""
    from datetime import datetime, timezone as tz

    now = datetime.now(tz.utc).isoformat()
    async with get_db(db_path) as db:
        cur = await db.execute(
            """INSERT INTO meal_feedback (user_id, meal_id, rating, comment, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id, meal_id) DO UPDATE SET rating=excluded.rating, comment=excluded.comment, created_at=excluded.created_at""",
            (user_id, meal_id, rating, comment, now),
        )
        await db.commit()
        return cur.lastrowid  # type: ignore[return-value]


async def get_feedback_totals(db_path: str, since: str) -> dict:
    """Aggregate thumbs-up / thumbs-down counts since `since`.

    Compared against meal_accept events in the same window to produce a
    submission rate (what fraction of accepted meals received a rating).
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            """
            SELECT
                SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS thumbs_up,
                SUM(CASE WHEN rating = 0 THEN 1 ELSE 0 END) AS thumbs_down,
                COUNT(*) AS total
            FROM meal_feedback
            WHERE created_at >= ?
            """,
            (since,),
        )).fetchone()
        accepted_row = await (await db.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM user_events
            WHERE event_type = 'meal_accept' AND created_at >= ?
            """,
            (since,),
        )).fetchone()
    thumbs_up = int(row["thumbs_up"] or 0) if row else 0
    thumbs_down = int(row["thumbs_down"] or 0) if row else 0
    total = int(row["total"] or 0) if row else 0
    meals_logged = int(accepted_row["cnt"] or 0) if accepted_row else 0
    submission_rate = (total / meals_logged) if meals_logged > 0 else 0.0
    return {
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "total": total,
        "meals_logged": meals_logged,
        "submission_rate": submission_rate,
    }


async def get_feedback_thumbs_downs(db_path: str, since: str, limit: int = 50) -> list[dict]:
    """Return recent thumbs-down rows with the original analysis snapshot.

    Joins meal_feedback → meal_logs so each row carries the item_name and
    the analysis_snapshot_json needed to reproduce the Gemini analysis
    (conversation, original/final nutrition, image_path, barcode, etc.).
    Ordered newest-first so the admin page sees the latest complaints.
    """
    import json
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            """
            SELECT
                f.id AS feedback_id,
                f.user_id,
                f.meal_id,
                f.rating,
                f.comment,
                f.created_at,
                m.item_name,
                m.meal_description,
                m.source,
                m.image_path,
                m.analysis_snapshot_json
            FROM meal_feedback f
            LEFT JOIN meal_logs m ON m.id = f.meal_id
            WHERE f.rating = 0 AND f.created_at >= ?
            ORDER BY f.created_at DESC
            LIMIT ?
            """,
            (since, int(limit)),
        )).fetchall()
    out = []
    for r in rows:
        snapshot_raw = r["analysis_snapshot_json"] or ""
        try:
            snapshot = json.loads(snapshot_raw) if snapshot_raw else None
        except Exception:
            snapshot = None
        out.append({
            "feedback_id": int(r["feedback_id"]),
            "user_id": int(r["user_id"]),
            "meal_id": int(r["meal_id"]),
            "rating": int(r["rating"]),
            "comment": r["comment"] or "",
            "created_at": r["created_at"],
            "item_name": r["item_name"] or "",
            "meal_description": r["meal_description"] or "",
            "source": r["source"] or "",
            "image_path": r["image_path"] or "",
            "snapshot": snapshot,
        })
    return out


# ── Weight Logging ──────────────────────────────────────────────


async def log_weight(db_path: str, user_id: int, weight_kg: float, logged_at: str) -> int:
    """Insert a weight entry and update users.weight_kg if this is the newest entry."""
    async with get_db(db_path) as db:
        cur = await db.execute(
            "INSERT INTO weight_logs (user_id, logged_at, weight_kg) VALUES (?, ?, ?)",
            (user_id, logged_at, weight_kg),
        )
        row_id = cur.lastrowid
        # Only update profile weight if this is the most recent entry
        latest = await (await db.execute(
            "SELECT weight_kg FROM weight_logs WHERE user_id = ? ORDER BY logged_at DESC LIMIT 1",
            (user_id,),
        )).fetchone()
        if latest:
            await db.execute(
                "UPDATE users SET weight_kg = ? WHERE user_id = ?",
                (latest[0], user_id),
            )
        await db.commit()
    return row_id


async def get_weight_history(db_path: str, user_id: int, limit: int = 90) -> list[dict]:
    """Return recent weight entries ordered by logged_at DESC."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT id, logged_at, weight_kg FROM weight_logs WHERE user_id = ? ORDER BY logged_at DESC LIMIT ?",
                (user_id, limit),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def delete_weight_entry(db_path: str, weight_id: int, user_id: int) -> bool:
    """Delete a weight entry if owned by user. Updates users.weight_kg to latest remaining."""
    async with get_db(db_path) as db:
        cur = await db.execute(
            "DELETE FROM weight_logs WHERE id = ? AND user_id = ?",
            (weight_id, user_id),
        )
        if cur.rowcount > 0:
            # Update profile to the most recent remaining entry (or NULL if none)
            latest = await (await db.execute(
                "SELECT weight_kg FROM weight_logs WHERE user_id = ? ORDER BY logged_at DESC LIMIT 1",
                (user_id,),
            )).fetchone()
            await db.execute(
                "UPDATE users SET weight_kg = ? WHERE user_id = ?",
                (latest[0] if latest else None, user_id),
            )
        await db.commit()
    return cur.rowcount > 0



async def delete_all_user_data(db_path: str, user_id: int) -> dict:
    """Permanently delete all data for a user across all tables.

    GDPR Article 17 (right to erasure): every per-user table must be
    listed here.  When you add a new table that stores user data, add it
    to this list.  The "user_events" table is intentionally last so it's
    visually grouped with the telemetry tables.

    Returns a dict with a count-per-table summary so the caller can show
    the user what was erased (e.g. "Deleted 247 meals, 89 photos, ...").
    """
    counts: dict[str, int] = {}
    async with get_db(db_path) as db:
        # FK-dependent tables first
        for table in [
            "meal_logs", "weight_logs", "user_targets", "user_prefs",
            "meal_aliases", "meal_sessions", "chat_sessions", "user_memories",
            "push_subscriptions", "fatsecret_comparison", "gemini_calls",
            "subscriptions", "usage_tracking",
            "strava_tokens", "fitbit_tokens", "oura_tokens", "workout_logs",
            "fitbit_activity", "fitbit_oauth_state", "strava_oauth_state",
            "oura_activity", "oura_oauth_state",
            "tos_acceptances",
            "feedback", "meal_feedback",
            "badge_earned", "streak_shields",
            "barcode_corrections", "email_engagement",
            # Internal telemetry - also user-identifiable, must be wiped
            "user_events",
            # Idempotency markers for sent push reminders - small, but they
            # tie a (user_id, tag, sent_at_utc) tuple back to the user.
            "reminder_sent",
        ]:
            cursor = await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            counts[table] = cursor.rowcount or 0
        await db.execute("DELETE FROM email_pins WHERE email = (SELECT email FROM web_auth WHERE user_id = ?)", (user_id,))
        await db.execute("DELETE FROM web_auth WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        await db.commit()
    logger.info("Deleted all data for user_id=%s (rows=%s)", user_id, sum(counts.values()))
    return counts


async def export_user_meals(db_path: str, user_id: int) -> list[dict]:
    """Return all meals for CSV export."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT logged_at, item_name, meal_description, calories, protein, carbs, fat, meal_type, source FROM meal_logs WHERE user_id = ? ORDER BY logged_at DESC",
                (user_id,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def export_user_weights(db_path: str, user_id: int) -> list[dict]:
    """Return all weight entries for CSV export."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT logged_at, weight_kg FROM weight_logs WHERE user_id = ? ORDER BY logged_at DESC",
                (user_id,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


# ── Chat sessions (AI coaching) ──────────────────────────────────────

async def create_chat_session(
    db_path: str, session_id: str, user_id: int,
    title: str = "", conversation: str = "[]",
) -> None:
    """Create a new chat session."""
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title, conversation, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, user_id, title, conversation, now, now),
        )
        await db.commit()


async def get_chat_session(db_path: str, session_id: str, user_id: int) -> dict | None:
    """Return a chat session dict or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, user_id, title, conversation, created_at, updated_at "
                "FROM chat_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        ).fetchone()
    if row is None:
        return None
    return dict(row)


async def update_chat_session(
    db_path: str, session_id: str, user_id: int, **kwargs,
) -> bool:
    """Update chat session fields (title, conversation). Returns True if updated.

    Uses optimistic locking via expected_updated_at to prevent concurrent overwrites.
    """
    from datetime import datetime, timezone as _tz
    allowed = {"title", "conversation"}
    safe = {k: v for k, v in kwargs.items() if k in allowed}
    if not safe:
        return True
    expected_updated_at = kwargs.get("expected_updated_at")
    safe["updated_at"] = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    set_clause = ", ".join(f"{k} = ?" for k in safe)
    where = "id = ? AND user_id = ?"
    params = [*safe.values(), session_id, user_id]
    if expected_updated_at:
        where += " AND updated_at = ?"
        params.append(expected_updated_at)
    async with get_db(db_path) as db:
        cur = await db.execute(
            f"UPDATE chat_sessions SET {set_clause} WHERE {where}",
            params,
        )
        await db.commit()
    return cur.rowcount > 0


async def list_chat_sessions(db_path: str, user_id: int, limit: int = 20) -> list[dict]:
    """Return recent chat sessions (id, title, updated_at) for a user."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT id, title, created_at, updated_at FROM chat_sessions "
                "WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def delete_chat_session(db_path: str, session_id: str, user_id: int) -> bool:
    """Delete a chat session. Returns True if deleted."""
    async with get_db(db_path) as db:
        cur = await db.execute(
            "DELETE FROM chat_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        await db.commit()
    return cur.rowcount > 0


# ── Subscriptions ────────────────────────────────────────────────────


async def get_subscription(db_path: str, user_id: int) -> dict | None:
    """Return the subscription row for a user, or None if no record exists."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, user_id, plan, status, stripe_customer_id, "
                "stripe_subscription_id, trial_ends_at, trial_used, is_og, "
                "current_period_start, current_period_end, cancelled_at, "
                "created_at, updated_at "
                "FROM subscriptions WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return dict(row)


async def create_or_update_subscription(db_path: str, user_id: int, **kwargs) -> None:
    """Upsert a subscription row for the user.

    On conflict (user already has a subscription), updates only the provided fields.
    Always updates `updated_at`.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")

    allowed = {
        "plan", "status", "stripe_customer_id", "stripe_subscription_id",
        "trial_ends_at", "trial_used", "is_og", "current_period_start",
        "current_period_end", "cancelled_at",
    }
    safe = {k: v for k, v in kwargs.items() if k in allowed}

    async with get_db(db_path) as db:
        # Check if row exists
        existing = await (
            await db.execute(
                "SELECT id FROM subscriptions WHERE user_id = ?", (user_id,),
            )
        ).fetchone()

        if existing is None:
            # INSERT with defaults + any provided overrides
            cols = {
                "user_id": user_id,
                "plan": safe.get("plan", "free"),
                "status": safe.get("status", "active"),
                "stripe_customer_id": safe.get("stripe_customer_id", ""),
                "stripe_subscription_id": safe.get("stripe_subscription_id", ""),
                "trial_ends_at": safe.get("trial_ends_at"),
                "trial_used": safe.get("trial_used", 0),
                "is_og": safe.get("is_og", 0),
                "current_period_start": safe.get("current_period_start"),
                "current_period_end": safe.get("current_period_end"),
                "cancelled_at": safe.get("cancelled_at"),
                "created_at": now,
                "updated_at": now,
            }
            col_names = ", ".join(cols.keys())
            placeholders = ", ".join("?" for _ in cols)
            await db.execute(
                f"INSERT INTO subscriptions ({col_names}) VALUES ({placeholders})",
                list(cols.values()),
            )
        else:
            # UPDATE only provided fields
            safe["updated_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in safe)
            values = list(safe.values()) + [user_id]
            await db.execute(
                f"UPDATE subscriptions SET {set_clause} WHERE user_id = ?",
                values,
            )
        await db.commit()


async def get_usage(
    db_path: str, user_id: int, usage_type: str, date_str: str,
) -> dict:
    """Return usage for a user/type/day. Returns {used_count, limit_count}."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT used_count, limit_count FROM usage_tracking "
                "WHERE user_id = ? AND usage_type = ? AND period = ?",
                (user_id, usage_type, date_str),
            )
        ).fetchone()
    if row is None:
        return {"used_count": 0, "limit_count": 5}
    return dict(row)


async def increment_usage(
    db_path: str,
    user_id: int,
    usage_type: str,
    date_str: str,
    limit: int = 5,
) -> dict:
    """Increment usage count and return {used_count, limit_count, allowed}.

    `allowed` is True if the usage was under the limit *before* incrementing
    (i.e., this request is permitted). If already at or over the limit,
    the count is NOT incremented and `allowed` is False.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Atomic upsert: INSERT OR IGNORE to ensure the row exists, then
        # atomically UPDATE only if under the limit (prevents TOCTOU race).
        await db.execute(
            "INSERT OR IGNORE INTO usage_tracking (user_id, usage_type, period, used_count, limit_count, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (user_id, usage_type, date_str, limit, now),
        )
        cursor = await db.execute(
            "UPDATE usage_tracking SET used_count = used_count + 1, updated_at = ? "
            "WHERE user_id = ? AND usage_type = ? AND period = ? AND used_count < ?",
            (now, user_id, usage_type, date_str, limit),
        )
        await db.commit()

        row = await (
            await db.execute(
                "SELECT used_count, limit_count FROM usage_tracking "
                "WHERE user_id = ? AND usage_type = ? AND period = ?",
                (user_id, usage_type, date_str),
            )
        ).fetchone()

        current = row["used_count"] if row else 0
        return {"used_count": current, "limit_count": limit, "allowed": cursor.rowcount > 0}


# ── user_events: private telemetry for admin insights ────────────────
#
# Every meaningful per-user action writes a row here. This is a separate
# channel from Umami (which is aggregate, cookieless, front-end only) and
# from usage_tracking (which is a per-day counter for quota enforcement).
# Admin-only endpoints query this table for per-user rollups.
#
# Errors are always swallowed: telemetry must never break the main flow.

async def log_event(
    db_path: str,
    user_id: int,
    event_type: str,
    metadata: dict | None = None,
) -> None:
    """Append a user event. Never raises - logging failures are silent."""
    import json as _json
    from datetime import datetime, timezone as _tz

    try:
        now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
        meta_str = _json.dumps(metadata, separators=(",", ":")) if metadata else ""
        async with get_db(db_path) as db:
            await db.execute(
                "INSERT INTO user_events (user_id, event_type, metadata_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, event_type, meta_str, now),
            )
            await db.commit()
    except Exception:
        logger.debug("log_event failed (non-fatal)", exc_info=True)


async def get_event_overview(
    db_path: str,
    since: str,
) -> list[dict]:
    """Return per-user event counts since `since` (ISO datetime).

    Joins user_events with users (for first_name) and web_auth (for email),
    both via LEFT JOIN so legacy users (no web_auth row) still appear.
    Returns one row per active user with a field per event type.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT
                    e.user_id        AS user_id,
                    wa.email         AS email,
                    u.first_name     AS first_name,
                    e.event_type     AS event_type,
                    COUNT(*)         AS cnt
                FROM user_events e
                LEFT JOIN users u     ON u.user_id = e.user_id
                LEFT JOIN web_auth wa ON wa.user_id = e.user_id
                WHERE e.created_at >= ?
                GROUP BY e.user_id, e.event_type
                """,
                (since,),
            )
        ).fetchall()

    # Pivot rows → per-user dict
    per_user: dict[int, dict] = {}
    for r in rows:
        uid = int(r["user_id"])
        bucket = per_user.setdefault(uid, {
            "user_id": uid,
            "email": r["email"] or "",
            "first_name": r["first_name"] or "",
            "events": {},
            "total": 0,
        })
        bucket["events"][r["event_type"]] = int(r["cnt"])
        bucket["total"] += int(r["cnt"])
    return sorted(per_user.values(), key=lambda u: u["total"], reverse=True)


async def get_user_event_timeseries(
    db_path: str,
    user_id: int,
    since: str,
) -> list[dict]:
    """Return per-day per-event-type counts for one user since `since`.

    Used for the per-user drilldown view.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT substr(created_at, 1, 10) AS day, event_type, COUNT(*) AS cnt
                FROM user_events
                WHERE user_id = ? AND created_at >= ?
                GROUP BY day, event_type
                ORDER BY day ASC
                """,
                (user_id, since),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def get_total_event_counts(
    db_path: str,
    since: str,
) -> dict:
    """Return global aggregate: {event_type: count, ...} since `since`."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT event_type, COUNT(*) AS cnt FROM user_events "
                "WHERE created_at >= ? GROUP BY event_type",
                (since,),
            )
        ).fetchall()
    return {r["event_type"]: int(r["cnt"]) for r in rows}


async def count_user_meals(db_path: str, user_id: int) -> int:
    """Return total meal_logs count for a user.

    Used to detect the "first meal" milestone on meal_accept - the caller
    reads this BEFORE inserting the new row, so a return of 0 means this
    is the user's very first meal.
    """
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM meal_logs WHERE user_id = ?", (user_id,),
            )
        ).fetchone()
    return row[0] if row else 0


async def cleanup_old_events(db_path: str, max_age_days: int = 365) -> int:
    """Delete user_events rows older than max_age_days. Returns rows deleted.

    Called periodically from the lifespan background task so user_events
    can't grow unbounded. 365 days matches the admin dashboard's max window.
    """
    from datetime import datetime, timezone as _tz, timedelta

    cutoff = (datetime.now(_tz.utc) - timedelta(days=max_age_days)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM user_events WHERE created_at < ?", (cutoff,),
        )
        await db.commit()
        return cursor.rowcount or 0


# ── Admin dashboard aggregations (Phase 3/4) ─────────────────────────


async def get_funnel_counts(db_path: str, since: str) -> dict:
    """Return meal funnel counts since `since`.

    Returns per-stage totals for the analyze → correct → accept → delete
    pipeline, plus quick/relog/copy_day side channels. The frontend
    computes drop-off rates from these raw counts.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT event_type, COUNT(*) AS cnt
                FROM user_events
                WHERE created_at >= ?
                  AND event_type IN (
                    'meal_analyze_image', 'meal_analyze_text', 'meal_analyze_combined',
                    'meal_correct', 'meal_accept', 'meal_cancel', 'meal_delete',
                    'meal_quick_log', 'meal_relog', 'meal_copy_day', 'meal_barcode_scan'
                  )
                GROUP BY event_type
                """,
                (since,),
            )
        ).fetchall()
    counts = {r["event_type"]: int(r["cnt"]) for r in rows}
    analyze_total = (
        counts.get("meal_analyze_image", 0)
        + counts.get("meal_analyze_text", 0)
        + counts.get("meal_analyze_combined", 0)
    )
    return {
        "analyze_total": analyze_total,
        "analyze_image": counts.get("meal_analyze_image", 0),
        "analyze_text": counts.get("meal_analyze_text", 0),
        "analyze_combined": counts.get("meal_analyze_combined", 0),
        "correct": counts.get("meal_correct", 0),
        "accept": counts.get("meal_accept", 0),
        "cancel": counts.get("meal_cancel", 0),
        "delete": counts.get("meal_delete", 0),
        "quick_log": counts.get("meal_quick_log", 0),
        "relog": counts.get("meal_relog", 0),
        "copy_day": counts.get("meal_copy_day", 0),
        "barcode_scan": counts.get("meal_barcode_scan", 0),
    }


async def get_acquisition_mix(db_path: str, since: str) -> dict:
    """Return signup breakdown since `since`.

    Counts auth_signup events by metadata.method (google vs email_pin),
    plus new user_events first-seen counts, plus waitlist size.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        # Count auth_signup events grouped by method (from metadata_json)
        signup_rows = await (
            await db.execute(
                """
                SELECT metadata_json, COUNT(*) AS cnt
                FROM user_events
                WHERE event_type = 'auth_signup' AND created_at >= ?
                GROUP BY metadata_json
                """,
                (since,),
            )
        ).fetchall()

        google = 0
        email_pin = 0
        for r in signup_rows:
            meta = r["metadata_json"] or ""
            if '"method":"google"' in meta:
                google += int(r["cnt"])
            elif '"method":"email_pin"' in meta:
                email_pin += int(r["cnt"])

        waitlist_row = await (
            await db.execute(
                "SELECT COUNT(*) FROM waitlist WHERE invited_at IS NULL",
            )
        ).fetchone()
        waitlist_pending = int(waitlist_row[0]) if waitlist_row else 0

        waitlist_total_row = await (
            await db.execute("SELECT COUNT(*) FROM waitlist")
        ).fetchone()
        waitlist_total = int(waitlist_total_row[0]) if waitlist_total_row else 0

        # Total users in the system (reference number for denominators)
        users_row = await (
            await db.execute("SELECT COUNT(*) FROM users")
        ).fetchone()
        total_users = int(users_row[0]) if users_row else 0

    return {
        "signup_google": google,
        "signup_email": email_pin,
        "signup_total": google + email_pin,
        "waitlist_pending": waitlist_pending,
        "waitlist_total": waitlist_total,
        "total_users": total_users,
    }


async def get_feature_adoption(db_path: str, since: str) -> dict:
    """Return feature adoption counts (unique users).

    - gamification_on: users with prefs.gamification != 'off'
    - chat_users: users who have created at least one chat session
    - strava_connected / fitbit_connected: token rows present
    - push_subscribed: users with any push subscription
    - has_targets: users with a user_targets row
    - logged_weight_30d: unique users with weight_log events in window
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row

        async def _scalar(sql: str, params: tuple = ()) -> int:
            row = await (await db.execute(sql, params)).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        gamification_on = await _scalar(
            "SELECT COUNT(*) FROM user_prefs WHERE COALESCE(gamification,'full') != 'off'"
        )
        gamification_off = await _scalar(
            "SELECT COUNT(*) FROM user_prefs WHERE gamification = 'off'"
        )
        chat_users = await _scalar(
            "SELECT COUNT(DISTINCT user_id) FROM chat_sessions"
        )
        strava_connected = await _scalar(
            "SELECT COUNT(DISTINCT user_id) FROM strava_tokens"
        )
        fitbit_connected = await _scalar(
            "SELECT COUNT(DISTINCT user_id) FROM fitbit_tokens"
        )
        push_subscribed = await _scalar(
            "SELECT COUNT(DISTINCT user_id) FROM push_subscriptions"
        )
        has_targets = await _scalar(
            "SELECT COUNT(*) FROM user_targets"
        )
        reminders_on = await _scalar(
            "SELECT COUNT(*) FROM user_prefs WHERE reminders_on = 1"
        )
        logged_weight_recent = await _scalar(
            "SELECT COUNT(DISTINCT user_id) FROM user_events "
            "WHERE event_type = 'weight_log' AND created_at >= ?",
            (since,),
        )
        barcode_users = await _scalar(
            "SELECT COUNT(DISTINCT user_id) FROM user_events "
            "WHERE event_type = 'meal_barcode_scan' AND created_at >= ?",
            (since,),
        )
        quick_log_users = await _scalar(
            "SELECT COUNT(DISTINCT user_id) FROM user_events "
            "WHERE event_type = 'meal_quick_log' AND created_at >= ?",
            (since,),
        )
        premium_users = await _scalar(
            "SELECT COUNT(*) FROM subscriptions WHERE plan LIKE 'pro_%' OR is_og = 1"
        )
        trial_users = await _scalar(
            "SELECT COUNT(*) FROM subscriptions WHERE plan = 'trial' AND status = 'trialing'"
        )

    return {
        "gamification_on": gamification_on,
        "gamification_off": gamification_off,
        "chat_users": chat_users,
        "strava_connected": strava_connected,
        "fitbit_connected": fitbit_connected,
        "push_subscribed": push_subscribed,
        "has_targets": has_targets,
        "reminders_on": reminders_on,
        "logged_weight_recent": logged_weight_recent,
        "barcode_users_recent": barcode_users,
        "quick_log_users_recent": quick_log_users,
        "premium_users": premium_users,
        "trial_users": trial_users,
    }


async def get_limit_hit_counts(db_path: str, since: str) -> dict:
    """Return per-feature limit_hit counts and unique users hit since `since`.

    Parses the `feature` field from limit_hit metadata to group by feature
    (image_analysis, text_meal, meal_edit, ai_chat, saved_meals, etc.).
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT metadata_json, user_id
                FROM user_events
                WHERE event_type = 'limit_hit' AND created_at >= ?
                """,
                (since,),
            )
        ).fetchall()

    import json as _json
    by_feature: dict[str, dict] = {}
    for r in rows:
        try:
            meta = _json.loads(r["metadata_json"] or "{}")
        except Exception:
            meta = {}
        feature = meta.get("feature", "unknown")
        bucket = by_feature.setdefault(feature, {"count": 0, "users": set()})
        bucket["count"] += 1
        bucket["users"].add(int(r["user_id"]))

    return {
        f: {"count": v["count"], "unique_users": len(v["users"])}
        for f, v in by_feature.items()
    }


# Gemini 2.5 Flash Lite rates in USD per million tokens. Used by
# get_cost_metrics (operator dashboards) and get_monthly_gemini_cost_usd
# (the app-level spend gate in src/web/budget_gate.py). If Google
# changes prices or we switch primary model, update here in one place.
_GEMINI_INPUT_USD_PER_MTOK = 0.10
_GEMINI_OUTPUT_USD_PER_MTOK = 0.40


def _tokens_to_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        (input_tokens / 1_000_000) * _GEMINI_INPUT_USD_PER_MTOK
        + (output_tokens / 1_000_000) * _GEMINI_OUTPUT_USD_PER_MTOK
    )


async def get_cost_metrics(db_path: str, since: str) -> dict:
    """Return Gemini API usage + estimated cost since `since`.

    Reads from `gemini_calls` (which already tracks tokens per call).
    Cost estimate uses Gemini 2.5 Flash Lite pricing - see
    _GEMINI_INPUT_USD_PER_MTOK / _GEMINI_OUTPUT_USD_PER_MTOK.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT COUNT(*)                          AS calls,
                       COALESCE(SUM(input_tokens), 0)    AS input_tokens,
                       COALESCE(SUM(output_tokens), 0)   AS output_tokens,
                       COALESCE(SUM(has_image), 0)       AS image_calls,
                       COALESCE(SUM(web_searches), 0)    AS web_searches,
                       COUNT(DISTINCT user_id)           AS unique_users
                FROM gemini_calls
                WHERE called_at >= ?
                """,
                (since,),
            )
        ).fetchone()

    if not row:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                "image_calls": 0, "web_searches": 0, "unique_users": 0,
                "estimated_cost_usd": 0.0}

    in_tokens = int(row["input_tokens"])
    out_tokens = int(row["output_tokens"])
    cost = _tokens_to_usd(in_tokens, out_tokens)

    return {
        "calls": int(row["calls"]),
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "image_calls": int(row["image_calls"]),
        "web_searches": int(row["web_searches"]),
        "unique_users": int(row["unique_users"]),
        "estimated_cost_usd": round(cost, 4),
    }


async def get_monthly_gemini_cost_usd(db_path: str) -> float:
    """Return month-to-date Gemini spend in USD from `gemini_calls`.

    Window is [first-of-current-month 00:00 UTC, now). Used by the
    budget gate to decide whether to refuse new Gemini calls.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT COALESCE(SUM(input_tokens), 0)  AS in_tok,
                       COALESCE(SUM(output_tokens), 0) AS out_tok
                FROM gemini_calls
                WHERE called_at >= strftime('%Y-%m-01 00:00:00', 'now')
                """,
            )
        ).fetchone()

    if not row:
        return 0.0
    return _tokens_to_usd(int(row["in_tok"]), int(row["out_tok"]))


async def get_daily_active_users(db_path: str, since: str) -> list[dict]:
    """Return per-day unique-user activity counts since `since`.

    A user is "active" on day D if they emitted any user_events row on D.
    Used by the retention panel to show DAU/MAU trends.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(DISTINCT user_id)   AS users,
                       COUNT(*)                  AS events
                FROM user_events
                WHERE created_at >= ?
                GROUP BY day
                ORDER BY day ASC
                """,
                (since,),
            )
        ).fetchall()
    return [
        {"day": r["day"], "active_users": int(r["users"]), "events": int(r["events"])}
        for r in rows
    ]


async def get_today_activity(db_path: str, hours: int = 24) -> dict:
    """Return individual meal and weight entries from the last *hours* hours.

    Used by the admin daily activity feed to show "who logged what and when"
    with per-meal granularity.  logged_at is stored in each user's local
    timezone so the cutoff is approximate - close enough for admin purposes.
    """
    from datetime import datetime, timedelta

    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        meals = await (
            await db.execute(
                """
                SELECT m.id        AS meal_id,
                       m.user_id,
                       u.first_name,
                       COALESCE(w.email, '') AS email,
                       m.logged_at,
                       m.meal_description,
                       m.calories,
                       m.protein,
                       m.carbs,
                       m.fat,
                       m.source,
                       m.meal_type
                FROM meal_logs m
                JOIN users u     ON m.user_id = u.user_id
                LEFT JOIN web_auth w ON m.user_id = w.user_id
                WHERE m.logged_at >= ?
                ORDER BY m.logged_at DESC
                """,
                (cutoff,),
            )
        ).fetchall()
        weights = await (
            await db.execute(
                """
                SELECT wl.id AS weight_id,
                       wl.user_id,
                       u.first_name,
                       COALESCE(w.email, '') AS email,
                       wl.logged_at,
                       wl.weight_kg
                FROM weight_logs wl
                JOIN users u     ON wl.user_id = u.user_id
                LEFT JOIN web_auth w ON wl.user_id = w.user_id
                WHERE wl.logged_at >= ?
                ORDER BY wl.logged_at DESC
                """,
                (cutoff,),
            )
        ).fetchall()
    return {
        "meals": [dict(r) for r in meals],
        "weights": [dict(r) for r in weights],
    }


async def start_trial(db_path: str, user_id: int) -> bool:
    """Start a 7-day free trial. Returns False if the user already used their trial.

    Uses BEGIN IMMEDIATE + `WHERE trial_used = 0` so two concurrent calls
    can't both set the trial flag - the second caller either blocks on the
    write lock or sees `rowcount = 0` and returns False.
    """
    from datetime import datetime, timezone as _tz, timedelta

    now = datetime.now(_tz.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    trial_end = (now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("BEGIN IMMEDIATE")
        try:
            row = await (
                await db.execute(
                    "SELECT trial_used FROM subscriptions WHERE user_id = ?",
                    (user_id,),
                )
            ).fetchone()

            if row is not None and row["trial_used"]:
                await db.execute("ROLLBACK")
                logger.info("start_trial rejected for user %d: trial already used", user_id)
                return False

            if row is None:
                await db.execute(
                    "INSERT INTO subscriptions "
                    "(user_id, plan, status, trial_ends_at, trial_used, created_at, updated_at) "
                    "VALUES (?, 'trial', 'trialing', ?, 1, ?, ?)",
                    (user_id, trial_end, now_str, now_str),
                )
            else:
                cursor = await db.execute(
                    "UPDATE subscriptions SET plan = 'trial', status = 'trialing', "
                    "trial_ends_at = ?, trial_used = 1, updated_at = ? "
                    "WHERE user_id = ? AND trial_used = 0",
                    (trial_end, now_str, user_id),
                )
                if cursor.rowcount == 0:
                    await db.execute("ROLLBACK")
                    logger.info(
                        "start_trial race: user %d lost to a concurrent activation", user_id
                    )
                    return False
            await db.commit()
        except Exception:
            await db.execute("ROLLBACK")
            raise
    return True


async def cancel_subscription(db_path: str, user_id: int) -> None:
    """Cancel a subscription - sets status to 'cancelled' and plan to 'free'."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE subscriptions SET plan = 'free', status = 'cancelled', "
            "cancelled_at = ?, updated_at = ? WHERE user_id = ?",
            (now, now, user_id),
        )
        await db.commit()


async def create_pro_subscription(
    db_path: str, user_id: int, is_og: bool = False,
) -> None:
    """Create (or upgrade) a Pro subscription row with no expiry.

    Used by the beta signup flow: everyone who signs up during BETA_MODE=on
    gets free Pro access - same caps as paying Pro, no Stripe checkout, no
    renewal enforcement. Non-OG rows keep `stripe_customer_id` empty so a
    future migration (when BETA_MODE flips off) can find beta grandfathers
    to convert into paying customers or downgrade.

    OG rows set `is_og=1` so renewal logic in get_subscription_info skips
    them permanently, even after beta ends.

    Post-beta safety:
      • `trial_used=1` is set on INSERT so beta grandfathers cannot be
        offered the legacy 7-day trial after BETA_MODE flips off - which
        would UPDATE plan→'trial' and silently downgrade them to 'free'
        after 7 days.
      • UPDATE branch never demotes an existing `pro_yearly` row to
        `pro_monthly`. It only upgrades non-pro rows and preserves `is_og`
        once set.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    og_int = 1 if is_og else 0

    async with get_db(db_path) as db:
        existing_row = await (
            await db.execute(
                "SELECT id, plan, status, is_og FROM subscriptions WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()

        if existing_row is None:
            # Fresh row: trial_used=1 so a post-beta /subscription/trial
            # call cannot regress the user back into a 7-day trial.
            await db.execute(
                "INSERT INTO subscriptions "
                "(user_id, plan, status, is_og, trial_used, created_at, updated_at) "
                "VALUES (?, 'pro_monthly', 'active', ?, 1, ?, ?)",
                (user_id, og_int, now, now),
            )
        else:
            existing_plan = existing_row[1] or ""
            existing_is_og = int(existing_row[3] or 0)
            # Preserve any existing pro_* plan (don't demote pro_yearly →
            # pro_monthly). Only upgrade free / trial / empty rows.
            new_plan = existing_plan if existing_plan.startswith("pro_") else "pro_monthly"
            # Preserve is_og once set; allow an un-flagged row to be
            # upgraded to OG via this helper.
            new_is_og = 1 if (existing_is_og or is_og) else 0
            await db.execute(
                "UPDATE subscriptions "
                "SET plan = ?, status = 'active', is_og = ?, "
                "trial_used = 1, updated_at = ? "
                "WHERE user_id = ?",
                (new_plan, new_is_og, now, user_id),
            )
        await db.commit()


async def set_og_status(db_path: str, user_id: int, is_og: bool) -> bool:
    """Toggle the OG flag on a user's subscription row.

    When flagging OG (is_og=True), creates a Pro row if one doesn't exist
    - so admin can mark someone OG even if they signed up before
    subscriptions were auto-provisioned. `trial_used=1` is set to prevent
    post-beta trial regression, matching `create_pro_subscription`.

    When un-flagging (is_og=False), does NOT create a new row - silently
    no-ops if no subscription exists. Creating a Pro row just to un-flag
    a non-OG user would be a gratuitous upgrade to free tier, a footgun
    post-beta.

    Returns True if a row exists (or was created) at the end of the call.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    og_int = 1 if is_og else 0

    async with get_db(db_path) as db:
        existing = await (
            await db.execute(
                "SELECT id FROM subscriptions WHERE user_id = ?", (user_id,),
            )
        ).fetchone()
        if existing is None:
            if not is_og:
                # Un-flagging a user who has no subscription row - no-op.
                return False
            await db.execute(
                "INSERT INTO subscriptions "
                "(user_id, plan, status, is_og, trial_used, created_at, updated_at) "
                "VALUES (?, 'pro_monthly', 'active', 1, 1, ?, ?)",
                (user_id, now, now),
            )
        else:
            await db.execute(
                "UPDATE subscriptions SET is_og = ?, updated_at = ? WHERE user_id = ?",
                (og_int, now, user_id),
            )
        await db.commit()
    return True


async def count_web_users(db_path: str) -> int:
    """Return the total number of rows in web_auth.

    This is a diagnostic helper used by tests and admin dashboards. It
    is NOT safe to use for the signup cap enforcement - that runs
    inline inside `create_web_user` under a `BEGIN IMMEDIATE` write
    lock so two concurrent signups can't both pass the cap check.
    Calling `count_web_users` outside that lock would re-introduce the
    TOCTOU race.
    """
    async with get_db(db_path) as db:
        row = await (
            await db.execute("SELECT COUNT(*) FROM web_auth")
        ).fetchone()
    return int(row[0]) if row else 0


async def add_to_waitlist(
    db_path: str,
    email: str,
    source: str = "",
    first_name: str | None = None,
    referrer: str = "",
) -> bool:
    """Insert an email into the waitlist. Idempotent via UNIQUE(email).

    Returns True if the row was newly inserted, False if the email was
    already present.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "INSERT OR IGNORE INTO waitlist "
            "(email, source, first_name, referrer, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (email, source, first_name, referrer, now),
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_waitlist(db_path: str) -> list[dict]:
    """Return all waitlist rows, oldest first. Used by admin CSV export."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT id, email, source, first_name, referrer, "
                "created_at, invited_at, notes "
                "FROM waitlist ORDER BY created_at ASC"
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def list_all_users(db_path: str) -> list[dict]:
    """Return all web users with their subscription state. Admin CSV export.

    Joins web_auth + users + subscriptions + user_prefs so one row has
    everything the operator needs for beta-period cohort management and
    newsletter sends.
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                """
                SELECT
                    wa.user_id,
                    wa.email,
                    wa.created_at AS signed_up_at,
                    u.first_name,
                    u.last_name,
                    s.plan,
                    s.status,
                    s.is_og,
                    s.stripe_customer_id,
                    s.current_period_end,
                    COALESCE(p.newsletter_opt_in, 1) AS newsletter_opt_in
                FROM web_auth wa
                LEFT JOIN users u       ON u.user_id = wa.user_id
                LEFT JOIN subscriptions s ON s.user_id = wa.user_id
                LEFT JOIN user_prefs p  ON p.user_id = wa.user_id
                ORDER BY wa.created_at ASC
                """
            )
        ).fetchall()
    return [dict(r) for r in rows]


class SignupCapReached(Exception):
    """Raised by create_web_user when the beta signup cap is full."""

    def __init__(self, email: str, cap: int):
        self.email = email
        self.cap = cap
        super().__init__(f"Beta signup cap reached ({cap}) - {email} rejected")


# ── TOS Acceptance ──────────────────────────────────────────────────

CURRENT_TOS_VERSION = "2026-04-11-v1"


async def get_tos_acceptance(db_path: str, user_id: int) -> str | None:
    """Return the latest TOS version the user accepted, or None."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT tos_version FROM tos_acceptances "
                "WHERE user_id = ? ORDER BY accepted_at DESC LIMIT 1",
                (user_id,),
            )
        ).fetchone()
    return row[0] if row else None


async def accept_tos(db_path: str, user_id: int) -> None:
    """Record that the user accepted the current TOS version."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO tos_acceptances (user_id, tos_version, accepted_at) VALUES (?, ?, ?)",
            (user_id, CURRENT_TOS_VERSION, now),
        )
        await db.commit()


# ── Strava token CRUD ─────────────────────────────────────────────


class ExternalAccountAlreadyLinked(Exception):
    """Raised when an external OAuth account ID is already linked to another user.

    A7: prevents account hijack by linking the same Fitbit/Strava/Oura
    user ID to two MacroShot accounts and then forging webhooks.
    """

    def __init__(self, source: str):
        super().__init__(
            f"This {source} account is already linked to another MacroShot user; "
            "disconnect there first."
        )
        self.source = source


async def save_strava_tokens(
    db_path: str,
    user_id: int,
    strava_athlete_id: int,
    access_token: str,
    refresh_token: str,
    expires_at: int,
    scope: str = "",
) -> None:
    """Upsert Strava OAuth tokens for a user.

    A7: refuses if `strava_athlete_id` is already attached to a different
    user_id (the UNIQUE index added in v4 enforces this at the SQL layer;
    we surface a domain-specific exception so the route can return a
    clean error).
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    enc_access = _encrypt_token(access_token)
    enc_refresh = _encrypt_token(refresh_token)
    async with get_db(db_path) as db:
        # Pre-flight: another user already linked to this athlete_id?
        row = await (await db.execute(
            "SELECT user_id FROM strava_tokens WHERE strava_athlete_id = ? AND user_id != ?",
            (strava_athlete_id, user_id),
        )).fetchone()
        if row is not None:
            raise ExternalAccountAlreadyLinked("Strava")
        try:
            await db.execute(
                """
                INSERT INTO strava_tokens
                    (user_id, strava_athlete_id, access_token, refresh_token,
                     expires_at, scope, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    strava_athlete_id = excluded.strava_athlete_id,
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
                """,
                (user_id, strava_athlete_id, enc_access, enc_refresh,
                 expires_at, scope, now, now),
            )
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            if "strava_athlete_id" in str(exc).lower():
                raise ExternalAccountAlreadyLinked("Strava") from exc
            raise


def _decrypt_token_row(row_dict: dict) -> dict | None:
    """Decrypt access_token and refresh_token in a token row dict.

    Returns the row with decrypted tokens, or None if either token cannot
    be decrypted (e.g. JWT_SECRET rotation, corrupted row).  Callers
    should treat None the same as "no tokens stored" - the user will
    need to re-authenticate the integration.
    """
    try:
        row_dict["access_token"] = _decrypt_token(row_dict["access_token"])
        row_dict["refresh_token"] = _decrypt_token(row_dict["refresh_token"])
    except RuntimeError:
        return None
    return row_dict


async def get_strava_tokens(db_path: str, user_id: int) -> dict | None:
    """Return the Strava token row for a user, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT user_id, strava_athlete_id, access_token, refresh_token, "
                "expires_at, scope, created_at, updated_at "
                "FROM strava_tokens WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return _decrypt_token_row(dict(row))


async def delete_strava_tokens(db_path: str, user_id: int) -> None:
    """Remove Strava tokens for a user (disconnect)."""
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM strava_tokens WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_strava_tokens_by_athlete_id(db_path: str, athlete_id: int) -> dict | None:
    """Look up Strava tokens by strava_athlete_id (used in webhook handler)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT user_id, strava_athlete_id, access_token, refresh_token, "
                "expires_at, scope, created_at, updated_at "
                "FROM strava_tokens WHERE strava_athlete_id = ?",
                (athlete_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return _decrypt_token_row(dict(row))


# ── Fitbit token CRUD ──────────────────────────────────────────────


async def save_fitbit_tokens(
    db_path: str,
    user_id: int,
    fitbit_user_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: str,
    scope: str = "",
) -> None:
    """Upsert Fitbit OAuth tokens for a user.

    A7: refuses if `fitbit_user_id` is already linked to another user.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    enc_access = _encrypt_token(access_token)
    enc_refresh = _encrypt_token(refresh_token)
    async with get_db(db_path) as db:
        if fitbit_user_id:
            row = await (await db.execute(
                "SELECT user_id FROM fitbit_tokens WHERE fitbit_user_id = ? AND user_id != ?",
                (fitbit_user_id, user_id),
            )).fetchone()
            if row is not None:
                raise ExternalAccountAlreadyLinked("Fitbit")
        try:
            await db.execute(
                """
                INSERT INTO fitbit_tokens
                    (user_id, fitbit_user_id, access_token, refresh_token,
                     expires_at, scope, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    fitbit_user_id = excluded.fitbit_user_id,
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
                """,
                (user_id, fitbit_user_id, enc_access, enc_refresh,
                 expires_at, scope, now, now),
            )
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            if "fitbit_user_id" in str(exc).lower():
                raise ExternalAccountAlreadyLinked("Fitbit") from exc
            raise


async def get_fitbit_tokens(db_path: str, user_id: int) -> dict | None:
    """Return the Fitbit token row for a user, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT user_id, fitbit_user_id, access_token, refresh_token, "
                "expires_at, scope, created_at, updated_at "
                "FROM fitbit_tokens WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return _decrypt_token_row(dict(row))


async def get_all_fitbit_user_ids(db_path: str) -> list[int]:
    """Return all user_ids that have Fitbit tokens."""
    async with get_db(db_path) as db:
        rows = await (await db.execute("SELECT user_id FROM fitbit_tokens")).fetchall()
    return [r[0] for r in rows]


async def get_all_strava_user_ids(db_path: str) -> list[int]:
    """Return all user_ids that have Strava tokens."""
    async with get_db(db_path) as db:
        rows = await (await db.execute("SELECT user_id FROM strava_tokens")).fetchall()
    return [r[0] for r in rows]


async def delete_fitbit_tokens(db_path: str, user_id: int) -> None:
    """Remove Fitbit tokens for a user (disconnect)."""
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM fitbit_tokens WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_fitbit_tokens_by_fitbit_user_id(
    db_path: str, fitbit_user_id: str
) -> dict | None:
    """Look up Fitbit token row by Fitbit user ID (used in webhook handler)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT user_id, fitbit_user_id, access_token, refresh_token, "
                "expires_at, scope, created_at, updated_at "
                "FROM fitbit_tokens WHERE fitbit_user_id = ?",
                (fitbit_user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return _decrypt_token_row(dict(row))


# ── Fitbit OAuth state (PKCE) ─────────────────────────────────────


async def save_fitbit_oauth_state(
    db_path: str,
    state: str,
    user_id: int,
    code_verifier: str,
    expires_at: str,
    session_token_hash: str | None = None,
) -> None:
    """Store a single-use PKCE state for Fitbit OAuth callback.

    A22: code_verifier is encrypted at rest with the same Fernet helpers
    used for OAuth tokens. A leak of fitbit_oauth_state would otherwise
    let an attacker who also intercepted the authorization code finish
    the PKCE handshake.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    enc_verifier = _encrypt_token(code_verifier)
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO fitbit_oauth_state (state, user_id, code_verifier, session_token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (state, user_id, enc_verifier, session_token_hash, now, expires_at),
        )
        await db.commit()


async def get_and_delete_fitbit_oauth_state(db_path: str, state: str) -> dict | None:
    """Retrieve and delete a PKCE state row (single-use). Returns None if missing or expired."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT state, user_id, code_verifier, session_token_hash, created_at, expires_at "
                "FROM fitbit_oauth_state WHERE state = ? AND expires_at > ?",
                (state, now),
            )
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        await db.execute("DELETE FROM fitbit_oauth_state WHERE state = ?", (state,))
        await db.commit()
    # Decrypt code_verifier; tolerate legacy plaintext rows (pre-A22) by
    # detecting Fernet-shape strings vs raw verifiers.
    cv = result.get("code_verifier") or ""
    try:
        result["code_verifier"] = _decrypt_token(cv)
    except Exception:
        # Legacy unencrypted row - leave as-is. Will be replaced on the
        # next OAuth round-trip.
        result["code_verifier"] = cv
    return result


# ── Strava OAuth state ─────────────────────────────────────────────


async def save_strava_oauth_state(
    db_path: str,
    state: str,
    user_id: int,
    expires_at: str,
    session_token_hash: str | None = None,
) -> None:
    """Store a single-use state token for Strava OAuth callback."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO strava_oauth_state (state, user_id, session_token_hash, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (state, user_id, session_token_hash, now, expires_at),
        )
        await db.commit()


async def get_and_delete_strava_oauth_state(db_path: str, state: str) -> dict | None:
    """Retrieve and delete a state row (single-use). Returns None if missing or expired."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT state, user_id, session_token_hash, created_at, expires_at "
                "FROM strava_oauth_state WHERE state = ? AND expires_at > ?",
                (state, now),
            )
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        await db.execute("DELETE FROM strava_oauth_state WHERE state = ?", (state,))
        await db.commit()
    return result


# ── Workout logs CRUD ──────────────────────────────────────────────


async def upsert_workout(
    db_path: str,
    user_id: int,
    source: str,
    external_id: str,
    activity_type: str,
    name: str,
    started_at: str,
    duration_sec: int,
    calories_burned: float = 0,
    distance_m: float = 0,
    avg_heart_rate: float = 0,
    logged_at: str = "",
    raw_json: str = "",
) -> int:
    """Insert or update a workout log. Returns the row id."""
    async with get_db(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO workout_logs
                (user_id, source, external_id, activity_type, name,
                 started_at, duration_sec, calories_burned, distance_m,
                 avg_heart_rate, logged_at, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, source, external_id) DO UPDATE SET
                activity_type = excluded.activity_type,
                name = excluded.name,
                started_at = excluded.started_at,
                duration_sec = excluded.duration_sec,
                calories_burned = excluded.calories_burned,
                distance_m = excluded.distance_m,
                avg_heart_rate = excluded.avg_heart_rate,
                logged_at = excluded.logged_at,
                raw_json = excluded.raw_json
            """,
            (user_id, source, external_id, activity_type, name,
             started_at, duration_sec, calories_burned, distance_m,
             avg_heart_rate, logged_at, raw_json),
        )
        await db.commit()
        return cursor.lastrowid


async def delete_workout(db_path: str, user_id: int, source: str, external_id: str) -> None:
    """Delete a workout log by source and external ID."""
    async with get_db(db_path) as db:
        await db.execute(
            "DELETE FROM workout_logs WHERE user_id = ? AND source = ? AND external_id = ?",
            (user_id, source, external_id),
        )
        await db.commit()


async def get_workouts_for_date(db_path: str, user_id: int, date_str: str) -> list[dict]:
    """Return all workout logs for a user on a given date (YYYY-MM-DD prefix match on logged_at)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT id, user_id, source, external_id, activity_type, name, "
                "started_at, duration_sec, calories_burned, distance_m, "
                "avg_heart_rate, logged_at, raw_json "
                "FROM workout_logs "
                "WHERE user_id = ? AND logged_at LIKE ? "
                "ORDER BY started_at ASC",
                (user_id, f"{date_str}%"),
            )
        ).fetchall()
    return [dict(r) for r in rows]


# ── Fitbit activity CRUD ──────────────────────────────────────────


async def upsert_fitbit_activity(
    db_path: str,
    user_id: int,
    date_str: str,
    calories_out: float = 0,
    activity_calories: float = 0,
    calories_bmr: float = 0,
    steps: int = 0,
    fairly_active_min: int = 0,
    very_active_min: int = 0,
    resting_heart_rate: int = 0,
) -> None:
    """Insert or update Fitbit daily activity summary."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO fitbit_activity
                (user_id, date, calories_out, activity_calories, calories_bmr, steps,
                 fairly_active_min, very_active_min, resting_heart_rate, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                calories_out = excluded.calories_out,
                activity_calories = excluded.activity_calories,
                calories_bmr = excluded.calories_bmr,
                steps = excluded.steps,
                fairly_active_min = excluded.fairly_active_min,
                very_active_min = excluded.very_active_min,
                resting_heart_rate = excluded.resting_heart_rate,
                fetched_at = excluded.fetched_at
            """,
            (user_id, date_str, calories_out, activity_calories, calories_bmr, steps,
             fairly_active_min, very_active_min, resting_heart_rate, now),
        )
        await db.commit()


async def get_fitbit_activity(db_path: str, user_id: int, date_str: str) -> dict | None:
    """Return Fitbit daily activity for a user/date, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, user_id, date, calories_out, activity_calories, calories_bmr, "
                "steps, fairly_active_min, very_active_min, resting_heart_rate, fetched_at "
                "FROM fitbit_activity WHERE user_id = ? AND date = ?",
                (user_id, date_str),
            )
        ).fetchone()
    if row is None:
        return None
    return dict(row)


async def get_last_strava_sync(db_path: str, user_id: int) -> str | None:
    """Return the time of the last Strava sync, or None.

    Uses strava_tokens.updated_at as a proxy - the token is refreshed + saved
    on every sync, so its updated_at tracks actual API-fetch time. This is
    more accurate than MAX(logged_at), which returns the date of the last
    workout (misleading when the user hasn't worked out in a while).
    """
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT updated_at FROM strava_tokens WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    return row[0] if row and row[0] else None


async def get_last_fitbit_sync(db_path: str, user_id: int) -> str | None:
    """Return the most recent fetched_at for Fitbit activity, or None."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT MAX(fetched_at) AS last FROM fitbit_activity WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    return row[0] if row and row[0] else None


# ── Oura token CRUD ───────────────────────────────────────────────


async def save_oura_tokens(
    db_path: str,
    user_id: int,
    oura_user_id: str,
    access_token: str,
    refresh_token: str,
    expires_at: str,
    scope: str = "",
) -> None:
    """Upsert Oura OAuth tokens for a user.

    A7: refuses if `oura_user_id` is already linked to another user.
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    enc_access = _encrypt_token(access_token)
    enc_refresh = _encrypt_token(refresh_token)
    async with get_db(db_path) as db:
        if oura_user_id:
            row = await (await db.execute(
                "SELECT user_id FROM oura_tokens WHERE oura_user_id = ? AND user_id != ?",
                (oura_user_id, user_id),
            )).fetchone()
            if row is not None:
                raise ExternalAccountAlreadyLinked("Oura")
        try:
            await db.execute(
                """
                INSERT INTO oura_tokens
                    (user_id, oura_user_id, access_token, refresh_token,
                     expires_at, scope, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    oura_user_id = excluded.oura_user_id,
                    access_token = excluded.access_token,
                    refresh_token = excluded.refresh_token,
                    expires_at = excluded.expires_at,
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
                """,
                (user_id, oura_user_id, enc_access, enc_refresh,
                 expires_at, scope, now, now),
            )
            await db.commit()
        except aiosqlite.IntegrityError as exc:
            if "oura_user_id" in str(exc).lower():
                raise ExternalAccountAlreadyLinked("Oura") from exc
            raise


async def get_oura_tokens(db_path: str, user_id: int) -> dict | None:
    """Return the Oura token row for a user, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT user_id, oura_user_id, access_token, refresh_token, "
                "expires_at, scope, created_at, updated_at "
                "FROM oura_tokens WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return _decrypt_token_row(dict(row))


async def get_all_oura_user_ids(db_path: str) -> list[int]:
    """Return all user_ids that have Oura tokens."""
    async with get_db(db_path) as db:
        rows = await (await db.execute("SELECT user_id FROM oura_tokens")).fetchall()
    return [r[0] for r in rows]


async def delete_oura_tokens(db_path: str, user_id: int) -> None:
    """Remove Oura tokens for a user (disconnect)."""
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM oura_tokens WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_oura_tokens_by_oura_user_id(
    db_path: str, oura_user_id: str
) -> dict | None:
    """Look up Oura token row by Oura user ID (used in webhook handler)."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT user_id, oura_user_id, access_token, refresh_token, "
                "expires_at, scope, created_at, updated_at "
                "FROM oura_tokens WHERE oura_user_id = ?",
                (oura_user_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return _decrypt_token_row(dict(row))


# ── Oura OAuth state ──────────────────────────────────────────────


async def save_oura_oauth_state(
    db_path: str,
    state: str,
    user_id: int,
    expires_at: str,
    session_token_hash: str | None = None,
    code_verifier: str | None = None,
) -> None:
    """Store a single-use state token for Oura OAuth callback.

    code_verifier (A21): when present, encrypts and stores the PKCE
    verifier so /callback can complete the exchange. Same Fernet helpers
    as fitbit_oauth_state (A22).
    """
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    enc_verifier = _encrypt_token(code_verifier) if code_verifier else None
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO oura_oauth_state (state, user_id, session_token_hash, code_verifier, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (state, user_id, session_token_hash, enc_verifier, now, expires_at),
        )
        await db.commit()


async def get_and_delete_oura_oauth_state(db_path: str, state: str) -> dict | None:
    """Retrieve and delete a state row (single-use). Returns None if missing or expired."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT state, user_id, session_token_hash, code_verifier, created_at, expires_at "
                "FROM oura_oauth_state WHERE state = ? AND expires_at > ?",
                (state, now),
            )
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        await db.execute("DELETE FROM oura_oauth_state WHERE state = ?", (state,))
        await db.commit()
    cv = result.get("code_verifier") or ""
    if cv:
        try:
            result["code_verifier"] = _decrypt_token(cv)
        except Exception:
            result["code_verifier"] = cv  # Legacy plaintext or new-empty.
    return result


# ── Oura activity CRUD ────────────────────────────────────────────


async def upsert_oura_activity(
    db_path: str,
    user_id: int,
    date_str: str,
    calories_out: float = 0,
    activity_calories: float = 0,
    steps: int = 0,
    fairly_active_min: int = 0,
    very_active_min: int = 0,
    resting_heart_rate: int = 0,
) -> None:
    """Insert or update Oura daily activity summary."""
    from datetime import datetime, timezone as _tz

    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            """
            INSERT INTO oura_activity
                (user_id, date, calories_out, activity_calories, steps,
                 fairly_active_min, very_active_min, resting_heart_rate, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET
                calories_out = excluded.calories_out,
                activity_calories = excluded.activity_calories,
                steps = excluded.steps,
                fairly_active_min = excluded.fairly_active_min,
                very_active_min = excluded.very_active_min,
                resting_heart_rate = excluded.resting_heart_rate,
                fetched_at = excluded.fetched_at
            """,
            (user_id, date_str, calories_out, activity_calories, steps,
             fairly_active_min, very_active_min, resting_heart_rate, now),
        )
        await db.commit()


async def get_oura_activity(db_path: str, user_id: int, date_str: str) -> dict | None:
    """Return Oura daily activity for a user/date, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT id, user_id, date, calories_out, activity_calories, steps, "
                "fairly_active_min, very_active_min, resting_heart_rate, fetched_at "
                "FROM oura_activity WHERE user_id = ? AND date = ?",
                (user_id, date_str),
            )
        ).fetchone()
    if row is None:
        return None
    return dict(row)


async def get_last_oura_sync(db_path: str, user_id: int) -> str | None:
    """Return the most recent fetched_at for Oura activity, or None."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT MAX(fetched_at) AS last FROM oura_activity WHERE user_id = ?",
                (user_id,),
            )
        ).fetchone()
    return row[0] if row and row[0] else None


# ── Badge / Achievement CRUD ──────────────────────────────────────────────────


async def get_user_badges(db_path: str, user_id: int) -> list[dict]:
    """Return all earned badges for a user."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT badge_id, tier, earned_at, seen FROM badge_earned WHERE user_id = ?",
                (user_id,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def get_badge(db_path: str, user_id: int, badge_id: str) -> dict | None:
    """Return a single badge row or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT badge_id, tier, earned_at, seen FROM badge_earned WHERE user_id = ? AND badge_id = ?",
                (user_id, badge_id),
            )
        ).fetchone()
    return dict(row) if row else None


async def upsert_badge(db_path: str, user_id: int, badge_id: str, tier: int) -> dict | None:
    """Upsert badge. Returns info dict if tier upgraded, else None."""
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        # Check existing
        db.row_factory = aiosqlite.Row
        existing = await (
            await db.execute(
                "SELECT tier FROM badge_earned WHERE user_id = ? AND badge_id = ?",
                (user_id, badge_id),
            )
        ).fetchone()
        old_tier = existing["tier"] if existing else -1
        if tier <= old_tier:
            return None  # No upgrade
        await db.execute(
            """INSERT INTO badge_earned (user_id, badge_id, tier, earned_at, seen)
               VALUES (?, ?, ?, ?, 0)
               ON CONFLICT(user_id, badge_id) DO UPDATE SET tier = ?, earned_at = ?, seen = 0""",
            (user_id, badge_id, tier, now, tier, now),
        )
        await db.commit()
    return {"badge_id": badge_id, "old_tier": old_tier, "new_tier": tier, "is_new": old_tier == -1}


async def mark_badges_seen(db_path: str, user_id: int, badge_ids: list[str]) -> None:
    """Mark badges as seen."""
    if not badge_ids:
        return
    async with get_db(db_path) as db:
        placeholders = ",".join("?" * len(badge_ids))
        await db.execute(
            f"UPDATE badge_earned SET seen = 1 WHERE user_id = ? AND badge_id IN ({placeholders})",
            [user_id, *badge_ids],
        )
        await db.commit()


async def get_unseen_badges(db_path: str, user_id: int) -> list[dict]:
    """Return unseen earned badges."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT badge_id, tier, earned_at FROM badge_earned WHERE user_id = ? AND seen = 0",
                (user_id,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def get_streak_shields(db_path: str, user_id: int) -> dict:
    """Return shield info: available count and total earned."""
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM streak_shields WHERE user_id = ? AND used_at IS NULL", (user_id,)
        )).fetchone()
        available = row[0] if row else 0
        row2 = await (await db.execute(
            "SELECT COUNT(*) FROM streak_shields WHERE user_id = ?", (user_id,)
        )).fetchone()
        total = row2[0] if row2 else 0
    return {"available": available, "total_earned": total}


async def earn_streak_shield(db_path: str, user_id: int) -> bool:
    """Award a shield if < 3 unused. Returns True if awarded."""
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        # BEGIN IMMEDIATE serializes the SELECT-then-INSERT against concurrent
        # earners on the same user, so the cap of 3 unused can't be exceeded.
        await db.execute("BEGIN IMMEDIATE")
        try:
            row = await (await db.execute(
                "SELECT COUNT(*) FROM streak_shields WHERE user_id = ? AND used_at IS NULL", (user_id,)
            )).fetchone()
            if (row[0] if row else 0) >= 3:
                await db.execute("ROLLBACK")
                return False
            await db.execute(
                "INSERT INTO streak_shields (user_id, earned_at) VALUES (?, ?)",
                (user_id, now),
            )
            await db.commit()
        except Exception:
            await db.execute("ROLLBACK")
            raise
    return True


# Cap how far back a shield can bridge a gap. Without this, a long historical
# absence can burn every available shield in a single walk-back pass - a
# real prod bug we hit (one user lost 7 shields to a single ancient gap).
MAX_STREAK_GAP_LOOKBACK_DAYS = 7


async def _auto_bridge_with_conn(
    db: aiosqlite.Connection, user_id: int, today_str: str
) -> list[str]:
    """Connection-scoped shield bridging. Caller commits.

    Bridges gap days inside the active streak window (from the anchor back
    to the oldest log/bridged day within MAX_STREAK_GAP_LOOKBACK_DAYS).
    Two guards keep this from misbehaving:

    1. **Lookback cap.** Don't extend the active window past the cap. A user
       returning after a long absence shouldn't have months-old gaps eat
       their shields (real prod failure: 7 shields lost to one ancient gap).

    2. **All-or-nothing.** Only bridge if the user has enough shields to
       cover *every* gap inside the window. Partial bridging would burn
       shields without restoring the streak.

    Returns list of newly bridged dates.
    """
    db.row_factory = aiosqlite.Row
    today = date.fromisoformat(today_str)
    bridge_cutoff = today - timedelta(days=MAX_STREAK_GAP_LOOKBACK_DAYS)

    log_rows = await (await db.execute(
        "SELECT DISTINCT substr(logged_at, 1, 10) AS d FROM meal_logs WHERE user_id = ?",
        (user_id,),
    )).fetchall()
    date_set = {r["d"] for r in log_rows}
    if not date_set:
        return []
    earliest_date_str = min(date_set)

    bridged_rows = await (await db.execute(
        "SELECT bridged_date FROM streak_shields "
        "WHERE user_id = ? AND bridged_date IS NOT NULL",
        (user_id,),
    )).fetchall()
    already_bridged = {r["bridged_date"] for r in bridged_rows if r["bridged_date"]}
    all_valid = date_set | already_bridged

    yesterday = today - timedelta(days=1)
    if today_str in all_valid:
        anchor = today
    elif yesterday.isoformat() in all_valid:
        anchor = yesterday
    else:
        # Provisional anchor at yesterday - only meaningful if it's within
        # the lookback window and the user's history. We'll only commit a
        # bridge for it below if the all-or-nothing guard passes.
        if yesterday < bridge_cutoff or yesterday.isoformat() < earliest_date_str:
            return []
        anchor = yesterday

    # Walk back from anchor collecting day status until we leave the
    # lookback window or fall before the user's first-ever log.
    walk: list[tuple[date, bool]] = []
    cur = anchor
    while cur >= bridge_cutoff and cur.isoformat() >= earliest_date_str:
        walk.append((cur, cur.isoformat() in all_valid))
        cur -= timedelta(days=1)
    if not walk:
        return []

    # Active window = anchor down to the oldest valid (logged/bridged) day
    # in the walk. Anything past that is a "trailing tail" of gaps that
    # would only inflate the streak past the user's actual history.
    oldest_valid_idx = None
    for i in range(len(walk) - 1, -1, -1):
        if walk[i][1]:
            oldest_valid_idx = i
            break
    if oldest_valid_idx is None:
        return []

    window = walk[: oldest_valid_idx + 1]
    gaps = [d.isoformat() for d, ok in window if not ok]
    if not gaps:
        return []

    avail_rows = await (await db.execute(
        "SELECT id FROM streak_shields "
        "WHERE user_id = ? AND used_at IS NULL ORDER BY id ASC",
        (user_id,),
    )).fetchall()
    available_ids = [r["id"] for r in avail_rows]
    if len(available_ids) < len(gaps):
        return []  # not enough shields to fully bridge - preserve them

    # used_at is stored as a date string (no time) for parity with the
    # legacy meal_accept consumption path and the `bridged_date >= since_date`
    # comparisons in was_shield_used_recently / get_shields_used_recently.
    from datetime import datetime as _dt, timezone as _tz
    now = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    newly_bridged: list[str] = []
    for shield_id, gap_date in zip(available_ids, gaps):
        cur_result = await db.execute(
            "UPDATE streak_shields SET used_at = ?, bridged_date = ? "
            "WHERE id = ? AND used_at IS NULL",
            (now, gap_date, shield_id),
        )
        if cur_result.rowcount > 0:
            newly_bridged.append(gap_date)
    return newly_bridged


async def auto_consume_shields_for_streak(
    db_path: str, user_id: int, today_str: str
) -> list[str]:
    """Bridge unprotected gap days up to today using available shields.

    Without this, the dashboard's `_calculate_streak` shows streak=0 the
    moment a user misses a day, even when shields would protect them - the
    badge engine only consumes shields on meal_accept, so a user who simply
    opens the app sees a "broken" streak before any consumption ran.

    Idempotent and safe under concurrency: only updates rows still
    `used_at IS NULL`, so a racing caller can't double-consume the same shield.

    Returns list of newly bridged dates. Also fires (fire-and-forget) a push
    notification surfacing the rescue so the user knows shields saved them.
    """
    import asyncio as _asyncio
    async with get_db(db_path) as db:
        bridged = await _auto_bridge_with_conn(db, user_id, today_str)
        if bridged:
            await db.commit()
    if bridged:
        try:
            from src.web.push_scheduler import notify_shield_consumed
            _asyncio.create_task(notify_shield_consumed(db_path, user_id, list(bridged)))
        except Exception:
            pass  # Non-critical; in-app toast still covers active sessions
    return bridged



async def was_shield_used_recently(db_path: str, user_id: int, since_date: str) -> bool:
    """Check if any shield bridged a gap on or after since_date."""
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COUNT(*) FROM streak_shields WHERE user_id = ? AND used_at IS NOT NULL AND bridged_date >= ?",
            (user_id, since_date),
        )).fetchone()
    return (row[0] if row else 0) > 0


async def get_shield_history(db_path: str, user_id: int) -> list[dict]:
    """Return shield earned/used history."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(
            "SELECT earned_at, used_at, bridged_date FROM streak_shields WHERE user_id = ? ORDER BY earned_at DESC LIMIT 10",
            (user_id,),
        )).fetchall()
    return [dict(r) for r in rows]


async def get_shields_used_recently(db_path: str, user_id: int, since_date: str) -> list[str]:
    """Return bridged_date values for shields consumed on or after since_date."""
    async with get_db(db_path) as db:
        rows = await (await db.execute(
            """SELECT bridged_date FROM streak_shields
               WHERE user_id = ? AND used_at IS NOT NULL AND used_at >= ?
               AND bridged_date IS NOT NULL
               ORDER BY used_at DESC""",
            (user_id, since_date),
        )).fetchall()
    return [r[0] for r in rows]


async def _fetch_on_target_dates(db, user_id: int) -> list[date]:
    rows = await (await db.execute(
        """SELECT substr(ml.logged_at, 1, 10) AS d
             FROM meal_logs ml JOIN user_targets ut ON ut.user_id = ml.user_id
             WHERE ml.user_id = ?
             GROUP BY d
             HAVING SUM(ml.calories) BETWEEN ut.calories * 0.8 AND ut.calories * 1.2
             ORDER BY d""",
        (user_id,),
    )).fetchall()
    return [date.fromisoformat(r[0]) for r in rows]


def _count_consecutive_runs_of_3(dates: list[date]) -> int:
    """Number of completed non-overlapping 3-day runs across all on-target dates.

    Each maximal chain of K consecutive calendar days contributes K // 3 shields.
    This is the lifetime count of shields the user has earned the right to.
    """
    if not dates:
        return 0
    runs = 0
    chain = 1
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            chain += 1
        else:
            runs += chain // 3
            chain = 1
    runs += chain // 3
    return runs


def _current_on_target_run(dates: list[date], today: date) -> int:
    """Length of the user's *active* trailing run of consecutive on-target days.

    The run can end at today or yesterday - today's missing entry is treated
    as still-in-progress, not a break, so we don't tell users their progress
    just evaporated because they haven't logged dinner yet. Any earlier
    missed/off-target day breaks the run.
    """
    if not dates:
        return 0
    on_target = set(dates)
    cursor = today if today in on_target else today - timedelta(days=1)
    if cursor not in on_target:
        return 0
    length = 0
    while cursor in on_target:
        length += 1
        cursor -= timedelta(days=1)
    return length


async def get_shield_progress(db_path: str, user_id: int, today_str: str | None = None) -> dict:
    """Return shield earning progress: on_target_days, shields_earned_total, days_until_next.

    Shields are earned per run of 3 *consecutive* on-target days. `days_until_next`
    reflects the active run only - if the user has missed/off-target days breaking
    the chain, the counter resets to 3.
    """
    today = date.fromisoformat(today_str) if today_str else date.today()
    async with get_db(db_path) as db:
        on_target_dates = await _fetch_on_target_dates(db, user_id)

        total_earned_row = await (await db.execute(
            "SELECT COUNT(*) FROM streak_shields WHERE user_id = ?", (user_id,)
        )).fetchone()
        shields_earned_total = total_earned_row[0] if total_earned_row else 0

        avail_row = await (await db.execute(
            "SELECT COUNT(*) FROM streak_shields WHERE user_id = ? AND used_at IS NULL", (user_id,)
        )).fetchone()
        available = avail_row[0] if avail_row else 0

    on_target_days = len(on_target_dates)
    shields_deserved = _count_consecutive_runs_of_3(on_target_dates)
    new_shields_possible = shields_deserved - shields_earned_total
    current_run = _current_on_target_run(on_target_dates, today)

    if available >= 3:
        days_until_next = -1  # at cap, no progress to show
    elif new_shields_possible > 0:
        # They earned one already but the unused-cap blocked the award.
        days_until_next = -1
    else:
        # 3 - (run % 3) gives 3 right after a run completes (start fresh),
        # 2/1 mid-run, and 3 when no active run.
        days_until_next = 3 - (current_run % 3) if current_run % 3 else 3

    return {
        "on_target_days": on_target_days,
        "shields_earned_total": shields_earned_total,
        "days_until_next": days_until_next,
    }


async def increment_target_set_count(db_path: str, user_id: int) -> None:
    """Increment the target_set_count on the users table."""
    async with get_db(db_path) as db:
        await db.execute(
            "UPDATE users SET target_set_count = COALESCE(target_set_count, 0) + 1 WHERE user_id = ?",
            (user_id,),
        )
        await db.commit()


async def get_target_set_count(db_path: str, user_id: int) -> int:
    """Return how many times user has set targets."""
    async with get_db(db_path) as db:
        row = await (await db.execute(
            "SELECT COALESCE(target_set_count, 0) FROM users WHERE user_id = ?", (user_id,)
        )).fetchone()
    return row[0] if row else 0


# ── Barcode cache ──────────────────────────────────────────────────

async def get_barcode_cache(db_path: str, barcode: str) -> dict | None:
    """Return cached barcode data if present and within TTL, else None.

    Found products use a 90-day TTL. Not-found entries use a 2-day TTL.
    Returns a dict with not_found=1 for cached misses (caller should treat as no product).
    """
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM barcode_cache "
            "WHERE barcode = ? AND ("
            "  (not_found = 0 AND fetched_at > datetime('now', '-90 days')) OR "
            "  (not_found = 1 AND fetched_at > datetime('now', '-2 days'))"
            ")",
            (barcode,),
        )).fetchone()
    if not row:
        return None
    return dict(row)


async def set_barcode_cache(
    db_path: str,
    barcode: str,
    product_name: str,
    brand: str = "",
    serving_size_g: float | None = None,
    serving_size_unit: str = "g",
    serving_label: str = "",
    calories: float = 0,
    protein: float = 0,
    carbs: float = 0,
    fat: float = 0,
    cal_per_100g: float | None = None,
    protein_per_100g: float | None = None,
    carbs_per_100g: float | None = None,
    fat_per_100g: float | None = None,
    source: str = "openfoodfacts",
    raw_json: str = "",
    image_url: str = "",
) -> None:
    """Insert or update a barcode cache entry."""
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    unit = (serving_size_unit or "g").lower()
    if unit not in {"g", "ml"}:
        unit = "g"
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO barcode_cache "
            "(barcode, product_name, brand, serving_size_g, serving_size_unit, serving_label, "
            "calories, protein, carbs, fat, "
            "cal_per_100g, protein_per_100g, carbs_per_100g, fat_per_100g, "
            "source, raw_json, image_url, not_found, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                barcode, product_name, brand, serving_size_g, unit, serving_label,
                calories, protein, carbs, fat,
                cal_per_100g, protein_per_100g, carbs_per_100g, fat_per_100g,
                source, raw_json, image_url, 0, now_str,
            ),
        )
        await db.commit()


async def set_barcode_cache_not_found(db_path: str, barcode: str) -> None:
    """Cache a 'not found' result for a barcode (2-day TTL enforced by get_barcode_cache)."""
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO barcode_cache "
            "(barcode, product_name, brand, calories, protein, carbs, fat, "
            "source, not_found, fetched_at) "
            "VALUES (?, '', '', 0, 0, 0, 0, 'not_found', 1, ?)",
            (barcode, now_str),
        )
        await db.commit()


# ── Per-user barcode corrections ───────────────────────────────────

async def get_barcode_correction(
    db_path: str, user_id: int, barcode: str
) -> dict | None:
    """Return the user's saved correction for this barcode, or None."""
    async with get_db(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT * FROM barcode_corrections WHERE user_id = ? AND barcode = ?",
            (user_id, barcode),
        )).fetchone()
    return dict(row) if row else None


async def save_barcode_correction(
    db_path: str,
    user_id: int,
    barcode: str,
    product_name: str,
    brand: str,
    calories: float,
    protein: float,
    carbs: float,
    fat: float,
    serving_size_g: float | None,
    serving_label: str,
    serving_size_unit: str = "g",
) -> None:
    """Insert or update a per-user barcode correction."""
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    unit = (serving_size_unit or "g").lower()
    if unit not in {"g", "ml"}:
        unit = "g"
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO barcode_corrections "
            "(user_id, barcode, product_name, brand, calories, protein, carbs, fat, "
            "serving_size_g, serving_size_unit, serving_label, corrected_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, barcode, product_name, brand,
                calories, protein, carbs, fat,
                serving_size_g, unit, serving_label, now_str,
            ),
        )
        await db.commit()


async def delete_barcode_correction(
    db_path: str, user_id: int, barcode: str
) -> bool:
    """Delete the user's correction for this barcode. Returns True if a row was removed."""
    async with get_db(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM barcode_corrections WHERE user_id = ? AND barcode = ?",
            (user_id, barcode),
        )
        await db.commit()
        return (cursor.rowcount or 0) > 0


# ── Stripe webhook idempotency ────────────────────────────────────────────
async def was_stripe_webhook_event_processed(db_path: str, event_id: str) -> bool:
    """Return True if this Stripe event_id was already handled."""
    async with get_db(db_path) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM stripe_webhook_events WHERE event_id = ?",
                (event_id,),
            )
        ).fetchone()
    return row is not None


async def record_stripe_webhook_event(db_path: str, event_id: str, event_type: str) -> None:
    """Mark this Stripe event_id as processed so retries are no-ops.

    INSERT OR IGNORE so a genuine race between two concurrent retries
    doesn't raise - the first write wins, the second is a no-op.
    """
    from datetime import datetime, timezone as _tz
    now_str = datetime.now(_tz.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO stripe_webhook_events (event_id, event_type, processed_at) "
            "VALUES (?, ?, ?)",
            (event_id, event_type, now_str),
        )
        await db.commit()
