"""Azure terminology and use case mappings for AI role recommendations."""

from functools import lru_cache
from typing import Final

# Azure service name synonyms and abbreviations
AZURE_SERVICE_SYNONYMS: Final[dict[str, list[str]]] = {
    # Storage
    "storage": ["blob", "file", "queue", "table", "azure storage", "storage account"],
    "blob": ["blob storage", "binary large object", "blobs", "container"],
    "file": ["file share", "azure files", "file storage"],
    # Compute
    "vm": ["virtual machine", "virtual machines", "compute", "vms"],
    "aks": ["kubernetes", "k8s", "azure kubernetes service", "container service"],
    "container": ["docker", "containers", "container instance", "aci"],
    "function": ["azure function", "functions", "serverless", "function app"],
    "app service": ["web app", "webapp", "app services"],
    # Database
    "sql": ["sql server", "azure sql", "sql database", "database", "mssql"],
    "cosmos": ["cosmosdb", "cosmos db", "document db", "documentdb"],
    "mysql": ["azure mysql", "mysql database"],
    "postgres": ["postgresql", "azure postgresql", "postgres database"],
    # Security
    "keyvault": ["key vault", "secrets", "keys", "certificates", "vault"],
    "secret": ["secrets", "secret management", "credentials"],
    "key": ["keys", "encryption key", "cryptographic key"],
    "certificate": ["certificates", "cert", "certs", "ssl", "tls"],
    # Identity
    "ad": ["active directory", "azure ad", "entra", "entra id", "aad"],
    "identity": ["managed identity", "service principal", "user identity"],
    "rbac": ["role based access", "access control", "permissions", "authorization"],
    # Networking
    "vnet": ["virtual network", "network", "networking", "vpc"],
    "nsg": ["network security group", "security group", "firewall rules"],
    "lb": ["load balancer", "load balancing"],
    "dns": ["azure dns", "domain name"],
    "cdn": ["content delivery network", "content delivery"],
    # Monitoring
    "monitor": ["monitoring", "azure monitor", "metrics", "logs"],
    "log analytics": ["logs", "log workspace", "oms", "workspace"],
    "insights": ["application insights", "app insights", "telemetry"],
    # AI/ML
    "ml": ["machine learning", "azure ml", "aml", "ai"],
    "cognitive": ["cognitive services", "ai services"],
    "openai": ["azure openai", "gpt", "chatgpt"],
    # Integration
    "service bus": ["servicebus", "messaging", "message queue"],
    "event hub": ["eventhub", "event hubs", "streaming"],
    "logic app": ["logic apps", "workflow", "automation"],
}

# Common abbreviations for query expansion (used by fuzzy matching)
ABBREVIATIONS: Final[dict[str, str]] = {
    "vm": "virtual machine",
    "vms": "virtual machines",
    "k8s": "kubernetes",
    "aks": "azure kubernetes service",
    "acr": "container registry",
    "sql": "sql server",
    "kv": "key vault",
    "keyvault": "key vault",
    "rg": "resource group",
    "nsg": "network security group",
    "lb": "load balancer",
    "vnet": "virtual network",
    "mi": "managed identity",
}

# Common permission level keywords
PERMISSION_LEVELS: Final[dict[str, list[str]]] = {
    "read": [
        "read",
        "view",
        "get",
        "list",
        "see",
        "access",
        "reader",
        "readonly",
        "read-only",
    ],
    "write": ["write", "create", "update", "modify", "edit", "change", "contributor"],
    "delete": ["delete", "remove", "destroy", "purge"],
    "admin": [
        "admin",
        "administrator",
        "manage",
        "full access",
        "owner",
        "full control",
    ],
    "operator": ["operator", "operate", "run", "execute", "start", "stop", "restart"],
}

