"""Tests for the monitor module - role scan application logic."""

import pytest
from sqlalchemy import select

from rbaccatalog.azure.models import RoleDefinition
from rbaccatalog.backgroundjobs.roles_monitor import apply_role_scan
from rbaccatalog.core import Role, RoleHistory
from rbaccatalog.core.enums import EventType, RoleStatus


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
    @pytest.mark.parametrize(
        ("stored_updated_on", "incoming_updated_on", "should_warn"),
        [
            pytest.param(
                "2022-01-01T00:00:00Z",
                "2021-01-01T00:00:00Z",
                True,
                id="stale_update_older_timestamp",
            ),
            pytest.param(
                "2022-01-01T00:00:00Z",
                "2022-01-01T00:00:00Z",
                False,
                id="equal_timestamp_no_warn",
            ),
        ],
    )
    async def test_rejects_stale_or_equal_updated_on(
        self,
        db_session,
        caplog,
        stored_updated_on: str,
        incoming_updated_on: str,
        should_warn: bool,
    ):
        """Test that updates with stale or equal updated_on are rejected.

        - Stale updates (incoming < stored) are rejected with a warning
        - Equal timestamps (incoming == stored) are rejected silently
        """
        import logging

        # First scan - create role with initial timestamp
        roles_v1 = [_make_role("role-1", "Reader", updated_on=stored_updated_on)]
        await apply_role_scan(db_session, roles_v1)

        # Second scan - attempt update with stale/equal timestamp
        roles_v2 = [
            _make_role(
                "role-1",
                "Reader Updated",  # Changed name to ensure diff would be detected
                updated_on=incoming_updated_on,
                description="Updated description",
            )
        ]

        with caplog.at_level(logging.INFO, logger="rbaccatalog.backgroundjobs.roles_monitor"):
            stats = await apply_role_scan(db_session, roles_v2)

        # Should be rejected (no update)
        assert stats.updated == 0

        # Check warning log
        if should_warn:
            assert any("Rejecting stale update" in record.message for record in caplog.records)
            assert any("role-1" in record.message for record in caplog.records)
        else:
            assert not any("Rejecting stale update" in record.message for record in caplog.records)

        # Only one history entry (the creation)
        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 1
        assert history[0].event_type == EventType.CREATED

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


def _make_operation(
    name: str,
    display_name: str = "Display Name",
    description: str = "Description",
    provider_display_name: str = "Microsoft Test",
    resource_type: str = "resources",
    is_data_action: bool = False,
    **extra,
):
    """Helper to create an OperationData object."""
    from rbaccatalog.azure.models import OperationData

    return OperationData(
        name=name,
        display_name=display_name,
        description=description,
        provider_display_name=provider_display_name,
        resource_type=resource_type,
        resource_type_display_name=extra.get("resource_type_display_name", "Resources"),
        is_data_action=is_data_action,
        origin=extra.get("origin"),
    )


class TestApplyOperationsScan:
    """Tests for the apply_operations_scan function."""

    @pytest.mark.asyncio
    async def test_creates_new_operations(self, db_session):
        """Test adding new operations."""
        from rbaccatalog.backgroundjobs.operations_monitor import apply_operations_scan

        operations = [
            _make_operation("Microsoft.Test/resources/read", "Read Test Resources"),
            _make_operation("Microsoft.Test/resources/write", "Write Test Resources"),
        ]

        stats = await apply_operations_scan(db_session, operations)

        assert stats.created == 2
        assert stats.updated == 0
        assert stats.total == 2

    @pytest.mark.asyncio
    async def test_handles_empty_list(self, db_session):
        """Test scanning with empty operations list."""
        from rbaccatalog.backgroundjobs.operations_monitor import apply_operations_scan

        stats = await apply_operations_scan(db_session, [])
        assert stats.created == 0
        assert stats.updated == 0
        assert stats.total == 0

    @pytest.mark.asyncio
    async def test_updates_existing_operation(self, db_session):
        """Test that changed operations are updated."""
        from rbaccatalog.backgroundjobs.operations_monitor import apply_operations_scan

        # First scan
        operations_v1 = [_make_operation("Microsoft.Test/read", "Read")]
        await apply_operations_scan(db_session, operations_v1)

        # Second scan with updated display_name
        operations_v2 = [_make_operation("Microsoft.Test/read", "Read V2")]
        stats = await apply_operations_scan(db_session, operations_v2)

        assert stats.created == 0
        assert stats.updated == 1

    @pytest.mark.asyncio
    async def test_deduplicates_operations(self, db_session):
        """Test that duplicate operations are deduplicated (keeps last)."""
        from rbaccatalog.backgroundjobs.operations_monitor import apply_operations_scan

        operations = [
            _make_operation("Microsoft.Test/read", "First"),
            _make_operation("Microsoft.Test/read", "Second"),  # Duplicate - should keep this
            _make_operation("Microsoft.Test/read", "Third"),  # Duplicate - should keep this
        ]

        stats = await apply_operations_scan(db_session, operations)

        assert stats.created == 1
        assert stats.duplicates_skipped == 2
        assert stats.total == 1

    @pytest.mark.asyncio
    async def test_counts_unique_providers(self, db_session):
        """Test that providers are counted correctly."""
        from rbaccatalog.backgroundjobs.operations_monitor import apply_operations_scan

        operations = [
            _make_operation("Microsoft.Compute/read", provider_display_name="Compute"),
            _make_operation("Microsoft.Compute/write", provider_display_name="Compute"),
            _make_operation("Microsoft.Storage/read", provider_display_name="Storage"),
        ]

        stats = await apply_operations_scan(db_session, operations)

        assert stats.providers == 2  # Compute and Storage

    @pytest.mark.asyncio
    async def test_skips_operations_without_name(self, db_session):
        """Test that operations without a name are skipped."""
        from rbaccatalog.azure.models import OperationData
        from rbaccatalog.backgroundjobs.operations_monitor import apply_operations_scan

        operations = [
            OperationData(
                name="",  # Empty name - should be skipped
                display_name="No Name Op",
                description="desc",
                provider_display_name="Test",
                resource_type="resources",
                resource_type_display_name="Resources",
                is_data_action=False,
            ),
            _make_operation("Microsoft.Test/read"),  # Valid
        ]

        stats = await apply_operations_scan(db_session, operations)

        assert stats.created == 1
        assert stats.total == 1


