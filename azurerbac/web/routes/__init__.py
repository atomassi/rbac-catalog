"""Routes package."""

from azurerbac.web.routes.api import router as api_router
from azurerbac.web.routes.dashboard import router as dashboard_router
from azurerbac.web.routes.feeds import router as feeds_router
from azurerbac.web.routes.health import router as health_router
from azurerbac.web.routes.pages import router as pages_router
from azurerbac.web.routes.static import router as static_router

__all__ = [
    "api_router",
    "dashboard_router",
    "feeds_router",
    "health_router",
    "pages_router",
    "static_router",
]
