"""Background job worker for scheduled Azure data synchronization."""

from __future__ import annotations

import asyncio
import logging
import signal
import time as time_module
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from azurerbac.azure import fetch_builtin_roles, fetch_provider_operations
from azurerbac.backgroundjobs.exceptions import EmptyFetchResultError
from azurerbac.backgroundjobs.models import (
    RoleScanResult,
    ScanResult,
)
from azurerbac.backgroundjobs.operations_monitor import apply_operations_scan
from azurerbac.backgroundjobs.roles_monitor import apply_role_scan
from azurerbac.core import (
    DBEngine,
    create_sessionmaker,
    ensure_db,
)
from azurerbac.settings import Settings
from azurerbac.telemetry import (
    WorkerOperationContext,
    configure_logging,
    track_worker_result,
)

logger = logging.getLogger("azurerbac.worker")


@dataclass(frozen=True, slots=True)
class JobSpec:
    name: str
    enabled: bool
    fetch_label: str
    fetch: Callable[[], Awaitable[list[Any]]]
    apply: Callable[[Any, list[Any]], Awaitable[ScanResult]]
    on_success: Callable[[float, list[Any], ScanResult], None]
    interval_seconds: int


def _create_job_specs(settings: Settings) -> list[JobSpec]:
    """Create job specifications based on settings."""
    from azurerbac.telemetry import track_operations_scan, track_role_scan

    def _on_roles_success(elapsed: float, roles: list[Any], stats: ScanResult) -> None:
        # stats is always RoleScanResult here
        assert isinstance(stats, RoleScanResult)
        track_role_scan(
            duration_seconds=elapsed,
            roles_fetched=len(roles),
            roles_added=stats.created,
            roles_updated=stats.updated,
            roles_deleted=stats.deleted,
        )

    def _on_operations_success(
        elapsed: float, operations: list[Any], stats: ScanResult
    ) -> None:  # pylint: disable=unused-argument
        track_operations_scan(elapsed, len(operations))

    return [
        JobSpec(
            name="role-scan",
            enabled=settings.role_scan_enabled,
            fetch_label="Fetching built-in role definitions...",
            fetch=fetch_builtin_roles,
            apply=apply_role_scan,
            on_success=_on_roles_success,
            interval_seconds=settings.roles_poll_interval_seconds,
        ),
        JobSpec(
            name="operations-scan",
            enabled=settings.operations_scan_enabled,
            fetch_label="Fetching Azure provider operations...",
            fetch=fetch_provider_operations,
            apply=apply_operations_scan,
            on_success=_on_operations_success,
            interval_seconds=settings.operations_poll_interval_seconds,
        ),
    ]


def _setup_scheduler(
    jobs: list[JobSpec],
    run_job: Callable[[JobSpec], Awaitable[None]],
) -> AsyncIOScheduler:
    """Create and configure the scheduler with job intervals."""
    scheduler = AsyncIOScheduler()
    for job in jobs:
        if not job.enabled:
            logger.info("Skipping disabled job: %s", job.name)
            continue

        scheduler.add_job(
            run_job,
            "interval",
            id=job.name,
            seconds=job.interval_seconds,
            args=(job,),
        )
        logger.info("Scheduled job: %s (every %ss)", job.name, job.interval_seconds)
    return scheduler


def _setup_shutdown_handler(shutdown_event: asyncio.Event) -> None:
    """Configure signal handlers for graceful shutdown."""

    def handle_shutdown(signum: int, _frame: Any) -> None:
        signame = signal.Signals(signum).name
        if shutdown_event.is_set():
            logger.warning("Force exiting on repeated %s", signame)
            raise SystemExit(1)
        logger.info("Received %s, shutting down...", signame)
        shutdown_event.set()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)


async def _run_startup_jobs(
    settings: Settings,
    jobs_by_name: dict[str, JobSpec],
    run_job: Callable[[JobSpec], Awaitable[None]],
) -> None:
    """Run optional startup jobs based on settings."""
    if settings.run_roles_scan_on_startup:
        await run_job(jobs_by_name["role-scan"])
    if settings.run_operations_scan_on_startup:
        await run_job(jobs_by_name["operations-scan"])


