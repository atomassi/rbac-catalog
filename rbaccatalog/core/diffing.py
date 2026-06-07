from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rbaccatalog.azure.models import RoleDefinition
from rbaccatalog.core.utils import format_iso_z


@dataclass(slots=True)
class DiffChange:
    """A single change between two role definitions."""

    path: str
    from_value: Any
    to_value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "from": self.from_value, "to": self.to_value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiffChange:
        """Create a DiffChange from a dictionary."""
        return cls(
            path=data.get("path", ""),
            from_value=data.get("from"),
            to_value=data.get("to"),
        )


@dataclass(slots=True)
class RoleDiff:
    """Result of comparing two role definitions."""

    changed: bool
    changes: list[DiffChange] = field(default_factory=list)
    before_json: dict | None = None
    after_json: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "changed": self.changed,
            "changes": [c.to_dict() for c in self.changes],
        }
        if self.before_json is not None:
            result["before_json"] = self.before_json
        if self.after_json is not None:
            result["after_json"] = self.after_json
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RoleDiff | None:
        """Create a RoleDiff from a dictionary.

        Returns None if data is None or empty.
        """
        if not data:
            return None
        return cls(
            changed=data.get("changed", False),
            changes=[DiffChange.from_dict(c) for c in data.get("changes", [])],
            before_json=data.get("before_json"),
            after_json=data.get("after_json"),
        )


METADATA_ONLY_FIELDS = frozenset(
    {
        "properties.updatedOn",
        "properties.updatedBy",
        "properties.createdOn",
        "properties.createdBy",
    }
)

_ROOT_PATH = "<root>"


def diff_roles(old: RoleDefinition | None, new: RoleDefinition | None) -> RoleDiff:
    """Compute diff between two role definitions for UI rendering."""
    if old is None and new is None:
        return RoleDiff(changed=False)

    if old is None:
        # new is guaranteed not-None here (both-None case returned above)
        assert new is not None
        return RoleDiff(
            changed=True,
            changes=[DiffChange(path=_ROOT_PATH, from_value=None, to_value=new.to_dict())],
        )

    if new is None:
        return RoleDiff(
            changed=True,
            changes=[DiffChange(path=_ROOT_PATH, from_value=old.to_dict(), to_value=None)],
        )

    changes: list[DiffChange] = []

    def add(path: str, a: Any, b: Any) -> None:
        if a != b:
            changes.append(DiffChange(path=path, from_value=a, to_value=b))

    add("id", old.id, new.id)
    add("name", old.name, new.name)
    add("type", old.type, new.type)

    oldp, newp = old.properties, new.properties
    add("properties.roleName", oldp.role_name, newp.role_name)
    add("properties.description", oldp.description, newp.description)
    add("properties.type", oldp.type, newp.type)

    add("properties.updatedOn", format_iso_z(oldp.updated_on), format_iso_z(newp.updated_on))
    add("properties.updatedBy", oldp.updated_by, newp.updated_by)
    # createdOn is immutable - don't include in diff (APIs may return different values)
    add("properties.createdBy", oldp.created_by, newp.created_by)

    add(
        "properties.assignableScopes",
        sorted(oldp.assignable_scopes),
        sorted(newp.assignable_scopes),
    )

    old_perms = [p.to_comparable_dict() for p in oldp.permissions]
    new_perms = [p.to_comparable_dict() for p in newp.permissions]
    if old_perms != new_perms:
        changes.append(
            DiffChange(path="properties.permissions", from_value=old_perms, to_value=new_perms)
        )

    has_meaningful = any(c.path not in METADATA_ONLY_FIELDS for c in changes)
    return RoleDiff(changed=has_meaningful, changes=changes)


def diff_summary(diff: RoleDiff, limit: int = 4) -> str:
    """Generate human-readable diff summary for logging and storage."""
    parts = [c.path for c in diff.changes[:limit]]
    extra = max(0, len(diff.changes) - limit)
    return ", ".join(parts) + (f" (+{extra} more)" if extra else "")
