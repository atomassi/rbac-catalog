"""Tests for the monitor module - role scan application logic."""

import pytest
from sqlalchemy import select

from azurerbac.azure.models import RoleDefinition
from azurerbac.backgroundjobs.roles_monitor import apply_role_scan
from azurerbac.core import Role, RoleHistory
from azurerbac.core.constants import EventType, RoleStatus


def _make_role(
    role_id: str,
    role_name: str,
    updated_on: str = "2021-01-01T00:00:00Z",
    created_on: str | None = None,
    **extra_props,
) -> RoleDefinition:
    """Helper to create a RoleDefinition object.

    Args:
        role_id: Unique role identifier
        role_name: Display name of the role
        updated_on: Azure updatedOn timestamp
        created_on: Azure createdOn timestamp. If None, defaults to updated_on
                   (simulating a newly created role where created == updated)
        **extra_props: Additional properties to include
    """
    # Default created_on to updated_on to simulate truly new roles
    if created_on is None:
        created_on = updated_on

    props = {
        "roleName": role_name,
        "type": "BuiltInRole",
        "description": f"Description for {role_name}",
        "assignableScopes": ["/"],
        "permissions": [
            {
                "actions": ["*/read"],
                "notActions": [],
                "dataActions": [],
                "notDataActions": [],
            }
        ],
        "createdOn": created_on,
        "updatedOn": updated_on,
        **extra_props,
    }
    return RoleDefinition.model_validate(
        {
            "id": f"/providers/Microsoft.Authorization/roleDefinitions/{role_id}",
            "type": "Microsoft.Authorization/roleDefinitions",
            "name": role_id,
            "properties": props,
        }
    )


