"""Rate limiting configuration."""

from fastapi import Request
from slowapi import Limiter


def get_real_client_ip(request: Request) -> str:
    """Get real client IP from Cloudflare headers."""
    if cf_ip := request.headers.get("CF-Connecting-IP"):
        return cf_ip
    if xff := request.headers.get("X-Forwarded-For"):
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=get_real_client_ip)
