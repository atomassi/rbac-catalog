"""Background job definitions for Azure data synchronization."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from enum import StrEnum

from rbaccatalog.azure import fetch_builtin_roles, fetch_provider_operations
from rbaccatalog.backgroundjobs.exceptions import EmptyFetchResultError
from rbaccatalog.backgroundjobs.operations_monitor import apply_operations_scan
from rbaccatalog.backgroundjobs.roles_monitor import apply_role_scan
from rbaccatalog.core import DBEngine, create_sessionmaker
from rbaccatalog.settings import Settings
from rbaccatalog.telemetry import track_operations_scan, track_role_scan

logger = logging.getLogger("rbaccatalog.worker")


class JobResult(StrEnum):
    """Result status of a job execution."""

    SUCCESS = "success"
    FAILURE = "failure"


class Job(ABC):
    """Abstract base class for background jobs."""

    @property
    def _settings(self) -> Settings:
        return Settings.get()

    @property
    def _dry_run(self) -> bool:
        """Run in "what-if" mode (compute + log changes, write nothing)."""
        return self._settings.scan_dry_run

    @property
    @abstractmethod
    def name(self) -> str:
        """Job identifier used for scheduling and logging."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this job should run."""

    @property
    @abstractmethod
    def interval(self) -> timedelta:
        """How often to run this job."""

    @abstractmethod
    async def run(self) -> None:
        """Execute the job. Handles fetch, apply, logging, and telemetry."""


# Canonical job names, shared by the job classes and the JOB_FACTORIES registry.
ROLE_SCAN_JOB_NAME = "role-scan"
OPERATIONS_SCAN_JOB_NAME = "operations-scan"


class RoleScanJob(Job):
    """Scans and synchronizes Azure built-in role definitions."""

    _JOB_NAME = ROLE_SCAN_JOB_NAME

    @property
    def name(self) -> str:
        return self._JOB_NAME

    @property
    def enabled(self) -> bool:
        return self._settings.role_scan_enabled

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=self._settings.roles_poll_interval_seconds)

    async def run(self) -> None:
        logger.info("Fetching built-in role definitions...")
        roles = await fetch_builtin_roles()
        logger.info("Fetched %d roles", len(roles))

        if not roles:
            raise EmptyFetchResultError(self.name)

        session_factory = create_sessionmaker(DBEngine.get())
        async with session_factory() as session:
            result = await apply_role_scan(session, roles, dry_run=self._dry_run)

        logger.info("Role scan complete: %s", result)

        # Skip telemetry in what-if mode: the counts describe changes that
        # were never persisted, so emitting them would misreport real scans.
        if not self._dry_run:
            track_role_scan(
                roles_fetched=len(roles),
                roles_added=result.created,
                roles_updated=result.updated,
                roles_deleted=result.deleted,
            )


class OperationsScanJob(Job):
    """Scans and synchronizes Azure provider operations."""

    _JOB_NAME = OPERATIONS_SCAN_JOB_NAME

    @property
    def name(self) -> str:
        return self._JOB_NAME

    @property
    def enabled(self) -> bool:
        return self._settings.operations_scan_enabled

    @property
    def interval(self) -> timedelta:
        return timedelta(seconds=self._settings.operations_poll_interval_seconds)

    async def run(self) -> None:
        logger.info("Fetching Azure provider operations...")
        operations = await fetch_provider_operations()
        logger.info("Fetched %d operations", len(operations))

        if not operations:
            raise EmptyFetchResultError(self.name)

        session_factory = create_sessionmaker(DBEngine.get())
        async with session_factory() as session:
            result = await apply_operations_scan(session, operations, dry_run=self._dry_run)

        logger.info("Operations scan complete: %s", result)

        # Skip telemetry in what-if mode (see RoleScanJob.run).
        if not self._dry_run:
            track_operations_scan(len(operations))


# Registry of job constructors keyed by job name. Single source of truth for
# both the scheduler (create_all_jobs) and one-shot runners (scan_once), so
# adding a job here wires it into both paths.
JOB_FACTORIES: dict[str, type[Job]] = {
    ROLE_SCAN_JOB_NAME: RoleScanJob,
    OPERATIONS_SCAN_JOB_NAME: OperationsScanJob,
}


def create_job(name: str) -> Job:
    """Create a single job by name (raises KeyError if unknown)."""
    return JOB_FACTORIES[name]()


def create_all_jobs() -> list[Job]:
    """Create all job instances."""
    return [factory() for factory in JOB_FACTORIES.values()]
