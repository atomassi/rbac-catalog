"""Rate limiting configuration."""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter

from azurerbac.settings import Settings


def get_real_client_ip(request: Request) -> str:
    """Resolve the client IP used as the rate-limit key.

    ``CF-Connecting-IP`` / ``X-Forwarded-For`` are client-supplied and only
    trustworthy behind the expected proxy (Cloudflare + Azure App Service),
    so we honor them only in deployed environments. Otherwise (local or
    self-hosted without that proxy) we use the socket peer to avoid trivial
    rate-limit bypass via header spoofing. In all cases we fall back to the
    socket peer when no trusted header is present.
    """
    if Settings.get().is_deployed:
        if cf_ip := request.headers.get("CF-Connecting-IP"):
            return cf_ip
        if xff := request.headers.get("X-Forwarded-For"):
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=get_real_client_ip)
