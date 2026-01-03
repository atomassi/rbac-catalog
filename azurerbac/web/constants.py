"""Shared constants for web routes."""

from typing import Final

# Domain configuration
NEW_DOMAIN: Final = "rbac-catalog.dev"
SITE_URL: Final = f"https://{NEW_DOMAIN}"
OLD_DOMAINS: Final = frozenset(
    {
        "azurerbac-builtinroles.azurewebsites.net",
        "azurerbac-builtinroles-atgjgtc9a9exbahe.z02.azurefd.net",
        "azure-rbac-catalog.org",
        "www.azure-rbac-catalog.org",
    }
)

# Paths that should never be redirected (health checks, probes)
HEALTH_PATHS: Final = frozenset({"/healthz", "/version"})

# Pagination and date bounds
MAX_PAGE_SIZE: Final = 1000
MAX_PAGE_NUMBER: Final = 10000
MAX_DAYS: Final = 365

# Default values for pagination and filters
DEFAULT_PAGE: Final = 1
DEFAULT_LIMIT: Final = 25
DEFAULT_DAYS: Final = 15
DEFAULT_FROM_PAGE: Final = "recent"

# Query and search limits
MAX_ROLE_EVENTS: Final = 200

# API request validation thresholds
MIN_SEARCH_CHARS: Final = 2
MIN_AI_QUERY_CHARS: Final = 3
MAX_QUERY_LENGTH: Final = 500
MAX_SEARCH_LIMIT: Final = 500
MAX_TOP_K: Final = 20

# Cache durations (in seconds)
CACHE_BROWSER_SHORT: Final = 120  # 2 minutes
CACHE_CDN_MEDIUM: Final = 600  # 10 minutes
CACHE_CDN_LONG: Final = 1800  # 30 minutes
CACHE_STALE_REVALIDATE: Final = 1200  # 20 minutes

# Cache-Control header values
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

# Security header values
CSP_HEADER: Final = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
    "font-src 'self' fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)
PERMISSIONS_POLICY_HEADER: Final = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(), usb=(), interest-cohort=()"
)

# GZip compression threshold
GZIP_MIN_SIZE: Final = 500