# NEGATIVE PATTERNS: Roles that should be penalized for certain queries
# These roles have notActions or limitations that make them unsuitable
# Format: query_pattern -> list of roles to penalize
NEGATIVE_PATTERNS: Final[dict[str, list[str]]] = {
    # Contributor cannot manage role assignments (Microsoft.Authorization/*/Write in notActions)
    "role assignment": ["Contributor"],
    "assign role": ["Contributor"],
    "create role assignment": ["Contributor"],
    "manage role assignment": ["Contributor"],
    "grant permission": ["Contributor"],
    "grant access": ["Contributor"],
    "give access": ["Contributor"],
    "remove access": ["Contributor"],
    "revoke access": ["Contributor"],
    "rbac": ["Contributor"],
    "access control": ["Contributor"],
    "iam": ["Contributor"],
    "manage access": ["Contributor"],
    "manage permission": ["Contributor"],
    # Reader cannot write/modify anything
    "create": ["Reader"],
    "write": ["Reader"],
    "modify": ["Reader"],
    "update": ["Reader"],
    "delete": ["Reader"],
    "manage": ["Reader"],
    # Storage Blob Data Reader cannot write
    "upload blob": ["Storage Blob Data Reader"],
    "write blob": ["Storage Blob Data Reader"],
    "create blob": ["Storage Blob Data Reader"],
    "delete blob": ["Storage Blob Data Reader"],
}


@lru_cache(maxsize=256)
def get_negative_patterns(query: str) -> tuple[str, ...]:
    """Get list of roles that should be penalized for this query.

    Args:
        query: The user's query

    Returns:
        Tuple of role names that should be penalized (tuple for cache hashability)
    """
    query_lower = query.lower()
    penalized_roles: set[str] = set()

    for pattern, roles in NEGATIVE_PATTERNS.items():
        if pattern in query_lower:
            penalized_roles.update(roles)

    return tuple(penalized_roles)


