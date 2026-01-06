#!/usr/bin/env python3
"""Seed test data for E2E tests.

Creates comprehensive test data (roles, operations) needed for E2E tests to pass.
Run this before starting the web server for E2E testing.

Requirements for tests:
- 30+ roles for pagination tests
- 21+ roles with Microsoft.Authorization/roleAssignments/delete for limit test
- Roles with "storage" in name for search test
- Operations that match role permissions
"""

import asyncio
import os

# Set up path for imports
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from azurerbac.core.models import Base, Operation, Role, RoleHistory


def make_role(
    role_id: str,
    name: str,
    description: str,
    actions: list[str],
    not_actions: list[str] = None,
    data_actions: list[str] = None,
    condition: str = None,
) -> dict:
    """Helper to create a role definition."""
    permission = {
        "actions": actions,
        "notActions": not_actions or [],
        "dataActions": data_actions or [],
        "notDataActions": [],
    }
    if condition:
        permission["condition"] = condition

    return {
        "id": role_id,
        "name": name,
        "type": "BuiltInRole",
        "description": description,
        "role_json": {
            "id": f"/subscriptions/xxx/providers/Microsoft.Authorization/roleDefinitions/{role_id}",
            "name": role_id,
            "type": "Microsoft.Authorization/roleDefinitions",
            "properties": {
                "roleName": name,
                "type": "BuiltInRole",
                "description": description,
                "assignableScopes": ["/"],
                "permissions": [permission],
                "createdOn": "2015-02-02T21:55:09.880Z",
                "updatedOn": "2021-11-11T20:13:54.703Z",
                "createdBy": None,
                "updatedBy": None,
            },
        },
    }


# Core roles that E2E tests expect
CORE_ROLES = [
    make_role(
        "acdd72a7-3385-48ef-bd42-f606fba81ae7",
        "Reader",
        "View all resources, but does not allow you to make any changes.",
        actions=["*/read"],
    ),
    make_role(
        "b24988ac-6180-42a0-ab88-20f7382dd24c",
        "Contributor",
        "Grants full access to manage all resources, but does not allow you to assign roles.",
        actions=["*"],
        not_actions=[
            "Microsoft.Authorization/*/Delete",
            "Microsoft.Authorization/*/Write",
            "Microsoft.Authorization/elevateAccess/Action",
        ],
    ),
    make_role(
        "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",
        "Owner",
        "Grants full access to manage all resources, including the ability to assign roles.",
        actions=["*"],
    ),
    make_role(
        "f58310d9-a9f6-439a-9e8d-f62e7b41a168",
        "Role Based Access Control Administrator",
        "Manage access to Azure resources by assigning roles using Azure RBAC.",
        actions=[
            "Microsoft.Authorization/roleAssignments/write",
            "Microsoft.Authorization/roleAssignments/delete",
            "Microsoft.Authorization/roleDefinitions/read",
        ],
        condition="((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})))",
    ),
]

# Roles with conditions for conditional badge tests on roleAssignments/write page
CONDITIONAL_ROLES = [
    make_role(
        "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9",
        "User Access Administrator",
        "Lets you manage user access to Azure resources.",
        actions=["*/read", "Microsoft.Authorization/*", "Microsoft.Support/*"],
        condition="((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:RoleDefinitionId] ForAnyOfAnyValues:GuidEquals {acdd72a7-3385-48ef-bd42-f606fba81ae7}))",
    ),
    make_role(
        "b24988ac-6180-42a0-ab88-20f7382dd24d",
        "Privileged Role Administrator",
        "Manage privileged role assignments.",
        actions=[
            "Microsoft.Authorization/roleAssignments/read",
            "Microsoft.Authorization/roleAssignments/write",
            "Microsoft.Authorization/roleAssignments/delete",
        ],
        condition="@Principal[Microsoft.Directory/CustomSecurityAttributes/Id:Engineering_Project]",
    ),
    make_role(
        "c7393b34-138c-406f-901b-d8cf2b17e6ae",
        "Delegation Administrator",
        "Manage delegation of Azure resources.",
        actions=[
            "Microsoft.Authorization/roleAssignments/write",
            "Microsoft.Authorization/roleDefinitions/read",
        ],
        condition="((!(ActionMatches{'Microsoft.Authorization/roleAssignments/write'})) OR (@Request[Microsoft.Authorization/roleAssignments:PrincipalType] StringEqualsIgnoreCase 'ServicePrincipal'))",
    ),
]

