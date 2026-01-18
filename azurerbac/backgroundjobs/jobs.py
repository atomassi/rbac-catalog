"""Background job definitions for Azure data synchronization."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import timedelta
from enum import StrEnum

from azurerbac.azure import fetch_builtin_roles, fetch_provider_operations
from azurerbac.backgroundjobs.exceptions import EmptyFetchResultError
from azurerbac.backgroundjobs.operations_monitor import apply_operations_scan
from azurerbac.backgroundjobs.roles_monitor import apply_role_scan
from azurerbac.core import DBEngine, create_sessionmaker
from azurerbac.settings import Settings
from azurerbac.telemetry import track_operations_scan, track_role_scan

logger = logging.getLogger("azurerbac.worker")


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

    @property
    @abstractmethod
    def interval(self) -> timedelta:
        """How often to run this job."""

    @abstractmethod
    async def run(self) -> None:
        """Execute the job. Handles fetch, apply, logging, and telemetry."""


class RoleScanJob(Job):
    """Scans and synchronizes Azure built-in role definitions."""

    _JOB_NAME = "role-scan"

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

    _JOB_NAME = "operations-scan"

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
            result = await apply_operations_scan(session, operations)

        logger.info("Operations scan complete: %s", result)

        track_operations_scan(len(operations))


def create_jobs() -> list[Job]:
    """Create all job instances."""
    return [
        RoleScanJob(),
        OperationsScanJob(),
    ]
