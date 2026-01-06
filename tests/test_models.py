"""Tests for SQLAlchemy models."""

from datetime import UTC

import pytest
from sqlalchemy import select

from azurerbac.core import Role, RoleHistory


def _make_role_json(role_name: str, role_type: str = "BuiltInRole") -> dict:
    """Helper to create role JSON with standard structure."""
    return {"properties": {"roleName": role_name, "type": role_type}}


def _make_history_entry(
    db_session,
    role_id: str,
    version_number: int,
    role_json_data: dict,
    event_type: str = "created",
    role_name: str = "",
) -> RoleHistory:
    """Helper to create RoleHistory with inline role_json."""
    history = RoleHistory(
        role_id=role_id,
        version_number=version_number,
        role_name=role_name or role_json_data.get("properties", {}).get("roleName", ""),
        event_type=event_type,
        role_json=role_json_data,
        diff_json={},
        summary=f"Event: {event_type}",
    )
    db_session.add(history)
    return history


class TestRole:
    """Tests for Role model."""

    @pytest.mark.asyncio
    async def test_create_role_snapshot(self, db_session):
        """Test creating a role snapshot with a history entry."""
        role = Role(role_id="test-role-id", role_name="Test Role", status="active")
        db_session.add(role)

        _make_history_entry(
            db_session,
            "test-role-id",
            1,
            _make_role_json("Test Role", "BuiltInRole"),
        )
        await db_session.commit()

        loaded = await db_session.get(Role, "test-role-id")
        assert loaded is not None
        assert loaded.role_name == "Test Role"
        assert loaded.status == "active"

    @pytest.mark.asyncio
    async def test_role_json_from_history(self, db_session):
        """Test that last_known_json property is derived from history[0] JSON."""
        role = Role(role_id="name-test", role_name="Derived Name")
        db_session.add(role)

        _make_history_entry(db_session, "name-test", 1, _make_role_json("Derived Name"))
        await db_session.commit()

        await db_session.refresh(role)
        # last_known_json property returns history[0].role_json
        assert role.last_known_json.get("properties", {}).get("roleName") == "Derived Name"

    @pytest.mark.asyncio
    async def test_default_status_is_active(self, db_session):
        """Test that default status is 'active'."""
        role = Role(role_id="default-status")
        db_session.add(role)
        await db_session.commit()
        await db_session.refresh(role)

        assert role.status == "active"


