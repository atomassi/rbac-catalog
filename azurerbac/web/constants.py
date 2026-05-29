"""Web route constants."""

from typing import Final

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
RECOMMEND_ROLES_RATE_LIMIT_PER_MINUTE: Final = 60
OPERATIONS_SEARCH_RATE_LIMIT_PER_MINUTE: Final = 60
MAX_QUERY_LENGTH: Final = 100
MAX_SEARCH_LIMIT: Final = 100

CACHE_BROWSER_SHORT: Final = 120
CACHE_CDN_MEDIUM: Final = 600
CACHE_CDN_LONG: Final = 1800
CACHE_STALE_REVALIDATE: Final = 1200

CACHE_HEADER_NONE: Final = "no-store"
CACHE_HEADER_NOT_FOUND: Final = "public, max-age=60, s-maxage=60"
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
    "frame-src 'none'; "
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

# Hard cap on inbound request bodies. The largest legitimate request is
# /api/recommend-roles with up to 100 OperationItem entries; even with
# 512-char names that's ~ 60 KB. 256 KB is a generous safety margin and
# guards against malformed Content-Length headers and JSON bombs.
MAX_REQUEST_BODY_BYTES: Final = 256 * 1024

# ---------------------------------------------------------------------------
# Decommission banner
# ---------------------------------------------------------------------------
# Banner is ENABLED via ``Settings.decommission_banner_enabled`` (env var
# ``DECOMMISSION_BANNER_ENABLED``). Default is False so a local dev run
# never sees the notice; production sets the env var to ``true``.
#
# Bump ``DECOMMISSION_BANNER_VERSION`` whenever the message text materially
# changes — the dismiss state is keyed on it, so users who dismissed the
# old text will see the new one.
#
# Both the message HTML and the optional feedback URL are constants here
# (not env vars) because they should travel with the deployment and be
# code-reviewed.
DECOMMISSION_BANNER_VERSION: Final[str] = "2026-05-decommission-v4"
DECOMMISSION_BANNER_FEEDBACK_URL: Final[str] = "https://forms.gle/N323bjAWGKJzUWb49"
DECOMMISSION_BANNER_MESSAGE: Final[str] = (
    "This site is being decommissioned on <strong>June 12, 2026</strong>. "
    "It's "
    '<a href="https://github.com/atomassi/rbac-catalog" '
    'class="underline font-medium hover:no-underline">open source</a>, '
    "so you can host your own copy."
)
