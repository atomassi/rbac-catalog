from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from azurerbac.azure.models import RoleDefinition
from azurerbac.core.utils import format_iso_z

# Fields that are tracked but don't constitute a "real" change on their own
METADATA_ONLY_FIELDS = frozenset(
    {
        "properties.updatedOn",
        "properties.updatedBy",
        "properties.createdOn",
        "properties.createdBy",
    }
)


def _sorted_list(value: Iterable) -> list:
    # Stable sort for lists of primitives/strings.
    try:
        return sorted(value)
    except TypeError:
        # Elements are not comparable (e.g., mixed types or dicts)
        return list(value)


def diff_roles(old: RoleDefinition | None, new: RoleDefinition | None) -> dict:
    """Compute a small, opinionated diff between two Azure role definitions.

    Output is JSON-serializable and geared towards UI rendering.
    """
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

    # Common top-level fields
    add("id", old.id, new.id)
    add("name", old.name, new.name)
    add("type", old.type, new.type)

    oldp = old.properties
    newp = new.properties

    add("properties.roleName", oldp.role_name, newp.role_name)
    add("properties.description", oldp.description, newp.description)
    add("properties.type", oldp.type, newp.type)

    # Metadata fields - tracked but don't count as "real" changes on their own
    # Serialize to ISO string with Z suffix for JSON compatibility
    old_updated = format_iso_z(oldp.updated_on)
    new_updated = format_iso_z(newp.updated_on)
    old_created = format_iso_z(oldp.created_on)
    new_created = format_iso_z(newp.created_on)
    add("properties.updatedOn", old_updated, new_updated)
    add("properties.updatedBy", oldp.updated_by, newp.updated_by)
    add("properties.createdOn", old_created, new_created)
    add("properties.createdBy", oldp.created_by, newp.created_by)

    # assignableScopes (set diff)
    old_scopes = _sorted_list(oldp.assignable_scopes)
    new_scopes = _sorted_list(newp.assignable_scopes)
    add("properties.assignableScopes", old_scopes, new_scopes)

    # permissions: normalize for comparison
    old_perms = [p.to_comparable_dict() for p in oldp.permissions]
    new_perms = [p.to_comparable_dict() for p in newp.permissions]

    if old_perms != new_perms:
        changes.append({"path": "properties.permissions", "from": old_perms, "to": new_perms})

    # Determine if there are any "real" changes (not just metadata)
    has_meaningful_changes = any(c["path"] not in METADATA_ONLY_FIELDS for c in changes)

    return {"changed": has_meaningful_changes, "changes": changes}


def diff_summary(diff: dict, limit: int = 4) -> str:
    """Generate a human-readable summary of a diff."""
    if not diff.get("changed"):
        return "No changes"

    changes = diff.get("changes", [])
    parts = [c.get("path", "?") for c in changes[:limit]]
    extra = max(0, len(changes) - limit)

    s = ", ".join(parts)
    if extra:
        s += f" (+{extra} more)"
    return s