class TestRoleHistory:
    """Tests for RoleHistory model (combined version + event)."""

    @pytest.mark.asyncio
    async def test_create_history_entry(self, db_session):
        """Test creating a history entry."""
        role = Role(role_id="event-test-role", role_name="Event Test Role")
        db_session.add(role)
        await db_session.flush()

        history = _make_history_entry(
            db_session,
            "event-test-role",
            1,
            {"name": "Event Test Role"},
            role_name="Event Test Role",
        )
        await db_session.commit()

        assert history.role_id == "event-test-role"
        assert history.event_type == "created"
        assert history.version_number == 1

    @pytest.mark.asyncio
    async def test_event_types(self, db_session):
        """Test different event types including delete events."""
        role = Role(role_id="event-types", role_name="Test")
        db_session.add(role)
        await db_session.flush()

        # Created event - version 1
        _make_history_entry(
            db_session, "event-types", 1, {}, event_type="created", role_name="Test"
        )

        # Updated event - version 2
        _make_history_entry(
            db_session, "event-types", 2, {"updated": True}, event_type="updated", role_name="Test"
        )

        # Delete event - version 3, no JSON
        delete_entry = RoleHistory(
            role_id="event-types",
            version_number=3,
            role_json=None,  # No JSON for deleted role
            role_name="Test",
            event_type="deleted",
            diff_json={},
            summary="Event: deleted",
        )
        db_session.add(delete_entry)

        await db_session.commit()

        history = (await db_session.execute(select(RoleHistory))).scalars().all()

        assert len(history) == 3
        event_types = {h.event_type for h in history}
        assert event_types == {"created", "updated", "deleted"}

    @pytest.mark.asyncio
    async def test_cascade_delete(self, db_session):
        """Test that history entries are deleted when role is deleted."""
        role = Role(role_id="cascade-test", role_name="Test")
        db_session.add(role)
        await db_session.flush()

        _make_history_entry(db_session, "cascade-test", 1, {}, role_name="Test")
        await db_session.commit()

        # Delete the role - should cascade to history
        await db_session.delete(role)
        await db_session.commit()

        # History entries should be deleted too
        history = (await db_session.execute(select(RoleHistory))).scalars().all()
        assert len(history) == 0

    @pytest.mark.asyncio
    async def test_create_history_entry_versioned(self, db_session):
        """Test creating a history entry with version number."""
        role = Role(role_id="version-test")
        db_session.add(role)
        await db_session.flush()

        _make_history_entry(db_session, "version-test", 1, {"properties": {"roleName": "Test v1"}})
        await db_session.commit()

        loaded = (
            await db_session.execute(
                select(RoleHistory).where(RoleHistory.role_id == "version-test")
            )
        ).scalar_one()

        assert loaded.version_number == 1

    @pytest.mark.asyncio
    async def test_multiple_versions_ordered(self, db_session):
        """Test that history entries are ordered by version_number desc."""
        role = Role(role_id="multi-version")
        db_session.add(role)
        await db_session.flush()

        for i in range(1, 4):
            _make_history_entry(
                db_session, "multi-version", i, {"properties": {"roleName": f"Test v{i}"}}
            )

        await db_session.commit()

        # Query history directly with explicit ordering to test
        history = (
            (
                await db_session.execute(
                    select(RoleHistory)
                    .where(RoleHistory.role_id == "multi-version")
                    .order_by(RoleHistory.version_number.desc())
                )
            )
            .scalars()
            .all()
        )

        # History should be ordered descending by version_number
        assert len(history) == 3
        assert history[0].version_number == 3  # Most recent first
        assert history[1].version_number == 2
        assert history[2].version_number == 1

    @pytest.mark.asyncio
    async def test_history_json_preserved(self, db_session):
        """Test that each history entry preserves its JSON at that point in time."""
        role = Role(role_id="preserve-json")
        db_session.add(role)
        await db_session.flush()

        for i in range(1, 4):
            _make_history_entry(db_session, "preserve-json", i, {"v": i})

        await db_session.commit()

        history = (
            (
                await db_session.execute(
                    select(RoleHistory)
                    .where(RoleHistory.role_id == "preserve-json")
                    .order_by(RoleHistory.version_number)
                )
            )
            .scalars()
            .all()
        )

        assert history[0].role_json == {"v": 1}
        assert history[1].role_json == {"v": 2}
        assert history[2].role_json == {"v": 3}

    @pytest.mark.asyncio
    async def test_deleted_event_requires_scan_for_event_date(self, db_session):
        """Test that a deleted event without scan_id has no event date.

        When recording a role deletion, you must associate it with a RoleScanStatus
        to ensure the event_date (scan_timestamp) is populated in the dashboard.
        """
        from datetime import datetime

        from azurerbac.core.models import RoleScanStatus

        role = Role(role_id="delete-scan-test", role_name="Test", status="active")
        db_session.add(role)
        await db_session.flush()

        # Create initial history
        _make_history_entry(db_session, "delete-scan-test", 1, {"name": "Test"}, role_name="Test")

        # Create a scan to associate with the delete event
        scan = RoleScanStatus(
            scan_timestamp=datetime.now(UTC),
            roles_scanned=0,
            additions=0,
            updates=0,
            deletions=1,
        )
        db_session.add(scan)
        await db_session.flush()

        # Create properly associated delete event
        delete_entry = RoleHistory(
            role_id="delete-scan-test",
            version_number=2,
            role_json={"name": "Test"},  # Keep the last known JSON
            role_name="Test",
            event_type="deleted",
            diff_json={"deleted": True},
            summary="Role deleted",
            scan_id=scan.id,  # Associate with scan for event_date
            azure_updated_on=datetime.now(UTC),
        )
        db_session.add(delete_entry)
        role.status = "deleted"
        await db_session.commit()

        # Verify the delete event has a scan association
        loaded = (
            await db_session.execute(
                select(RoleHistory)
                .where(RoleHistory.role_id == "delete-scan-test")
                .where(RoleHistory.event_type == "deleted")
            )
        ).scalar_one()

        assert loaded.scan_id == scan.id
        assert loaded.scan.scan_timestamp is not None

    @pytest.mark.asyncio
    async def test_updated_event_creates_new_version(self, db_session):
        """Test that updating a role creates a new history entry, not modifies existing.

        When recording a role update, create a NEW RoleHistory entry with
        event_type='updated' and increment the version_number. Do not modify
        the existing history entry in-place.
        """
        from datetime import datetime

        from azurerbac.core.models import RoleScanStatus

        role = Role(role_id="update-version-test", role_name="Original Name", status="active")
        db_session.add(role)
        await db_session.flush()

        # Create initial history (version 1, created)
        _make_history_entry(
            db_session,
            "update-version-test",
            1,
            {"properties": {"roleName": "Original Name"}},
            event_type="created",
            role_name="Original Name",
        )
        await db_session.flush()

        # Create a scan for the update
        scan = RoleScanStatus(
            scan_timestamp=datetime.now(UTC),
            roles_scanned=1,
            additions=0,
            updates=1,
            deletions=0,
        )
        db_session.add(scan)
        await db_session.flush()

        # Update the role (create a NEW history entry)
        role.role_name = "Updated Name"
        updated_entry = RoleHistory(
            role_id="update-version-test",
            version_number=2,  # New version, not modifying v1
            role_json={"properties": {"roleName": "Updated Name"}},
            role_name="Updated Name",
            event_type="updated",
            diff_json={
                "changes": [{"field": "roleName", "old": "Original Name", "new": "Updated Name"}]
            },
            summary="Role renamed",
            scan_id=scan.id,
            azure_updated_on=datetime.now(UTC),
        )
        db_session.add(updated_entry)
        await db_session.commit()

        # Verify we have two history entries
        history = (
            (
                await db_session.execute(
                    select(RoleHistory)
                    .where(RoleHistory.role_id == "update-version-test")
                    .order_by(RoleHistory.version_number)
                )
            )
            .scalars()
            .all()
        )

        assert len(history) == 2
        assert history[0].version_number == 1
        assert history[0].event_type == "created"
        assert history[0].role_json["properties"]["roleName"] == "Original Name"
        assert history[1].version_number == 2
        assert history[1].event_type == "updated"
        assert history[1].role_json["properties"]["roleName"] == "Updated Name"
        assert history[1].scan_id == scan.id


