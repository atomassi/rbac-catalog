"""Tests for the monitor module - role scan application logic."""

import pytest
from sqlalchemy import select

from azurerbac.backgroundjobs.roles_monitor import apply_role_scan
from azurerbac.backgroundjobs.utils import parse_azure_date
from azurerbac.core import Role, RoleHistory


def _make_role(
    role_id: str,
    role_name: str,
    updated_on: str = "2021-01-01T00:00:00Z",
    created_on: str | None = None,
    **extra_props,
) -> dict:
    """Helper to create a role definition dict.

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
    return {
        "id": f"/providers/Microsoft.Authorization/roleDefinitions/{role_id}",
        "type": "Microsoft.Authorization/roleDefinitions",
        "name": role_id,
        "properties": props,
    }


class TestParseAzureDate:
    """Tests for Azure date parsing."""

    def test_parses_standard_iso_format(self):
        role_json = {"properties": {"updatedOn": "2021-11-11T20:13:47Z"}}
        result = parse_azure_date(role_json, "updatedOn")
        assert result is not None
        assert result.year == 2021
        assert result.month == 11
        assert result.day == 11
        assert result.microsecond == 0
        assert result.utcoffset() is not None
        assert result.utcoffset().total_seconds() == 0

    def test_parses_azure_format_with_microseconds(self):
        role_json = {"properties": {"updatedOn": "2021-11-11T20:13:47.8628684Z"}}
        result = parse_azure_date(role_json, "updatedOn")
        assert result is not None
        assert result.year == 2021
        # Azure can return 7-digit fractional seconds; we normalize to 6 digits.
        assert result.microsecond == 862868
        assert result.utcoffset() is not None
        assert result.utcoffset().total_seconds() == 0

    def test_parses_seven_digit_microseconds(self):
        """Azure sometimes returns 7-digit microseconds which fromisoformat doesn't handle."""
        role_json = {"properties": {"updatedOn": "2021-11-11T20:13:47.3564306Z"}}
        result = parse_azure_date(role_json, "updatedOn")
        assert result is not None
        assert result.microsecond == 356430
        assert result.utcoffset() is not None
        assert result.utcoffset().total_seconds() == 0

    def test_returns_none_for_missing_field(self):
        role_json = {"properties": {}}
        result = parse_azure_date(role_json, "updatedOn")
        assert result is None

    def test_returns_none_for_invalid_date(self):
        role_json = {"properties": {"updatedOn": "not-a-date"}}
        result = parse_azure_date(role_json, "updatedOn")
        assert result is None

    def test_returns_none_for_non_string(self):
        role_json = {"properties": {"updatedOn": 12345}}
        result = parse_azure_date(role_json, "updatedOn")
        assert result is None


class TestApplyRoleScan:
    """Tests for the main scan application logic."""

    @pytest.mark.asyncio
    async def test_creates_new_roles(self, db_session):
        """Test that new roles are created with proper events and versions."""
        roles = [_make_role("role-1", "Reader"), _make_role("role-2", "Contributor")]

        stats = await apply_role_scan(db_session, roles)

        assert stats["created"] == 2
        assert stats["updated"] == 0
        assert stats["deleted"] == 0
        assert stats["total"] == 2

        # Verify snapshots created
        snapshots = (await db_session.execute(select(Role))).scalars().all()
        assert len(snapshots) == 2

        # Verify history entries created (combined events + versions)
        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 2
        assert all(h.event_type == "created" for h in history)
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

        assert stats["created"] == 0
        assert stats["updated"] == 1

        # Verify history entries
        history = (
            (await db_session.execute(select(RoleHistory).order_by(RoleHistory.scan_id)))
            .scalars()
            .all()
        )
        assert len(history) == 2
        assert history[0].event_type == "created"
        assert history[1].event_type == "updated"

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

        assert stats["created"] == 0
        assert stats["updated"] == 0

        # Only one history entry (the creation)
        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 1
        assert history[0].event_type == "created"

    @pytest.mark.asyncio
    async def test_deletes_missing_role(self, db_session):
        """Test that roles missing from scan are marked as deleted."""
        # First scan with two roles
        roles_v1 = [_make_role("role-1", "Reader"), _make_role("role-2", "Contributor")]
        await apply_role_scan(db_session, roles_v1)

        # Second scan with only one role
        roles_v2 = [_make_role("role-1", "Reader")]
        stats = await apply_role_scan(db_session, roles_v2)

        assert stats["deleted"] == 1

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

        # Verify snapshot status
        snapshot = await db_session.get(Role, "role-2")
        assert snapshot.status == "deleted"

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
        assert snapshot.status == "active"

    @pytest.mark.asyncio
    async def test_handles_empty_roles_list(self, db_session):
        """Test handling of empty roles list."""
        stats = await apply_role_scan(db_session, [])

        assert stats["created"] == 0
        assert stats["updated"] == 0
        assert stats["deleted"] == 0
        assert stats["total"] == 0

    @pytest.mark.asyncio
    async def test_skips_roles_without_id(self, db_session):
        """Test that roles without id/name are skipped."""
        roles = [{"properties": {"roleName": "No ID Role"}}]
        stats = await apply_role_scan(db_session, roles)

        assert stats["total"] == 0
        assert stats["created"] == 0

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
        from azurerbac.backgroundjobs.operations_monitor import apply_operations_scan

        operations = [
            {
                "name": "Microsoft.Test/resources/read",
                "display_name": "Read Test Resources",
                "description": "Read test resources",
                "provider_display_name": "Microsoft Test",
                "resource_type": "resources",
                "resource_type_display_name": "Resources",
                "is_data_action": False,
            },
        ]

        stats = await apply_operations_scan(db_session, operations)
        await db_session.commit()

        assert stats["created"] == 1

    @pytest.mark.asyncio
    async def test_apply_operations_scan_empty_list(self, db_session):
        """Test scanning with empty operations list."""
        from azurerbac.backgroundjobs.operations_monitor import apply_operations_scan

        stats = await apply_operations_scan(db_session, [])
        assert stats["created"] == 0
        assert stats["updated"] == 0


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
