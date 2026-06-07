# MCP Server Reference

This is the full reference for the [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server exposed by Azure RBAC Catalog. The short version (endpoint + VS Code setup) lives in the [main README](../README.md#mcp-server-integration).

The server speaks MCP over streamable HTTP at `https://rbac-catalog.dev/mcp/`. Any MCP-capable client (GitHub Copilot, Claude, Cursor) can connect to it without an API key; usage is governed by the [rate limits](#rate-limiting) below.

## Available Tools

| Tool | Parameters | Description |
|------|------------|-------------|
| `search_operations` | `query`, `limit` | Search Azure operations by name/pattern (supports wildcards like `Microsoft.Storage/*/read`) |
| `search_roles` | `query`, `limit` | Search roles by name or description |
| `get_role` | `role_id_or_name` | Get detailed role info including all permissions |
| `get_role_permissions` | `role_id_or_name`, `include_data_actions` | Get the expanded list of actual operations a role grants |
| `recommend_roles_tool` | `operations`, `wildcards_control`, `wildcards_data`, `max_results` | Find least-privilege roles for specific operations |
| `ai_recommend` | `query`, `top_k` | Natural-language role recommendations |

## Example queries

Natural-language questions you can ask an AI assistant connected to this MCP server:

- "What permissions does the Storage Blob Data Contributor role have?"
- "Compare Storage Blob Data Contributor and Storage Blob Data Owner"
- "Which roles allow `Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read` and `Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read`?"
- "What operations correspond to `Microsoft.Storage/*`?"
- "Describe role `b7e6dc6d-f1e8-4753-8033-0f276bb0955b`"
- "What Azure roles can read blob storage?"
- "Find the least-privilege role for reading Key Vault secrets"

## Direct tool invocations

Search for storage operations:

```
search_operations("Microsoft.Storage/storageAccounts/read", limit=10)
```

Find all operations under a resource provider (wildcard search):

```
search_operations("Microsoft.Storage/*", limit=50)
```

Find roles matching a description:

```
search_roles("blob storage", limit=5)
```

Get AI-powered recommendations:

```
ai_recommend("I need to read and write blobs in Azure Storage", top_k=3)
```

Find least-privilege roles for specific operations:

```
recommend_roles_tool(
    ["Microsoft.Storage/storageAccounts/read",
     "Microsoft.Storage/storageAccounts/blobServices/containers/read"],
    max_results=5,
)
```

Find roles that grant specific blob operations:

```
recommend_roles_tool(
    ["Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read",
     "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/tags/read"],
    max_results=5,
)
```

Get detailed info about a role by ID:

```
get_role("b7e6dc6d-f1e8-4753-8033-0f276bb0955b")
```

Get detailed info about a role by name:

```
get_role("Storage Blob Data Reader")
```

## Rate Limiting

The MCP server implements dual-layer rate limiting using a token bucket algorithm:

- **Global**: protects against server overload (shared bucket across all clients)
- **Per-session**: prevents individual clients from monopolizing resources (separate bucket per session ID)

Tokens refill continuously at a configurable rate, allowing burst capacity while enforcing sustained limits. Rate-limited requests receive informative error messages with retry-after guidance. Session buckets use LRU eviction to bound memory usage.