class TestDeletedRoleProperties:
    """Tests for Role properties that work correctly for deleted roles."""

    @pytest.mark.asyncio
    async def test_last_known_version_returns_non_null_json(self, db_session):
        """Test that last_known_version returns the version before deletion."""
        role = Role(role_id="deleted-role", role_name="Deleted Role", status="deleted")
        db_session.add(role)
        await db_session.flush()

        # Version 1: created with role_json
        _make_history_entry(
            db_session,
            "deleted-role",
            1,
            {"properties": {"roleName": "Deleted Role", "type": "BuiltInRole"}},
            event_type="created",
        )

        # Version 2: deleted (role_json is NULL)
        h2 = RoleHistory(
            role_id="deleted-role",
            version_number=2,
            role_name="Deleted Role",
            event_type="deleted",
            role_json=None,  # NULL for deleted
            diff_json={},
            summary="Role deleted from Azure",
        )
        db_session.add(h2)
        await db_session.commit()
        await db_session.refresh(role)

        # current_version should be the delete event (no JSON)
        assert role.current_version.event_type == "deleted"
        assert role.current_version.role_json is None

        # last_known_version should be the created event (with JSON)
        assert role.last_known_version.event_type == "created"
        assert role.last_known_version.role_json is not None

    @pytest.mark.asyncio
    async def test_last_known_json_returns_previous_version_json(self, db_session):
        """Test that last_known_json returns JSON from before deletion."""
        role = Role(role_id="lkj-test", role_name="LKJ Test", status="deleted")
        db_session.add(role)
        await db_session.flush()

        _make_history_entry(
            db_session,
            "lkj-test",
            1,
            {"properties": {"roleName": "LKJ Test", "type": "CustomRole"}},
            event_type="created",
        )

        h2 = RoleHistory(
            role_id="lkj-test",
            version_number=2,
            role_name="LKJ Test",
            event_type="deleted",
            role_json=None,
            diff_json={},
            summary="Deleted",
        )
        db_session.add(h2)
        await db_session.commit()
        await db_session.refresh(role)

        # last_known_json returns the JSON from before deletion
        assert role.last_known_json == {
            "properties": {"roleName": "LKJ Test", "type": "CustomRole"}
        }

    @pytest.mark.asyncio
    async def test_role_type_works_for_deleted_roles(self, db_session):
        """Test that role_type returns type even for deleted roles."""
        role = Role(role_id="type-test", role_name="Type Test", status="deleted")
        db_session.add(role)
        await db_session.flush()

        _make_history_entry(
            db_session,
            "type-test",
            1,
            {"properties": {"roleName": "Type Test", "type": "BuiltInRole"}},
            event_type="created",
        )

        h2 = RoleHistory(
            role_id="type-test",
            version_number=2,
            role_name="Type Test",
            event_type="deleted",
            role_json=None,
            diff_json={},
            summary="Deleted",
        )
        db_session.add(h2)
        await db_session.commit()
        await db_session.refresh(role)

        # Should return type from last known JSON
        assert role.role_type == "BuiltInRole"

    @pytest.mark.asyncio
    async def test_created_on_works_for_deleted_roles(self, db_session):
        """Test that created_on parses date from last known JSON."""
        role = Role(role_id="created-test", role_name="Created Test", status="deleted")
        db_session.add(role)
        await db_session.flush()

        _make_history_entry(
            db_session,
            "created-test",
            1,
            {
                "properties": {
                    "roleName": "Created Test",
                    "type": "BuiltInRole",
                    "createdOn": "2024-06-15T10:30:00Z",
                }
            },
            event_type="created",
        )

        h2 = RoleHistory(
            role_id="created-test",
            version_number=2,
            role_name="Created Test",
            event_type="deleted",
            role_json=None,
            diff_json={},
            summary="Deleted",
        )
        db_session.add(h2)
        await db_session.commit()
        await db_session.refresh(role)

        import datetime as dt

        # Should return datetime from last known JSON
        expected = dt.datetime(2024, 6, 15, 10, 30, 0, tzinfo=dt.UTC)
        assert role.created_on == expected

    @pytest.mark.asyncio
    async def test_created_on_handles_invalid_date(self, db_session):
        """Test that created_on returns None for invalid date strings."""
        role = Role(role_id="bad-date", role_name="Bad Date")
        db_session.add(role)
        await db_session.flush()

        _make_history_entry(
            db_session,
            "bad-date",
            1,
            {"properties": {"roleName": "Bad Date", "createdOn": "not-a-date"}},
            event_type="created",
        )
        await db_session.commit()
        await db_session.refresh(role)

        assert role.created_on is None

    @pytest.mark.asyncio
    async def test_properties_with_empty_history(self, db_session):
        """Test that all properties handle roles with no history."""
        role = Role(role_id="no-history", role_name="No History")
        db_session.add(role)
        await db_session.commit()
        await db_session.refresh(role)

        assert role.current_version is None
        assert role.last_known_version is None
        assert role.last_known_json == {}
        assert role.role_type is None
        assert role.created_on is None
        assert role.first_seen_at is None
        assert role.last_seen_at is None
