"""Cache build and refresh operations."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from azurerbac.azure.models import OperationData, RoleDefinition
from azurerbac.cache.backends import get_cache_backend
from azurerbac.cache.models import (
    CACHE_VERSION,
    CacheData,
    CachedChangeEvent,
    CachedRole,
    CacheMetadata,
    build_indexes,
    compute_operations_hash,
    compute_roles_hash,
)
from azurerbac.core.constants import RoleStatus
from azurerbac.core.patterns import is_wildcard_pattern, matches_pattern

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Module-level lock to prevent concurrent cache rebuilds
_rebuild_lock = threading.Lock()


# ============================================================================
# Pattern matching helpers
# ============================================================================


def get_matching_operations(
    pattern: str,
    ops: set[str],
    cache_key: int,
    pattern_cache: dict[tuple[str, int], set[str]],
) -> set[str]:
    """Get operations matching a pattern, using cache for performance."""
    key = (pattern.lower(), cache_key)
    if key in pattern_cache:
        return pattern_cache[key]

    matching = {op for op in ops if matches_pattern(op, pattern)}
    pattern_cache[key] = matching
    return matching


def build_operations_prefix_index(
    ops: set[str],
    cache_key: int,
    prefix_cache: dict[int, dict[str, set[str]]],
) -> dict[str, set[str]]:
    """Build an index of operations by their provider prefix."""
    if cache_key in prefix_cache:
        return prefix_cache[cache_key]

    index: dict[str, set[str]] = {}
    for op in ops:
        slash_idx = op.find("/")
        if slash_idx > 0:
            prefix = op[: slash_idx + 1].lower()
            index.setdefault(prefix, set()).add(op)

    prefix_cache[cache_key] = index
    return index


# ============================================================================
# Precompute functions
# ============================================================================


def _add_operations_for_patterns(
    dst: set[str],
    patterns: list[str],
    *,
    all_ops: set[str],
    cache_key: int,
    pattern_match: dict[tuple[str, int], set[str]],
    ops_lower_to_orig: dict[str, str],
) -> None:
    """Add operations matching patterns to destination set."""
    for pattern in patterns:
        if pattern == "*":
            dst.update(all_ops)
        elif is_wildcard_pattern(pattern):
            dst.update(get_matching_operations(pattern, all_ops, cache_key, pattern_match))
        else:
            orig_op = ops_lower_to_orig.get(pattern.lower())
            if orig_op:
                dst.add(orig_op)


def _precompute_common_patterns(
    all_control_ops: set[str],
    all_data_ops: set[str],
    control_cache_key: int,
    data_cache_key: int,
    pattern_match: dict[tuple[str, int], set[str]],
) -> None:
    """Precompute common wildcard patterns."""
    common_patterns = ["*/read", "*/write", "*/delete", "*/action", "*/listkeys/action", "*"]
    for pattern in common_patterns:
        get_matching_operations(pattern, all_control_ops, control_cache_key, pattern_match)
        get_matching_operations(pattern, all_data_ops, data_cache_key, pattern_match)


def _collect_role_patterns(roles: list[RoleDefinition]) -> set[str]:
    """Collect all unique wildcard action patterns from roles."""
    patterns: set[str] = set()
    for role in roles:
        for perm in role.properties.permissions:
            all_actions = (
                perm.actions + perm.not_actions + perm.data_actions + perm.not_data_actions
            )
            for action in all_actions:
                if is_wildcard_pattern(action):
                    patterns.add(action.lower())
    return patterns


def _compute_role_coverage(
    role: RoleDefinition,
    all_control_ops: set[str],
    all_data_ops: set[str],
    control_cache_key: int,
    data_cache_key: int,
    pattern_match: dict[tuple[str, int], set[str]],
    control_ops_lower_to_orig: dict[str, str],
    data_ops_lower_to_orig: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Compute effective operations (granted - excluded) for a role."""
    control_granted: set[str] = set()
    data_granted: set[str] = set()
    control_excluded: set[str] = set()
    data_excluded: set[str] = set()

    for perm in role.properties.permissions:
        _add_operations_for_patterns(
            control_granted,
            perm.actions,
            all_ops=all_control_ops,
            cache_key=control_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=control_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            control_excluded,
            perm.not_actions,
            all_ops=all_control_ops,
            cache_key=control_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=control_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            data_granted,
            perm.data_actions,
            all_ops=all_data_ops,
            cache_key=data_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=data_ops_lower_to_orig,
        )
        _add_operations_for_patterns(
            data_excluded,
            perm.not_data_actions,
            all_ops=all_data_ops,
            cache_key=data_cache_key,
            pattern_match=pattern_match,
            ops_lower_to_orig=data_ops_lower_to_orig,
        )

    return control_granted - control_excluded, data_granted - data_excluded