# Common use case patterns mapped to role keywords
# NOTE: Owner is excluded from most patterns to enforce least privilege.
# Owner should only be suggested when explicitly requested.
USE_CASE_PATTERNS: Final[dict[str, list[str]]] = {
    # ========== RBAC / Role Assignment scenarios ==========
    # Singular forms
    "create role assignment": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "role assignment": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "assign role": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "manage role assignment": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    # Plural forms
    "create role assignments": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "role assignments": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "assign roles": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "manage role assignments": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "grant permissions": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "manage access": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "manage permissions": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "rbac": ["User Access Administrator", "Role Based Access Control Administrator"],
    "access control": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "iam": ["User Access Administrator", "Role Based Access Control Administrator"],
    "give access": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "grant access": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "remove access": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    "revoke access": [
        "User Access Administrator",
        "Role Based Access Control Administrator",
    ],
    # ========== Storage scenarios ==========
    "read storage blobs": ["Storage Blob Data Reader"],
    "read blobs": ["Storage Blob Data Reader"],
    "write storage blobs": ["Storage Blob Data Contributor"],
    "write blobs": ["Storage Blob Data Contributor"],
    "manage storage": ["Storage Account Contributor"],
    "upload files": [
        "Storage Blob Data Contributor",
        "Storage File Data SMB Share Contributor",
    ],
    "download files": [
        "Storage Blob Data Reader",
        "Storage File Data SMB Share Reader",
    ],
    "storage account": ["Storage Account Contributor"],
    "blob container": ["Storage Blob Data Contributor", "Storage Blob Data Reader"],
    # ========== VM scenarios ==========
    "manage virtual machines": ["Virtual Machine Contributor"],
    "manage vms": ["Virtual Machine Contributor"],
    "manage vm": ["Virtual Machine Contributor"],
    "start stop vms": ["Virtual Machine Contributor"],
    "login to vm": [
        "Virtual Machine User Login",
        "Virtual Machine Administrator Login",
    ],
    "create vm": ["Virtual Machine Contributor"],
    "create vms": ["Virtual Machine Contributor"],
    "create virtual machines": ["Virtual Machine Contributor"],
    "create virtual machine": ["Virtual Machine Contributor"],
    "vm admin": ["Virtual Machine Administrator Login"],
    # ========== Key Vault scenarios ==========
    "read secrets": ["Key Vault Secrets User", "Key Vault Reader"],
    "manage secrets": ["Key Vault Secrets Officer", "Key Vault Administrator"],
    "read keys": ["Key Vault Crypto User", "Key Vault Reader"],
    "manage keys": ["Key Vault Crypto Officer", "Key Vault Administrator"],
    "read certificates": ["Key Vault Certificates Officer", "Key Vault Reader"],
    "key vault": ["Key Vault Administrator", "Key Vault Contributor"],
    "keyvault": ["Key Vault Administrator", "Key Vault Contributor"],
    "secrets": ["Key Vault Secrets User", "Key Vault Secrets Officer"],
    # ========== Kubernetes scenarios ==========
    "manage kubernetes": [
        "Azure Kubernetes Service RBAC Admin",
        "Azure Kubernetes Service RBAC Writer",
    ],
    "manage kubernetes workloads": [
        "Azure Kubernetes Service RBAC Admin",
        "Azure Kubernetes Service RBAC Writer",
    ],
    "kubernetes workloads": [
        "Azure Kubernetes Service RBAC Admin",
        "Azure Kubernetes Service RBAC Writer",
    ],
    "deploy to aks": [
        "Azure Kubernetes Service Cluster User Role",
        "Azure Kubernetes Service RBAC Writer",
    ],
    "view kubernetes": [
        "Azure Kubernetes Service Cluster User Role",
        "Azure Kubernetes Service RBAC Reader",
    ],
    "kubernetes": [
        "Azure Kubernetes Service Contributor",
        "Azure Kubernetes Service Cluster Admin Role",
    ],
    "aks": [
        "Azure Kubernetes Service Contributor",
        "Azure Kubernetes Service Cluster Admin Role",
    ],
    "k8s": [
        "Azure Kubernetes Service Contributor",
        "Azure Kubernetes Service Cluster Admin Role",
    ],
    # ========== Database scenarios ==========
    "read database": ["SQL DB Contributor", "Cosmos DB Account Reader Role"],
    "manage database": ["SQL Server Contributor", "DocumentDB Account Contributor"],
    "sql server": ["SQL Server Contributor", "SQL DB Contributor"],
    "cosmos db": ["Cosmos DB Account Contributor", "DocumentDB Account Contributor"],
    # ========== Network scenarios ==========
    "manage network": ["Network Contributor"],
    "view network": ["Network Contributor", "Reader"],
    "virtual network": ["Network Contributor"],
    "vnet": ["Network Contributor"],
    "load balancer": ["Network Contributor"],
    "dns": ["DNS Zone Contributor", "Private DNS Zone Contributor"],
    # ========== Monitoring scenarios ==========
    "view metrics": ["Monitoring Reader"],
    "manage monitoring": ["Monitoring Contributor"],
    "view logs": ["Log Analytics Reader"],
    "log analytics": ["Log Analytics Contributor", "Log Analytics Reader"],
    "alerts": ["Monitoring Contributor"],
    # ========== Security scenarios ==========
    "manage security": ["Security Admin"],
    "view security": ["Security Reader"],
    "security center": ["Security Admin", "Security Reader"],
    "defender": ["Security Admin"],
    # ========== Resource Group / Subscription scenarios ==========
    # NOTE: Owner excluded - use Contributor for least privilege
    "manage resource group": ["Contributor"],
    "manage resource groups": ["Contributor"],
    "manage subscription": ["Contributor"],
    "create resources": ["Contributor"],
    "delete resources": ["Contributor"],
    "delete resource group": ["Contributor"],
    "delete resource groups": ["Contributor"],
    "delete rg": ["Contributor"],
    "manage resources": ["Contributor"],
    "manage resource tags": ["Tag Contributor", "Contributor"],
    "resource tags": ["Tag Contributor", "Contributor"],
    "tags": ["Tag Contributor"],
    # ========== App Service / Web Apps ==========
    "manage web app": ["Website Contributor"],
    "deploy web app": ["Website Contributor"],
    "app service": ["Website Contributor"],
    "function app": ["Website Contributor"],
    "function apps": ["Website Contributor"],
    "manage function apps": ["Website Contributor"],
    "manage function": ["Website Contributor"],
    # ========== Logic Apps ==========
    "logic apps": ["Logic App Contributor"],
    "logic app": ["Logic App Contributor"],
    "manage logic apps": ["Logic App Contributor"],
    "manage logic app": ["Logic App Contributor"],
    # ========== Managed Identity ==========
    "managed identity": ["Managed Identity Contributor", "Managed Identity Operator"],
    "managed identities": ["Managed Identity Contributor", "Managed Identity Operator"],
    "manage managed identity": [
        "Managed Identity Contributor",
        "Managed Identity Operator",
    ],
    "manage managed identities": [
        "Managed Identity Contributor",
        "Managed Identity Operator",
    ],
    "create managed identity": ["Managed Identity Contributor"],
    "assign managed identity": ["Managed Identity Operator"],
    "user assigned identity": [
        "Managed Identity Contributor",
        "Managed Identity Operator",
    ],
    # ========== Container Registry ==========
    "push images": ["AcrPush", "Contributor"],
    "pull images": ["AcrPull", "Reader"],
    "container registry": ["AcrPush", "AcrPull", "Contributor"],
    "acr": ["AcrPush", "AcrPull"],
    # ========== General scenarios ==========
    "read everything": ["Reader"],
    "view everything": ["Reader"],
    "manage everything": ["Contributor"],
    # Owner only when explicitly requested
    "full access owner": ["Owner"],
    "owner": ["Owner"],
    "contributor": ["Contributor"],
    "reader": ["Reader"],
}

