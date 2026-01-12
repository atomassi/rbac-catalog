"""Azure RBAC Database Models.

Schema Overview
---------------

┌─────────────────────────────────────────────────────────────────────────────────┐
│                            ROLE TRACKING TABLES                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────┐      ┌──────────────────┐                                    │
│  │     roles     │      │   role_history   │                                    │
│  ├───────────────┤      ├──────────────────┤                                    │
│  │ role_id (PK)  │◄─────│ role_id (FK)     │                                    │
│  │ role_name     │      │ id (PK)          │                                    │
│  │ status        │      │ version_number   │                                    │
│  └───────────────┘      │ scan_id ─────────│────┐                               │
│                         │ azure_updated    │    │                               │
│                         │ role_name        │    │   ← created | initial_scan |  │
│                         │ event_type       │    │     updated | deleted         │
│                         │ role_json (JSONB)│    │   (NULL for deletes)          │
│                         │ diff_json        │    │                               │
│                         │ summary          │    │                               │
│                         └──────────────────┘    │                               │
│                                                 │                               │
│  ┌────────────────────┐                         │                               │
│  │  role_scan_status  │◄────────────────────────┘                               │
│  ├────────────────────┤                                                         │
│  │ id (PK)            │                                                         │
│  │ scan_timestamp     │                                                         │
│  │ roles_scanned      │                                                         │
│  │ additions          │                                                         │
│  │ updates            │                                                         │
│  │ deletions          │                                                         │
│  └────────────────────┘                                                         │
│                                                                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                             OPERATION TABLES                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────────────────┐                                              │
│  │          operations           │                                              │
│  ├───────────────────────────────┤                                              │
│  │ name (PK)                     │                                              │
│  │ display_name                  │                                              │
│  │ description                   │                                              │
│  │ origin                        │                                              │
│  │ provider_display_name         │                                              │
│  │ resource_type                 │                                              │
│  │ resource_type_display_name    │                                              │
│  │ is_data_action                │                                              │
│  │ first_seen_at                 │                                              │
│  │ last_seen_at                  │                                              │
│  └───────────────────────────────┘                                              │
│                                                                                 │
│  ┌─────────────────────────┐                                                    │
│  │  operation_scan_status  │                                                    │
│  ├─────────────────────────┤                                                    │
│  │ id (PK)                 │                                                    │
│  │ scan_timestamp          │                                                    │
│  │ operations_scanned      │                                                    │
│  │ providers_scanned       │                                                    │
│  │ additions               │                                                    │
│  │ updates                 │                                                    │
│  │ deletions               │                                                    │
│  └─────────────────────────┘                                                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘

Relationships: Role 1:N RoleHistory (cascade delete), RoleHistory N:1 RoleScanStatus
Event types: CREATED, INITIAL_SCAN, UPDATED, DELETED
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .constants import EventType, RoleStatus

if TYPE_CHECKING:
    from azurerbac.azure.models import RoleDefinition


class Base(DeclarativeBase):
    pass


class Role(Base):
    """Tracks role identity and current state. Access latest version via history[0]."""

    __tablename__ = "roles"

    # Azure role definition GUID
    role_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # Denormalized for efficient DB ORDER BY/WHERE queries
    role_name: Mapped[str] = mapped_column(String(256), index=True, default="")

    status: Mapped[RoleStatus] = mapped_column(
        Enum(RoleStatus, native_enum=False, values_callable=lambda e: [x.value for x in e]),
        default=RoleStatus.ACTIVE,
        server_default="active",
        index=True,
    )

    history: Mapped[list[RoleHistory]] = relationship(
        back_populates="role",
        cascade="all, delete-orphan",
        order_by="RoleHistory.version_number.desc()",
        lazy="selectin",  # Eager load history efficiently
    )

    def __repr__(self) -> str:
        return f"<Role {self.role_id} '{self.role_name}' ({self.status})>"

    @property
    def current_version(self) -> RoleHistory | None:
        """Get the latest version (highest version_number)."""
        return self.history[0] if self.history else None

    @property
    def last_known_version(self) -> RoleHistory | None:
        """Most recent version with role_json (not NULL). Works for deleted roles."""
        for h in self.history:
            if h.role_json is not None:
                return h
        return None

    @property
    def last_known_json(self) -> dict:
        """Most recent role JSON. Works for deleted roles."""
        lkv = self.last_known_version
        return lkv.role_json if lkv and lkv.role_json else {}

    @property
    def role_type(self) -> str | None:
        """Role type from role JSON (works for deleted roles)."""
        return self.last_known_json.get("properties", {}).get("type")

    @property
    def created_on(self) -> dt.datetime | None:
        """Azure's createdOn timestamp. Works for deleted roles via last_known_json."""
        created_str = self.last_known_json.get("properties", {}).get("createdOn")
        if created_str:
            try:
                return dt.datetime.fromisoformat(created_str)
            except (ValueError, AttributeError):
                pass
        return None

    @property
    def updated_on(self) -> dt.datetime | None:
        cv = self.current_version
        return cv.azure_updated_on if cv else None

    @property
    def first_seen_at(self) -> dt.datetime | None:
        """When this role was first seen (oldest history entry's scan)."""
        if self.history:
            # history is ordered by version_number DESC, so last item is oldest
            oldest = self.history[-1]
            return oldest.scan.scan_timestamp if oldest.scan else None
        return None

    @property
    def last_seen_at(self) -> dt.datetime | None:
        cv = self.current_version
        return cv.scan.scan_timestamp if cv and cv.scan else None

    @property
    def role_definition(self) -> RoleDefinition | None:
        """RoleDefinition from current version. None for deleted roles."""
        cv = self.current_version
        return cv.role_definition if cv else None

    @property
    def last_known_definition(self) -> RoleDefinition | None:
        """Most recent RoleDefinition, even for deleted roles."""
        lkv = self.last_known_version
        return lkv.role_definition if lkv else None


