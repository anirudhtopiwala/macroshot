"""Beta → post-beta transition test: BETA_MODE flips off with existing users.

The critical risk when the operator eventually flips BETA_MODE=off is
that beta grandfathers (users auto-granted Pro during beta) will see
either:
  (a) their plan silently downgraded to 'trial' / 'free',
  (b) a "Start Trial" button in Settings that, if clicked, downgrades
      them to a 7-day trial then expires them to 'free',
  (c) chat / meal caps disappearing or becoming inconsistent.

This test simulates that exact transition. It signs up 3 users in
hosted+beta, flips BETA_MODE off, and then asserts every grandfather
still has Pro access with the same caps as before. If this test fails
after a future refactor, the transition will break user Pro access on
the operator's timeline - a silent data regression.

The behavior under test:
  • `create_pro_subscription` sets `trial_used=1` on INSERT so the
    `trial_available` flag is False post-beta (no Start Trial button).
  • `get_subscription_info` in hosted+no-beta mode returns
    `is_premium=True` for plan='pro_monthly', status='active', and
    `image_queries_limit=PRO_IMAGE_LIMIT`, `chats_limit=PRO_CHAT_LIMIT`,
    etc. - same caps as beta.
  • An OG user toggled via admin stays Pro regardless.
  • A post-beta /subscription/trial call from a grandfather returns
    409 `error: "beta_mode"` during beta, and in the no-beta branch is
    guarded by `trial_used=1` so start_trial() returns False.
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

os.environ.setdefault("JWT_SECRET", "test-secret-key")
os.environ.setdefault("TZ", "America/Los_Angeles")

from httpx import ASGITransport, AsyncClient

from src.db import (
    create_web_user,
    ensure_user,
    get_subscription,
    init_db,
    set_og_status,
    start_trial,
)
from src.web.app import app
from src.web.deps import get_current_user, get_db_path


SUB_API = "/macro_app/api/v1/subscription"


def _set_mode(app_mode: str, beta_mode: bool, cap: int = 200) -> None:
    """Patch APP_MODE / BETA_MODE / BETA_SIGNUP_CAP in every module that
    imports them by name. Must keep in sync with the equivalent helper in
    test_beta_journey.py - the call sites are documented there."""
    import src.web.constants as _constants
    import src.web.deps as _deps
    import src.web.routes.auth as _auth
    import src.web.routes.subscription as _sub_route

    _constants.APP_MODE = app_mode
    _deps.APP_MODE = app_mode
    _auth.APP_MODE = app_mode
    _constants.BETA_MODE = beta_mode
    _deps.BETA_MODE = beta_mode
    _auth.BETA_MODE = beta_mode
    _sub_route.BETA_MODE = beta_mode
    _constants.BETA_SIGNUP_CAP = cap
    _auth.BETA_SIGNUP_CAP = cap


@pytest.fixture
def mode_env():
    import src.web.constants as _constants
    import src.web.deps as _deps
    import src.web.routes.auth as _auth
    import src.web.routes.subscription as _sub_route

    snapshot = (
        _constants.APP_MODE,
        _deps.APP_MODE,
        _auth.APP_MODE,
        _constants.BETA_MODE,
        _deps.BETA_MODE,
        _auth.BETA_MODE,
        _sub_route.BETA_MODE,
        _constants.BETA_SIGNUP_CAP,
        _auth.BETA_SIGNUP_CAP,
    )
    yield _set_mode
    (
        _constants.APP_MODE,
        _deps.APP_MODE,
        _auth.APP_MODE,
        _constants.BETA_MODE,
        _deps.BETA_MODE,
        _auth.BETA_MODE,
        _sub_route.BETA_MODE,
        _constants.BETA_SIGNUP_CAP,
        _auth.BETA_SIGNUP_CAP,
    ) = snapshot


@pytest_asyncio.fixture
async def db(tmp_path):
    path = str(tmp_path / "transition.db")
    await init_db(path)
    return path


def _override_current_user(user_id: int, email: str):
    async def mock_user():
        return {
            "user_id": user_id,
            "email": email,
            "username": email,
            "first_name": "U",
            "google_sub": None,
            "created_at": "2026-01-01 00:00:00",
        }
    app.dependency_overrides[get_current_user] = mock_user


@pytest.mark.asyncio
async def test_beta_grandfathers_survive_beta_mode_off(mode_env, db):
    """Users who signed up in hosted+beta remain Pro after BETA_MODE flips off.

    Core regression guard: create_pro_subscription sets trial_used=1 so
    the legacy `POST /subscription/trial` flow can't downgrade the user
    to a 7-day trial that then expires to free.
    """
    mode_env("hosted", True)

    # Phase 1: three signups during beta - each gets Pro.
    grandfather_ids: list[int] = []
    for i in range(3):
        uid = await create_web_user(db, f"beta{i}@example.com", first_name=f"Beta{i}")
        grandfather_ids.append(uid)
        # The auth flow would call this - we do it directly.
        from src.db import create_pro_subscription
        await create_pro_subscription(db, uid, is_og=False)

    for uid in grandfather_ids:
        sub = await get_subscription(db, uid)
        assert sub is not None
        assert sub["plan"] == "pro_monthly"
        assert sub["status"] == "active"
        assert sub["is_og"] == 0
        # trial_used=1 is the critical fix: without it, post-beta the
        # user would see trial_available=True and be offered a trial
        # button that downgrades them.
        assert sub["trial_used"] == 1, (
            "Beta grandfathers must have trial_used=1 to survive BETA_MODE=off"
        )

    # Phase 2: the operator flips BETA_MODE=off. Hosted mode persists.
    mode_env("hosted", False)

    # Grandfathers keep Pro access and correct caps.
    from src.web.deps import get_subscription_info

    for uid in grandfather_ids:
        info = await get_subscription_info(
            user={"user_id": uid, "email": "x@y"}, db_path=db,
        )
        assert info.is_premium is True, f"user {uid} lost premium post-beta"
        assert info.plan == "pro_monthly"
        assert info.status == "active"
        # trial_available must be False - otherwise Settings renders a
        # "Start Trial" button that would downgrade the user.
        assert info.trial_available is False, (
            f"user {uid} still shows trial_available=True post-beta - "
            "Settings will render a downgrade button"
        )
        # All four daily caps still at the Pro values.
        from src.web.constants import (
            PRO_IMAGE_LIMIT,
            PRO_TEXT_MEAL_LIMIT,
            PRO_CHAT_LIMIT,
            PRO_AI_EDITS_PER_MEAL,
        )
        assert info.image_queries_limit == PRO_IMAGE_LIMIT
        assert info.text_meals_limit == PRO_TEXT_MEAL_LIMIT
        assert info.chats_limit == PRO_CHAT_LIMIT
        assert info.meal_edits_limit == PRO_AI_EDITS_PER_MEAL


@pytest.mark.asyncio
async def test_beta_grandfather_start_trial_refused_post_beta(mode_env, db):
    """A grandfather calling start_trial() post-beta must be refused.

    This is the second line of defense if the frontend ever leaks a
    trial button. Even if the user directly POSTs to /subscription/trial,
    start_trial() must return False because trial_used=1 is already set.
    """
    mode_env("hosted", True)
    uid = await create_web_user(db, "grandpa@example.com", first_name="G")
    from src.db import create_pro_subscription
    await create_pro_subscription(db, uid, is_og=False)

    mode_env("hosted", False)

    # start_trial should refuse because trial_used=1 from the beta signup.
    result = await start_trial(db, uid)
    assert result is False, (
        "start_trial must refuse grandfathers or it will downgrade them "
        "from plan='pro_monthly' to plan='trial' with a 7-day timer"
    )

    # Subscription row untouched - still Pro.
    sub = await get_subscription(db, uid)
    assert sub["plan"] == "pro_monthly"
    assert sub["status"] == "active"


@pytest.mark.asyncio
async def test_og_users_survive_beta_mode_off(mode_env, db):
    """OG users stay Pro post-beta even if their trial_used is somehow 0.

    OG status is the strongest form of grandfathering - it bypasses
    renewal checks entirely (see get_subscription_info hosted+no-beta
    branch). This test double-checks by building a deliberately broken
    row (is_og=1, plan='trial', past trial_ends_at) and verifying the
    OG bypass kicks in.
    """
    mode_env("hosted", True)
    await ensure_user(db, 1, "og@example.com", "OG")
    from src.db import create_or_update_subscription
    await create_or_update_subscription(
        db, 1,
        plan="trial", status="trialing",
        trial_ends_at="2020-01-01 00:00:00",
        trial_used=1,
    )
    await set_og_status(db, 1, True)

    mode_env("hosted", False)

    from src.web.deps import get_subscription_info
    info = await get_subscription_info(
        user={"user_id": 1, "email": "og@example.com"}, db_path=db,
    )
    assert info.is_og is True
    assert info.is_premium is True
    # Presentation override: OG always looks like Pro in the UI regardless
    # of the underlying row state.
    assert info.plan == "pro_monthly"
    assert info.status == "active"


@pytest.mark.asyncio
async def test_subscription_status_endpoint_grandfather_post_beta(mode_env, db):
    """GET /subscription for a grandfather post-beta returns a clean Pro
    response - no trial_available, no beta_mode flag, no upgrade CTA
    state. This exercises the full deps → route → schema pipeline."""
    mode_env("hosted", True)
    uid = await create_web_user(db, "api_gp@example.com", first_name="API")
    from src.db import create_pro_subscription
    await create_pro_subscription(db, uid, is_og=False)

    mode_env("hosted", False)

    app.dependency_overrides[get_db_path] = lambda: db
    _override_current_user(uid, "api_gp@example.com")

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-Requested-With": "MacroApp"},
        ) as c:
            res = await c.get(SUB_API)
            assert res.status_code == 200
            body = res.json()
            assert body["is_premium"] is True
            assert body["plan"] == "pro_monthly"
            assert body["status"] == "active"
            assert body["beta_mode"] is False
            assert body["trial_available"] is False, (
                "grandfather status must not offer a trial CTA"
            )
    finally:
        app.dependency_overrides.pop(get_db_path, None)
        app.dependency_overrides.pop(get_current_user, None)