# Storage roles for search test
STORAGE_ROLES = [
    make_role(
        "17d1049b-9a84-46fb-8f53-869881c3d3ab",
        "Storage Account Contributor",
        "Lets you manage storage accounts, including accessing storage account keys.",
        actions=[
            "Microsoft.Authorization/*/read",
            "Microsoft.Insights/alertRules/*",
            "Microsoft.Insights/diagnosticSettings/*",
            "Microsoft.Network/virtualNetworks/subnets/joinViaServiceEndpoint/action",
            "Microsoft.ResourceHealth/availabilityStatuses/read",
            "Microsoft.Resources/deployments/*",
            "Microsoft.Resources/subscriptions/resourceGroups/read",
            "Microsoft.Storage/storageAccounts/*",
            "Microsoft.Support/*",
            "Microsoft.Authorization/roleAssignments/delete",
        ],
    ),
    make_role(
        "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
        "Storage Blob Data Contributor",
        "Allows for read, write and delete access to Azure Storage blob containers and data.",
        actions=["Microsoft.Storage/storageAccounts/blobServices/containers/delete"],
        data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/*"],
    ),
    make_role(
        "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
        "Storage Blob Data Reader",
        "Allows for read access to Azure Storage blob containers and data.",
        actions=[],
        data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"],
    ),
    make_role(
        "b7e6dc6d-f1e8-4753-8033-0f276bb0955b",
        "Storage Blob Data Owner",
        "Allows for full access to Azure Storage blob containers and data.",
        actions=["Microsoft.Storage/storageAccounts/blobServices/containers/*"],
        data_actions=["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/*"],
    ),
    make_role(
        "0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3",
        "Storage Table Data Contributor",
        "Allows for read, write and delete access to Azure Storage tables and entities.",
        actions=[],
        data_actions=["Microsoft.Storage/storageAccounts/tableServices/tables/entities/*"],
    ),
    make_role(
        "76199698-9eea-4c19-bc75-cec21354c6b6",
        "Storage Table Data Reader",
        "Allows for read access to Azure Storage tables and entities.",
        actions=[],
        data_actions=["Microsoft.Storage/storageAccounts/tableServices/tables/entities/read"],
    ),
    make_role(
        "0c867c2a-1d8c-454a-a3db-ab2ea1bdc8bb",
        "Storage File Data SMB Share Contributor",
        "Allows for read, write, and delete access to files and directories in Azure file shares.",
        actions=[],
        data_actions=["Microsoft.Storage/storageAccounts/fileServices/fileshares/files/*"],
    ),
    make_role(
        "aba4ae5f-2193-4029-9191-0cb91df5e314",
        "Storage File Data SMB Share Reader",
        "Allows for read access to files and directories in Azure file shares.",
        actions=[],
        data_actions=["Microsoft.Storage/storageAccounts/fileServices/fileshares/files/read"],
    ),
]

# Generate 25 more roles that include roleAssignments/delete to pass the "no artificial limit" test
# These are fictional but realistic-looking roles for testing purposes
RBAC_ROLES = []
RBAC_ROLE_TEMPLATES = [
    ("Virtual Machine Administrator", "Microsoft.Compute/virtualMachines/*"),
    ("Network Administrator", "Microsoft.Network/*"),
    ("Key Vault Administrator", "Microsoft.KeyVault/*"),
    ("SQL DB Administrator", "Microsoft.Sql/*"),
    ("App Service Administrator", "Microsoft.Web/*"),
    ("Container Registry Administrator", "Microsoft.ContainerRegistry/*"),
    ("Kubernetes Administrator", "Microsoft.ContainerService/*"),
    ("Cosmos DB Administrator", "Microsoft.DocumentDB/*"),
    ("Event Hub Administrator", "Microsoft.EventHub/*"),
    ("Service Bus Administrator", "Microsoft.ServiceBus/*"),
    ("Logic App Administrator", "Microsoft.Logic/*"),
    ("Function App Administrator", "Microsoft.Web/sites/functions/*"),
    ("API Management Administrator", "Microsoft.ApiManagement/*"),
    ("CDN Administrator", "Microsoft.Cdn/*"),
    ("DNS Administrator", "Microsoft.Network/dnsZones/*"),
    ("Load Balancer Administrator", "Microsoft.Network/loadBalancers/*"),
    ("Application Gateway Administrator", "Microsoft.Network/applicationGateways/*"),
    ("Firewall Administrator", "Microsoft.Network/azureFirewalls/*"),
    ("Backup Administrator", "Microsoft.RecoveryServices/*"),
    ("Monitor Administrator", "Microsoft.Insights/*"),
    ("Log Analytics Administrator", "Microsoft.OperationalInsights/*"),
    ("Automation Administrator", "Microsoft.Automation/*"),
    ("DevTest Labs Administrator", "Microsoft.DevTestLab/*"),
    ("Batch Administrator", "Microsoft.Batch/*"),
    ("Media Services Administrator", "Microsoft.Media/*"),
    ("Search Administrator", "Microsoft.Search/*"),
    ("Cognitive Services Administrator", "Microsoft.CognitiveServices/*"),
    ("Machine Learning Administrator", "Microsoft.MachineLearningServices/*"),
    ("IoT Hub Administrator", "Microsoft.Devices/*"),
    ("SignalR Administrator", "Microsoft.SignalRService/*"),
]

for i, (name, action) in enumerate(RBAC_ROLE_TEMPLATES):
    # Generate a deterministic UUID based on index
    role_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"test-role-{i}"))
    RBAC_ROLES.append(
        make_role(
            role_id,
            name,
            f"Manage {name.replace(' Administrator', '')} resources including role assignments.",
            actions=[
                action,
                "Microsoft.Authorization/roleAssignments/read",
                "Microsoft.Authorization/roleAssignments/write",
                "Microsoft.Authorization/roleAssignments/delete",  # This is what the test checks for
                "Microsoft.Resources/subscriptions/resourceGroups/read",
            ],
        )
    )

# Combine all roles
TEST_ROLES = CORE_ROLES + STORAGE_ROLES + RBAC_ROLES + CONDITIONAL_ROLES

# Test operations - comprehensive list to match role permissions
TEST_OPERATIONS = [
    # Storage operations (for Reader role's */read wildcard)
    {
        "name": "Microsoft.Storage/storageAccounts/read",
        "provider_display_name": "Microsoft Storage",
        "resource_type": "storageAccounts",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Storage/storageAccounts/write",
        "provider_display_name": "Microsoft Storage",
        "resource_type": "storageAccounts",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Storage/storageAccounts/delete",
        "provider_display_name": "Microsoft Storage",
        "resource_type": "storageAccounts",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Storage/storageAccounts/blobServices/read",
        "provider_display_name": "Microsoft Storage",
        "resource_type": "blobServices",
        "is_data_action": False,
    },
    # Compute operations (for Reader role's */read wildcard)
    {
        "name": "Microsoft.Compute/virtualMachines/read",
        "provider_display_name": "Microsoft Compute",
        "resource_type": "virtualMachines",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Compute/virtualMachines/write",
        "provider_display_name": "Microsoft Compute",
        "resource_type": "virtualMachines",
        "is_data_action": False,
    },
    # Network operations (for Reader role's */read wildcard)
    {
        "name": "Microsoft.Network/virtualNetworks/read",
        "provider_display_name": "Microsoft Network",
        "resource_type": "virtualNetworks",
        "is_data_action": False,
    },
    # Authorization operations
    {
        "name": "Microsoft.Authorization/roleAssignments/read",
        "provider_display_name": "Microsoft Authorization",
        "resource_type": "roleAssignments",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Authorization/roleAssignments/write",
        "provider_display_name": "Microsoft Authorization",
        "resource_type": "roleAssignments",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Authorization/roleAssignments/delete",
        "provider_display_name": "Microsoft Authorization",
        "resource_type": "roleAssignments",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Authorization/roleDefinitions/read",
        "provider_display_name": "Microsoft Authorization",
        "resource_type": "roleDefinitions",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Authorization/roleDefinitions/delete",
        "provider_display_name": "Microsoft Authorization",
        "resource_type": "roleDefinitions",
        "is_data_action": False,
    },
    # Resources operations
    {
        "name": "Microsoft.Resources/subscriptions/read",
        "provider_display_name": "Microsoft Resources",
        "resource_type": "subscriptions",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Resources/subscriptions/resourceGroups/read",
        "provider_display_name": "Microsoft Resources",
        "resource_type": "resourceGroups",
        "is_data_action": False,
    },
    # More read operations for Reader role
    {
        "name": "Microsoft.KeyVault/vaults/read",
        "provider_display_name": "Microsoft Key Vault",
        "resource_type": "vaults",
        "is_data_action": False,
    },
    {
        "name": "Microsoft.Web/sites/read",
        "provider_display_name": "Microsoft Web",
        "resource_type": "sites",
        "is_data_action": False,
    },
]


