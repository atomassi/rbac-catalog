"""Pytest configuration and shared fixtures."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Generator
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

from rbaccatalog.azure.models import OperationData, RoleDefinition
from rbaccatalog.cache.models import CachedRole
from rbaccatalog.core import Base
from rbaccatalog.core.enums import RoleStatus

# Import factory functions from helpers for use in fixtures
from tests.helpers import (
    create_mock_embedding_model,
    create_mock_knowledge_base,
    create_mock_ollama_client,
    make_operation,
)

# =============================================================================
# Settings Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_settings_singleton() -> Generator[None, None, None]:
    """Reset settings singleton before/after each test."""
    from rbaccatalog.settings import Settings

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
    """Create in-memory SQLite engine for test isolation."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def async_session_maker(
    async_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create sessionmaker bound to the test engine."""
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(
    async_session_maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Create database session for testing."""
    async with async_session_maker() as session:
        yield session


# =============================================================================
# Mock Session Fixtures - For Testing Code That Uses Database Sessions
# =============================================================================


@pytest.fixture
def mock_async_session() -> AsyncMock:
    """Create mock async session with context manager support."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


@pytest.fixture
def mock_session_factory(mock_async_session: AsyncMock) -> MagicMock:
    """Create mock SessionLocal factory."""
    factory = MagicMock()
    factory.return_value = mock_async_session
    return factory


# =============================================================================
# AI/ML Mock Fixtures
#
# These fixtures use factory functions from helpers.py to provide
# sensible defaults while allowing tests to customize behavior as needed.
# =============================================================================


@pytest.fixture
def mock_embedding_model() -> MagicMock:
    """Mock embedding model with test embeddings (role-1: high, role-2: med, role-3: low)."""
    return create_mock_embedding_model()


@pytest.fixture
def mock_knowledge_base() -> MagicMock:
    """Mock knowledge base with test roles."""
    return create_mock_knowledge_base()


@pytest.fixture
def mock_ollama_client() -> MagicMock:
    """Mock Ollama client."""
    return create_mock_ollama_client()


@pytest.fixture
def mock_tfidf_recommender() -> MagicMock:
    """Mock TF-IDF recommender returning 4-tuple format."""
    recommender = MagicMock()
    recommender.recommend = MagicMock(
        return_value=[
            ("role-1", "Storage Blob Data Reader", 0.9, {"signals": 2}),
            ("role-2", "Storage Account Contributor", 0.7, {"signals": 1}),
        ]
    )
    return recommender


# =============================================================================
# Shared Test Data Fixtures
# =============================================================================


@pytest.fixture
def operation_names() -> set[str]:
    """Sample operation names for pattern matching tests."""
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
def sample_operations() -> list[OperationData]:
    """Sample operations with control plane and data plane for cache tests."""
    return [
        # Control plane - Storage
        make_operation(
            "Microsoft.Storage/storageAccounts/read",
            display_name="Get Storage Account",
            description="Returns storage account details",
            provider_display_name="Microsoft Storage",
            resource_type_display_name="Storage Accounts",
        ),
        make_operation(
            "Microsoft.Storage/storageAccounts/write",
            display_name="Create Storage Account",
            description="Creates a storage account",
            provider_display_name="Microsoft Storage",
            resource_type_display_name="Storage Accounts",
        ),
        make_operation(
            "Microsoft.Storage/storageAccounts/delete",
            display_name="Delete Storage Account",
            description="Deletes a storage account",
            provider_display_name="Microsoft Storage",
            resource_type_display_name="Storage Accounts",
        ),
        # Control plane - Compute
        make_operation(
            "Microsoft.Compute/virtualMachines/read",
            display_name="Get Virtual Machine",
            description="Returns VM details",
            provider_display_name="Microsoft Compute",
            resource_type_display_name="Virtual Machines",
        ),
        make_operation(
            "Microsoft.Compute/virtualMachines/write",
            display_name="Create Virtual Machine",
            description="Creates a VM",
            provider_display_name="Microsoft Compute",
            resource_type_display_name="Virtual Machines",
        ),
        make_operation(
            "Microsoft.Compute/virtualMachines/delete",
            display_name="Delete Virtual Machine",
            description="Deletes a VM",
            provider_display_name="Microsoft Compute",
            resource_type_display_name="Virtual Machines",
        ),
        # Control plane - Authorization (for testing notActions exclusions)
        make_operation(
            "Microsoft.Authorization/roleAssignments/read",
            display_name="Get Role Assignment",
            description="Returns role assignment",
            provider_display_name="Microsoft Authorization",
            resource_type_display_name="Role Assignments",
        ),
        make_operation(
            "Microsoft.Authorization/roleAssignments/write",
            display_name="Create Role Assignment",
            description="Creates a role assignment",
            provider_display_name="Microsoft Authorization",
            resource_type_display_name="Role Assignments",
        ),
        # Control plane - Network
        make_operation(
            "Microsoft.Network/virtualNetworks/read",
            display_name="Get Virtual Network",
            description="Returns virtual network details",
            provider_display_name="Microsoft Network",
            resource_type_display_name="Virtual Networks",
        ),
        # Control plane - KeyVault
        make_operation(
            "Microsoft.KeyVault/vaults/read",
            display_name="Get Key Vault",
            description="Returns key vault details",
            provider_display_name="Microsoft KeyVault",
            resource_type_display_name="Vaults",
        ),
        # Data plane - Storage blobs
        make_operation(
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
            is_data_action=True,
            display_name="Read Blob",
            description="Reads blob data",
            provider_display_name="Microsoft Storage",
            resource_type_display_name="Blobs",
        ),
        make_operation(
            "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/write",
            is_data_action=True,
            display_name="Write Blob",
            description="Writes blob data",
            provider_display_name="Microsoft Storage",
            resource_type_display_name="Blobs",
        ),
        # Data plane - KeyVault secrets
        make_operation(
            "Microsoft.KeyVault/vaults/secrets/read",
            is_data_action=True,
            display_name="Read Secret",
            description="Reads secret value",
            provider_display_name="Microsoft Key Vault",
            resource_type_display_name="Secrets",
        ),
    ]


@pytest.fixture
def populated_cache(sample_operations: list[OperationData]) -> Generator[None, None, None]:
    """Populate global cache with sample_operations for role recommender tests."""
    from rbaccatalog.cache import get_cache_service
    from rbaccatalog.cache.build import precompute_all
    from rbaccatalog.cache.models import CacheData

    # Create minimal cache with just the operations (no roles needed for op_sets)
    cache = precompute_all(roles=[], all_operations=sample_operations)
    get_cache_service().swap(cache)
    yield
    # Reset to empty cache after test (CacheData() is cheaper than precompute_all)
    get_cache_service().swap(CacheData())


@pytest.fixture
def large_operations() -> list[OperationData]:
    """Large set (~575) of operations for cache/performance testing."""
    ops: list[OperationData] = []
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
        make_operation(f"{provider}/{resource}/{action}", is_data_action=False)
        for provider in providers
        for resource in resources
        for action in actions
    )

    # Data plane operations (subset of providers)
    data_providers = ["Microsoft.Storage", "Microsoft.KeyVault", "Microsoft.ServiceBus"]
    data_resources = ["blobs", "secrets", "keys", "messages", "queues"]

    ops.extend(
        make_operation(f"{provider}/data/{resource}/{action}", is_data_action=True)
        for provider in data_providers
        for resource in data_resources
        for action in actions
    )

    return ops


@pytest.fixture
def sample_roles() -> list[RoleDefinition]:
    """Sample roles with varying permission patterns (wildcards, notActions, conditions)."""
    role_dicts = [
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
    return [RoleDefinition.model_validate(r) for r in role_dicts]


@pytest.fixture
def sample_roles_db_format() -> list[CachedRole]:
    """Sample roles in CachedRole format for cache testing."""
    role1_def = RoleDefinition.model_validate(
        {
            "name": "role-1",
            "properties": {
                "roleName": "Reader",
                "permissions": [{"actions": ["*/read"], "notActions": []}],
            },
        }
    )
    role2_def = RoleDefinition.model_validate(
        {
            "name": "role-2",
            "properties": {
                "roleName": "Contributor",
                "permissions": [{"actions": ["*"], "notActions": []}],
            },
        }
    )
    return [
        CachedRole(
            definition=role1_def,
            status=RoleStatus.ACTIVE,
            last_seen_at=None,
        ),
        CachedRole(
            definition=role2_def,
            status=RoleStatus.ACTIVE,
            last_seen_at=None,
        ),
    ]
