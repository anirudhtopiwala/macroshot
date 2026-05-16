"""Unit tests for src.web.constants - limit_message helper copy."""

from __future__ import annotations

import pytest


def _with_beta_mode(flag: bool):
    """Flip BETA_MODE in the constants module for the duration of one test.

    `limit_message` reads the module-level BETA_MODE at call time, so we
    only need to flip it here (unlike the gate tests which need to patch
    the name in every consumer that imported it by name).
    """
    import src.web.constants as _constants

    return _constants, _constants.BETA_MODE


@pytest.fixture
def beta_copy():
    import src.web.constants as _constants
    old = _constants.BETA_MODE
    _constants.BETA_MODE = True
    yield _constants.limit_message
    _constants.BETA_MODE = old


@pytest.fixture
def non_beta_copy():
    import src.web.constants as _constants
    old = _constants.BETA_MODE
    _constants.BETA_MODE = False
    yield _constants.limit_message
    _constants.BETA_MODE = old


class TestLimitMessage:
    """limit_message produces consistent copy that switches on BETA_MODE."""

    def test_beta_copy_for_image_analysis(self, beta_copy):
        msg = beta_copy("image_analysis", 5)
        assert "5 photo scans" in msg
        assert "free beta" in msg
        assert "paid tier" in msg
        assert "today" in msg  # default scope

    def test_beta_copy_for_text_meal(self, beta_copy):
        msg = beta_copy("text_meal", 10)
        assert "10 text meal entries" in msg
        assert "free beta" in msg

    def test_beta_copy_for_ai_chat(self, beta_copy):
        msg = beta_copy("ai_chat", 10)
        assert "10 AI coach chats" in msg
        assert "free beta" in msg

    def test_beta_copy_for_meal_edit_with_scope(self, beta_copy):
        msg = beta_copy("meal_edit", 10, scope="on this meal")
        assert "on this meal" in msg
        assert "10 AI meal edits" in msg
        assert "today" not in msg  # scope replaced default
        assert "free beta" in msg

    def test_non_beta_copy_says_upgrade_to_pro(self, non_beta_copy):
        msg = non_beta_copy("image_analysis", 3)
        assert "free beta" not in msg
        assert "Upgrade to Pro" in msg
        assert "3 photo scans" in msg

    def test_unknown_feature_falls_through_to_requests(self, beta_copy):
        msg = beta_copy("something_new", 42)
        # Unknown key → generic "requests" label
        assert "42 requests" in msg
        assert "free beta" in msg

    def test_scope_substitution_changes_wording(self, beta_copy):
        today_msg = beta_copy("meal_edit", 10, scope="today")
        meal_msg = beta_copy("meal_edit", 10, scope="on this meal")
        assert "today" in today_msg
        assert "on this meal" in meal_msg
        assert today_msg != meal_msg
