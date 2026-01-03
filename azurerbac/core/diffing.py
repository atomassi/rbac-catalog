from __future__ import annotations

from typing import TYPE_CHECKING, Any

from azurerbac.azure.roles import get_permission_condition, get_permission_condition_version

if TYPE_CHECKING:
    from collections.abc import Iterable


def _sorted_list(value: Iterable) -> list:
    # Stable sort for lists of primitives/strings.
    try:
        return sorted(value)
    except TypeError:
        # Elements are not comparable (e.g., mixed types or dicts)
        return list(value)


def diff_roles(old: dict | None, new: dict | None) -> dict:
    """Compute a small, opinionated diff between two Azure role definition JSON blobs.

    Output is JSON-serializable and geared towards UI rendering.
    """
    if old is None and new is None:
        return {"changed": False, "changes": []}

    if old is None:
        return {
            "changed": True,
            "changes": [{"path": "<root>", "from": None, "to": new}],
        }

    if new is None:
        return {
            "changed": True,
            "changes": [{"path": "<root>", "from": old, "to": None}],
        }

    changes: list[dict] = []

    def add(path: str, a: Any, b: Any) -> None:
        if a != b:
            changes.append({"path": path, "from": a, "to": b})

    # Common top-level fields
    add("id", old.get("id"), new.get("id"))
    add("name", old.get("name"), new.get("name"))
    add("type", old.get("type"), new.get("type"))

    oldp = old.get("properties", {}) or {}
    newp = new.get("properties", {}) or {}

    add("properties.roleName", oldp.get("roleName"), newp.get("roleName"))
    add("properties.description", oldp.get("description"), newp.get("description"))
    add("properties.type", oldp.get("type"), newp.get("type"))
    add("properties.updatedOn", oldp.get("updatedOn"), newp.get("updatedOn"))

    # assignableScopes (set diff)
    old_scopes = _sorted_list(oldp.get("assignableScopes", []) or [])
    new_scopes = _sorted_list(newp.get("assignableScopes", []) or [])
    add("properties.assignableScopes", old_scopes, new_scopes)

    # permissions: normalize each permission object into sorted lists
    def norm_permissions(p: dict) -> dict:
        """Normalize a permission block, including conditions."""
        normalized = {
            "actions": _sorted_list(p.get("actions", []) or []),
            "notActions": _sorted_list(p.get("notActions", []) or []),
            "dataActions": _sorted_list(p.get("dataActions", []) or []),
            "notDataActions": _sorted_list(p.get("notDataActions", []) or []),
        }
        # Include condition fields if present (ABAC conditions)
        condition = get_permission_condition(p)
        condition_version = get_permission_condition_version(p)
        if condition:
            normalized["Condition"] = condition
        if condition_version:
            normalized["ConditionVersion"] = condition_version
        return normalized

    old_perms = [norm_permissions(p) for p in (oldp.get("permissions", []) or [])]
    new_perms = [norm_permissions(p) for p in (newp.get("permissions", []) or [])]

    if old_perms != new_perms:
        changes.append({"path": "properties.permissions", "from": old_perms, "to": new_perms})

    return {"changed": len(changes) > 0, "changes": changes}


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
