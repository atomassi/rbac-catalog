"""Background job worker for scheduled Azure data synchronization."""

import asyncio
import logging
import signal
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

from azurerbac.backgroundjobs.jobs import Job, JobResult, create_jobs
from azurerbac.core import (
    DBEngine,
    ensure_db,
)
from azurerbac.settings import Settings
from azurerbac.telemetry import (
    WorkerOperationContext,
    configure_logging,
    track_worker_result,
)

logger = logging.getLogger("azurerbac.worker")


class Worker:
    """Background job worker that schedules and executes Azure data sync jobs."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize worker with optional settings override."""
        self._settings = settings or Settings.get()
        self._jobs: list[Job] = []
        self._jobs_by_name: dict[str, Job] = {}
        self._scheduler: AsyncIOScheduler | None = None
        self._shutdown_event: asyncio.Event | None = None

    def _setup_scheduler(self) -> AsyncIOScheduler:
        """Create and configure the scheduler with job intervals."""
        scheduler = AsyncIOScheduler()
        for job in self._jobs:
            if not job.enabled:
                logger.info("Skipping disabled job: %s", job.name)
                continue

            scheduler.add_job(
                self.run_job,
                "interval",
                id=job.name,
                seconds=job.interval.total_seconds(),
                args=(job,),
            )
            logger.info("Scheduled job: %s (every %s)", job.name, job.interval)
        return scheduler

    def _setup_shutdown_handler(self) -> None:
        """Configure signal handlers for graceful shutdown.

        Uses ``loop.add_signal_handler`` so the handler runs on the event
        loop thread. ``signal.signal`` callbacks fire from an arbitrary
        thread and cannot safely call :py:meth:`asyncio.Event.set`, which
        can lose the wakeup and leave the worker hanging on Ctrl-C /
        SIGTERM (typical container shutdown).
        """
        if self._shutdown_event is None:
            return

        shutdown_event = self._shutdown_event
        loop = asyncio.get_running_loop()

        def handle_shutdown(signum: int) -> None:
            signame = signal.Signals(signum).name
            if shutdown_event.is_set():
                logger.warning("Force exiting on repeated %s", signame)
                raise SystemExit(1)
            logger.info("Received %s, shutting down...", signame)
            shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, handle_shutdown, sig)
            except NotImplementedError:
                # Windows event loops do not support add_signal_handler;
                # fall back to the threaded signal.signal path.
                signal.signal(sig, lambda s, _f: handle_shutdown(s))

    async def _run_startup_jobs(self) -> None:
        """Run optional startup jobs based on settings."""
        if self._settings.run_roles_scan_on_startup:
            await self.run_job(self._jobs_by_name["role-scan"])
        if self._settings.run_operations_scan_on_startup:
            await self.run_job(self._jobs_by_name["operations-scan"])

    async def _cleanup(self) -> None:
        """Clean up resources on shutdown."""
        if self._scheduler is not None:
            logger.info("Shutting down scheduler...")
            self._scheduler.shutdown(wait=True)
        logger.info("Disposing database engine...")
        await DBEngine.dispose()
        logger.info("Worker shutdown complete")

    async def run_job(self, job: Job) -> None:
        """Execute a job with telemetry tracking."""
        if not job.enabled:
            logger.info("Job disabled: %s", job.name)
            return

        logger.info("Starting job: %s", job.name)

        with WorkerOperationContext(job.name):
            start_time = time.perf_counter()
            try:
                await job.run()
                elapsed = time.perf_counter() - start_time
                logger.info("%s completed (took %.2fs)", job.name, elapsed)
                track_worker_result(job.name, JobResult.SUCCESS, elapsed)
            except Exception as e:
                elapsed = time.perf_counter() - start_time
                logger.exception("Failed to run %s: %s", job.name, e)
                track_worker_result(job.name, JobResult.FAILURE, elapsed, str(e))

    def _log_configuration(self) -> None:
        """Log worker configuration at startup."""
        logger.info("Azure RBAC Worker starting with configuration:")
        logger.info(
            "  Role scan enabled: %s (interval: %ds)",
            self._settings.role_scan_enabled,
            self._settings.roles_poll_interval_seconds,
        )
        logger.info(
            "  Operations scan enabled: %s (interval: %ds)",
            self._settings.operations_scan_enabled,
            self._settings.operations_poll_interval_seconds,
        )
        logger.info("  Run role scan on startup: %s", self._settings.run_roles_scan_on_startup)
        logger.info(
            "  Run operations scan on startup: %s",
            self._settings.run_operations_scan_on_startup,
        )

    async def start(self) -> None:
        """Start the worker and run until shutdown signal received."""
        self._log_configuration()

        self._jobs = create_jobs()
        self._jobs_by_name = {job.name: job for job in self._jobs}

        try:
            await ensure_db(DBEngine.get())
            self._scheduler = self._setup_scheduler()
            self._scheduler.start()
            logger.info(
                "Worker started; role scan every %ss, operations scan every %ss",
                self._settings.roles_poll_interval_seconds,
                self._settings.operations_poll_interval_seconds,
            )

            await self._run_startup_jobs()

            self._shutdown_event = asyncio.Event()
            self._setup_shutdown_handler()
            await self._shutdown_event.wait()
        finally:
            await self._cleanup()


async def main() -> None:
    """Entry point for the worker process."""
    load_dotenv()
    configure_logging("worker")
    worker = Worker()
    await worker.start()


if __name__ == "__main__":
    asyncio.run(main())
