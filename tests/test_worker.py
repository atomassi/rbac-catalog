"""Tests for Worker.run_job error handling and the reraise flag."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from rbaccatalog.backgroundjobs.jobs import Job
from rbaccatalog.backgroundjobs.worker import Worker


def _fake_job(name: str = "role-scan", *, enabled: bool = True) -> MagicMock:
    """Build a stand-in Job whose run() is an awaitable mock."""
    job = MagicMock(spec=Job)
    job.name = name
    job.enabled = enabled
    job.run = AsyncMock()
    return job


class TestWorkerRunJob:
    """Tests for Worker.run_job."""

    @pytest.mark.asyncio
    async def test_swallows_failure_by_default(self):
        job = _fake_job()
        job.run.side_effect = RuntimeError("boom")

        # Default reraise=False: failure is logged/tracked but not raised.
        await Worker().run_job(job)

        job.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reraises_failure_when_requested(self):
        job = _fake_job()
        job.run.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            await Worker().run_job(job, reraise=True)

    @pytest.mark.asyncio
    async def test_skips_disabled_job(self):
        job = _fake_job(enabled=False)

        await Worker().run_job(job, reraise=True)

        job.run.assert_not_awaited()
