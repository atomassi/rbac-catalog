"""Core business logic: models, database, diffing, configuration, constants, patterns."""

from .constants import HIGH_PRIVILEGE_ROLES, EventType, RoleStatus
from .db import DBEngine, EngineFactory, create_sessionmaker
from .models import (
    Base,
    Operation,
    OperationScanStatus,
    Role,
    RoleHistory,
    RoleScanStatus,
)
from .patterns import (
    is_wildcard_pattern,
    matches_pattern,
    pattern_to_regex,
    wildcard_to_sql_like,
)
from .schema import ensure_db
from .singleton import ThreadSafeSingleton
from .utils import ensure_utc, ensure_utc_or_min, format_iso_z, normalize_uuid_or_none, utcnow

__all__ = [
    "HIGH_PRIVILEGE_ROLES",
    "Base",
    "DBEngine",
    "EngineFactory",
    "EventType",
    "Operation",
    "OperationScanStatus",
    "Role",
    "RoleHistory",
    "RoleScanStatus",
    "RoleStatus",
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
