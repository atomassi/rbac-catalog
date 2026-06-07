"""Routes package."""

from rbaccatalog.web.routes.analytics import router as analytics_router
from rbaccatalog.web.routes.api import router as api_router
from rbaccatalog.web.routes.dashboard import router as dashboard_router
from rbaccatalog.web.routes.feeds import router as feeds_router
from rbaccatalog.web.routes.health import router as health_router
from rbaccatalog.web.routes.pages import router as pages_router
from rbaccatalog.web.routes.static import router as static_router

__all__ = [
    "analytics_router",
    "api_router",
    "dashboard_router",
    "feeds_router",
    "health_router",
    "pages_router",
    "static_router",
]