# =============================================================================
# Worker Module Tests
# =============================================================================


class TestWorkerImports:
    """Tests for worker module imports and basic structure."""

    def test_worker_class_exists(self):
        """Verify Worker class exists and exposes run_job."""
        from rbaccatalog.backgroundjobs.worker import Worker

        worker = Worker()
        assert hasattr(worker, "run_job")

    def test_job_abstract_class_has_required_methods(self):
        """Verify Job abstract class has required abstract methods."""
        import inspect

        from rbaccatalog.backgroundjobs.jobs import Job

        # Check abstract methods/properties exist
        assert hasattr(Job, "name")
        assert hasattr(Job, "enabled")
        assert hasattr(Job, "run")
        assert inspect.isabstract(Job)


class TestEmptyFetchResultError:
    """Tests for empty fetch result validation."""

    def test_empty_fetch_result_error_message(self):
        """Test EmptyFetchResultError has correct message."""
        from rbaccatalog.backgroundjobs.exceptions import EmptyFetchResultError

        error = EmptyFetchResultError("role-scan")
        assert "role-scan" in str(error)
        assert "0 results" in str(error)
        assert error.job_name == "role-scan"

    @pytest.mark.asyncio
    async def test_run_job_fails_on_empty_fetch(self, db_session):
        """Test that run job handles EmptyFetchResultError when fetch returns empty list."""
        from rbaccatalog.backgroundjobs.exceptions import EmptyFetchResultError
        from rbaccatalog.backgroundjobs.jobs import Job
        from rbaccatalog.backgroundjobs.worker import Worker

        class EmptyFetchJob(Job):
            @property
            def name(self) -> str:
                return "test-empty-job"

            @property
            def enabled(self) -> bool:
                return True

            async def run(self):
                raise EmptyFetchResultError(self.name)

        job = EmptyFetchJob()
        worker = Worker()

        # Should not raise - errors are logged and tracked
        await worker.run_job(job)

    @pytest.mark.asyncio
    async def test_run_job_succeeds_with_results(self, db_session):
        """Test that run job succeeds when job runs without error."""
        from rbaccatalog.backgroundjobs.jobs import Job
        from rbaccatalog.backgroundjobs.worker import Worker

        run_called = False

        class SuccessfulJob(Job):
            @property
            def name(self) -> str:
                return "test-success-job"

            @property
            def enabled(self) -> bool:
                return True

            async def run(self):
                nonlocal run_called
                run_called = True

        job = SuccessfulJob()
        worker = Worker()

        await worker.run_job(job)

        assert run_called


class TestCreateJobs:
    """Tests for create_all_jobs function."""

    def test_creates_both_jobs(self):
        from rbaccatalog.backgroundjobs.jobs import create_all_jobs

        jobs = create_all_jobs()

        assert len(jobs) == 2
        job_names = {job.name for job in jobs}
        assert "role-scan" in job_names
        assert "operations-scan" in job_names

    def test_jobs_are_job_instances(self):
        from rbaccatalog.backgroundjobs.jobs import Job, create_all_jobs

        jobs = create_all_jobs()

        for job in jobs:
            assert isinstance(job, Job)
            assert callable(job.run)


class TestWorker:
    """Tests for Worker class methods."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "job_enabled,should_raise,expect_run_called",
        [
            pytest.param(False, False, False, id="disabled_job_skipped"),
            pytest.param(True, False, True, id="enabled_job_executed"),
            pytest.param(True, True, True, id="failing_job_caught"),
        ],
    )
    async def test_run_job_behavior(
        self, job_enabled: bool, should_raise: bool, expect_run_called: bool
    ):
        """run_job should handle enabled/disabled jobs and catch exceptions."""
        from rbaccatalog.backgroundjobs.jobs import Job
        from rbaccatalog.backgroundjobs.worker import Worker

        run_called = False

        class TestJob(Job):
            @property
            def name(self) -> str:
                return "test-job"

            @property
            def enabled(self) -> bool:
                return job_enabled

            async def run(self):
                nonlocal run_called
                run_called = True
                if should_raise:
                    raise RuntimeError("Test error")

        job = TestJob()
        worker = Worker()

        # Should never propagate exceptions
        await worker.run_job(job)

        assert run_called == expect_run_called
