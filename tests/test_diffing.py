import copy

import pytest

from azurerbac.core.diffing import diff_roles, diff_summary


def _reader_role() -> dict:
    return {
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
        # Azure role definition ID format
        "id": "/providers/Microsoft.Authorization/roleDefinitions/acdd72a7-3385-48ef-bd42-f606fba81ae7",  # noqa: E501
        "type": "Microsoft.Authorization/roleDefinitions",
        "name": "acdd72a7-3385-48ef-bd42-f606fba81ae7",
    }


def test_no_change_returns_changed_false():
    role = _reader_role()
    d = diff_roles(role, copy.deepcopy(role))
    assert d["changed"] is False
    assert d["changes"] == []
    assert diff_summary(d) == "No changes"


def test_created_role_has_root_diff():
    role = _reader_role()
    d = diff_roles(None, role)
    assert d["changed"] is True
    assert d["changes"][0]["path"] == "<root>"


def test_deleted_role_has_root_diff():
    role = _reader_role()
    d = diff_roles(role, None)
    assert d["changed"] is True
    assert d["changes"][0]["path"] == "<root>"


def test_updated_on_change_is_detected():
    old = _reader_role()
    new = copy.deepcopy(old)
    new["properties"]["updatedOn"] = "2025-12-14T00:00:00.0000000Z"

    d = diff_roles(old, new)
    assert d["changed"] is True
    paths = {c["path"] for c in d["changes"]}
    assert "properties.updatedOn" in paths


def test_assignable_scopes_are_order_insensitive():
    old = _reader_role()
    new = copy.deepcopy(old)

    old["properties"]["assignableScopes"] = ["/a", "/b", "/c"]
    new["properties"]["assignableScopes"] = ["/c", "/a", "/b"]

    d = diff_roles(old, new)
    assert d["changed"] is False


def test_permissions_actions_order_is_ignored():
    old = _reader_role()
    new = copy.deepcopy(old)

    old["properties"]["permissions"] = [
        {
            "actions": ["b", "a"],
            "notActions": [],
            "dataActions": [],
            "notDataActions": [],
        }
    ]
    new["properties"]["permissions"] = [
        {
            "actions": ["a", "b"],
            "notActions": [],
            "dataActions": [],
            "notDataActions": [],
        }
    ]

    d = diff_roles(old, new)
    assert d["changed"] is False


def test_permissions_change_is_detected():
    old = _reader_role()
    new = copy.deepcopy(old)

    new["properties"]["permissions"][0]["actions"].append("Microsoft.Authorization/*")

    d = diff_roles(old, new)
    assert d["changed"] is True
    assert any(c["path"] == "properties.permissions" for c in d["changes"])


def test_summary_includes_paths_and_limit():
    old = _reader_role()
    new = copy.deepcopy(old)

    new["properties"]["description"] = "new desc"
    new["properties"]["roleName"] = "Reader v2"
    new["properties"]["assignableScopes"] = ["/", "/more"]
    new["type"] = "Microsoft.Authorization/roleDefinitionsV2"
    new["properties"]["updatedOn"] = "2025-12-14T00:00:00Z"

    d = diff_roles(old, new)
    s = diff_summary(d, limit=2)
    # should list two paths plus +N more
    assert "+" in s


@pytest.mark.parametrize(
    "test_id",
    ["added", "changed", "removed"],
)
def test_condition_change_detected(test_id):
    """Test that Condition add/change/remove is detected."""
    old = _reader_role()

    if test_id == "added":
        # Old has no condition, new has condition
        new = copy.deepcopy(old)
        new["properties"]["permissions"][0][
            "Condition"
        ] = "@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals '123'"
        new["properties"]["permissions"][0]["ConditionVersion"] = "2.0"
    elif test_id == "changed":
        # Old has condition A, new has condition B
        old["properties"]["permissions"][0][
            "Condition"
        ] = "@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals 'abc'"
        old["properties"]["permissions"][0]["ConditionVersion"] = "2.0"
        new = copy.deepcopy(old)
        new["properties"]["permissions"][0][
            "Condition"
        ] = "@Resource[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals 'xyz'"
    else:  # removed
        # Old has condition, new has none
        old["properties"]["permissions"][0][
            "Condition"
        ] = "@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] GuidEquals '123'"
        old["properties"]["permissions"][0]["ConditionVersion"] = "2.0"
        new = copy.deepcopy(old)
        del new["properties"]["permissions"][0]["Condition"]
        del new["properties"]["permissions"][0]["ConditionVersion"]

    d = diff_roles(old, new)
    assert d["changed"] is True
    assert any(c["path"] == "properties.permissions" for c in d["changes"])
