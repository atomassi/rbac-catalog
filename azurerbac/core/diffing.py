from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from azurerbac.azure.models import RoleDefinition
from azurerbac.core.utils import format_iso_z

METADATA_ONLY_FIELDS = frozenset(
    {
        "properties.updatedOn",
        "properties.updatedBy",
        "properties.createdOn",
        "properties.createdBy",
    }
)


def _sorted_list(value: Iterable) -> list:
    try:
        return sorted(value)
    except TypeError:
        return list(value)


def diff_roles(old: RoleDefinition | None, new: RoleDefinition | None) -> dict:
    """Compute diff between two role definitions for UI rendering."""
    if old is None and new is None:
        return {"changed": False, "changes": []}

    if old is None:
        return {
            "changed": True,
            "changes": [{"path": "<root>", "from": None, "to": new.to_dict() if new else None}],
        }

    if new is None:
        return {
            "changed": True,
            "changes": [{"path": "<root>", "from": old.to_dict() if old else None, "to": None}],
        }

    changes: list[dict] = []

    def add(path: str, a: Any, b: Any) -> None:
        if a != b:
            changes.append({"path": path, "from": a, "to": b})

    add("id", old.id, new.id)
    add("name", old.name, new.name)
    add("type", old.type, new.type)

    oldp, newp = old.properties, new.properties
    add("properties.roleName", oldp.role_name, newp.role_name)
    add("properties.description", oldp.description, newp.description)
    add("properties.type", oldp.type, newp.type)

    add("properties.updatedOn", format_iso_z(oldp.updated_on), format_iso_z(newp.updated_on))
    add("properties.updatedBy", oldp.updated_by, newp.updated_by)
    add("properties.createdOn", format_iso_z(oldp.created_on), format_iso_z(newp.created_on))
    add("properties.createdBy", oldp.created_by, newp.created_by)

    add(
        "properties.assignableScopes",
        _sorted_list(oldp.assignable_scopes),
        _sorted_list(newp.assignable_scopes),
    )

    old_perms = [p.to_comparable_dict() for p in oldp.permissions]
    new_perms = [p.to_comparable_dict() for p in newp.permissions]
    if old_perms != new_perms:
        changes.append({"path": "properties.permissions", "from": old_perms, "to": new_perms})

    has_meaningful = any(c["path"] not in METADATA_ONLY_FIELDS for c in changes)
    return {"changed": has_meaningful, "changes": changes}


def diff_summary(diff: dict, limit: int = 4) -> str:
    """Generate human-readable diff summary."""
    if not diff.get("changed"):
        return "No changes"
    changes = diff.get("changes", [])
    parts = [c.get("path", "?") for c in changes[:limit]]
    extra = max(0, len(changes) - limit)
    return ", ".join(parts) + (f" (+{extra} more)" if extra else "")
