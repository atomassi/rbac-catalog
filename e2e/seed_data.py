#!/usr/bin/env python3
"""Seed test data for E2E tests.

Loads production-like test data from a single JSON fixture for comprehensive E2E testing.
Run this before starting the web server for E2E testing.

Test data includes:
- Core roles (Reader, Contributor, Owner, User Access Administrator, RBAC Admin)
- Storage-related roles (for search tests)
- Roles with roleAssignments/delete (for no-limit API test - 50+ roles)
- Roles with conditions (for conditional badge tests)
- A deleted role (for status filter tests)
- Comprehensive operations (storage, authorization, compute, network, etc.)
- History entries with different event types (initial_scan, created, updated, deleted)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Set up path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azurerbac.core.models import Base, Operation, Role, RoleHistory, RoleScanStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Path to consolidated fixture
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "test_data.json"


def load_fixture() -> dict:
    """Load the consolidated JSON fixture file."""
    if not FIXTURE_PATH.exists():
        print(f"⚠ Fixture file not found: {FIXTURE_PATH}")  # noqa: T201
        return {"roles": [], "operations": [], "extra_history": []}
    with open(FIXTURE_PATH) as f:
        return json.load(f)


def parse_azure_timestamp(ts: str | None) -> datetime | None:
    """Parse Azure timestamp string to datetime."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


async def seed_roles(session: AsyncSession, roles_data: list[dict], scan_id: int) -> int:
    """Seed roles from fixture data."""
    count = 0

    for role_data in roles_data:
        role = Role(
            role_id=role_data["role_id"],
            role_name=role_data["role_name"],
            status=role_data.get("status", "active"),
        )
        session.add(role)

        # Add role history entry
        role_json = role_data.get("role_json", {})
        azure_updated = None
        if role_json and "properties" in role_json:
            azure_updated = parse_azure_timestamp(role_json["properties"].get("updatedOn"))

        # Use azure_updated_on from fixture if available
        if not azure_updated and role_data.get("azure_updated_on"):
            azure_updated = parse_azure_timestamp(role_data["azure_updated_on"])

        history = RoleHistory(
            role_id=role_data["role_id"],
            version_number=role_data.get("version_number", 1),
            scan_id=scan_id,
            role_name=role_data["role_name"],
            event_type=role_data.get("event_type", "initial_scan"),
            role_json=role_json,
            azure_updated_on=azure_updated,
            diff_json=role_data.get("diff_json", {}),
            summary=role_data.get("summary", "<root>"),
        )
        session.add(history)
        count += 1

    return count


async def seed_extra_history(
    session: AsyncSession,
    extra_history: list[dict],
    scan_id: int,
    roles_by_id: dict[str, dict],
) -> int:
    """Seed additional history entries for testing different event types.

    Args:
        session: Database session
        extra_history: Extra history entries from fixture
        scan_id: The scan ID to link history to
        roles_by_id: Dict of role_id -> role_data from roles fixture for
            looking up original role_json for updated events
    """
    count = 0

    for entry in extra_history:
        azure_updated = parse_azure_timestamp(entry.get("azure_updated_on"))
        event_type = entry["event_type"]

        # Determine role_json based on event type:
        # - deleted: None (role was removed)
        # - created: from the entry itself (new role, no prior data)
        # - updated: from the original role's role_json
        if event_type == "deleted":
            role_json = None
        elif event_type == "created":
            role_json = entry.get("role_json", {})
        else:
            # For updated events, use the original role's role_json
            original_role = roles_by_id.get(entry["role_id"], {})
            role_json = original_role.get("role_json", {})

        history = RoleHistory(
            role_id=entry["role_id"],
            version_number=entry.get("version_number", 2),
            scan_id=scan_id,
            role_name=entry["role_name"],
            event_type=event_type,
            role_json=role_json,
            azure_updated_on=azure_updated,
            diff_json=entry.get("diff_json", {}),
            summary=entry.get("summary", ""),
        )
        session.add(history)
        count += 1

    return count


async def seed_operations(session: AsyncSession, operations_data: list[dict]) -> int:
    """Seed operations from fixture data."""
    now = datetime.now(UTC)
    count = 0

    for op_data in operations_data:
        op = Operation(
            name=op_data["name"],
            display_name=op_data.get("display_name"),
            description=op_data.get("description"),
            origin=op_data.get("origin"),
            provider_display_name=op_data.get("provider_display_name"),
            resource_type=op_data.get("resource_type"),
            resource_type_display_name=op_data.get("resource_type_display_name"),
            is_data_action=op_data.get("is_data_action", False),
            first_seen_at=now,
            last_seen_at=now,
        )
        session.add(op)
        count += 1

    return count


async def seed_scan_status(session: AsyncSession, roles_count: int) -> int:
    """Seed role scan status and return the scan ID."""
    scan = RoleScanStatus(
        scan_timestamp=datetime.now(UTC),
        roles_scanned=roles_count,
        additions=0,
        updates=0,
        deletions=0,
    )
    session.add(scan)
    await session.flush()  # Flush to get the ID
    return scan.id


async def seed_database(db_url: str) -> None:
    """Seed the database with test data from JSON fixture."""
    print(f"Seeding database: {db_url}")  # noqa: T201
    print(f"Loading fixture from: {FIXTURE_PATH}")  # noqa: T201

    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    fixture = load_fixture()
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # Create scan status FIRST to get scan_id for history entries
        roles_data = fixture.get("roles", [])
        scan_id = await seed_scan_status(session, len(roles_data))

        # Seed roles with scan_id
        roles_count = await seed_roles(session, roles_data, scan_id)

        # Build roles_by_id dict for extra_history to look up role_json
        roles_by_id = {r["role_id"]: r for r in roles_data}
        extra_history_count = await seed_extra_history(
            session, fixture.get("extra_history", []), scan_id, roles_by_id
        )
        ops_count = await seed_operations(session, fixture.get("operations", []))
        await session.commit()

    await engine.dispose()
    print(  # noqa: T201 - CLI script output
        f"✓ Seeded {roles_count} roles, {ops_count} operations, "
        f"{extra_history_count} extra history entries"
    )


async def main() -> None:
    """Run the seed script."""
    db_url = os.environ.get("DB_CONNECTION_STRING", "sqlite+aiosqlite:///:memory:")
    await seed_database(db_url)


if __name__ == "__main__":
    asyncio.run(main())