class TestApplyRoleScan:
    """Tests for the main scan application logic."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("created_on", "updated_on", "expected_event_type"),
        [
            pytest.param(
                "2024-01-15T10:00:00Z",
                "2024-01-15T10:00:00Z",
                "created",
                id="truly_new_role",
            ),
            pytest.param(
                "2020-01-01T00:00:00Z",
                "2023-06-15T12:30:00Z",
                "initial_scan",
                id="preexisting_role",
            ),
        ],
    )
    async def test_event_type_based_on_timestamps(
        self,
        db_session,
        created_on: str,
        updated_on: str,
        expected_event_type: str,
    ):
        """Test that event_type is determined by comparing created_on and updated_on timestamps.

        - Truly new roles (created_on == updated_on) get 'created' event_type
        - Pre-existing roles (created_on != updated_on) get 'initial_scan' event_type
        """
        roles = [
            _make_role(
                "test-role",
                "Test Role",
                created_on=created_on,
                updated_on=updated_on,
            )
        ]

        stats = await apply_role_scan(db_session, roles)

        assert stats.created == 1

        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 1
        assert history[0].event_type == expected_event_type

    @pytest.mark.asyncio
    async def test_mixed_new_and_preexisting_roles(self, db_session):
        """Test that a mix of truly new and pre-existing roles get correct event types."""
        roles = [
            # Truly new role (timestamps match)
            _make_role(
                "brand-new",
                "Brand New",
                created_on="2024-01-15T10:00:00Z",
                updated_on="2024-01-15T10:00:00Z",
            ),
            # Pre-existing role (timestamps differ)
            _make_role(
                "pre-existing",
                "Pre-existing",
                created_on="2018-03-01T00:00:00Z",
                updated_on="2023-11-20T15:45:00Z",
            ),
        ]

        await apply_role_scan(db_session, roles)

        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 2

        event_types = {h.role_id: h.event_type for h in history}
        assert event_types["brand-new"] == EventType.CREATED
        assert event_types["pre-existing"] == EventType.INITIAL_SCAN

    @pytest.mark.asyncio
    async def test_creates_new_roles(self, db_session):
        """Test that new roles are created with proper events and versions."""
        roles = [_make_role("role-1", "Reader"), _make_role("role-2", "Contributor")]

        stats = await apply_role_scan(db_session, roles)

        assert stats.created == 2
        assert stats.updated == 0
        assert stats.deleted == 0
        assert stats.total == 2

        # Verify snapshots created
        snapshots = (await db_session.execute(select(Role))).scalars().all()
        assert len(snapshots) == 2

        # Verify history entries created (combined events + versions)
        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 2
        # Default _make_role has created_on == updated_on, so these are "created"
        assert all(h.event_type == EventType.CREATED for h in history)
        assert all(h.version_number == 1 for h in history)

    @pytest.mark.asyncio
    async def test_updates_existing_role(self, db_session):
        """Test that changed roles are updated with events and new versions."""
        # First scan - create roles
        roles_v1 = [_make_role("role-1", "Reader", updated_on="2021-01-01T00:00:00Z")]
        await apply_role_scan(db_session, roles_v1)

        # Second scan - update role
        roles_v2 = [
            _make_role(
                "role-1",
                "Reader",
                updated_on="2022-01-01T00:00:00Z",
                description="Updated desc",
            )
        ]
        stats = await apply_role_scan(db_session, roles_v2)

        assert stats.created == 0
        assert stats.updated == 1

        # Verify history entries
        history = (
            (await db_session.execute(select(RoleHistory).order_by(RoleHistory.scan_id)))
            .scalars()
            .all()
        )
        assert len(history) == 2
        assert history[0].event_type == EventType.CREATED
        assert history[1].event_type == EventType.UPDATED

        # Verify version numbers
        role_history = (
            (
                await db_session.execute(
                    select(RoleHistory)
                    .where(RoleHistory.role_id == "role-1")
                    .order_by(RoleHistory.version_number)
                )
            )
            .scalars()
            .all()
        )
        assert len(role_history) == 2
        assert role_history[0].version_number == 1
        assert role_history[1].version_number == 2

    @pytest.mark.asyncio
    async def test_no_update_if_unchanged(self, db_session):
        """Test that identical roles don't create update events."""
        roles = [_make_role("role-1", "Reader", updated_on="2021-01-01T00:00:00Z")]

        # First scan
        await apply_role_scan(db_session, roles)

        # Second scan with same data
        stats = await apply_role_scan(db_session, roles)

        assert stats.created == 0
        assert stats.updated == 0

        # Only one history entry (the creation)
        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 1
        assert history[0].event_type == EventType.CREATED

    @pytest.mark.asyncio
    async def test_deletes_missing_role(self, db_session):
        """Test that roles missing from scan are marked as deleted."""
        # First scan with two roles
        roles_v1 = [_make_role("role-1", "Reader"), _make_role("role-2", "Contributor")]
        await apply_role_scan(db_session, roles_v1)

        # Second scan with only one role
        roles_v2 = [_make_role("role-1", "Reader")]
        stats = await apply_role_scan(db_session, roles_v2)

        assert stats.deleted == 1

        # Verify delete history entry
        deleted_history = (
            (
                await db_session.execute(
                    select(RoleHistory).where(RoleHistory.event_type == "deleted")
                )
            )
            .scalars()
            .all()
        )
        assert len(deleted_history) == 1
        assert deleted_history[0].role_id == "role-2"
        # Deleted events should have azure_updated_on set to scan timestamp (for UX)
        assert deleted_history[0].azure_updated_on is not None

        # Verify snapshot status
        snapshot = await db_session.get(Role, "role-2")
        assert snapshot.status == RoleStatus.DELETED

    @pytest.mark.asyncio
    async def test_reactivates_deleted_role(self, db_session):
        """Test that a deleted role reappearing is reactivated."""
        # First scan
        roles = [_make_role("role-1", "Reader")]
        await apply_role_scan(db_session, roles)

        # Second scan - role disappears
        await apply_role_scan(db_session, [])

        # Third scan - role reappears with new timestamp
        roles_v3 = [_make_role("role-1", "Reader", updated_on="2023-01-01T00:00:00Z")]
        await apply_role_scan(db_session, roles_v3)

        # Should be updated (reactivated)
        snapshot = await db_session.get(Role, "role-1")
        assert snapshot.status == RoleStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_handles_empty_roles_list(self, db_session):
        """Test handling of empty roles list."""
        stats = await apply_role_scan(db_session, [])

        assert stats.created == 0
        assert stats.updated == 0
        assert stats.deleted == 0
        assert stats.total == 0

    @pytest.mark.asyncio
    async def test_diff_contains_change_details(self, db_session):
        """Test that diff_json contains meaningful change information."""
        # Create role
        roles_v1 = [_make_role("role-1", "Reader")]
        await apply_role_scan(db_session, roles_v1)

        # Update role with different description
        roles_v2 = [_make_role("role-1", "Reader Updated", updated_on="2022-01-01T00:00:00Z")]
        await apply_role_scan(db_session, roles_v2)

        # Check the update history entry has diff info
        update_history = (
            await db_session.execute(select(RoleHistory).where(RoleHistory.event_type == "updated"))
        ).scalar_one()

        assert update_history.diff_json["changed"] is True
        assert len(update_history.diff_json["changes"]) > 0

    @pytest.mark.asyncio
    async def test_summary_is_populated(self, db_session):
        """Test that event summary is populated."""
        roles = [_make_role("role-1", "Reader")]
        await apply_role_scan(db_session, roles)

        history = (await db_session.execute(select(RoleHistory))).scalar_one()
        assert history.summary is not None
        assert len(history.summary) > 0


