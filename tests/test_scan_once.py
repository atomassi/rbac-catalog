"""Tests for the one-shot scan runner (scan_once)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rbaccatalog.backgroundjobs import scan_once
from rbaccatalog.backgroundjobs.jobs import Job


def _fake_job(name: str) -> MagicMock:
    """Build a stand-in Job whose run() is an awaitable mock."""
    job = MagicMock(spec=Job)
    job.name = name
    job.run = AsyncMock()
    return job


class TestSelectJobs:
    """Tests for job selection from the registry."""

    def test_returns_all_jobs_when_name_is_none(self):
        names = {job.name for job in scan_once.select_jobs(None)}
        assert names == {"role-scan", "operations-scan"}

    def test_filters_to_single_job_by_name(self):
        jobs = scan_once.select_jobs("role-scan")
        assert [job.name for job in jobs] == ["role-scan"]

    def test_unknown_name_raises_key_error(self):
        with pytest.raises(KeyError):
            scan_once.select_jobs("does-not-exist")


class TestParseArgs:
    """Tests for command-line argument parsing."""

    def test_no_job_defaults_to_none(self):
        assert scan_once.parse_args([]).job is None

    def test_valid_job_name(self):
        assert scan_once.parse_args(["role-scan"]).job == "role-scan"

    def test_invalid_job_name_exits(self):
        with pytest.raises(SystemExit):
            scan_once.parse_args(["bogus"])


class TestRunOnce:
    """Tests for the run_once orchestration."""

    @pytest.mark.asyncio
    async def test_runs_jobs_in_order_and_disposes(self):
        order: list[str] = []
        jobs: list[Job] = [_fake_job("role-scan"), _fake_job("operations-scan")]

        async def fake_run_job(job, *, reraise=False):
            order.append(job.name)

        with (
            patch.object(scan_once, "Worker") as mock_worker_cls,
            patch.object(scan_once, "DBEngine") as mock_engine,
            patch.object(scan_once, "ensure_db", new=AsyncMock()) as mock_ensure,
        ):
            mock_worker_cls.return_value.run_job = AsyncMock(side_effect=fake_run_job)
            mock_engine.dispose = AsyncMock()
            await scan_once.run_once(jobs)

        assert order == ["role-scan", "operations-scan"]
        mock_ensure.assert_awaited_once()
        mock_engine.dispose.assert_awaited_once()
        assert mock_worker_cls.return_value.run_job.await_count == 2

    @pytest.mark.asyncio
    async def test_reraises_failure_and_still_disposes(self):
        jobs: list[Job] = [_fake_job("role-scan"), _fake_job("operations-scan")]

        async def fake_run_job(job, *, reraise=False):
            if job.name == "operations-scan":
                raise RuntimeError("boom")

        with (
            patch.object(scan_once, "Worker") as mock_worker_cls,
            patch.object(scan_once, "DBEngine") as mock_engine,
            patch.object(scan_once, "ensure_db", new=AsyncMock()),
        ):
            mock_worker_cls.return_value.run_job = AsyncMock(side_effect=fake_run_job)
            mock_engine.dispose = AsyncMock()
            with pytest.raises(RuntimeError, match="boom"):
                await scan_once.run_once(jobs)

        mock_engine.dispose.assert_awaited_once()
