"""Shared rate limiter instance for all routes."""

import ipaddress
import logging

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger("macro_app")

# Cloudflare's published IPv4 and IPv6 egress ranges (https://www.cloudflare.com/ips/).
# Only requests originating from one of these subnets are allowed to set
# the CF-Connecting-IP header - otherwise an attacker hitting the origin
# directly could spoof their IP, defeat rate limiting per IP, and brute
# force unauthenticated endpoints (PIN send, Google auth).
_CLOUDFLARE_RANGES = [
    ipaddress.ip_network(n) for n in (
        # IPv4
        "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22",
        "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18",
        "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22",
        "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
        "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
        # IPv6
        "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32",
        "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
]


def _is_cloudflare_source(client_host: str | None) -> bool:
    if not client_host:
        return False
    try:
        ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    return any(ip in net for net in _CLOUDFLARE_RANGES)


def _get_real_ip(request: Request) -> str:
    """Get the real client IP - never trusting client-supplied headers blindly.

    Trust CF-Connecting-IP only if the immediate TCP source is in
    Cloudflare's published egress range.  Otherwise an attacker who can
    reach the origin directly (or anyone making a curl request to the
    VM's public IP) can spoof CF-Connecting-IP and defeat rate limiting.
    """
    client_host = request.client.host if request.client else None
    if _is_cloudflare_source(client_host):
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
    # Direct (non-Cloudflare) connection - use the actual TCP source.
    return get_remote_address(request)


def _rate_limit_key(request: Request) -> str:
    """Per-user rate limit key (JWT user_id), fallback to real IP for unauthenticated."""
    from src.web.auth import decode_jwt
    # Prefer the new host-prefixed cookie; fall back to legacy during transition.
    cookie = (
        request.cookies.get("__Host-macro_session")
        or request.cookies.get("macro_session")
    )
    if cookie:
        payload = decode_jwt(cookie)
        if payload and payload.get("sub"):
            return f"user:{payload['sub']}"
    return _get_real_ip(request)


limiter = Limiter(key_func=_rate_limit_key)