# =============================================================================
# Operations Monitor Tests
# =============================================================================


class TestApplyOperationsScan:
    """Tests for the apply_operations_scan function."""

    @pytest.mark.asyncio
    async def test_apply_operations_scan_new_operations(self, db_session):
        """Test adding new operations."""
        from azurerbac.azure.models import OperationData
        from azurerbac.backgroundjobs.operations_monitor import apply_operations_scan

        operations = [
            OperationData(
                name="Microsoft.Test/resources/read",
                display_name="Read Test Resources",
                description="Read test resources",
                provider_display_name="Microsoft Test",
                resource_type="resources",
                resource_type_display_name="Resources",
                is_data_action=False,
            ),
        ]

        stats = await apply_operations_scan(db_session, operations)
        await db_session.commit()

        assert stats.created == 1

    @pytest.mark.asyncio
    async def test_apply_operations_scan_empty_list(self, db_session):
        """Test scanning with empty operations list."""
        from azurerbac.backgroundjobs.operations_monitor import apply_operations_scan

        stats = await apply_operations_scan(db_session, [])
        assert stats.created == 0
        assert stats.updated == 0


# =============================================================================
# Worker Module Tests
# =============================================================================


class TestWorkerImports:
    """Tests for worker module imports and basic structure."""

    def test_worker_imports_engine_correctly(self):
        """Verify worker uses DBEngine.get() from core."""
        from azurerbac.backgroundjobs import worker

        # Check that DBEngine is imported (not create_engine)
        assert hasattr(worker, "DBEngine") or "DBEngine" in dir(worker)

    def test_worker_main_is_async(self):
        """Verify main() is an async function."""
        import asyncio

        from azurerbac.backgroundjobs.worker import main

        assert asyncio.iscoroutinefunction(main)

    def test_cleanup_uses_engine_dispose(self):
        """Verify _cleanup() signature doesn't require engine parameter."""
        import inspect

        from azurerbac.backgroundjobs.worker import _cleanup

        sig = inspect.signature(_cleanup)
        params = list(sig.parameters.keys())
        # Should only have scheduler parameter, not engine
        assert params == ["scheduler"]

    def test_job_runner_has_required_methods(self):
        """Verify JobRunner has execute_with_telemetry and run_job methods."""
        from azurerbac.backgroundjobs.worker import JobRunner

        assert hasattr(JobRunner, "execute_with_telemetry")
        assert hasattr(JobRunner, "run_job")

    def test_job_spec_is_dataclass(self):
        """Verify JobSpec is a frozen dataclass."""
        from dataclasses import fields

        from azurerbac.backgroundjobs.worker import JobSpec

        # Should have expected fields
        field_names = {f.name for f in fields(JobSpec)}
        assert "name" in field_names
        assert "enabled" in field_names
        assert "fetch" in field_names
        assert "apply" in field_names
        assert "interval_seconds" in field_names


class TestEmptyFetchResultError:
    """Tests for empty fetch result validation."""

    def test_empty_fetch_result_error_message(self):
        """Test EmptyFetchResultError has correct message."""
        from azurerbac.backgroundjobs.worker import EmptyFetchResultError

        error = EmptyFetchResultError("role-scan")
        assert "role-scan" in str(error)
        assert "0 results" in str(error)
        assert error.job_name == "role-scan"

    @pytest.mark.asyncio
    async def test_run_job_fails_on_empty_fetch(self, db_session):
        """Test that run_job raises EmptyFetchResultError when fetch returns empty list."""
        from unittest.mock import AsyncMock, MagicMock

        from azurerbac.backgroundjobs.worker import JobRunner, JobSpec

        # Create a mock session factory
        mock_session_factory = MagicMock()

        runner = JobRunner(session_factory=mock_session_factory)

        # Create a job spec that returns empty list
        spec = JobSpec(
            name="test-empty-job",
            enabled=True,
            fetch_label="Fetching test items...",
            fetch=AsyncMock(return_value=[]),  # Returns empty list
            apply=AsyncMock(return_value={"created": 0}),
            on_success=MagicMock(),
            interval_seconds=60,
        )

        # run_job should catch the error internally (via execute_with_telemetry)
        # but it should log the failure. Let's verify the fetch was called
        # and apply was NOT called (because of the empty result check)
        await runner.run_job(spec)

        spec.fetch.assert_called_once()
        spec.apply.assert_not_called()  # Should not reach apply due to empty check
        spec.on_success.assert_not_called()  # Should not call success callback

    @pytest.mark.asyncio
    async def test_run_job_succeeds_with_results(self, db_session):
        """Test that run_job succeeds when fetch returns results."""
        from unittest.mock import AsyncMock, MagicMock

        from azurerbac.backgroundjobs.worker import JobRunner, JobSpec

        # Create mock session factory that returns an async context manager
        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=None)

        runner = JobRunner(session_factory=mock_session_factory)

        # Create a job spec that returns non-empty list
        mock_apply = AsyncMock(return_value={"created": 1, "updated": 0})
        mock_on_success = MagicMock()

        spec = JobSpec(
            name="test-success-job",
            enabled=True,
            fetch_label="Fetching test items...",
            fetch=AsyncMock(return_value=[{"id": "1", "name": "test"}]),  # Non-empty
            apply=mock_apply,
            on_success=mock_on_success,
            interval_seconds=60,
        )

        await runner.run_job(spec)

        spec.fetch.assert_called_once()
        mock_apply.assert_called_once()  # apply should be called
        mock_on_success.assert_called_once()  # success callback should be called