def _build_operation_role_count(
    role_coverage: dict[str, tuple[set[str], set[str]]],
) -> dict[str, int]:
    """Build operation -> role count index from role coverage."""
    counts: dict[str, int] = {}
    for control_ops, data_ops in role_coverage.values():
        for op in control_ops:
            op_lower = op.lower()
            counts[op_lower] = counts.get(op_lower, 0) + 1
        for op in data_ops:
            op_lower = op.lower()
            counts[op_lower] = counts.get(op_lower, 0) + 1
    return counts


def precompute_all(
    roles: list[RoleDefinition],
    all_operations: list[OperationData],
    *,
    metadata: CacheMetadata | None = None,
    roles_by_id: dict[str, CachedRole] | None = None,
    all_change_events: list[CachedChangeEvent] | None = None,
    last_scan: datetime | None = None,
    first_scan: datetime | None = None,
) -> CacheData:
    """Pre-compute ALL caches and return complete CacheData.

    Precomputes:
    1. Common wildcard pattern matches (*/read, */write, etc.)
    2. All unique action patterns found in roles
    3. Role coverage data (which operations each role grants)
    4. Role net permission counts
    5. Prefix indexes for fast lookup

    Args:
        roles: List of RoleDefinition Pydantic models
        all_operations: List of OperationData models
        metadata: Optional CacheMetadata for versioning/invalidation.
        roles_by_id: Optional dict of roles by ID.
        all_change_events: Optional list of change events.
        last_scan: Optional timestamp.
        first_scan: Optional timestamp.

    Returns:
        Complete CacheData with all computed fields.
    """
    start = time.time()
    logger.debug(
        "Precomputing caches for %d roles, %d operations...",
        len(roles),
        len(all_operations),
    )

    providers: set[str] = {
        op.provider_display_name for op in all_operations if op.provider_display_name
    }
    unique_providers = sorted(providers, key=str.casefold)

    # Build computed data into temporary dicts
    pattern_match: dict[tuple[str, int], set[str]] = {}
    wildcard_count: dict[tuple[str, int], int] = {}
    operations_by_prefix_computed: dict[int, dict[str, set[str]]] = {}
    role_coverage: dict[str, tuple[set[str], set[str]]] = {}
    role_net_permissions: dict[str, tuple[int, int]] = {}
    partial_coverage: dict[tuple, tuple[int, int, int, list[str]]] = {}

    # Separate control and data plane operations
    all_control_ops = {op.name for op in all_operations if not op.is_data_action}
    all_data_ops = {op.name for op in all_operations if op.is_data_action}

    logger.debug("Operations: %d control, %d data plane", len(all_control_ops), len(all_data_ops))

    # Build lowercase lookup sets for case-insensitive matching
    control_ops_lower_to_orig = {op.lower(): op for op in all_control_ops}
    data_ops_lower_to_orig = {op.lower(): op for op in all_data_ops}

    cache_ops_count = [len(all_control_ops), len(all_data_ops)]
    control_cache_key = len(all_control_ops)
    data_cache_key = len(all_data_ops) + 1000000

    # Build prefix indexes
    logger.debug("Building prefix indexes...")
    build_operations_prefix_index(all_control_ops, control_cache_key, operations_by_prefix_computed)
    build_operations_prefix_index(all_data_ops, data_cache_key, operations_by_prefix_computed)

    # 1. Precompute common patterns
    logger.debug("Precomputing common patterns...")
    _precompute_common_patterns(
        all_control_ops, all_data_ops, control_cache_key, data_cache_key, pattern_match
    )

    # 2. Collect and precompute all unique action patterns from roles
    logger.debug("Collecting unique action patterns from roles...")
    all_action_patterns = _collect_role_patterns(roles)
    logger.debug("Precomputing %d unique action patterns...", len(all_action_patterns))
    for pattern in all_action_patterns:
        get_matching_operations(pattern, all_control_ops, control_cache_key, pattern_match)
        get_matching_operations(pattern, all_data_ops, data_cache_key, pattern_match)

    # 3. Precompute role coverage and net permissions
    logger.debug("Computing role coverage and net permissions...")
    builtin_count = 0
    for role in roles:
        if not role.is_builtin:
            continue

        builtin_count += 1
        net_control, net_data = _compute_role_coverage(
            role,
            all_control_ops,
            all_data_ops,
            control_cache_key,
            data_cache_key,
            pattern_match,
            control_ops_lower_to_orig,
            data_ops_lower_to_orig,
        )
        role_coverage[role.role_id] = (net_control, net_data)
        role_net_permissions[role.role_id] = (len(net_control), len(net_data))

    logger.debug("Computed coverage for %d built-in roles", builtin_count)

    # Build operation -> role count from role coverage
    logger.debug("Building operation role count index...")
    operation_role_count = _build_operation_role_count(role_coverage)

    # Build operation indexes
    logger.debug("Building operation indexes...")
    ops_by_name_lower, ops_by_prefix = build_indexes(all_operations)

    # Create complete cache with source data + computed fields
    new_cache = CacheData(
        metadata=metadata or CacheMetadata(),
        all_operations=all_operations,
        roles_by_id=roles_by_id or {},
        all_change_events=all_change_events or [],
        unique_providers=unique_providers,
        last_scan=last_scan,
        first_scan=first_scan,
        ops_by_name_lower=ops_by_name_lower,
        ops_by_prefix=ops_by_prefix,
        role_coverage=role_coverage,
        role_net_permissions=role_net_permissions,
        operation_role_count=operation_role_count,
        pattern_match=pattern_match,
        partial_coverage=partial_coverage,
        wildcard_count=wildcard_count,
        operations_by_prefix_computed=operations_by_prefix_computed,
        cache_ops_count=cache_ops_count,
    )

    elapsed = time.time() - start
    logger.info(
        f"Precomputed all caches in {elapsed:.2f}s: "
        f"{len(all_action_patterns)} patterns, {len(role_coverage)} roles, "
        f"{len(pattern_match)} pattern matches"
    )

    return new_cache


