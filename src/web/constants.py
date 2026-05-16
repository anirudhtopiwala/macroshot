"""Shared constants for the web app.

## Mode model

Two independent env flags control gating:

- `APP_MODE` - "self" (default, uncapped for local dev & self-hosters) or
  "hosted" (production-style: caps enforced, subscription table consulted).
- `BETA_MODE` - "on" or "off". Only meaningful when APP_MODE=hosted. When
  on: signup is capped at BETA_SIGNUP_CAP, every new signup is auto-granted
  free Pro access, Stripe checkout is blocked, renewal checks are skipped.
  When off: normal Stripe-driven flow (trial → pro_monthly → free).

Stripe key presence is orthogonal - it decides whether billing calls can
actually be made, not whether gating applies.
"""

import logging
import os

_logger = logging.getLogger("macro_app")


APP_MODE = os.environ.get("APP_MODE", "self").strip().lower()  # "self" | "hosted"

# Deployment environment marker. Set to "staging" on the staging VM so
# environment-specific gates (e.g. the staging login allowlist) can
# fire without affecting production. Unset / "production" / "self" all
# mean "not staging".
APP_ENV = os.environ.get("APP_ENV", "").strip().lower()

# Comma-separated allowlist of emails permitted to log into the staging
# environment. When APP_ENV=staging, any login attempt from an email not
# in this list is rejected with a 403 and a redirect to the production
# URL. Ignored in every other environment.
_staging_allowed_raw = os.environ.get("STAGING_ALLOWED_EMAILS", "")
STAGING_ALLOWED_EMAILS = frozenset(
    e.strip().lower() for e in _staging_allowed_raw.split(",") if e.strip()
)

# Additional substring patterns - any email containing one of these is
# allowed into staging. Useful for letting the maintainer and trusted
# testers spin up throwaway accounts (e.g. "maintainer+test123@gmail.com")
# without having to update the exact-match list. Substrings are checked
# case-insensitively against the full email.
_staging_allowed_substr_raw = os.environ.get("STAGING_ALLOWED_SUBSTRINGS", "")
STAGING_ALLOWED_SUBSTRINGS = tuple(
    s.strip().lower() for s in _staging_allowed_substr_raw.split(",") if s.strip()
)

# URL the frontend navigates to when a user is blocked from staging.
STAGING_REDIRECT_URL = os.environ.get("STAGING_REDIRECT_URL", "")

# BETA_MODE defaults to **on** - this is a deliberate fail-safe. If the env
# var is unset, missing, or misspelled, the safe behavior is free-beta
# (no one gets charged). Turning off beta to enable real Stripe billing
# must be an explicit, deliberate act: set `BETA_MODE=off` in the systemd
# EnvironmentFile. See `CLAUDE.local.md` → "BETA_MODE is a launch-critical
# switch" for the full policy. Do NOT change this default.
BETA_MODE = os.environ.get("BETA_MODE", "on").strip().lower() != "off"

# Cap total web_auth rows when hosted + beta. New signups beyond this land
# in the `waitlist` table instead of creating an account.
BETA_SIGNUP_CAP = int(os.environ.get("BETA_SIGNUP_CAP", "200"))


# ── Pro-tier limits (apply to everyone in hosted+beta mode) ─────────
#
# During beta, all hosted-mode users are on the Pro plan (free). These
# caps apply uniformly to both regular Pros and OG users. OG status does
# NOT raise limits - it only skips renewal enforcement and flips UI copy.

# Daily image-based meal analysis cap. Covers initial /analyze calls that
# upload an image. Subsequent /correct calls do NOT count - corrections
# are text-only Gemini calls (no image bytes re-sent) and are gated per-
# session by PRO_AI_EDITS_PER_MEAL instead. Text-only initial analyses
# are gated separately via PRO_TEXT_MEAL_LIMIT.
PRO_IMAGE_LIMIT = 5

# Daily cap on text-only initial meal analyses (no images attached).
PRO_TEXT_MEAL_LIMIT = 10

# Daily cap on AI coach chat sessions created.
PRO_CHAT_LIMIT = 10

# Per-meal-session cap on Gemini-backed AI edits / corrections. Manual
# macro adjustments (dial pickers, field edits) do NOT count - only the
# /sessions/{id}/correct endpoint which calls Gemini. Applies to fresh
# analyze sessions, edit-from-meal sessions, and edit-from-alias sessions.
PRO_AI_EDITS_PER_MEAL = 10


# ── Legacy free-tier limits (dormant during beta) ───────────────────
#
# Kept intact so the non-beta hosted path still compiles and the post-beta
# rollout has sensible defaults when Stripe turns on. No user can hit
# these while BETA_MODE=on because everyone is auto-granted Pro on signup.

FREE_IMAGE_LIMIT = 3
FREE_BARCODE_LIMIT = 10     # reference only, unenforced
FREE_CORRECTION_LIMIT = 2
FREE_CHAT_LIMIT = 1
FREE_ALIAS_LIMIT = 10


# ── Gemini spend cap (project-level safety gate) ────────────────────
#
# Month-to-date $ cost of Gemini API calls is computed from the
# `gemini_calls` table and checked before every top-level Gemini entry
# point. Once the cap is hit, routes return 503 with BUDGET_EXCEEDED_MESSAGE
# and the AI features go dark until the month rolls over or the cap is
# raised. The cap is *global* - it ignores BETA_MODE and subscription
# status because the concern is our total API spend, not per-user
# fairness. Barcode lookup and saved-meal relogs do not call Gemini and
# continue to work when the gate trips.
#
# Paired with a GCP budget → Pub/Sub → Cloud Function kill switch at a
# higher threshold (see infra/budget_killswitch/) as a backstop if this
# gate fails.
MONTHLY_GEMINI_BUDGET_USD = float(os.environ.get("MONTHLY_GEMINI_BUDGET_USD", "60"))

BUDGET_EXCEEDED_MESSAGE = (
    "MacroShot AI is temporarily down. "
    "Barcode scanning and saved meals still work."
)


# ── Rate-limit error copy ───────────────────────────────────────────
#
# Every 429 response from a gated endpoint routes through `limit_message`
# so wording stays consistent and swaps in one place when BETA_MODE flips
# off. The frontend's 429 handler reads `detail.message` as-is.

_FEATURE_LABELS = {
    "image_analysis": "photo scans",
    "text_meal": "text meal entries",
    "ai_chat": "AI coach chats",
    "meal_edit": "AI meal edits",
}


def limit_message(feature: str, limit: int, scope: str = "today") -> str:
    """Return a user-facing 429 message for a hit cap.

    `feature` is one of the keys in _FEATURE_LABELS. `scope` is "today"
    for daily caps and "on this meal" for the per-session AI-edit cap.

    Unknown feature keys still return a message (so we never 500 on a
    typo in production) but emit a warning log so the operator can fix
    the callsite. Every production callsite should pass one of the four
    known keys.
    """
    if feature not in _FEATURE_LABELS:
        _logger.warning(
            "limit_message called with unknown feature=%r - fix the callsite",
            feature,
        )
    label = _FEATURE_LABELS.get(feature, "requests")
    if BETA_MODE:
        return (
            f"You've hit {scope}'s {limit} {label}. Caps are tight during "
            "the free beta - a paid tier with bigger limits is coming soon."
        )
    return (
        f"You've hit {scope}'s {limit} {label}. "
        "Upgrade to Pro for larger caps."
    )
