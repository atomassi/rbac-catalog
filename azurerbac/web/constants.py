"""Web route constants."""

from typing import Final

from azurerbac.core.constants import NEW_DOMAIN, SITE_URL

__all__ = ["NEW_DOMAIN", "SITE_URL"]  # Backwards-compatible re-exports

OLD_DOMAINS: Final = frozenset(
    {
        "azurerbac-builtinroles.azurewebsites.net",
        "azurerbac-builtinroles-atgjgtc9a9exbahe.z02.azurefd.net",
        "azure-rbac-catalog.org",
        "www.azure-rbac-catalog.org",
    }
)

HEALTH_PATHS: Final = frozenset({"/healthz", "/version"})

MAX_PAGE_SIZE: Final = 1000
MAX_PAGE_NUMBER: Final = 10000
MAX_DAYS: Final = 365
DEFAULT_PAGE: Final = 1
DEFAULT_LIMIT: Final = 25
DEFAULT_DAYS: Final = 30
MAX_ROLE_EVENTS: Final = 200

MIN_SEARCH_CHARS: Final = 2
MIN_AI_QUERY_CHARS: Final = 3
AI_RATE_LIMIT_PER_MINUTE: Final = 10
MAX_QUERY_LENGTH: Final = 100
MAX_SEARCH_LIMIT: Final = 100
MAX_OPERATIONS: Final = 100

CACHE_BROWSER_SHORT: Final = 120
CACHE_CDN_MEDIUM: Final = 600
CACHE_CDN_LONG: Final = 1800
CACHE_STALE_REVALIDATE: Final = 1200

CACHE_HEADER_NONE: Final = "no-store"
CACHE_HEADER_STATIC: Final = "public, max-age=31536000, immutable"
CACHE_HEADER_MAIN_PAGE: Final = (
    f"public, max-age={CACHE_BROWSER_SHORT}, s-maxage={CACHE_CDN_MEDIUM}, "
    f"stale-while-revalidate={CACHE_STALE_REVALIDATE}"
)
CACHE_HEADER_DETAIL_PAGE: Final = (
    f"public, max-age={CACHE_BROWSER_SHORT}, s-maxage={CACHE_CDN_LONG}, "
    f"stale-while-revalidate={CACHE_CDN_LONG * 2}"
)
VARY_ENCODING: Final = "Accept-Encoding"

CSP_HEADER: Final = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
    "cdn.jsdelivr.net static.cloudflareinsights.com; "
    "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
    "font-src 'self' fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self' cdn.jsdelivr.net cloudflareinsights.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "upgrade-insecure-requests"
)

# Cross-Origin-Opener-Policy header for origin isolation
COOP_HEADER: Final = "same-origin"

PERMISSIONS_POLICY_HEADER: Final = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(), usb=(), interest-cohort=()"
)

GZIP_MIN_SIZE: Final = 500