# Stop words to filter out from queries
STOP_WORDS: Final[frozenset[str]] = frozenset(
    {
        "i",
        "need",
        "want",
        "to",
        "a",
        "an",
        "the",
        "for",
        "in",
        "on",
        "at",
        "with",
        "that",
        "this",
        "is",
        "are",
        "be",
        "have",
        "has",
        "do",
        "does",
        "can",
        "could",
        "would",
        "should",
        "will",
        "able",
        "access",
        "role",
        "permission",
        "permissions",
        "azure",
        "please",
        "help",
        "me",
        "my",
        "user",
        "users",
        "give",
        "grant",
        "allow",
        "lets",
        "let",
        "enables",
    }
)


def extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a natural language query.

    Args:
        query: Natural language query like "I need to read storage blobs"

    Returns:
        List of meaningful keywords
    """
    # Lowercase and split
    words = query.lower().split()

    # Remove stop words
    keywords = [
        w.strip(".,!?;:'\"()[]{}") for w in words if w.strip(".,!?;:'\"()[]{}") not in STOP_WORDS
    ]

    # Expand synonyms
    expanded = list(keywords)
    for kw in keywords:
        # Check if this keyword is a synonym for something
        for canonical, synonyms in AZURE_SERVICE_SYNONYMS.items():
            if kw in synonyms or kw == canonical:
                expanded.append(canonical)
                break

    return list(set(expanded))


# Pre-compute pattern word sets to avoid set() allocation on every call
_PATTERN_WORDS: Final[dict[str, frozenset[str]]] = {
    pattern: frozenset(pattern.split()) for pattern in USE_CASE_PATTERNS
}


def find_matching_use_cases(query: str) -> list[tuple[str, float]]:
    """Find use case patterns that match the query.

    Args:
        query: Natural language query

    Returns:
        List of (role_name, score) tuples sorted by relevance
    """
    query_lower = query.lower().strip()
    query_words = set(query_lower.split())

    # Remove common stop words for matching
    query_words_filtered = query_words - {
        "i",
        "need",
        "to",
        "a",
        "the",
        "want",
        "for",
        "can",
    }

    role_scores: dict[str, float] = {}

    for pattern, roles in USE_CASE_PATTERNS.items():
        pattern_words = _PATTERN_WORDS[pattern]

        # Check for exact phrase match (highest priority)
        if pattern in query_lower:
            for role in roles:
                role_scores[role] = max(role_scores.get(role, 0), 1.0)
            continue

        # Check for word overlap
        overlap = len(pattern_words & query_words_filtered)
        if overlap > 0:
            # Score based on how many pattern words matched
            pattern_len = len(pattern_words)
            score = overlap / pattern_len

            # Boost if all pattern words are present
            if overlap == pattern_len:
                score = 0.95
            # Boost if query contains the pattern as substring
            elif all(pw in query_lower for pw in pattern_words):
                score = min(score + 0.2, 0.9)

            for role in roles:
                role_scores[role] = max(role_scores.get(role, 0), score)

    # Sort by score descending
    sorted_roles = sorted(role_scores.items(), key=lambda x: x[1], reverse=True)

    # Only return roles with score >= 0.4
    return [(role, score) for role, score in sorted_roles if score >= 0.4]


def expand_query_with_synonyms(query: str) -> str:
    """Expand a query by adding Azure-specific synonyms.

    Args:
        query: Original query

    Returns:
        Expanded query with synonyms added
    """
    query_lower = query.lower()
    expansions = [query]

    for canonical, synonyms in AZURE_SERVICE_SYNONYMS.items():
        if canonical in query_lower:
            # Add synonyms (limit to 2 to avoid explosion)
            expansions.extend(query_lower.replace(canonical, syn) for syn in synonyms[:2])
        else:
            # Check if any synonym is in query
            for syn in synonyms:
                if syn in query_lower:
                    expansions.append(query_lower.replace(syn, canonical))
                    break

    return " ".join(expansions)