# ============================================================================
# Build from database
# ============================================================================


async def build_from_db(session: AsyncSession) -> CacheData:
    """Build cache data from database (pure function).

    Queries database for all roles, operations, and events, builds indexes,
    and returns a complete CacheData object. Does NOT modify any global state.

    Args:
        session: SQLAlchemy async session for queries

    Returns:
        Complete CacheData ready for use

    Raises:
        Exception: If database queries fail
    """
    from sqlalchemy import func, select

    from azurerbac.core import Operation, Role, RoleHistory, RoleScanStatus
    from azurerbac.telemetry import TimedDbQuery

    logger.info("Building cache from database...")

    # Fetch ALL roles (active and deleted) for roles_by_id index
    async with TimedDbQuery("fetch_all_roles") as timer:
        all_roles_result = await session.execute(select(Role))
        all_role_snapshots = list(all_roles_result.scalars().all())
        timer.rows = len(all_role_snapshots)

    # Fetch active roles for role_jsons (used by recommender)
    active_roles = [r for r in all_role_snapshots if r.status == RoleStatus.ACTIVE]
    logger.debug(f"Found {len(active_roles)} active roles out of {len(all_role_snapshots)} total")

    # Fetch all operations
    async with TimedDbQuery("fetch_all_operations") as timer:
        ops_result = await session.execute(select(Operation))
        all_ops = list(ops_result.scalars().all())
        timer.rows = len(all_ops)

    # Fetch all history events
    async with TimedDbQuery("fetch_all_history_events") as timer:
        events_result = await session.execute(
            select(RoleHistory).order_by(RoleHistory.scan_id.desc())
        )
        all_events = list(events_result.scalars().all())
        timer.rows = len(all_events)

    # Get scan status
    async with TimedDbQuery("fetch_scan_status"):
        last_scan = await session.scalar(
            select(RoleScanStatus.scan_timestamp)
            .order_by(RoleScanStatus.scan_timestamp.desc())
            .limit(1)
        )
        first_scan = await session.scalar(select(func.min(RoleScanStatus.scan_timestamp)))

    # Collect RoleDefinition objects
    role_definitions = [role.role_definition for role in active_roles if role.role_definition]

    # Convert DB Operation models to OperationData Pydantic models
    all_operations = [
        OperationData.model_validate(
            {
                "name": op.name,
                "displayName": op.display_name,
                "description": op.description,
                "provider_display_name": op.provider_display_name or "",
                "resource_type_display_name": op.resource_type_display_name,
                "isDataAction": op.is_data_action,
            }
        )
        for op in all_ops
    ]

    # Build roles_by_id index with CachedRole objects
    roles_by_id: dict[str, CachedRole] = {}
    for role in all_role_snapshots:
        role_def = role.last_known_definition
        if role_def:
            roles_by_id[role.role_id] = CachedRole(
                definition=role_def,
                status=role.status,
                last_seen_at=role.last_seen_at,
            )

    # Build change events list
    all_change_events: list[CachedChangeEvent] = []
    for ev in all_events:
        cached_role = roles_by_id.get(ev.role_id)
        role_name = cached_role.role_name if cached_role else ev.role_name
        all_change_events.append(
            CachedChangeEvent(
                id=ev.id,
                role_id=ev.role_id,
                role_name=role_name,
                event_type=ev.event_type,
                scan_timestamp=ev.scan.scan_timestamp if ev.scan else None,
                azure_updated_on=ev.azure_updated_on,
                summary=ev.summary,
                diff_json=ev.diff_json,
                role_json=ev.role_definition.to_dict() if ev.role_definition else None,
            )
        )

    # Compute hashes
    roles_hash = compute_roles_hash(role_definitions)
    operations_hash = compute_operations_hash(all_operations)

    # Create metadata
    metadata = CacheMetadata(
        roles_count=len(active_roles),
        operations_count=len(all_operations),
        roles_hash=roles_hash,
        operations_hash=operations_hash,
    )

    # Precompute all caches and build complete CacheData
    cache_data = precompute_all(
        role_definitions,
        all_operations,
        metadata=metadata,
        roles_by_id=roles_by_id,
        all_change_events=all_change_events,
        last_scan=last_scan.replace(microsecond=0) if last_scan else None,
        first_scan=first_scan.replace(microsecond=0) if first_scan else None,
    )

    logger.info(
        f"Cache built: {len(active_roles)} roles, {len(all_operations)} operations, "
        f"{len(roles_by_id)} indexed, {len(all_change_events)} events"
    )

    return cache_data


