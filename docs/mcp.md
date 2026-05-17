# MCP Server Reference

This is the full reference for the [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server exposed by Azure RBAC Catalog. The short version (endpoint + VS Code setup) lives in the [main README](../README.md#mcp-server-integration).

## Available Tools

| Tool | Description |
|------|-------------|
| `search_operations` | Search Azure operations by name/pattern (supports wildcards like `Microsoft.Storage/*/read`) |
| `search_roles` | Search roles by name or description |
| `get_role` | Get detailed role info including all permissions |
| `get_role_permissions` | Get expanded list of actual operations a role grants |
| `recommend_roles` | Find least-privilege roles for specific operations |
| `ai_recommend` | Natural language role recommendations |

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
recommend_roles(
    ["Microsoft.Storage/storageAccounts/read",
     "Microsoft.Storage/storageAccounts/blobServices/containers/read"],
    max_results=5,
)
```

Find roles that grant specific blob operations:

```
recommend_roles(
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
