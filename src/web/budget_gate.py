"""Project-wide monthly Gemini spend gate.

Every top-level Gemini entry function (in `src/gemini.py` and
`src/ai_chat.py`) calls `assert_gemini_budget()` before invoking the
API. When month-to-date spend from `gemini_calls` reaches
`MONTHLY_GEMINI_BUDGET_USD`, the gate raises `BudgetExceededError`.
Routes translate that to a 503 with `BUDGET_EXCEEDED_MESSAGE` in the
body.

Design notes:
- **Global, not per-user.** The concern is our total API spend, not
  per-user fairness. BETA_MODE is ignored here; per-user daily caps
  (PRO_IMAGE_LIMIT etc.) already enforce fairness separately.
- **60s cache** on the cost query so we don't hit SQLite on every
  Gemini call. Under the cap this is a no-op; over the cap the cache
  keeps the gate closed until TTL expires.
- **Fail-closed when within 5% of cap.** A DB error inside the safety
  band is treated as "over". Far below the cap, a transient DB error
  still fails open so an isolated SQLite hiccup doesn't take the app
  down — the kill-switch / Sentry alerts are the catastrophic backstop.
- **Per-call reservation.** We subtract a small estimated cost from the
  cached value before returning allowed, so a burst of concurrent calls
  starting in the same 60s cache window can't all see "under cap" and
  collectively overshoot.
"""

from __future__ import annotations

import logging
import time

from src.db import get_monthly_gemini_cost_usd
from src.web.constants import MONTHLY_GEMINI_BUDGET_USD

_logger = logging.getLogger("macro_app")

_CACHE_TTL_SECONDS = 60.0

# Estimated cost reserved per in-flight Gemini call. A typical chat turn
# costs ~$0.001-$0.01; reserving 1¢ per call deflates the cached cost so a
# concurrent burst can't all squeeze under the cap. The reservation is
# pessimistic on purpose — actual cost is logged via log_gemini_call and
# the next cache refresh corrects to truth.
_PER_CALL_RESERVATION_USD = 0.01

# How close to the cap we treat as "danger zone" — within this band, a DB
# error fails CLOSED instead of open.
_FAIL_CLOSED_BAND_FRACTION = 0.95

# (cost_usd, monotonic_timestamp) - module-level cache, shared across
# all callers in the process. Uvicorn runs a single worker on the VM,
# so this is effectively global.
_cache: tuple[float, float] | None = None

# Sentinel: once we've observed a tripped cap from any caller, stay tripped
# until the next cache refresh shows a fresh under-cap reading. Prevents
# rare "cache ttl just expired" windows from briefly waving traffic past
# the cap before it settles again.
_tripped_sentinel: bool = False

# Remember whether we already logged the "budget exceeded" warning for
# the current trip, so Sentry doesn't get flooded - one warning per
# process per trip is enough signal.
_logged_trip: bool = False


class BudgetExceededError(Exception):
    """Raised when month-to-date Gemini spend is at or over the cap."""


async def assert_gemini_budget(db_path: str) -> None:
    """Raise BudgetExceededError if month-to-date Gemini cost ≥ cap.

    Side effect: deflates the cached cost by a small reservation per
    successful (non-raising) check, so concurrent in-flight callers don't
    collectively overshoot when the live cost number lags behind.
    """
    global _cache, _logged_trip, _tripped_sentinel

    now = time.monotonic()
    if _cache is not None and now - _cache[1] < _CACHE_TTL_SECONDS:
        cost = _cache[0]
    else:
        try:
            cost = await get_monthly_gemini_cost_usd(db_path)
        except Exception:
            # Within 5% of the cap we cannot afford to fail open — a transient
            # DB hiccup at the worst possible moment would let unbounded spend
            # through. Outside the band, fail open to keep the app available.
            danger_threshold = MONTHLY_GEMINI_BUDGET_USD * _FAIL_CLOSED_BAND_FRACTION
            last_known = _cache[0] if _cache else 0.0
            if _tripped_sentinel or last_known >= danger_threshold:
                _logger.exception(
                    "budget_gate: DB read failed in danger band - failing CLOSED"
                )
                raise BudgetExceededError(
                    "Could not read budget; conservatively rejecting near cap."
                )
            _logger.exception("budget_gate: DB read failed - failing open (far from cap)")
            return
        _cache = (cost, now)

    if cost >= MONTHLY_GEMINI_BUDGET_USD:
        _tripped_sentinel = True
        if not _logged_trip:
            _logger.warning(
                "budget_gate: TRIPPED - monthly Gemini spend $%.4f ≥ $%.2f cap",
                cost,
                MONTHLY_GEMINI_BUDGET_USD,
            )
            _logged_trip = True
        raise BudgetExceededError(
            f"monthly Gemini spend ${cost:.4f} at or above cap ${MONTHLY_GEMINI_BUDGET_USD:.2f}"
        )

    # Fresh under-cap reading clears the sentinel.
    _logged_trip = False
    _tripped_sentinel = False

    # Reserve a small estimated cost so concurrent callers within the
    # same TTL window can't collectively exceed the cap.
    if _cache is not None:
        _cache = (_cache[0] + _PER_CALL_RESERVATION_USD, _cache[1])


def _reset_cache_for_tests() -> None:
    """Test helper: clear the module-level cache and trip flag."""
    global _cache, _logged_trip, _tripped_sentinel
    _cache = None
    _logged_trip = False
    _tripped_sentinel = False