class TestJobRunnerExecuteWithTelemetry:
    """Tests for JobRunner.execute_with_telemetry method."""

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        from unittest.mock import MagicMock

        from azurerbac.backgroundjobs.worker import JobRunner

        runner = JobRunner(session_factory=MagicMock())

        async def work():
            return {"result": "success"}

        result = await runner.execute_with_telemetry("test-op", work)
        assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_calls_on_success_callback(self):
        from unittest.mock import MagicMock

        from azurerbac.backgroundjobs.worker import JobRunner

        runner = JobRunner(session_factory=MagicMock())
        on_success = MagicMock()

        async def work():
            return "result"

        await runner.execute_with_telemetry("test-op", work, on_success=on_success)
        on_success.assert_called_once()
        # First arg is elapsed time (float), second is result
        call_args = on_success.call_args[0]
        assert isinstance(call_args[0], float)
        assert call_args[1] == "result"

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        from unittest.mock import MagicMock

        from azurerbac.backgroundjobs.worker import JobRunner

        runner = JobRunner(session_factory=MagicMock())

        async def work():
            raise RuntimeError("test error")

        result = await runner.execute_with_telemetry("test-op", work)
        assert result is None


class TestCreateJobSpecs:
    """Tests for _create_job_specs function."""

    def test_creates_both_job_specs(self):
        from azurerbac.backgroundjobs.worker import _create_job_specs
        from azurerbac.settings import Settings

        settings = Settings.get()
        jobs = _create_job_specs(settings)

        assert len(jobs) == 2
        job_names = {job.name for job in jobs}
        assert "role-scan" in job_names
        assert "operations-scan" in job_names

    def test_job_specs_have_correct_types(self):
        from azurerbac.backgroundjobs.worker import JobSpec, _create_job_specs
        from azurerbac.settings import Settings

        settings = Settings.get()
        jobs = _create_job_specs(settings)

        for job in jobs:
            assert isinstance(job, JobSpec)
            assert callable(job.fetch)
            assert callable(job.apply)
            assert callable(job.on_success)
            assert isinstance(job.interval_seconds, int)


class TestSetupScheduler:
    """Tests for _setup_scheduler function."""

    def test_creates_scheduler_with_enabled_jobs(self):
        from unittest.mock import AsyncMock, MagicMock

        from azurerbac.backgroundjobs.worker import JobSpec, _setup_scheduler

        jobs = [
            JobSpec(
                name="job1",
                enabled=True,
                fetch_label="Fetch 1",
                fetch=AsyncMock(),
                apply=AsyncMock(),
                on_success=MagicMock(),
                interval_seconds=60,
            ),
            JobSpec(
                name="job2",
                enabled=False,
                fetch_label="Fetch 2",
                fetch=AsyncMock(),
                apply=AsyncMock(),
                on_success=MagicMock(),
                interval_seconds=120,
            ),
        ]

        run_job = AsyncMock()
        scheduler = _setup_scheduler(jobs, run_job)

        # Only enabled jobs should be scheduled
        scheduled_jobs = scheduler.get_jobs()
        assert len(scheduled_jobs) == 1
        assert scheduled_jobs[0].id == "job1"


class TestRunJobDisabled:
    """Tests for disabled job handling."""

    @pytest.mark.asyncio
    async def test_run_job_skips_disabled_job(self):
        from unittest.mock import AsyncMock, MagicMock

        from azurerbac.backgroundjobs.worker import JobRunner, JobSpec

        runner = JobRunner(session_factory=MagicMock())

        spec = JobSpec(
            name="disabled-job",
            enabled=False,
            fetch_label="Fetch disabled",
            fetch=AsyncMock(),
            apply=AsyncMock(),
            on_success=MagicMock(),
            interval_seconds=60,
        )

        await runner.run_job(spec)

        # Fetch should not be called for disabled jobs
        spec.fetch.assert_not_called()
        spec.apply.assert_not_called()
