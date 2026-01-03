"""Routes package for the Azure RBAC Catalog web application.

This package contains all route handlers organized by functionality:
- api: JSON API endpoints for role recommendation
- dashboard: Dashboard rendering for /recent and /roles pages
- health: Health check endpoint
- pages: Page handlers for role details, operations, recommend, about
- static: Static content routes (robots.txt, sitemap, favicons)
"""

from azurerbac.web.routes.api import router as api_router
from azurerbac.web.routes.dashboard import router as dashboard_router
from azurerbac.web.routes.health import router as health_router
from azurerbac.web.routes.pages import router as pages_router
from azurerbac.web.routes.static import router as static_router

__all__ = [
    "api_router",
    "dashboard_router",
    "health_router",
    "pages_router",
    "static_router",
]
