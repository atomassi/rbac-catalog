import copy

import pytest

from azurerbac.azure.models import RoleDefinition
from azurerbac.core.diffing import diff_roles, diff_summary

READER_ROLE_DICT = {
    "properties": {
        "roleName": "Reader",
        "type": "BuiltInRole",
        "description": "View all resources, but does not allow you to make any changes.",
        "assignableScopes": ["/"],
        "permissions": [
            {
                "actions": ["*/read"],
                "notActions": [],
                "dataActions": [],
                "notDataActions": [],
            }
        ],
        "createdOn": "2015-02-02T21:55:09.8806423Z",
        "updatedOn": "2021-11-11T20:13:47.8628684Z",
    },
    "id": "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7",
    "type": "Microsoft.Authorization/roleDefinitions",
    "name": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
}


def _to_model(d: dict | None) -> RoleDefinition | None:
    return RoleDefinition.model_validate(d) if d else None


def test_no_change_returns_changed_false():
    role_dict = copy.deepcopy(READER_ROLE_DICT)
    d = diff_roles(_to_model(role_dict), _to_model(copy.deepcopy(role_dict)))
    assert d.changed is False
    assert d.changes == []
    assert diff_summary(d) == ""


@pytest.mark.parametrize(
    ("old_role", "new_role"),
    [
        pytest.param(None, "_model", id="created_role"),
        pytest.param("_model", None, id="deleted_role"),
    ],
)
def test_created_or_deleted_role_has_root_diff(old_role: str | None, new_role: str | None):
    """Created or deleted roles should have a root-level diff."""
    role_dict = copy.deepcopy(READER_ROLE_DICT)
    model = _to_model(role_dict)
    old = model if old_role == "_model" else None
    new = model if new_role == "_model" else None
    d = diff_roles(old, new)
    assert d.changed is True
    assert d.changes[0].path == "<root>"


def test_updated_on_change_is_tracked_but_not_considered_update():
    """Metadata fields like updatedOn are tracked but don't trigger changed=True."""
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)
    new_dict["properties"]["updatedOn"] = "2025-12-14T00:00:00.0000000Z"

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    # changed should be False (metadata-only change)
    assert d.changed is False
    # But the change should still be tracked in changes
    paths = {c.path for c in d.changes}
    assert "properties.updatedOn" in paths


def test_metadata_only_changes_not_considered_update():
    """All metadata fields together should not trigger changed=True."""
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)
    new_dict["properties"]["updatedOn"] = "2025-12-14T00:00:00.0000000Z"
    new_dict["properties"]["updatedBy"] = "new-user-id"
    new_dict["properties"]["createdBy"] = "another-user-id"

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    assert d.changed is False
    # All metadata changes should be tracked
    paths = {c.path for c in d.changes}
    assert "properties.updatedOn" in paths
    assert "properties.updatedBy" in paths
    assert "properties.createdBy" in paths


def test_created_on_not_diffed():
    """createdOn is excluded from diff entirely - APIs return inconsistent values."""
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)
    new_dict["properties"]["createdOn"] = "2020-01-01T00:00:00.0000000Z"

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    assert d.changed is False
    # createdOn should NOT appear in changes at all
    paths = {c.path for c in d.changes}
    assert "properties.createdOn" not in paths


def test_real_change_with_metadata_is_detected():
    """A real change alongside metadata changes should trigger changed=True."""
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)
    new_dict["properties"]["updatedOn"] = "2025-12-14T00:00:00.0000000Z"
    new_dict["properties"]["description"] = "New description"

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    assert d.changed is True
    paths = {c.path for c in d.changes}
    assert "properties.updatedOn" in paths
    assert "properties.description" in paths


@pytest.mark.parametrize(
    ("old_scopes", "new_scopes"),
    [
        pytest.param(["/a", "/b", "/c"], ["/c", "/a", "/b"], id="assignable_scopes"),
    ],
)
def test_assignable_scopes_are_order_insensitive(old_scopes, new_scopes):
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)

    old_dict["properties"]["assignableScopes"] = old_scopes
    new_dict["properties"]["assignableScopes"] = new_scopes

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    assert d.changed is False


