"""Job execution helper for the one-shot scan runner (scan_once).

Scans run as cron-triggered Container Apps Jobs; the platform owns scheduling,
so there is no in-process scheduler here.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from rbaccatalog.backgroundjobs.jobs import JobResult
from rbaccatalog.telemetry import WorkerOperationContext, track_worker_result

if TYPE_CHECKING:
    from rbaccatalog.backgroundjobs.jobs import Job

logger = logging.getLogger("rbaccatalog.worker")


class Worker:
    """Executes scan jobs with telemetry tracking."""

    async def run_job(self, job: Job, *, reraise: bool = False) -> None:
        """Execute a job with telemetry tracking.

        Set ``reraise=True`` so one-shot runners exit non-zero on failure;
        the default (``False``) logs the failure and returns normally.
        """
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
                if reraise:
                    raise