# ============================================================================
# Swap and save operations
# ============================================================================


def swap_in_memory(cache_data: CacheData) -> None:
    """Swap cache data into the in-memory container.

    Args:
        cache_data: Complete cache data to swap in
    """
    from azurerbac.cache.container import get_cache_container

    container = get_cache_container()
    container.swap(cache_data)
    container.loaded_version = get_cache_backend().get_version()
    container.is_preloaded = True
    logger.debug("Cache swapped into memory")


async def save(cache_data: CacheData) -> None:
    """Save cache data to backend."""
    await get_cache_backend().save(cache_data)
    logger.debug("Cache saved to backend")


def _initialize_ai_recommender(cache_data: CacheData) -> None:
    """Re-initialize AI recommender with updated role data."""
    try:
        from azurerbac.airecommender import get_ai_recommender

        ai_recommender = get_ai_recommender()
        active_roles = [
            cached_role.definition
            for cached_role in cache_data.roles_by_id.values()
            if cached_role.status == RoleStatus.ACTIVE
        ]
        ai_recommender.initialize(active_roles)
        logger.info("AI recommender re-initialized with updated roles")
    except Exception as e:
        logger.exception("Failed to re-initialize AI recommender: %s", e)


# ============================================================================
# High-level rebuild operations
# ============================================================================


