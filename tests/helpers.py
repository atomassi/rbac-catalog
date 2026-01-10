"""Test helper functions and factory builders.

Utility functions for tests that aren't pytest fixtures.
These are plain functions that can be called directly in tests or used
within fixtures in conftest.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import CachedRole
from azurerbac.core.constants import ROLE_DEFINITION_TYPE, RoleStatus

if TYPE_CHECKING:
    from azurerbac.cache.container import CacheContainer
    from azurerbac.cache.models import CachedChangeEvent


# =============================================================================
# Domain Object Factories
# =============================================================================


def make_role_definition(
    role_name: str,
    role_id: str,
    actions: list[str] | None = None,
    not_actions: list[str] | None = None,
    data_actions: list[str] | None = None,
    not_data_actions: list[str] | None = None,
    *,
    description: str | None = None,
    condition: str | None = None,
) -> RoleDefinition:
    """Create a RoleDefinition for testing.

    Args:
        role_name: Display name of the role.
        role_id: Unique role identifier (GUID).
        actions: Optional list of control plane actions.
        not_actions: Optional list of excluded control plane actions.
        data_actions: Optional list of data plane actions.
        not_data_actions: Optional list of excluded data plane actions.
        description: Optional description (defaults to "Test role: {role_name}").
        condition: Optional ABAC condition expression.

    Returns:
        Configured RoleDefinition for testing.
    """
    permission_dict: dict = {
        "actions": actions or [],
        "notActions": not_actions or [],
        "dataActions": data_actions or [],
        "notDataActions": not_data_actions or [],
    }
    if condition:
        permission_dict["condition"] = condition

    return RoleDefinition.model_validate(
        {
            "name": role_id,
            "id": f"/providers/{ROLE_DEFINITION_TYPE}/{role_id}",
            "type": ROLE_DEFINITION_TYPE,
            "properties": {
                "roleName": role_name,
                "type": "BuiltInRole",
                "description": description or f"Test role: {role_name}",
                "permissions": [permission_dict],
                "assignableScopes": ["/"],
            },
        }
    )


def make_operation(
    name: str,
    is_data_action: bool = False,
    *,
    display_name: str | None = None,
    description: str | None = None,
    provider_display_name: str = "",
    resource_type_display_name: str | None = None,
) -> OperationData:
    """Create an OperationData object for testing.

    Args:
        name: Full operation name (e.g., "Microsoft.Storage/storageAccounts/read").
        is_data_action: Whether this is a data plane operation.
        display_name: Human-readable operation name.
        description: Operation description.
        provider_display_name: Display name of the resource provider.
        resource_type_display_name: Display name of the resource type.

    Returns:
        Configured OperationData for testing.
    """
    return OperationData(
        name=name,
        display_name=display_name,
        description=description,
        origin=None,
        provider_display_name=provider_display_name,
        resource_type=None,
        resource_type_display_name=resource_type_display_name,
        is_data_action=is_data_action,
    )


def make_cached_role(
    role_id: str,
    role_name: str,
    status: RoleStatus = RoleStatus.ACTIVE,
    *,
    description: str | None = None,
    actions: list[str] | None = None,
) -> CachedRole:
    """Create a CachedRole for testing.

    Args:
        role_id: Unique role identifier (GUID).
        role_name: Display name of the role.
        status: Role status (ACTIVE or DELETED).
        description: Optional description (defaults to "Test role: {role_name}").
        actions: Optional list of actions (defaults to ["*"]).

    Returns:
        Configured CachedRole for testing.
    """
    definition = make_role_definition(
        role_name=role_name,
        role_id=role_id,
        actions=actions or ["*"],
        description=description,
    )
    return CachedRole(definition=definition, status=status)


# =============================================================================
# Mock Factories
# =============================================================================


def create_mock_embedding_model(
    *,
    is_loaded: bool = True,
    embedding_vector: list[float] | None = None,
    role_embeddings: dict[str, list[float]] | None = None,
    search_results: list[tuple[str, float]] | None = None,
) -> MagicMock:
    """Factory function to create a mock embedding model.

    Args:
        is_loaded: Whether the model appears loaded.
        embedding_vector: Default vector returned by encode_single.
        role_embeddings: Mapping of role_id -> embedding vector.
        search_results: Results for search_vector calls.

    Returns:
        Configured MagicMock embedding model.
    """
    embedding_vector = embedding_vector or [0.1, 0.2, 0.3, 0.4]
    role_embeddings = role_embeddings or {
        "role-1": [0.1, 0.2, 0.3, 0.4],
        "role-2": [0.4, 0.3, 0.2, 0.1],
        "role-3": [-0.1, -0.2, -0.3, -0.4],
    }
    search_results = search_results or [
        ("role-1", 1.0),
        ("role-2", 0.5),
        ("role-3", -0.5),
    ]

    model = MagicMock()
    model.is_loaded = is_loaded
    model.encode_single = MagicMock(return_value=embedding_vector)
    model.encode_single_cached = MagicMock(return_value=tuple(embedding_vector))
    model.embeddings = role_embeddings
    model.search_vector = MagicMock(return_value=search_results)
    return model


def create_mock_knowledge_base(
    role_documents: dict[str, dict[str, str]] | None = None,
) -> MagicMock:
    """Factory function to create a mock knowledge base.

    Args:
        role_documents: Mapping of role_id -> role document dict.

    Returns:
        Configured MagicMock knowledge base.
    """
    role_documents = role_documents or {
        "role-1": {
            "role_name": "Storage Blob Data Reader",
            "description": "Read blob storage data",
        },
        "role-2": {
            "role_name": "Storage Account Contributor",
            "description": "Manage storage accounts",
        },
        "role-3": {
            "role_name": "Owner",
            "description": "Full access including RBAC",
        },
    }

    kb = MagicMock()
    kb.role_documents = role_documents
    kb.get_all_role_names = MagicMock(
        return_value=[doc["role_name"] for doc in role_documents.values()]
    )
    return kb


def create_mock_ollama_client(
    *,
    is_connected: bool = True,
    generate_response: str = "1. Storage Blob Data Reader\n2. Storage Account Contributor",
) -> MagicMock:
    """Factory function to create a mock Ollama client.

    Args:
        is_connected: Whether the client appears connected.
        generate_response: Response returned by generate().

    Returns:
        Configured MagicMock Ollama client.
    """
    client = MagicMock()
    client.is_connected = is_connected
    client.generate = MagicMock(return_value=generate_response)
    return client


# =============================================================================
# Cache Test Helpers
# =============================================================================


def populate_cache_with_operations(cache: CacheContainer, operations: list[OperationData]) -> None:
    """Helper to populate cache with operations for testing.

    Uses the proper swap() pattern with dataclass replace.
    """
    from dataclasses import replace

    from azurerbac.cache.models import build_indexes

    ops_by_name_lower, ops_by_prefix = build_indexes(operations)
    cache.swap(
        replace(
            cache.cache,
            all_operations=operations,
            ops_by_name_lower=ops_by_name_lower,
            ops_by_prefix=ops_by_prefix,
        )
    )


def populate_cache_with_roles(cache: CacheContainer, roles: list[CachedRole]) -> None:
    """Helper to populate cache with roles for testing.

    Uses the proper swap() pattern with dataclass replace.
    """
    from dataclasses import replace

    roles_by_id = {r.role_id: r for r in roles}
    cache.swap(replace(cache.cache, roles_by_id=roles_by_id))


def populate_cache_with_events(cache: CacheContainer, events: list[CachedChangeEvent]) -> None:
    """Helper to populate cache with change events for testing.

    Uses the proper swap() pattern with dataclass replace.
    """
    from dataclasses import replace

    cache.swap(replace(cache.cache, all_change_events=events))


def clear_computed_caches(container: CacheContainer | None = None) -> None:
    """Clear computed caches by swapping to cache with empty computed fields.

    Test-only helper to reset cache state between tests.
    """
    from azurerbac.cache import get_cache_service
    from azurerbac.cache.models import CacheData

    if container is None:
        container = get_cache_service().container

    current = container.cache
    new_cache = CacheData(
        all_operations=current.all_operations,
        roles_by_id=current.roles_by_id,
        all_change_events=current.all_change_events,
        unique_providers=current.unique_providers,
        last_scan=current.last_scan,
        first_scan=current.first_scan,
        ops_by_name_lower=current.ops_by_name_lower,
        ops_by_prefix=current.ops_by_prefix,
    )
    container.swap(new_cache)
