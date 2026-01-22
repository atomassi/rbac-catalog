"""MCP server for Azure RBAC Catalog."""

import logging
import time
from collections import OrderedDict

from mcp.server.fastmcp import Context, FastMCP
from starlette.applications import Starlette

from azurerbac.airecommender import ai_recommend_roles
from azurerbac.airecommender.modes import RecommenderMode
from azurerbac.cache.container import CacheContainer
from azurerbac.cache.models import CachedRole
from azurerbac.core.patterns import is_wildcard_pattern
from azurerbac.matching import recommend_roles
from azurerbac.mcp.constants import (
    AI_RECOMMEND_DESC,
    DEFAULT_AI_RECOMMEND_LIMIT,
    DEFAULT_OPERATIONS_LIMIT,
    DEFAULT_RECOMMEND_LIMIT,
    DEFAULT_ROLES_LIMIT,
    GET_ROLE_DESC,
    GET_ROLE_PERMISSIONS_DESC,
    MAX_AI_QUERY_LENGTH,
    MAX_AI_RECOMMEND_LIMIT,
    MAX_OPERATION_LENGTH,
    MAX_OPERATIONS_LIMIT,
    MAX_OPERATIONS_PER_REQUEST,
    MAX_QUERY_LENGTH,
    MAX_RECOMMEND_LIMIT,
    MAX_ROLE_ID_LENGTH,
    MAX_ROLES_LIMIT,
    MCP_SERVER_INSTRUCTIONS,
    MCP_SERVER_NAME,
    MIN_AI_QUERY_LENGTH,
    MIN_QUERY_LENGTH,
    RATE_LIMIT_GLOBAL_CAPACITY,
    RATE_LIMIT_GLOBAL_REFILL_RATE,
    RATE_LIMIT_MAX_SESSIONS,
    RATE_LIMIT_SESSION_CAPACITY,
    RATE_LIMIT_SESSION_REFILL_RATE,
    RATE_LIMIT_SESSION_TIMEOUT_SECONDS,
    RECOMMEND_ROLES_DESC,
    SEARCH_OPERATIONS_DESC,
    SEARCH_ROLES_DESC,
)
from azurerbac.mcp.utils import InputValidator, TokenBucketRateLimiter, ToolTimer, ValidationError
from azurerbac.telemetry import track_event, track_gauge
from azurerbac.web.constants import NEW_DOMAIN, SITE_URL

logger = logging.getLogger(__name__)


