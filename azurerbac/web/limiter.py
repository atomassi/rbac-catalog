"""Rate limiting configuration for the Azure RBAC Catalog API.

Cloudflare free tier only allows 1 rate limiting rule (global protection).
SlowAPI adds per-endpoint limits as defense in depth.
Uses in-memory storage - fine for single instance deployment, no Redis needed.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter


def get_real_client_ip(request: Request) -> str:
    """Get real client IP from Cloudflare headers.

    Cloudflare adds CF-Connecting-IP header with the original client IP.
    Falls back to X-Forwarded-For (first IP) then direct client connection.
    """
    if cf_ip := request.headers.get("CF-Connecting-IP"):
        return cf_ip
    if xff := request.headers.get("X-Forwarded-For"):
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Single limiter instance shared across all routes
# Uses Cloudflare headers to get the real client IP
limiter = Limiter(key_func=get_real_client_ip)