async def rebuild_in_memory(session: AsyncSession) -> bool:
    """Build cache from DB and swap into memory (web app startup).

    Thread-safe: Uses lock to prevent concurrent rebuilds.

    Args:
        session: SQLAlchemy async session

    Returns:
        True if successful, False if rebuild already in progress or failed
    """
    if not _rebuild_lock.acquire(blocking=False):
        logger.warning("Cache rebuild already in progress, skipping")
        return False

    try:
        cache_data = await build_from_db(session)
        swap_in_memory(cache_data)
        _initialize_ai_recommender(cache_data)
        return True
    except Exception as e:
        logger.exception("Failed to rebuild cache in memory: %s", e)
        return False
    finally:
        _rebuild_lock.release()


async def rebuild_and_save(session: AsyncSession) -> bool:
    """Build cache from DB and save to backend (worker process)."""
    if not _rebuild_lock.acquire(blocking=False):
        logger.warning("Cache rebuild already in progress, skipping")
        return False

    try:
        cache_data = await build_from_db(session)
        await save(cache_data)
        return True
    except Exception as e:
        logger.exception("Failed to rebuild cache to backend: %s", e)
        return False
    finally:
        _rebuild_lock.release()


async def invalidate_and_rebuild(session: AsyncSession) -> bool:
    """Delete cache and rebuild from DB (worker after changes).

    Args:
        session: SQLAlchemy async session

    Returns:
        True if successful, False otherwise
    """
    logger.info("Invalidating and rebuilding cache")
    await get_cache_backend().delete()
    return await rebuild_and_save(session)


async def invalidate_all() -> None:
    """Reset in-memory cache and delete disk cache file."""
    from azurerbac.cache.container import get_cache_container

    get_cache_container().reset()
    await get_cache_backend().delete()


# ============================================================================
# Reload from disk (web app detecting worker update)
# ============================================================================


def needs_reload() -> bool:
    """Check if backend cache was updated by worker.

    Compares loaded version with current backend version.

    Returns:
        True if cache version changed since last load.
    """
    from azurerbac.cache.container import get_cache_container

    container = get_cache_container()
    current_version = get_cache_backend().get_version()

    if current_version is None:
        return False

    if container.loaded_version is None:
        logger.info("New cache detected from worker (version: %s)", current_version)
        return True

    if current_version != container.loaded_version:
        logger.info(
            "Cache updated (version: %s != %s)",
            current_version,
            container.loaded_version,
        )
        return True

    return False


def mark_pending_reload() -> None:
    """Mark that a reload is pending (called by backend watcher).

    This is called from the watchdog thread when a cache change
    is detected. The actual reload happens on the next async check.
    """
    from azurerbac.cache.container import get_cache_container

    get_cache_container().pending_reload = True
    logger.debug("Cache reload marked as pending (watcher triggered)")


async def reload_if_needed() -> bool:
    """Reload cache from backend if updated by worker.

    Uses async lock to prevent concurrent reloads.
    The backend file contains complete CacheData - no recomputation needed.

    Returns:
        True if cache was reloaded, False otherwise.
    """
    from azurerbac.cache.container import get_cache_container

    container = get_cache_container()

    # Quick check without lock
    if not container.pending_reload and not needs_reload():
        return False

    reload_lock = container.reload_lock
    if reload_lock.locked():
        logger.debug("Cache reload already in progress, skipping")
        return False

    async with reload_lock:
        container.pending_reload = False

        if not needs_reload():
            return False

        logger.info("Reloading cache from backend...")
        start_time = time.time()

        backend = get_cache_backend()
        cached = await backend.load()
        if cached is None:
            logger.warning("Failed to load cache from disk")
            return False

        current_version = backend.get_version()

        # Validate required data
        if not cached.roles_by_id or not cached.all_operations:
            logger.warning("Loaded cache incomplete, keeping current")
            container.loaded_version = current_version
            return False

        # Check version compatibility
        if cached.metadata.version != CACHE_VERSION:
            logger.warning(
                f"Cache version mismatch: {cached.metadata.version} != {CACHE_VERSION}, "
                "keeping current (worker will rebuild)"
            )
            container.loaded_version = current_version
            return False

        # Swap in the loaded cache
        container.swap(cached)
        container.loaded_version = current_version
        container.is_preloaded = True

        elapsed = time.time() - start_time
        logger.info(
            f"Cache reloaded in {elapsed:.2f}s: {len(cached.roles_by_id)} roles, "
            f"{len(cached.all_operations)} ops, "
            f"{len(cached.role_coverage)} role coverages (precomputed)"
        )
        return True
