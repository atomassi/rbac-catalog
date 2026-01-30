"""Test helper functions and factory builders."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.models import CachedRole
from azurerbac.core.constants import ROLE_DEFINITION_TYPE, RoleStatus

if TYPE_CHECKING:
    from azurerbac.cache import CacheService
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
    """Create a RoleDefinition for testing with a single permission block."""
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


def make_role_with_multiple_permissions(
    role_name: str,
    role_id: str,
    permission_blocks: list[dict],
    *,
    description: str | None = None,
) -> RoleDefinition:
    """Create a RoleDefinition with multiple permission blocks.

    Args:
        role_name: Display name of the role.
        role_id: GUID of the role.
        permission_blocks: List of permission dicts, each with keys:
            - actions: list[str]
            - notActions: list[str] (optional)
            - dataActions: list[str] (optional)
            - notDataActions: list[str] (optional)
        description: Role description.

    Example:
        make_role_with_multiple_permissions(
            "Test Role", "test-guid",
            permission_blocks=[
                {"actions": ["Microsoft.Storage/*"], "notActions": ["Microsoft.Storage/*/write"]},
                {"actions": ["Microsoft.Storage/storageAccounts/write"]},
            ]
        )
    """
    # Normalize permission blocks to ensure all keys exist
    normalized_blocks = []
    for block in permission_blocks:
        normalized_blocks.append(
            {
                "actions": block.get("actions", []),
                "notActions": block.get("notActions", []),
                "dataActions": block.get("dataActions", []),
                "notDataActions": block.get("notDataActions", []),
            }
        )

    return RoleDefinition.model_validate(
        {
            "name": role_id,
            "id": f"/providers/{ROLE_DEFINITION_TYPE}/{role_id}",
            "type": ROLE_DEFINITION_TYPE,
            "properties": {
                "roleName": role_name,
                "type": "BuiltInRole",
                "description": description or f"Test role: {role_name}",
                "permissions": normalized_blocks,
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
    """Create an OperationData object for testing."""
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
    """Create a CachedRole for testing."""
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
    """Create a mock embedding model for testing."""
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
    """Create a mock knowledge base for testing."""
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
    """Create a mock Ollama client for testing."""
    client = MagicMock()
    client.is_connected = is_connected
    client.generate = MagicMock(return_value=generate_response)
    return client


# =============================================================================
# Cache Test Helpers
# =============================================================================


def populate_cache_with_operations(cache: CacheService, operations: list[OperationData]) -> None:
    """Populate cache with operations using swap() pattern."""
    from dataclasses import replace

    from azurerbac.cache.models import Indexes, build_indexes

    ops_by_name_lower, ops_by_prefix = build_indexes(operations)
    current = cache.cache
    new_source = replace(current.source, all_operations=operations)
    new_indexes = Indexes(
        ops_by_name_lower=ops_by_name_lower,
        ops_by_prefix=ops_by_prefix,
        ops_by_prefix_by_plane={},
    )
    cache.swap(replace(current, source=new_source, indexes=new_indexes))


def populate_cache_with_roles(cache: CacheService, roles: list[CachedRole]) -> None:
    """Populate cache with roles using swap() pattern."""
    from dataclasses import replace

    roles_by_id = {r.role_id: r for r in roles}
    current = cache.cache
    new_source = replace(current.source, roles_by_id=roles_by_id)
    cache.swap(replace(current, source=new_source))


def populate_cache_with_events(cache: CacheService, events: list[CachedChangeEvent]) -> None:
    """Populate cache with change events using swap() pattern."""
    from dataclasses import replace

    current = cache.cache
    new_source = replace(current.source, all_change_events=events)
    cache.swap(replace(current, source=new_source))


def clear_computed_caches(service: CacheService | None = None) -> None:
    """Clear computed caches by swapping to cache with empty computed fields."""
    from azurerbac.cache import get_cache_service
    from azurerbac.cache.models import CacheData

    if service is None:
        service = get_cache_service()

    current = service.cache
    new_cache = CacheData.create(
        all_operations=current.all_operations,
        roles_by_id=current.roles_by_id,
        all_change_events=current.all_change_events,
        unique_providers=current.unique_providers,
        last_scan=current.last_scan,
        first_scan=current.first_scan,
        ops_by_name_lower=current.ops_by_name_lower,
        ops_by_prefix=current.ops_by_prefix,
    )
    service.swap(new_cache)
