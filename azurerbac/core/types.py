"""Shared type aliases for the codebase.

Centralizes type aliases to ensure consistency across all modules.
"""

from typing import Any

# Type alias for JSON-like data structures
# Used by to_dict() methods for serialization
JsonDict = dict[str, Any]