class MCPServer:
    """MCP server providing Azure RBAC tools with rate limiting."""

    __slots__ = ("_cache", "_global_limiter", "_mcp", "_session_last_activity", "_session_limiter")

    def __init__(self, cache: CacheContainer) -> None:
        self._cache = cache
        self._global_limiter = TokenBucketRateLimiter(
            RATE_LIMIT_GLOBAL_CAPACITY, RATE_LIMIT_GLOBAL_REFILL_RATE, max_buckets=1
        )
        self._session_limiter = TokenBucketRateLimiter(
            RATE_LIMIT_SESSION_CAPACITY, RATE_LIMIT_SESSION_REFILL_RATE, RATE_LIMIT_MAX_SESSIONS
        )
        self._session_last_activity: OrderedDict[str, float] = OrderedDict()
        self._mcp = FastMCP(name=MCP_SERVER_NAME, instructions=MCP_SERVER_INSTRUCTIONS)
        # Configure internal path to "/" so when mounted at /mcp, endpoint is /mcp (not /mcp/mcp)
        self._mcp.settings.streamable_http_path = "/"
        # Allow production host for DNS rebinding protection
        # Standard HTTPS (443) sends Host header without port
        if self._mcp.settings.transport_security:
            if NEW_DOMAIN not in self._mcp.settings.transport_security.allowed_hosts:
                self._mcp.settings.transport_security.allowed_hosts.append(NEW_DOMAIN)
            if SITE_URL not in self._mcp.settings.transport_security.allowed_origins:
                self._mcp.settings.transport_security.allowed_origins.append(SITE_URL)
        self._register_tools()
        track_event("mcp_server_initialized", {})
        logger.info("MCP server '%s' initialized", MCP_SERVER_NAME)

    def streamable_http_app(self) -> Starlette:
        """Get Starlette Streamable HTTP app (modern MCP transport)."""
        return self._mcp.streamable_http_app()

    def _timer(self, tool_name: str) -> ToolTimer:
        return ToolTimer(tool_name)

    def _track_rate_limit(self, tool_name: str, limit_type: str) -> None:
        track_event("mcp_rate_limit", {"tool": tool_name, "type": limit_type})

    @staticmethod
    def _get_client_key(ctx: Context | None) -> str:
        """Extract client identifier for rate limiting.

        Uses session object identity as key since client_id is not sent by most clients.
        In Streamable HTTP mode, each session gets a unique ServerSession instance.
        """
        if ctx is None:
            logger.debug("No MCP context provided, using 'default' client key")
            return "default"
        # Use session object id - stable for the lifetime of the session
        session_key = f"session_{id(ctx.session)}"
        logger.debug("MCP session_key=%s, request_id=%s", session_key, ctx.request_id)
        return session_key

    # -------------------------------------------------------------------------
    # Rate limiting
    # -------------------------------------------------------------------------

    def _cleanup_expired_sessions(self) -> None:
        now = time.monotonic()
        expired = [
            sid
            for sid, last in self._session_last_activity.items()
            if now - last > RATE_LIMIT_SESSION_TIMEOUT_SECONDS
        ]
        for sid in expired:
            del self._session_last_activity[sid]
        if expired:
            logger.info("Cleaned up %d expired sessions", len(expired))

    def _check_rate_limit(self, tool_name: str, session_id: str = "default") -> str | None:
        """Check rate limits. Returns error message or None if allowed."""
        logger.debug("Rate limit check: tool=%s, session=%s", tool_name, session_id)
        now = time.monotonic()
        if self._session_last_activity:
            self._cleanup_expired_sessions()

        # Max sessions check
        if (
            session_id not in self._session_last_activity
            and len(self._session_last_activity) >= RATE_LIMIT_MAX_SESSIONS
        ):
            logger.warning("Max sessions reached, rejecting: %s", session_id)
            self._track_rate_limit(tool_name, "max_sessions")
            return f"Server at capacity ({RATE_LIMIT_MAX_SESSIONS} sessions). Try later."

        self._session_last_activity[session_id] = now
        self._session_last_activity.move_to_end(session_id)

        # Global limit
        result = self._global_limiter.is_allowed("global")
        if not result.allowed:
            logger.warning("Global rate limit exceeded for %s", tool_name)
            self._track_rate_limit(tool_name, "global")
            return f"Server under high load. Please wait {result.wait_seconds:.0f} seconds."

        # Session limit
        result = self._session_limiter.is_allowed(session_id)
        if not result.allowed:
            logger.warning("Session rate limit exceeded for %s", tool_name)
            self._track_rate_limit(tool_name, "session")
            return f"Rate limit exceeded. Please wait {result.wait_seconds:.0f} seconds."

        track_gauge("mcp_active_sessions", len(self._session_last_activity))
        return None

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _find_role(self, role_id_or_name: str) -> CachedRole | None:
        """Find role by ID or name."""
        if cached := self._cache.get_role_by_id(role_id_or_name):
            return cached
        for role in self._cache.get_all_roles():
            if role.role_name and role.role_name.lower() == role_id_or_name.lower():
                return self._cache.get_role_by_id(role.name)
        return None

    @staticmethod
    def _format_action_list(actions: list[str], limit: int = 50) -> list[str]:
        lines = [f"  • {a}" for a in actions[:limit]]
        if len(actions) > limit:
            lines.append(f"  ... and {len(actions) - limit} more")
        return lines

    # -------------------------------------------------------------------------
    # Tool registration
    # -------------------------------------------------------------------------

    def _register_tools(self) -> None:
        @self._mcp.tool(description=SEARCH_OPERATIONS_DESC)
        def search_operations(
            query: str, limit: int = DEFAULT_OPERATIONS_LIMIT, ctx: Context | None = None
        ) -> str:
            logger.debug("search_operations called: query=%r, limit=%d", query, limit)
            if err := self._check_rate_limit("search_operations", self._get_client_key(ctx)):
                return err

            with self._timer("search_operations") as timer:
                try:
                    query = InputValidator.validate(
                        query, MAX_QUERY_LENGTH, MIN_QUERY_LENGTH, "Query"
                    )
                except ValidationError as e:
                    timer.fail()
                    return str(e)

                results = self._cache.search_operations(
                    query,
                    limit=min(limit, MAX_OPERATIONS_LIMIT),
                    is_wildcard=is_wildcard_pattern(query),
                )
                timer.result_count = len(results)

            if not results:
                return f"No operations found matching '{query}'"

            control = sum(1 for op in results if not op.is_data_action)
            data = len(results) - control
            lines = [f"Found {len(results)} operations ({control} control, {data} data):\n"]
            for op in results:
                flag = " [DATA]" if op.is_data_action else ""
                desc = f" - {op.description}" if op.description else ""
                lines.append(f"• {op.name}{flag}{desc}")
            return "\n".join(lines)

        @self._mcp.tool(description=SEARCH_ROLES_DESC)
        def search_roles(
            query: str, limit: int = DEFAULT_ROLES_LIMIT, ctx: Context | None = None
        ) -> str:
            logger.debug("search_roles called: query=%r, limit=%d", query, limit)
            if err := self._check_rate_limit("search_roles", self._get_client_key(ctx)):
                return err

            with self._timer("search_roles") as timer:
                try:
                    query = InputValidator.validate(
                        query, MAX_QUERY_LENGTH, MIN_QUERY_LENGTH, "Query"
                    )
                except ValidationError as e:
                    timer.fail()
                    return str(e)

                query_lower = query.lower()
                matching = [
                    r
                    for r in self._cache.get_all_roles()
                    if query_lower in (r.role_name or "").lower()
                    or query_lower in (r.description or "").lower()
                ][: min(limit, MAX_ROLES_LIMIT)]
                timer.result_count = len(matching)

            if not matching:
                return f"No roles found matching '{query}'"

            lines = [f"Found {len(matching)} roles matching '{query}':\n"]
            for role in matching:
                desc = (role.description or "")[:100]
                if len(role.description or "") > 100:
                    desc += "..."
                lines.append(f"• **{role.role_name}** (ID: {role.name})")
                if desc:
                    lines.append(f"  {desc}")
            return "\n".join(lines)

        @self._mcp.tool(description=GET_ROLE_DESC)
        def get_role(role_id_or_name: str, ctx: Context | None = None) -> str:
            logger.debug("get_role called: role_id_or_name=%r", role_id_or_name)
            if err := self._check_rate_limit("get_role", self._get_client_key(ctx)):
                return err

            with self._timer("get_role") as timer:
                try:
                    role_id_or_name = InputValidator.validate(
                        role_id_or_name, MAX_ROLE_ID_LENGTH, 1, "Role identifier"
                    )
                except ValidationError as e:
                    timer.fail()
                    return str(e)

                cached = self._find_role(role_id_or_name)
                if not cached:
                    timer.fail()
                    return f"Role not found: '{role_id_or_name}'"

                timer.result_count = 1
                role = cached.definition

            perms = role.properties.permissions[0] if role.properties.permissions else None
            lines = [
                f"# {role.role_name}",
                f"**ID:** {role.name}",
                f"**Description:** {role.description or 'N/A'}",
                "",
            ]

            if perms:
                if perms.actions:
                    lines.append("**Actions (Control Plane):**")
                    lines.extend(self._format_action_list(perms.actions))
                if perms.not_actions:
                    lines.append("\n**NotActions (Excluded):**")
                    lines.extend(self._format_action_list(perms.not_actions, limit=100))
                if perms.data_actions:
                    lines.append("\n**DataActions (Data Plane):**")
                    lines.extend(self._format_action_list(perms.data_actions))
                if perms.not_data_actions:
                    lines.append("\n**NotDataActions (Excluded):**")
                    lines.extend(self._format_action_list(perms.not_data_actions, limit=100))

            return "\n".join(lines)

        @self._mcp.tool(description=GET_ROLE_PERMISSIONS_DESC)
        def get_role_permissions(
            role_id_or_name: str, include_data_actions: bool = True, ctx: Context | None = None
        ) -> str:
            logger.debug(
                "get_role_permissions called: role=%r, include_data=%s",
                role_id_or_name,
                include_data_actions,
            )
            if err := self._check_rate_limit("get_role_permissions", self._get_client_key(ctx)):
                return err

            with self._timer("get_role_permissions") as timer:
                try:
                    role_id_or_name = InputValidator.validate(
                        role_id_or_name, MAX_ROLE_ID_LENGTH, 1, "Role identifier"
                    )
                except ValidationError as e:
                    timer.fail()
                    return str(e)

                cached = self._find_role(role_id_or_name)
                if not cached:
                    timer.fail()
                    return f"Role not found: '{role_id_or_name}'"

                coverage = self._cache.get_role_coverage(cached.definition.name)
                if not coverage:
                    timer.fail()
                    return "Permission coverage not available for this role"

                timer.result_count = len(coverage.control) + (
                    len(coverage.data) if include_data_actions else 0
                )
                role_name = cached.definition.role_name

            lines = [f"# Expanded Permissions for {role_name}\n"]
            lines.append(f"**Control Plane Operations:** {len(coverage.control)}")
            if coverage.control:
                lines.extend(self._format_action_list(sorted(coverage.control), limit=100))

            if include_data_actions and coverage.data:
                lines.append(f"\n**Data Plane Operations:** {len(coverage.data)}")
                lines.extend(self._format_action_list(sorted(coverage.data), limit=100))

            return "\n".join(lines)

        @self._mcp.tool(description=RECOMMEND_ROLES_DESC)
        def recommend_roles_tool(
            operations: list[str] | None = None,
            wildcards_control: list[str] | None = None,
            wildcards_data: list[str] | None = None,
            max_results: int = DEFAULT_RECOMMEND_LIMIT,
            ctx: Context | None = None,
        ) -> str:
            logger.debug(
                "recommend_roles_tool called: ops=%d, wildcards_ctrl=%d, wildcards_data=%d",
                len(operations or []),
                len(wildcards_control or []),
                len(wildcards_data or []),
            )
            if err := self._check_rate_limit("recommend_roles_tool", self._get_client_key(ctx)):
                return err

            with self._timer("recommend_roles_tool") as timer:
                all_ops = (operations or []) + (wildcards_control or []) + (wildcards_data or [])
                if not all_ops:
                    timer.fail()
                    return "Provide operations, wildcards_control, or wildcards_data"
                if len(all_ops) > MAX_OPERATIONS_PER_REQUEST:
                    timer.fail()
                    return f"Maximum {MAX_OPERATIONS_PER_REQUEST} operations allowed"

                sanitized_ops: list[str] = []
                data_flags: dict[str, bool] = {}

                try:
                    # Explicit operations - auto-detect plane
                    for op in operations or []:
                        sanitized_ops.append(
                            InputValidator.validate(op, MAX_OPERATION_LENGTH, 1, "Operation")
                        )

                    # Control-plane wildcards
                    for op in wildcards_control or []:
                        clean = InputValidator.validate(op, MAX_OPERATION_LENGTH, 1, "Operation")
                        sanitized_ops.append(clean)
                        data_flags[clean] = False

                    # Data-plane wildcards
                    for op in wildcards_data or []:
                        clean = InputValidator.validate(op, MAX_OPERATION_LENGTH, 1, "Operation")
                        sanitized_ops.append(clean)
                        data_flags[clean] = True
                except ValidationError as e:
                    timer.fail()
                    return str(e)

                matches = recommend_roles(
                    requested_operations=sanitized_ops,
                    roles=self._cache.get_all_roles(),
                    all_operations=self._cache.get_all_operations(),
                    max_results=min(max_results, MAX_RECOMMEND_LIMIT),
                    requested_ops_data_flags=data_flags or None,
                )
                timer.result_count = len(matches)

            if not matches:
                return "No roles found that grant the requested operations"

            lines = [f"Found {len(matches)} roles:\n"]
            for i, m in enumerate(matches, 1):
                excess = m.total_permissions - m.matched_operations_count
                lines.append(f"## {i}. {m.role_name}")
                lines.append(f"   **Coverage:** {m.match_percentage:.0f}% | **Excess:** {excess}")
                lines.append(f"   **ID:** {m.role_id}")
                if m.missing_operations:
                    lines.append(f"   **Missing:** {', '.join(m.missing_operations[:5])}")
                lines.append("")
            lines.append("💡 Lower excess = better least-privilege fit")
            return "\n".join(lines)

        @self._mcp.tool(description=AI_RECOMMEND_DESC)
        def ai_recommend(
            query: str, top_k: int = DEFAULT_AI_RECOMMEND_LIMIT, ctx: Context | None = None
        ) -> str:
            logger.debug("ai_recommend called: query=%r, top_k=%d", query, top_k)
            if err := self._check_rate_limit("ai_recommend", self._get_client_key(ctx)):
                return err

            with self._timer("ai_recommend") as timer:
                try:
                    query = InputValidator.validate(
                        query, MAX_AI_QUERY_LENGTH, MIN_AI_QUERY_LENGTH, "Query"
                    )
                except ValidationError as e:
                    timer.fail()
                    return str(e)

                top_k = min(top_k, MAX_AI_RECOMMEND_LIMIT)

                try:
                    recommendations, mode = ai_recommend_roles(
                        query=query,
                        roles=self._cache.get_all_roles(),
                        top_k=top_k,
                        requested_mode=RecommenderMode.COLBERT.value,
                    )
                except Exception as e:
                    logger.exception("AI recommendation failed")
                    timer.fail()
                    return f"AI recommendation failed: {e!s}"

                timer.result_count = len(recommendations)

            if not recommendations:
                return f"No roles found matching '{query}'"

            lines = [f'AI recommendations for: "{query}"\n', f"_(Using {mode} engine)_\n"]
            for i, rec in enumerate(recommendations, 1):
                lines.append(f"## {i}. {rec['role_name']} (score: {rec.get('score', 0):.2f})")
                lines.append(f"   {rec.get('description', 'N/A')[:150]}")
                if rec.get("matched_keywords"):
                    lines.append(f"   **Matched:** {', '.join(rec['matched_keywords'][:5])}")
                lines.append("")
            return "\n".join(lines)


def create_mcp_server(cache: CacheContainer) -> Starlette:
    """Create the MCP Starlette app to mount.

    Returns the Starlette app from streamable_http_app().
    Configured with streamable_http_path="/" so when mounted at /mcp,
    the endpoint is /mcp (not /mcp/mcp).
    """
    return MCPServer(cache).streamable_http_app()