def make_operation_display_fields(name: str, resource: str) -> tuple[str, str]:
    """Create display_name and description from operation name.

    Returns:
        Tuple of (display_name, description)
    """
    parts = name.split("/")
    operation = parts[-1] if parts else name
    display_name = operation.replace("/", " ").title()
    description = f"Perform {operation} on {resource}."
    return display_name, description


async def seed_database(db_url: str) -> None:
    """Seed the database with test data."""
    print(f"Seeding database: {db_url}")

    engine = create_async_engine(db_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # Add roles
        for role_data in TEST_ROLES:
            role = Role(
                role_id=role_data["id"],
                role_name=role_data["name"],
            )
            session.add(role)

            # Add initial history entry
            azure_updated = role_data["role_json"]["properties"].get("updatedOn")
            history = RoleHistory(
                role_id=role_data["id"],
                version_number=1,
                role_name=role_data["name"],
                event_type="created",
                role_json=role_data["role_json"],
                azure_updated_on=datetime.fromisoformat(azure_updated.replace("Z", "+00:00"))
                if azure_updated
                else None,
                diff_json={},
                summary="<root>",
            )
            session.add(history)

        # Add operations
        now = datetime.now(UTC)
        for op_data in TEST_OPERATIONS:
            display_name, description = make_operation_display_fields(
                op_data["name"],
                op_data["resource_type"],
            )
            op = Operation(
                name=op_data["name"],
                display_name=display_name,
                description=description,
                origin=None,
                provider_display_name=op_data["provider_display_name"],
                resource_type=op_data["resource_type"],
                resource_type_display_name=op_data["resource_type"],
                is_data_action=op_data["is_data_action"],
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(op)

        await session.commit()

    await engine.dispose()
    print(f"✓ Seeded {len(TEST_ROLES)} roles and {len(TEST_OPERATIONS)} operations")


async def main():
    db_url = os.environ.get("DB_CONNECTION_STRING", "sqlite+aiosqlite:///:memory:")
    await seed_database(db_url)


if __name__ == "__main__":
    asyncio.run(main())
