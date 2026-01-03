"""Pytest configuration and shared fixtures.

This module provides the test infrastructure for azurerbac:
- Database fixtures with session-scoped engine for performance
- Mock factories for AI/ML components
- Shared test data fixtures (operations, roles)
- Async session management
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

# Clear Application Insights connection string to prevent OpenTelemetry
# from being enabled during tests (PYTEST_CURRENT_TEST is set automatically by pytest)
os.environ.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)

# Force in-memory database for ALL tests - prevents file-based DB from being used
# Must be set before any imports that trigger Settings.get()
os.environ["DB_CONNECTION_STRING"] = "sqlite+aiosqlite:///:memory:"

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from azurerbac.core import Base

# =============================================================================
# Settings Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_settings_singleton() -> Generator[None, None, None]:
    """Reset settings singleton before each test to ensure fresh environment reads."""
    from azurerbac.settings import Settings

    Settings.reset()
    yield
    Settings.reset()


# =============================================================================
# Database Fixtures - Layered Architecture for Performance
#
# 2. async_session_maker - Creates sessions from an engine
# 3. db_session - Ready-to-use session for tests
# =============================================================================


@pytest_asyncio.fixture
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Create an async engine for testing with in-memory SQLite.

    Function-scoped for test isolation. Each test gets a fresh database.
    Schema is created once per engine via Base.metadata.create_all.

    Yields:
        AsyncEngine: Configured SQLAlchemy async engine with schema initialized.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session_maker(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create an async sessionmaker bound to the test engine.

    Args:
        async_engine: The test database engine.

    Returns:
        Configured sessionmaker that creates sessions for the test engine.
    """
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(
    async_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Create an async database session for testing.

    Provides a clean session that auto-closes after the test.

    Args:
        async_session_maker: Factory for creating sessions.

    Yields:
        AsyncSession: Ready-to-use database session.
    """
    async with async_session_maker() as session:
        yield session


# =============================================================================
# Mock Session Fixtures - For Testing Code That Uses Database Sessions
# =============================================================================


@pytest.fixture
def mock_async_session() -> AsyncMock:
    """Create a mock async database session with context manager support.

    Returns:
        AsyncMock configured as an async context manager.
    """
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.fixture
def mock_session_factory(mock_async_session: AsyncMock) -> MagicMock:
    """Create a mock SessionLocal factory that returns the mock session.

    Args:
        mock_async_session: The mock session to return.

    Returns:
        MagicMock that returns the mock session when called.
    """
    factory = MagicMock()
    factory.return_value = mock_async_session
    return factory


# =============================================================================
# AI/ML Mock Factories - Configurable Mocks via Factory Pattern
#
# These fixtures use the Factory pattern to provide sensible defaults
# while allowing tests to customize behavior as needed.
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


@pytest.fixture
def mock_embedding_model() -> MagicMock:
    """Create a mock embedding model with standard test embeddings.

    Provides 3 roles with different similarity profiles:
    - role-1: High similarity (identical vector)
    - role-2: Medium similarity
    - role-3: Low similarity (opposite vector)

    Returns:
        Configured MagicMock embedding model.
    """
    return create_mock_embedding_model()


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


@pytest.fixture
def mock_knowledge_base() -> MagicMock:
    """Create a mock knowledge base with standard test roles.

    Returns:
        Configured MagicMock knowledge base.
    """
    return create_mock_knowledge_base()


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


@pytest.fixture
def mock_ollama_client() -> MagicMock:
    """Create a mock Ollama client.

    Returns:
        Configured MagicMock Ollama client.
    """
    return create_mock_ollama_client()


@pytest.fixture
def mock_tfidf_recommender() -> MagicMock:
    """Create a mock TF-IDF recommender returning 4-tuple format.

    Returns:
        MagicMock with recommend() returning (role_id, role_name, score, metadata).
    """
    recommender = MagicMock()
    recommender.recommend = MagicMock(
        return_value=[
            ("role-1", "Storage Blob Data Reader", 0.9, {"signals": 2}),
            ("role-2", "Storage Account Contributor", 0.7, {"signals": 1}),
        ]
    )
    return recommender


# =============================================================================
# Shared Test Data Fixtures - Single Source of Truth
#
# These fixtures provide consistent test data across all test files.
# Use these instead of defining local fixtures in individual test files.
# =============================================================================


@pytest.fixture
def operation_names() -> set[str]:
    """Sample operation names as a set for simple pattern matching tests.

    Returns:
        Set of Azure resource operation strings.
    """
    return {
        "Microsoft.Storage/storageAccounts/read",
        "Microsoft.Storage/storageAccounts/write",
        "Microsoft.Storage/storageAccounts/delete",
        "Microsoft.Storage/storageAccounts/listKeys/action",
        "Microsoft.Compute/virtualMachines/read",
        "Microsoft.Compute/virtualMachines/write",
        "Microsoft.Compute/virtualMachines/delete",
        "Microsoft.KeyVault/vaults/read",
        "Microsoft.KeyVault/vaults/secrets/read",
    }


@pytest.fixture
def sample_operations() -> list[dict[str, Any]]:
    """Comprehensive sample operations for role matching and cache tests.

    Includes control plane and data plane operations with full metadata.
    This is the standard fixture for most tests - use this unless you
    need a simpler format.

    Returns:
        List of operation dicts with name, is_data_action, and display fields.
    """
    return [
        # Control plane - Storage
        {
            "name": "Microsoft.Storage/storageAccounts/read",
            "display_name": "Get Storage Account",
            "description": "Returns storage account details",
            "provider_display_name": "Microsoft Storage",
            "resource_type_display_name": "Storage Accounts",
            "is_data_action": False,
        },
        {
            "name": "Microsoft.Storage/storageAccounts/write",
            "display_name": "Create Storage Account",
            "description": "Creates a storage account",
            "provider_display_name": "Microsoft Storage",
            "resource_type_display_name": "Storage Accounts",
            "is_data_action": False,
        },
        {
            "name": "Microsoft.Storage/storageAccounts/delete",
            "display_name": "Delete Storage Account",
            "description": "Deletes a storage account",
            "provider_display_name": "Microsoft Storage",
            "resource_type_display_name": "Storage Accounts",
            "is_data_action": False,
        },
        # Control plane - Compute
        {
            "name": "Microsoft.Compute/virtualMachines/read",
            "display_name": "Get Virtual Machine",
            "description": "Returns VM details",
            "provider_display_name": "Microsoft Compute",
            "resource_type_display_name": "Virtual Machines",
            "is_data_action": False,
        },
        {
            "name": "Microsoft.Compute/virtualMachines/write",
            "display_name": "Create Virtual Machine",
            "description": "Creates a VM",
            "provider_display_name": "Microsoft Compute",
            "resource_type_display_name": "Virtual Machines",
            "is_data_action": False,
        },
        {
            "name": "Microsoft.Compute/virtualMachines/delete",
            "display_name": "Delete Virtual Machine",
            "description": "Deletes a VM",
            "provider_display_name": "Microsoft Compute",
            "resource_type_display_name": "Virtual Machines",
            "is_data_action": False,
        },
        # Control plane - Authorization (for testing notActions exclusions)
        {
            "name": "Microsoft.Authorization/roleAssignments/read",
            "display_name": "Get Role Assignment",
            "description": "Returns role assignment",
            "provider_display_name": "Microsoft Authorization",
            "resource_type_display_name": "Role Assignments",
            "is_data_action": False,
        },
        {
            "name": "Microsoft.Authorization/roleAssignments/write",
            "display_name": "Create Role Assignment",
            "description": "Creates a role assignment",
            "provider_display_name": "Microsoft Authorization",
            "resource_type_display_name": "Role Assignments",
            "is_data_action": False,
        },
        # Control plane - Network
        {
            "name": "Microsoft.Network/virtualNetworks/read",
            "display_name": "Get Virtual Network",
            "description": "Returns virtual network details",
            "provider_display_name": "Microsoft Network",
            "resource_type_display_name": "Virtual Networks",
            "is_data_action": False,
        },
        # Control plane - KeyVault
        {
            "name": "Microsoft.KeyVault/vaults/read",
            "display_name": "Get Key Vault",
            "description": "Returns key vault details",
            "provider_display_name": "Microsoft KeyVault",
            "resource_type_display_name": "Vaults",
            "is_data_action": False,
        },
        # Data plane - Storage blobs
        {
            "name": "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
            "display_name": "Read Blob",
            "description": "Reads blob data",
            "provider_display_name": "Microsoft Storage",
            "resource_type_display_name": "Blobs",
            "is_data_action": True,
        },
        {
            "name": "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
            "display_name": "Write Blob",
            "description": "Writes blob data",
            "provider_display_name": "Microsoft Storage",
            "resource_type_display_name": "Blobs",
            "is_data_action": True,
        },
        # Data plane - KeyVault secrets
        {
            "name": "Microsoft.KeyVault/vaults/secrets/read",
            "display_name": "Read Secret",
            "description": "Reads secret value",
            "provider_display_name": "Microsoft Key Vault",
            "resource_type_display_name": "Secrets",
            "is_data_action": True,
        },
    ]


@pytest.fixture
def large_operations() -> list[dict[str, Any]]:
    """Large set of operations to simulate production data volume.

    Generates ~500 control plane operations and ~75 data plane operations
    across 10 providers, useful for cache and performance testing.

    Returns:
        List of operation dicts with name and is_data_action fields.
    """
    ops: list[dict[str, Any]] = []
    providers = [
        "Microsoft.Storage",
        "Microsoft.Compute",
        "Microsoft.Network",
        "Microsoft.Web",
        "Microsoft.Sql",
        "Microsoft.KeyVault",
        "Microsoft.ServiceBus",
        "Microsoft.EventHub",
        "Microsoft.CognitiveServices",
        "Microsoft.MachineLearning",
    ]
    resources = [
        "accounts",
        "instances",
        "resources",
        "items",
        "objects",
        "containers",
        "queues",
        "topics",
        "services",
        "endpoints",
    ]
    actions = ["read", "write", "delete", "action", "listKeys/action"]

    # Control plane operations
    ops.extend(
        {"name": f"{provider}/{resource}/{action}", "is_data_action": False}
        for provider in providers
        for resource in resources
        for action in actions
    )

    # Data plane operations (subset of providers)
    data_providers = ["Microsoft.Storage", "Microsoft.KeyVault", "Microsoft.ServiceBus"]
    data_resources = ["blobs", "secrets", "keys", "messages", "queues"]

    ops.extend(
        {"name": f"{provider}/data/{resource}/{action}", "is_data_action": True}
        for provider in data_providers
        for resource in data_resources
        for action in actions
    )

    return ops


@pytest.fixture
def sample_roles() -> list[dict[str, Any]]:
    """Sample Azure RBAC role definitions for testing.

    Provides a comprehensive set of roles with varying permission patterns:
    - Reader: Wildcard read-only (*/read)
    - Storage Data Reader: Provider-scoped with data plane
    - Contributor: Broad permissions with exclusions (notActions)
    - Owner: Full access
    - Storage Specific: Explicit operations (no wildcards)
    - Conditional Access: Permissions with conditions

    Returns:
        List of role definition dicts matching Azure API structure.
    """
    return [
        {
            "name": "reader-role-id",
            "properties": {
                "roleName": "Reader",
                "type": "BuiltInRole",
                "description": "Can read all resources",
                "permissions": [
                    {
                        "actions": ["*/read"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                    }
                ],
            },
        },
        {
            "name": "storage-data-reader-id",
            "properties": {
                "roleName": "Storage Data Reader",
                "type": "BuiltInRole",
                "description": "Read storage data",
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/*/read"],
                        "notActions": [],
                        "dataActions": ["Microsoft.Storage/data/*/read"],
                        "notDataActions": [],
                    }
                ],
            },
        },
        {
            "name": "contributor-role-id",
            "properties": {
                "roleName": "Contributor",
                "type": "BuiltInRole",
                "description": "Full access except storage delete",
                "permissions": [
                    {
                        "actions": ["*"],
                        "notActions": ["Microsoft.Storage/*/delete"],
                        "dataActions": ["*"],
                        "notDataActions": ["Microsoft.Storage/data/*/delete"],
                    }
                ],
            },
        },
        {
            "name": "owner-role-id",
            "properties": {
                "roleName": "Owner",
                "type": "BuiltInRole",
                "description": "Full access to all resources",
                "permissions": [
                    {
                        "actions": ["*"],
                        "notActions": [],
                        "dataActions": ["*"],
                        "notDataActions": [],
                    }
                ],
            },
        },
        {
            "name": "storage-specific-id",
            "properties": {
                "roleName": "Storage Specific",
                "type": "BuiltInRole",
                "description": "Only storage operations",
                "permissions": [
                    {
                        "actions": [
                            "Microsoft.Storage/accounts/read",
                            "Microsoft.Storage/accounts/write",
                        ],
                        "notActions": [],
                        "dataActions": [
                            "Microsoft.Storage/data/blobs/read",
                            "Microsoft.Storage/data/blobs/write",
                        ],
                        "notDataActions": [],
                    }
                ],
            },
        },
        {
            "name": "conditional-role-id",
            "properties": {
                "roleName": "Conditional Access",
                "type": "BuiltInRole",
                "description": "Access with conditions",
                "permissions": [
                    {
                        "actions": ["Microsoft.Storage/*"],
                        "notActions": [],
                        "dataActions": [],
                        "notDataActions": [],
                        "Condition": "some condition expression",
                    }
                ],
            },
        },
    ]


@pytest.fixture
def sample_roles_db_format() -> list[dict[str, Any]]:
    """Sample roles in database/cache format for preload_cache testing.

    Returns:
        List of role dicts as stored in database with role_json nested.
    """
    return [
        {
            "role_id": "role-1",
            "role_name": "Reader",
            "role_type": "BuiltInRole",
            "status": "active",
            "updated_on": None,
            "last_seen_at": None,
            "role_json": {
                "name": "role-1",
                "properties": {
                    "roleName": "Reader",
                    "permissions": [{"actions": ["*/read"], "notActions": []}],
                },
            },
        },
        {
            "role_id": "role-2",
            "role_name": "Contributor",
            "role_type": "BuiltInRole",
            "status": "active",
            "updated_on": None,
            "last_seen_at": None,
            "role_json": {
                "name": "role-2",
                "properties": {
                    "roleName": "Contributor",
                    "permissions": [{"actions": ["*"], "notActions": []}],
                },
            },
        },
    ]