@pytest.mark.parametrize(
    ("old_actions", "new_actions"),
    [
        pytest.param(["b", "a"], ["a", "b"], id="actions"),
    ],
)
def test_permissions_actions_order_is_ignored(old_actions, new_actions):
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)

    old_dict["properties"]["permissions"] = [
        {
            "actions": old_actions,
            "notActions": [],
            "dataActions": [],
            "notDataActions": [],
        }
    ]
    new_dict["properties"]["permissions"] = [
        {
            "actions": new_actions,
            "notActions": [],
            "dataActions": [],
            "notDataActions": [],
        }
    ]

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    assert d.changed is False


def test_permissions_change_is_detected():
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)

    new_dict["properties"]["permissions"][0]["actions"].append("Microsoft.Authorization/*")

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    assert d.changed is True
    assert any(c.path == "properties.permissions" for c in d.changes)


def test_summary_includes_paths_and_limit():
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)

    new_dict["properties"]["description"] = "new desc"
    new_dict["properties"]["roleName"] = "Reader v2"
    new_dict["properties"]["assignableScopes"] = ["/", "/more"]
    new_dict["type"] = "Microsoft.Authorization/roleDefinitionsV2"

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    s = diff_summary(d, limit=2)
    # should list two paths plus +N more
    assert "+" in s


@pytest.mark.parametrize(
    "test_id",
    ["added", "changed", "removed"],
)
def test_condition_change_detected(test_id):
    """Test that Condition add/change/remove is detected."""
    old_dict = copy.deepcopy(READER_ROLE_DICT)

    if test_id == "added":
        # Old has no condition, new has condition
        new_dict = copy.deepcopy(old_dict)
        new_dict["properties"]["permissions"][0]["condition"] = (
            "@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals '123'"
        )
        new_dict["properties"]["permissions"][0]["conditionVersion"] = "2.0"
    elif test_id == "changed":
        # Old has condition A, new has condition B
        old_dict["properties"]["permissions"][0]["condition"] = (
            "@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals 'abc'"
        )
        old_dict["properties"]["permissions"][0]["conditionVersion"] = "2.0"
        new_dict = copy.deepcopy(old_dict)
        new_dict["properties"]["permissions"][0]["condition"] = (
            "@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals 'xyz'"
        )
    else:  # removed
        # Old has condition, new has none
        old_dict["properties"]["permissions"][0]["condition"] = (
            "@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals '123'"
        )
        old_dict["properties"]["permissions"][0]["conditionVersion"] = "2.0"
        new_dict = copy.deepcopy(old_dict)
        del new_dict["properties"]["permissions"][0]["condition"]
        del new_dict["properties"]["permissions"][0]["conditionVersion"]

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))
    assert d.changed is True
    assert any(c.path == "properties.permissions" for c in d.changes)


def test_diff_timestamps_use_z_suffix():
    """Verify that diff output uses 'Z' suffix format for timestamps."""
    old_dict = copy.deepcopy(READER_ROLE_DICT)
    new_dict = copy.deepcopy(old_dict)
    # Change updatedOn to trigger a diff
    new_dict["properties"]["updatedOn"] = "2025-12-17T09:58:12.949Z"

    d = diff_roles(_to_model(old_dict), _to_model(new_dict))

    # Find the updatedOn change
    updated_change = next(c for c in d.changes if c.path == "properties.updatedOn")

    # Both from and to should use Z suffix (not +00:00)
    assert updated_change.from_value.endswith("Z"), (
        f"Expected 'Z' suffix, got: {updated_change.from_value}"
    )
    assert updated_change.to_value.endswith("Z"), (
        f"Expected 'Z' suffix, got: {updated_change.to_value}"
    )
    assert "+00:00" not in updated_change.from_value
    assert "+00:00" not in updated_change.to_value


def test_both_none_returns_unchanged():
    """diff_roles(None, None) returns an unchanged diff."""
    d = diff_roles(None, None)
    assert d.changed is False
    assert d.changes == []