class RoleHistory(Base):
    """Stores each historical version/event of a role definition.

    Combines version tracking with event metadata in a single table.
    For delete events: role_json is NULL (no JSON for deleted role).
    """

    __tablename__ = "role_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    role_id: Mapped[str] = mapped_column(ForeignKey("roles.role_id"), index=True)

    # Version number for this role (1, 2, 3, ...)
    version_number: Mapped[int] = mapped_column(Integer, index=True)

    # FK to the scan that detected this change
    scan_id: Mapped[int | None] = mapped_column(
        ForeignKey("role_scan_status.id"), nullable=True, index=True
    )

    # Azure's updatedOn timestamp for this version
    azure_updated_on: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Denormalized for historical accuracy and query efficiency
    role_name: Mapped[str] = mapped_column(String(256), index=True)

    event_type: Mapped[EventType] = mapped_column(String(32), index=True)

    # The full role definition JSON (NULL for delete events)
    role_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # System diffs: structured JSON containing field-level changes
    diff_json: Mapped[dict] = mapped_column(JSON)

    # Short text summary for quick UI lists
    summary: Mapped[str] = mapped_column(Text)

    role: Mapped[Role] = relationship(back_populates="history")

    # Link to the scan that detected this change
    scan: Mapped[RoleScanStatus | None] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return f"<RoleHistory {self.role_id} v{self.version_number} {self.event_type}>"

    @property
    def role_definition(self) -> RoleDefinition | None:
        """Parse role_json through RoleDefinition model.

        Returns None for delete events (where role_json is NULL).
        """
        if self.role_json is None:
            return None
        from azurerbac.azure.models import RoleDefinition

        return RoleDefinition.model_validate(self.role_json)


Index(
    "ix_role_history_role_version",
    RoleHistory.role_id,
    RoleHistory.version_number,
    unique=True,
)

# Composite index for dashboard queries filtering by event_type and scan
Index(
    "ix_role_history_type_scan",
    RoleHistory.event_type,
    RoleHistory.scan_id.desc(),
)


class RoleScanStatus(Base):
    """Tracks each role scan run."""

    __tablename__ = "role_scan_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    scan_timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), index=True
    )

    roles_scanned: Mapped[int] = mapped_column(Integer, default=0)

    additions: Mapped[int] = mapped_column(Integer, default=0)

    updates: Mapped[int] = mapped_column(Integer, default=0)

    deletions: Mapped[int] = mapped_column(Integer, default=0)


class Operation(Base):
    """Stores Azure provider operations (permissions).

    All operation fields are stored as proper columns for efficient querying.
    """

    __tablename__ = "operations"

    # Operation name is unique, e.g., "Microsoft.AAD/domainServices/read"
    name: Mapped[str] = mapped_column(String(512), primary_key=True)

    # Display name from Azure API (display.operation)
    display_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Description from Azure API (display.description)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Origin from Azure API
    origin: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Provider display name - indexed for grouping operations by provider
    provider_display_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True, index=True
    )

    # Resource type name - indexed for filtering by resource type
    resource_type: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)

    # Resource type display name from Azure API (display.resource)
    resource_type_display_name: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Whether this is a data action - indexed for filtering
    is_data_action: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # When this operation was first seen (set on insert, never updated)
    first_seen_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # When this operation was last seen
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), index=True
    )


class OperationScanStatus(Base):
    """Tracks each operations scan run."""

    __tablename__ = "operation_scan_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    scan_timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC), index=True
    )

    operations_scanned: Mapped[int] = mapped_column(Integer, default=0)

    providers_scanned: Mapped[int] = mapped_column(Integer, default=0)

    additions: Mapped[int] = mapped_column(Integer, default=0)

    updates: Mapped[int] = mapped_column(Integer, default=0)

    deletions: Mapped[int] = mapped_column(Integer, default=0)
