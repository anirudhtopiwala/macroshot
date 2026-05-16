"""Web Push notification sender using pywebpush."""

import asyncio
import json
import logging
import os

from pywebpush import webpush, WebPushException

logger = logging.getLogger("macro_app")

VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_CLAIM_EMAIL = os.environ.get("VAPID_CLAIM_EMAIL", "mailto:noreply@example.com")


def get_vapid_public_key() -> str:
    return VAPID_PUBLIC_KEY


async def send_push_notification(
    subscription_info: dict,
    title: str,
    body: str,
    url: str = "/macro_app/",
    tag: str = "",
) -> bool | None:
    """Send a push notification.

    Returns:
        True  -- sent successfully
        False -- subscription expired (404/410), caller should delete it
        None  -- transient/config error, caller should NOT delete
    """
    if not VAPID_PRIVATE_KEY:
        logger.warning("VAPID_PRIVATE_KEY not configured, skipping push")
        return None

    payload = json.dumps({
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
        "icon": "/macro_app/icons/icon-192.png",
    })

    try:
        await asyncio.to_thread(
            webpush,
            subscription_info=subscription_info,
            data=payload,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
        )
        return True
    except WebPushException as e:
        if hasattr(e, "response") and e.response is not None and e.response.status_code in (404, 410):
            logger.info("Push subscription expired (status %s)", e.response.status_code)
            return False  # Expired - safe to delete
        logger.exception("Push notification failed: %s", e)
        return None  # Transient - do NOT delete
    except Exception:
        logger.exception("Unexpected error sending push notification")
        return None  # Transient - do NOT delete
