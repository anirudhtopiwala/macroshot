"""Global test configuration. Loaded before any test module imports."""
import os

import pytest

# Default env vars for tests. These are set BEFORE src.web.app is imported so
# load_dotenv (which doesn't override existing keys) can't pull in the prod
# values from a developer's local .env.
os.environ.setdefault("JWT_SECRET", "test-secret-key-padding-to-meet-32-char-minimum")
os.environ.setdefault("TZ", "America/Los_Angeles")
# httpx AsyncClient defaults to base_url="http://test". Force DOMAIN=test so
# TrustedHostMiddleware allows that Host header.
os.environ["DOMAIN"] = "test"
os.environ.setdefault("APP_ENV", "development")

# Suppress Sentry during tests. The VM's .env carries SENTRY_BACKEND_DSN, so
# without this the daily-bugfix cron's pytest run reports every test-induced
# exception to Sentry tagged environment=production.
os.environ["SENTRY_BACKEND_DSN"] = ""

# Disable rate limiting in tests to prevent cross-test interference.
# Must be done after importing the app since slowapi reads RATELIMIT_ENABLED
# as a raw string (truthy) rather than a boolean.
from src.web.rate_limit import limiter
limiter.enabled = False


@pytest.fixture(autouse=True)
def _no_resend_emails(monkeypatch):
    """Prevent tests from sending real emails via Resend.

    load_dotenv() in src/web/app.py sets RESEND_API_KEY from .env at import
    time, so clearing os.environ is insufficient - we must patch the cached
    module-level variable in every module that reads it at import time.

    We also stub resend.Emails.send itself as a safety net so any future
    module that forgets to guard on RESEND_API_KEY still can't burn quota.
    """
    monkeypatch.setattr("src.web.auth.RESEND_API_KEY", "")
    monkeypatch.setattr("src.web.email_scheduler.RESEND_API_KEY", "")

    try:
        import resend
        monkeypatch.setattr(
            resend.Emails, "send",
            lambda *a, **kw: {"id": "test-blocked"},
        )
    except ImportError:
        pass
