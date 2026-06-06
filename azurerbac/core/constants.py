"""Domain constants for Azure RBAC."""

from typing import Final

# Domain constants - canonical source for domain references
NEW_DOMAIN: Final[str] = "rbac-catalog.dev"
SITE_URL: Final[str] = f"https://{NEW_DOMAIN}"


# Operation that indicates high-privilege (ability to escalate access)
HIGH_PRIVILEGE_OPERATION: Final[str] = "microsoft.authorization/roleassignments/write"

# Well-known high-privilege role IDs (always high-privilege regardless of permissions analysis)
HIGH_PRIVILEGE_ROLE_IDS: Final[frozenset[str]] = frozenset(
    {
        "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",  # Owner
        "b24988ac-6180-42a0-ab88-20f7382dd24c",  # Contributor
        "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9",  # User Access Administrator
    }
)

DEFAULT_ROLE_TYPE: Final[str] = "BuiltInRole"
ROLE_DEFINITION_TYPE: Final[str] = "Microsoft.Authorization/roleDefinitions"
DEFAULT_SEARCH_LIMIT: Final[int] = 50
MAX_UNCOVERED_SAMPLE: Final[int] = 50

# Popular role comparison pairs for the landing page and sitemap.
# Each tuple is (role_a_id, role_b_id, category).
# Role A vs Role B:
#   General:    Reader / Contributor / Owner
#   Storage:    Blob Data Reader / Contributor / Owner
#   Key Vault:  Reader / Secrets User / Administrator
#   Networking: Network Contributor / NSG Contributor
#   SQL:        SQL DB Contributor / SQL Server Contributor
#   Compute:    VM Contributor / VM Admin Login
#   Containers: AcrPull / AcrPush
#   Monitoring: Monitoring Reader / Contributor, Log Analytics R / C
#   Security:   Security Reader / Security Admin
#   Databases:  Cosmos DB Account Reader / Operator
POPULAR_COMPARE_PAIRS: Final[list[tuple[str, str, str]]] = [
    # General
    ("acdd72a7-3385-48ef-bd42-f606fba81ae7", "b24988ac-6180-42a0-ab88-20f7382dd24c", "General"),
    ("b24988ac-6180-42a0-ab88-20f7382dd24c", "8e3af657-a8ff-443c-a75c-2fe8c4bcb635", "General"),
    ("acdd72a7-3385-48ef-bd42-f606fba81ae7", "8e3af657-a8ff-443c-a75c-2fe8c4bcb635", "General"),
    # Storage
    ("2a2b9908-6ea1-4ae2-8e65-a410df84e7d1", "ba92f5b4-2d11-453d-a403-e96b0029c9fe", "Storage"),
    ("ba92f5b4-2d11-453d-a403-e96b0029c9fe", "b7e6dc6d-f1e8-4753-8033-0f276bb0955b", "Storage"),
    # Key Vault
    ("21090545-7ca7-4776-b22c-e363652d74d2", "4633458b-17de-408a-b874-0445c86b69e6", "Key Vault"),
    ("4633458b-17de-408a-b874-0445c86b69e6", "00482a5a-887f-4fb3-b363-3b7fe8e74483", "Key Vault"),
    # Networking
    ("4d97b98b-1d4f-4787-a291-c67834d212e7", "89c55b20-7731-49b0-83c5-f3ea77e12a0b", "Networking"),
    # SQL
    ("9b7fa17d-e63e-47b0-bb0a-15c516ac86ec", "6d8ee4ec-f05a-4a1d-8b00-a9b17e38b437", "SQL"),
    # Compute
    ("9980e02c-c2be-4d73-94e8-173b1dc7cf3c", "1c0163c0-47e6-4577-8991-ea5c82e286e4", "Compute"),
    # Containers
    ("7f951dda-4ed3-4680-a7ca-43fe172d538d", "8311e382-0749-4cb8-b61a-304f252e45ec", "Containers"),
    # Monitoring
    ("43d0d8ad-25c7-4714-9337-8ba259a9fe05", "749f88d5-cbae-40b8-bcfc-e573ddc772fa", "Monitoring"),
    # Security
    ("39bc4728-0917-49c7-9d2c-d95423bc2eb4", "fb1c8493-542b-48eb-b624-b4c8fea62acd", "Security"),
    # Monitoring - Log Analytics
    ("73c42c96-874c-492b-b04d-ab87d138a893", "92aaf0da-9dab-42b6-94a3-d43ce8d16293", "Monitoring"),
    # Databases
    ("fbdf93bf-df7d-467e-a4d2-9458aa1360c8", "230815da-be43-4aae-9cb4-875f7bd000aa", "Databases"),
]
