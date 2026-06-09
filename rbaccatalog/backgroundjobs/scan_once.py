"""One-shot scan runner for local catalog population and scheduled jobs.

Runs one or more scans and exits; there is no in-process scheduler. Locally this
is a one-off way to populate the catalog without leaving the background worker
running. In production each scan runs as its own scheduled job that invokes this
entry point for that single scan. A failed scan propagates as a non-zero exit
code so the caller (or job runner) can mark the run failed.

Usage:
    python -m rbaccatalog.backgroundjobs.scan_once              # run all scans
    python -m rbaccatalog.backgroundjobs.scan_once role-scan
    python -m rbaccatalog.backgroundjobs.scan_once operations-scan
"""

from __future__ import annotations

import argparse
import asyncio

from dotenv import load_dotenv

from rbaccatalog.backgroundjobs.jobs import (
    JOB_FACTORIES,
    Job,
    create_all_jobs,
    create_job,
)
from rbaccatalog.backgroundjobs.worker import Worker
from rbaccatalog.core import DBEngine, ensure_db
from rbaccatalog.telemetry import configure_logging


async def run_once(jobs: list[Job]) -> None:
    """Run the given scan jobs in order, then dispose the database engine.

    Each job runs through the worker, so it gets the same telemetry and
    operation context as the scheduled path, with ``reraise=True`` so the first
    failure surfaces as a non-zero exit code.

    Args:
        jobs: The scan jobs to run, in order.
    """
    worker = Worker()
    try:
        await ensure_db(DBEngine.get())
        for job in jobs:
            await worker.run_job(job, reraise=True)
    finally:
        await DBEngine.dispose()


def select_jobs(name: str | None) -> list[Job]:
    """Return all jobs, or just the one matching ``name`` (raises KeyError if unknown)."""
    return create_all_jobs() if name is None else [create_job(name)]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments, validating the job name against the registry."""
    parser = argparse.ArgumentParser(
        prog="python -m rbaccatalog.backgroundjobs.scan_once",
        description="Run a single Azure RBAC catalog scan and exit.",
    )
    parser.add_argument(
        "job",
        nargs="?",
        choices=sorted(JOB_FACTORIES),
        help="The scan to run. Omit to run all scans.",
    )
    return parser.parse_args(argv)


async def main() -> None:
    """Entry point: run the scan named on the command line, then exit."""
    load_dotenv()
    configure_logging("worker")
    args = parse_args()
    await run_once(select_jobs(args.job))


if __name__ == "__main__":
    asyncio.run(main())