async def _cleanup(scheduler: AsyncIOScheduler | None) -> None:
    """Clean up resources on shutdown."""
    if scheduler is not None:
        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=True)
    logger.info("Disposing database engine...")
    await DBEngine.dispose()
    logger.info("Worker shutdown complete")


@dataclass(slots=True)
class JobRunner:
    """Encapsulates job execution with telemetry and error handling."""

    session_factory: async_sessionmaker

    async def execute_with_telemetry(
        self,
        operation_name: str,
        work: Callable[[], Awaitable[Any]],
        *,
        on_success: Callable[[float, Any], None] | None = None,
    ) -> Any | None:
        """Execute work with telemetry tracking and error handling."""
        with WorkerOperationContext(operation_name):
            start_time = time_module.time()
            try:
                result = await work()
                elapsed = time_module.time() - start_time
                if on_success is not None:
                    on_success(elapsed, result)
                track_worker_result(operation_name, "success", elapsed)
                return result
            except Exception as e:
                elapsed = time_module.time() - start_time
                logger.exception("Failed to run %s: %s", operation_name, e)
                track_worker_result(operation_name, "failure", elapsed, str(e))
                return None

    async def run_job(self, spec: JobSpec) -> None:
        """Execute a job specification."""
        if not spec.enabled:
            logger.info("Job disabled: %s", spec.name)
            return

        logger.info("Starting job: %s", spec.name)

        async def work() -> tuple[list[Any], ScanResult]:
            logger.info("%s", spec.fetch_label)
            items = await spec.fetch()
            logger.info("Fetched %s items", len(items))

            # Azure should always return built-in roles and operations.
            # Zero results indicates an API issue, auth problem, or misconfiguration.
            if not items:
                logger.error(
                    "Fetch returned 0 items for %s - possible API/auth issue",
                    spec.name,
                )
                raise EmptyFetchResultError(spec.name)

            async with self.session_factory() as session:
                stats = await spec.apply(session, items)
            return items, stats

        def on_success(elapsed: float, result: tuple[list[Any], ScanResult]) -> None:
            items, stats = result
            logger.info("%s complete: %s (took %.2fs)", spec.name, stats, elapsed)
            spec.on_success(elapsed, items, stats)

        await self.execute_with_telemetry(spec.name, work, on_success=on_success)


async def main() -> None:
    load_dotenv()
    settings = Settings.get()
    configure_logging("worker")

    logger.info("Azure RBAC Worker starting with configuration:")
    logger.info(
        "  Role scan enabled: %s (interval: %ds)",
        settings.role_scan_enabled,
        settings.roles_poll_interval_seconds,
    )
    logger.info(
        "  Operations scan enabled: %s (interval: %ds)",
        settings.operations_scan_enabled,
        settings.operations_poll_interval_seconds,
    )
    logger.info("  Run role scan on startup: %s", settings.run_roles_scan_on_startup)
    logger.info("  Run operations scan on startup: %s", settings.run_operations_scan_on_startup)

    engine = DBEngine.get()
    session_local = create_sessionmaker(engine)

    # Create job runner with session factory
    runner = JobRunner(session_factory=session_local)

    jobs = _create_job_specs(settings)
    jobs_by_name = {job.name: job for job in jobs}
    scheduler: AsyncIOScheduler | None = None

    try:
        await ensure_db(engine)
        scheduler = _setup_scheduler(jobs, runner.run_job)
        scheduler.start()
        logger.info(
            "Worker started; role scan every %ss, operations scan every %ss",
            settings.roles_poll_interval_seconds,
            settings.operations_poll_interval_seconds,
        )

        await _run_startup_jobs(settings, jobs_by_name, runner.run_job)

        shutdown_event = asyncio.Event()
        _setup_shutdown_handler(shutdown_event)
        await shutdown_event.wait()
    finally:
        await _cleanup(scheduler)


if __name__ == "__main__":
    asyncio.run(main())
