"""Azure RBAC Catalog - FastAPI Web Application.

This is the main application module that:
- Configures the FastAPI application
- Sets up database connections and caching
- Registers all route handlers from submodules
- Configures middleware (security, caching, logging)
"""

from __future__ import annotations

# IMPORTANT: Configure telemetry BEFORE importing FastAPI to enable auto-instrumentation.
# The OpenTelemetry auto-instrumentors must patch FastAPI before the module is loaded.
import os

if not os.environ.get("PYTEST_CURRENT_TEST"):
    from azurerbac.telemetry import configure_logging

    configure_logging("ux")

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException
from starlette.middleware.gzip import GZipMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from azurerbac import __version__
from azurerbac.cache import get_cache_service
from azurerbac.core import (
    DBEngine,
    Operation,
    Role,
    RoleHistory,
    RoleScanStatus,
    create_sessionmaker,
)
from azurerbac.settings import Settings, is_running_in_azure, is_running_in_pytest
from azurerbac.web.constants import GZIP_MIN_SIZE, SITE_URL
from azurerbac.web.dependencies import BaseDeps, DashboardDeps, PagesDeps
from azurerbac.web.filters import diff_lines, format_date, format_datetime, full_json_diff
from azurerbac.web.middleware import (
    add_cache_headers,
    add_security_headers,
    redirect_old_domain,
)
from azurerbac.web.routes import api as api_routes
from azurerbac.web.routes import dashboard as dashboard_routes
from azurerbac.web.routes import feeds as feeds_routes
from azurerbac.web.routes import health as health_routes
from azurerbac.web.routes import pages as pages_routes
from azurerbac.web.routes import static as static_routes
from azurerbac.web.services.startup import (
    cache_refresh_task,
    ensure_db,
    preload_cache,
    warmup_colbert,
    warmup_crossencoder,
)
from azurerbac.web.utils import slugify

# Load .env for local development only (Azure App Service sets WEBSITE_SITE_NAME)
if not is_running_in_azure() and not is_running_in_pytest():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Database and Cache Setup
# ─────────────────────────────────────────────────────────────────────────────

settings = Settings.get()

# Get singleton database engine (handles SQLite, PostgreSQL, and MSI)
engine = DBEngine.get()
SessionLocal = create_sessionmaker(engine)

# ─────────────────────────────────────────────────────────────────────────────
# Templates Setup
# ─────────────────────────────────────────────────────────────────────────────

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Add filters to templates
templates.env.filters["slugify"] = slugify
templates.env.filters["diff_lines"] = diff_lines
templates.env.filters["full_json_diff"] = full_json_diff
templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["format_date"] = format_date

# Make version available to all templates
templates.env.globals["app_version"] = __version__

# SEO: Expose canonical site URL to templates
templates.env.globals["site_url"] = SITE_URL


# ─────────────────────────────────────────────────────────────────────────────
# Application Lifespan
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # pylint: disable=unused-argument
    """Application lifespan: startup and shutdown events.

    Uses anyio task group for automatic cancellation on shutdown.
    Background tasks are only started after critical startup steps succeed.
    """
    # Critical startup steps - must complete before accepting requests
    logger.info("Starting application...")
    await ensure_db(engine)
    await preload_cache(SessionLocal)

    # Warmup AI models BEFORE accepting requests
    logger.info("Warming up AI models...")
    async with anyio.create_task_group() as warmup_tg:
        warmup_tg.start_soon(warmup_colbert, name="colbert-warmup")
        warmup_tg.start_soon(warmup_crossencoder, name="crossencoder-warmup")

    logger.info("Application startup complete, starting background tasks...")
    async with anyio.create_task_group() as tg:
        tg.start_soon(cache_refresh_task, SessionLocal, name="cache-refresh")

        yield  # Application is running

        # Explicitly cancel on shutdown - uvicorn may not exit cleanly otherwise
        logger.info("Shutting down background tasks...")
        tg.cancel_scope.cancel()

    logger.info("Background tasks cancelled")

    # Cleanup database connections and MSI authenticator
    logger.info("Disposing database engine...")
    await DBEngine.dispose()
    logger.info("Application shutdown complete")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI Application
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="Azure RBAC Built-in Role Change Monitor", lifespan=lifespan)

# Store cache accessor in app.state for access by route handlers via request.app.state
app.state.app_cache = get_cache_service().container

# Store SessionLocal on app.state so tests can patch it in one place
app.state.session_local = SessionLocal

# ─────────────────────────────────────────────────────────────────────────────
# Dependency Injection Setup
# ─────────────────────────────────────────────────────────────────────────────

# Store typed dependency containers on app.state for FastAPI dependency injection
app.state.api_deps = BaseDeps(
    app_cache=get_cache_service().container,
    SessionLocal=SessionLocal,
)

app.state.dashboard_deps = DashboardDeps(
    app_cache=get_cache_service().container,
    SessionLocal=SessionLocal,
    Role=Role,
    RoleHistory=RoleHistory,
    RoleScanStatus=RoleScanStatus,
    Operation=Operation,
    templates=templates,
)

app.state.pages_deps = PagesDeps(
    app_cache=get_cache_service().container,
    SessionLocal=SessionLocal,
    Role=Role,
    RoleHistory=RoleHistory,
    Operation=Operation,
    templates=templates,
)

# ─────────────────────────────────────────────────────────────────────────────
# Register Route Modules
# ─────────────────────────────────────────────────────────────────────────────

# Static routes (robots.txt, sitemap, favicons) - uses app.state.app_cache
app.include_router(static_routes.router)

# API routes (/api/*)
app.include_router(api_routes.router)

# Feed routes (/feeds/*, /export/*)
app.include_router(feeds_routes.router)

# Register rate limiter state and exception handler
from azurerbac.web.limiter import limiter

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

# Dashboard routes (/, /recent, /roles) - uses FastAPI dependency injection
app.include_router(dashboard_routes.router)

# Health check route (/healthz)
app.include_router(health_routes.router)

# Page routes (/roles/{id}, /operations, /recommend, /about)
# Uses FastAPI dependency injection
app.include_router(pages_routes.router)

# ─────────────────────────────────────────────────────────────────────────────
# Static Files and Middleware
# ─────────────────────────────────────────────────────────────────────────────

# Mount static files (CSS, JS, images)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Performance: GZip compression for responses > GZIP_MIN_SIZE bytes
app.add_middleware(GZipMiddleware, minimum_size=GZIP_MIN_SIZE)

# Trust proxy headers (X-Forwarded-For) to get real client IP from Cloudflare
# trusts all proxies - Safe since we restrict to Cloudflare IPs at App Service level
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

# Note: Azure Application Insights auto-instrumentation is configured via
# configure_azure_monitor() at startup - no middleware needed


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
    """Handle validation errors (invalid query params) with a friendly 400 page."""
    return templates.TemplateResponse(
        request, "400.html", {"errors": exc.errors()}, status_code=400
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    """Handle HTTP exceptions with friendly HTML pages."""
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "404.html", status_code=404)
    # Return other HTTP exceptions directly with their headers (e.g., Allow header for 405)
    return Response(
        content=exc.detail or str(exc.status_code),
        status_code=exc.status_code,
        headers=dict(exc.headers) if exc.headers else None,
    )


# Register HTTP middleware (order matters - last added runs first)
app.middleware("http")(add_cache_headers)
app.middleware("http")(add_security_headers)
app.middleware("http")(redirect_old_domain)
