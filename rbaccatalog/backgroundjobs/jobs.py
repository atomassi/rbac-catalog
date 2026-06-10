"""Background job definitions for Azure data synchronization."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
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
    @abstractmethod
    def name(self) -> str:
        """Job identifier used for scheduling and logging."""

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether this job should run."""

    @abstractmethod
    async def run(self) -> None:
        """Execute the job. Handles fetch, apply, logging, and telemetry."""


class RoleScanJob(Job):
    """Scans and synchronizes Azure built-in role definitions."""

    JOB_NAME = "role-scan"

    @property
    def name(self) -> str:
        return self.JOB_NAME

    @property
    def enabled(self) -> bool:
        return self._settings.role_scan_enabled

    async def run(self) -> None:
        logger.info("Fetching built-in role definitions...")
        roles = await fetch_builtin_roles()
        logger.info("Fetched %d roles", len(roles))

        if not roles:
            raise EmptyFetchResultError(self.name)

        session_factory = create_sessionmaker(DBEngine.get())
        async with session_factory() as session:
            result = await apply_role_scan(session, roles)

        logger.info("Role scan complete: %s", result)

        track_role_scan(
            roles_fetched=len(roles),
            roles_added=result.created,
            roles_updated=result.updated,
            roles_deleted=result.deleted,
        )


class OperationsScanJob(Job):
    """Scans and synchronizes Azure provider operations."""

    JOB_NAME = "operations-scan"

    @property
    def name(self) -> str:
        return self.JOB_NAME

    @property
    def enabled(self) -> bool:
        return self._settings.operations_scan_enabled

    async def run(self) -> None:
        logger.info("Fetching Azure provider operations...")
        operations = await fetch_provider_operations()
        logger.info("Fetched %d operations", len(operations))

        if not operations:
            raise EmptyFetchResultError(self.name)

        session_factory = create_sessionmaker(DBEngine.get())
        async with session_factory() as session:
            result = await apply_operations_scan(session, operations)

        logger.info("Operations scan complete: %s", result)

        track_operations_scan(len(operations))


# Job constructors keyed by job name (used by create_all_jobs and scan_once).
JOB_FACTORIES: dict[str, type[Job]] = {
    RoleScanJob.JOB_NAME: RoleScanJob,
    OperationsScanJob.JOB_NAME: OperationsScanJob,
}


def create_job(name: str) -> Job:
    """Create a single job by name (e.g. ``role-scan``); raises KeyError if unknown."""
    return JOB_FACTORIES[name]()


def create_all_jobs() -> list[Job]:
    """Create all job instances."""
    return [factory() for factory in JOB_FACTORIES.values()]
