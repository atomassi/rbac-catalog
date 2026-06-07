"""Constants for MCP server configuration."""

from typing import Final

# Server metadata
MCP_SERVER_NAME: Final = "azure-rbac-catalog"
MCP_SESSION_ID_HEADER: Final = "mcp-session-id"
MCP_SERVER_INSTRUCTIONS: Final = (
    "Azure RBAC Catalog provides tools to search Azure built-in roles, "
    "find operations (permissions), and get least-privilege role recommendations. "
    "Use search_operations to find permissions, search_roles to find roles, "
    "get_role for details, recommend_roles for operation-based recommendations, "
    "and ai_recommend for natural language queries."
)

# Tool descriptions
SEARCH_OPERATIONS_DESC: Final = (
    "Search Azure resource provider operations (permissions) by name or pattern. "
    "Supports wildcards like 'Microsoft.Storage/*/read'. "
    "Use this to find what operations exist for a service."
)
SEARCH_ROLES_DESC: Final = (
    "Search Azure built-in RBAC roles by name or description. "
    "Returns role names, IDs, and brief descriptions."
)
GET_ROLE_DESC: Final = (
    "Get detailed information about a specific Azure built-in role "
    "including its full permissions. Use the role ID (GUID) or exact role name."
)
GET_ROLE_PERMISSIONS_DESC: Final = (
    "Get the expanded list of actual operations that a role grants. "
    "Expands wildcards like '*/read' into actual operation names."
)
RECOMMEND_ROLES_DESC: Final = (
    "Find least-privilege Azure built-in roles that grant specific operations. "
    "Provide required operations and get roles ranked by fewest excess permissions. "
    "Use 'operations' for explicit ops (auto-detect plane), "
    "'wildcards_control' for control-plane wildcards, 'wildcards_data' for data-plane wildcards."
)
AI_RECOMMEND_DESC: Final = (
    "Get AI-powered role recommendations from a natural language description. "
    "Example: 'I need to read and write blobs in Azure Storage'"
)

# Input limits
MAX_QUERY_LENGTH: Final = 200
MAX_ROLE_ID_LENGTH: Final = 100
MAX_AI_QUERY_LENGTH: Final = 500
MAX_OPERATION_LENGTH: Final = 200
MAX_OPERATIONS_PER_REQUEST: Final = 50
MIN_QUERY_LENGTH: Final = 2
MIN_AI_QUERY_LENGTH: Final = 5

# Result limits
DEFAULT_OPERATIONS_LIMIT: Final = 20
MAX_OPERATIONS_LIMIT: Final = 100
DEFAULT_ROLES_LIMIT: Final = 20
MAX_ROLES_LIMIT: Final = 50
DEFAULT_RECOMMEND_LIMIT: Final = 10
MAX_RECOMMEND_LIMIT: Final = 20
DEFAULT_AI_RECOMMEND_LIMIT: Final = 5
MAX_AI_RECOMMEND_LIMIT: Final = 10

# Rate limiting - Token Bucket
RATE_LIMIT_GLOBAL_CAPACITY: Final = 50  # Burst across all sessions
RATE_LIMIT_GLOBAL_REFILL_RATE: Final = 5.0  # 5 tokens/sec = 300/min
RATE_LIMIT_SESSION_CAPACITY: Final = 10  # Burst per session
RATE_LIMIT_SESSION_REFILL_RATE: Final = 0.2  # 1 token per 5 seconds
RATE_LIMIT_MAX_SESSIONS: Final = 100
RATE_LIMIT_SESSION_TIMEOUT_SECONDS: Final = 3600.0  # 1 hour idle timeout
