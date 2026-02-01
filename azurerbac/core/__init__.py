"""Core: models, database, diffing, constants, patterns."""

from .constants import (
    DEFAULT_ROLE_TYPE,
    HIGH_PRIVILEGE_OPERATION,
    ROLE_DEFINITION_TYPE,
    EventType,
    RoleStatus,
)
from .db import DBEngine, EngineFactory, create_sessionmaker
from .enums import EventTypeFilter, SortOrder, StatusFilter
from .models import Base, Operation, OperationScanStatus, Role, RoleHistory, RoleScanStatus
from .patterns import is_wildcard_pattern, matches_pattern, pattern_to_regex, wildcard_to_sql_like
from .schema import ensure_db
from .singleton import ThreadSafeSingleton
from .types import JsonDict
from .utils import ensure_utc, ensure_utc_or_min, format_iso_z, normalize_uuid_or_none, utcnow

__all__ = [
    "DEFAULT_ROLE_TYPE",
    "HIGH_PRIVILEGE_OPERATION",
    "ROLE_DEFINITION_TYPE",
    "Base",
    "DBEngine",
    "EngineFactory",
    "EventType",
    "EventTypeFilter",
    "JsonDict",
    "Operation",
    "OperationScanStatus",
    "Role",
    "RoleHistory",
    "RoleScanStatus",
    "RoleStatus",
    "SortOrder",
    "StatusFilter",
    "ThreadSafeSingleton",
    "create_sessionmaker",
    "ensure_db",
    "ensure_utc",
    "ensure_utc_or_min",
    "format_iso_z",
    "is_wildcard_pattern",
    "matches_pattern",
    "normalize_uuid_or_none",
    "pattern_to_regex",
    "utcnow",
    "wildcard_to_sql_like",
]
