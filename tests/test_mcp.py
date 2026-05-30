"""Tests for MCP server implementation."""

import asyncio
import inspect
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from azurerbac.mcp.server import MCPServer, create_disabled_mcp_app
from azurerbac.mcp.utils import ToolTimer

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_role() -> MagicMock:
    """Create a mock role."""
    role = MagicMock()
    role.name = "test-role-id"
    role.role_name = "Test Role"
    role.description = "A test role for unit testing"
    return role


@pytest.fixture
def mock_operation() -> MagicMock:
    """Create a mock operation."""
    op = MagicMock()
    op.name = "Microsoft.Test/resources/read"
    op.is_data_action = False
    op.description = "Read test resources"
    return op


@pytest.fixture
def mock_cached_role() -> MagicMock:
    """Create a mock cached role with full structure."""
    cached = MagicMock()
    cached.definition = MagicMock()
    cached.definition.name = "test-role-id"
    cached.definition.role_name = "Test Role"
    cached.definition.description = "A test role"
    cached.definition.properties = MagicMock()
    cached.definition.properties.permissions = [
        MagicMock(
            actions=["Microsoft.Test/*/read"],
            not_actions=[],
            data_actions=[],
            not_data_actions=[],
        )
    ]
    # Expose CachedRole-level properties used by search_roles
    cached.role_name = "Test Role"
    cached.role_id = "test-role-id"
    cached.description = "A test role"
    return cached


@pytest.fixture
def mock_cache(
    mock_role: MagicMock, mock_operation: MagicMock, mock_cached_role: MagicMock
) -> MagicMock:
    """Create a fully configured mock cache."""
    cache = MagicMock()
    cache.get_all_roles.return_value = [mock_role]
    cache.search_operations.return_value = [mock_operation]
    cache.get_all_operations.return_value = [mock_operation]
    cache.get_role_by_id.return_value = mock_cached_role

    # Expose roles_by_id on the inner .cache for search_roles
    cache.cache.roles_by_id = {"test-role-id": mock_cached_role}
    cache.cache.role_name_to_id = {"test role": "test-role-id"}

    coverage = MagicMock()
    coverage.control = {"Microsoft.Test/resources/read"}
    coverage.data = set()
    cache.get_role_coverage.return_value = coverage
    return cache


@pytest.fixture
def mcp_server(mock_cache: MagicMock) -> MCPServer:
    """Create an MCP server with mock cache."""
    return MCPServer(mock_cache)


# =============================================================================
# ToolTimer Tests
# =============================================================================


class TestToolTimer:
    """Tests for ToolTimer context manager."""

    def test_timer_records_success(self) -> None:
        with ToolTimer("test_tool") as timer:
            timer.result_count = 5
        assert timer._success is True

    def test_timer_records_failure_on_exception(self) -> None:
        timer: ToolTimer | None = None
        try:
            with ToolTimer("test_tool") as timer:
                raise ValueError("test error")
        except ValueError:
            pass
        assert timer is not None
        assert timer._success is False

    def test_timer_fail_method(self) -> None:
        with ToolTimer("test_tool") as timer:
            timer.fail()
        assert timer._success is False

    def test_timer_tracks_duration(self) -> None:
        import time

        with ToolTimer("test_tool") as timer:
            time.sleep(0.01)
        duration = time.perf_counter() - timer.start
        assert duration >= 0.01


# =============================================================================
# MCPServer Tests
# =============================================================================


class TestMCPServerInit:
    """Tests for MCPServer initialization."""

    @pytest.mark.parametrize(
        "tool_name",
        [
            "search_operations",
            "search_roles",
            "get_role",
            "get_role_permissions",
            "recommend_roles_tool",
            "ai_recommend",
        ],
    )
    def test_tool_registered(self, mcp_server: MCPServer, tool_name: str) -> None:
        """All expected tools are registered."""
        tools = mcp_server._mcp._tool_manager._tools
        assert tool_name in tools


class TestMCPServerClientKey:
    """Tests for _get_client_key method."""

    def test_returns_default_when_context_is_none(self) -> None:
        result = MCPServer._get_client_key(None)
        assert result == "default"

    def test_returns_session_id_from_header(self) -> None:
        """Test that session ID is extracted from mcp-session-id header."""
        mock_ctx = MagicMock()
        mock_ctx.session = MagicMock()
        mock_ctx.session.client_params = None
        mock_ctx.request_id = "123"
        mock_ctx.request_context = MagicMock()
        mock_ctx.request_context.request = MagicMock()
        # Session ID must be 8-128 chars with alphanumeric, hyphens, underscores
        mock_ctx.request_context.request.headers = {"mcp-session-id": "test-session-abc123"}
        result = MCPServer._get_client_key(mock_ctx)
        assert result == "test-session-abc123"

    def test_returns_default_when_no_session_header(self) -> None:
        """Test that 'default' is returned when mcp-session-id header is missing."""
        mock_ctx = MagicMock()
        mock_ctx.session = MagicMock()
        mock_ctx.session.client_params = None
        mock_ctx.request_id = "123"
        mock_ctx.request_context = MagicMock()
        mock_ctx.request_context.request = MagicMock()
        mock_ctx.request_context.request.headers = {}  # No mcp-session-id header
        result = MCPServer._get_client_key(mock_ctx)
        assert result == "default"

    def test_extracts_client_info_from_client_params(self) -> None:
        """Test that client info is correctly extracted from client_params for logging."""
        mock_ctx = MagicMock()
        mock_ctx.session = MagicMock()
        mock_ctx.session.client_params = MagicMock()
        mock_ctx.session.client_params.clientInfo = MagicMock()
        mock_ctx.session.client_params.clientInfo.name = "test-client"
        mock_ctx.session.client_params.clientInfo.version = "1.0.0"
        mock_ctx.request_id = "456"
        mock_ctx.request_context = MagicMock()
        mock_ctx.request_context.request = MagicMock()
        mock_ctx.request_context.request.headers = {"mcp-session-id": "valid-session-id-12345"}
        result = MCPServer._get_client_key(mock_ctx)
        # Should return the session ID (client info is just for logging)
        assert result == "valid-session-id-12345"


class TestMCPServerFindRole:
    """Tests for _find_role method."""

    def test_find_role_by_id(self, mcp_server: MCPServer, mock_cached_role: MagicMock) -> None:
        result = mcp_server._find_role("test-role-id")
        assert result == mock_cached_role

    def test_find_role_by_name_case_insensitive(
        self, mcp_server: MCPServer, mock_role: MagicMock, mock_cached_role: MagicMock
    ) -> None:
        # Configure mock to return None for ID lookup, forcing name search
        mcp_server._cache.get_role_by_id.side_effect = (  # type: ignore[union-attr]
            lambda x: mock_cached_role if x == "test-role-id" else None
        )
        # Set up the role_name_to_id index on the cache data
        mcp_server._cache.cache.role_name_to_id = {"test role": "test-role-id"}  # type: ignore[union-attr]
        result = mcp_server._find_role("TEST ROLE")
        assert result == mock_cached_role

    def test_find_role_returns_none_when_not_found(self, mcp_server: MCPServer) -> None:
        mcp_server._cache.get_role_by_id.return_value = None  # type: ignore[union-attr]
        mcp_server._cache.cache.role_name_to_id = {}  # type: ignore[union-attr]
        result = mcp_server._find_role("nonexistent")
        assert result is None


class TestMCPServerFormatActionList:
    """Tests for _format_action_list static method."""

    def test_format_empty_list(self) -> None:
        result = MCPServer._format_action_list([])
        assert result == []

    def test_format_within_limit(self) -> None:
        actions = ["action1", "action2", "action3"]
        result = MCPServer._format_action_list(actions, limit=10)
        assert len(result) == 3
        assert all(line.startswith("  • ") for line in result)

    def test_format_exceeds_limit(self) -> None:
        actions = [f"action{i}" for i in range(100)]
        result = MCPServer._format_action_list(actions, limit=50)
        assert len(result) == 51  # 50 items + "and X more"
        assert "... and 50 more" in result[-1]


class TestMCPServerRateLimiting:
    """Tests for rate limiting logic."""

    def test_check_rate_limit_allows_first_request(self, mcp_server: MCPServer) -> None:
        result = mcp_server._check_rate_limit("test_tool", "session_1")
        assert result is None  # No error = allowed

    def test_session_cleanup_removes_expired(self, mcp_server: MCPServer) -> None:
        import time

        mcp_server._session_last_activity["old_session"] = time.monotonic() - 7200  # type: ignore[index]
        mcp_server._cleanup_expired_sessions()
        assert "old_session" not in mcp_server._session_last_activity


class TestCreateMCPServer:
    """Tests for create_mcp_server factory function."""

    def test_returns_starlette_app(self) -> None:
        from starlette.applications import Starlette

        from azurerbac.mcp.server import create_mcp_server

        mock_cache = MagicMock()
        mock_cache.get_all_roles.return_value = []
        mock_cache.get_all_operations.return_value = []

        assert isinstance(create_mcp_server(mock_cache), Starlette)


class TestTransportSecurity:
    """Tests for MCP transport security configuration."""

    def test_adds_production_domain_to_allowed_hosts(self, mock_cache: MagicMock) -> None:
        """Verify production domain is added to allowed_hosts when transport_security exists."""
        from azurerbac.core.constants import NEW_DOMAIN

        server = MCPServer(mock_cache)
        transport_security = server._mcp.settings.transport_security

        assert transport_security is not None
        assert NEW_DOMAIN in transport_security.allowed_hosts

    def test_adds_production_origin_to_allowed_origins(self, mock_cache: MagicMock) -> None:
        """Verify production origin is added to allowed_origins when transport_security exists."""
        from azurerbac.core.constants import SITE_URL

        server = MCPServer(mock_cache)
        transport_security = server._mcp.settings.transport_security

        assert transport_security is not None
        assert SITE_URL in transport_security.allowed_origins

    def test_no_duplicate_entries_on_multiple_instantiation(self, mock_cache: MagicMock) -> None:
        """Verify multiple MCPServer instances don't create duplicate entries."""
        from azurerbac.core.constants import NEW_DOMAIN, SITE_URL

        # Create first server
        MCPServer(mock_cache)
        # Create second server (would share settings if they're class-level)
        server2 = MCPServer(mock_cache)

        # Each server should have exactly one entry for production domain
        # (checking server2 since it's the latest instantiation)
        ts = server2._mcp.settings.transport_security
        assert ts is not None
        assert ts.allowed_hosts.count(NEW_DOMAIN) == 1
        assert ts.allowed_origins.count(SITE_URL) == 1


# =============================================================================
# Disabled MCP App Tests
# =============================================================================


class TestDisabledMCPApp:
    """Tests for the disabled MCP app."""

    @pytest.fixture
    def disabled_client(self) -> TestClient:
        """Create a test client for the disabled MCP app."""
        app = create_disabled_mcp_app()
        return TestClient(app)

    def test_root_returns_503(self, disabled_client: TestClient) -> None:
        response = disabled_client.post("/")
        assert response.status_code == 503
        data = response.json()
        assert data["error"]["code"] == -32000
        assert "unavailable" in data["error"]["message"].lower()

    def test_subpath_returns_503(self, disabled_client: TestClient) -> None:
        response = disabled_client.post("/some/path")
        assert response.status_code == 503
        data = response.json()
        assert data["error"]["code"] == -32000

    def test_get_request_returns_503(self, disabled_client: TestClient) -> None:
        response = disabled_client.get("/")
        assert response.status_code == 503

    def test_response_is_jsonrpc_format(self, disabled_client: TestClient) -> None:
        response = disabled_client.post("/")
        data = response.json()
        assert "jsonrpc" in data
        assert data["jsonrpc"] == "2.0"
        assert "error" in data
        assert "id" in data


# =============================================================================
# Helpers for tool invocation
# =============================================================================


def _call_tool(server: MCPServer, tool_name: str, **kwargs: object) -> str:
    """Call a registered MCP tool by name via its internal function.

    Handles both sync and async tool handlers transparently so callers can
    invoke any tool without worrying about its coroutine status.
    """
    tool = server._mcp._tool_manager._tools[tool_name]
    result = tool.fn(**kwargs)
    if inspect.isawaitable(result):
        return asyncio.run(result)  # type: ignore[arg-type]
    return result  # type: ignore[return-value]


# =============================================================================
# Tool Handler Tests — search_operations
# =============================================================================


class TestSearchOperationsTool:
    """Tests for the search_operations MCP tool handler."""

    def test_returns_results(self, mcp_server: MCPServer, mock_operation: MagicMock) -> None:
        mcp_server._cache.search_operations.return_value = [mock_operation]  # type: ignore[union-attr]
        result = _call_tool(mcp_server, "search_operations", query="test read", limit=10, ctx=None)
        assert "Found 1 operations" in result
        assert "Microsoft.Test/resources/read" in result

    def test_no_results(self, mcp_server: MCPServer) -> None:
        mcp_server._cache.search_operations.return_value = []  # type: ignore[union-attr]
        result = _call_tool(
            mcp_server, "search_operations", query="nonexistent", limit=10, ctx=None
        )
        assert "No operations found" in result

    def test_validation_error_short_query(self, mcp_server: MCPServer) -> None:
        result = _call_tool(mcp_server, "search_operations", query="x", limit=10, ctx=None)
        assert "at least" in result.lower()

    def test_data_action_flag(self, mcp_server: MCPServer) -> None:
        """Data actions are labeled with [DATA] in output."""
        data_op = MagicMock()
        data_op.name = "Microsoft.Storage/blobServices/read"
        data_op.is_data_action = True
        data_op.description = "Read blobs"
        mcp_server._cache.search_operations.return_value = [data_op]  # type: ignore[union-attr]
        result = _call_tool(
            mcp_server, "search_operations", query="storage blob", limit=10, ctx=None
        )
        assert "[DATA]" in result

    def test_control_and_data_counts(self, mcp_server: MCPServer) -> None:
        """Output includes correct control/data counts."""
        ctrl = MagicMock(name="Microsoft.Compute/read", is_data_action=False, description="")
        data = MagicMock(name="Microsoft.Storage/read", is_data_action=True, description="")
        mcp_server._cache.search_operations.return_value = [ctrl, data]  # type: ignore[union-attr]
        result = _call_tool(mcp_server, "search_operations", query="read ops", limit=10, ctx=None)
        assert "1 control" in result
        assert "1 data" in result


# =============================================================================
# Tool Handler Tests — search_roles
# =============================================================================


class TestSearchRolesTool:
    """Tests for the search_roles MCP tool handler."""

    def test_returns_matching_roles(self, mcp_server: MCPServer, mock_role: MagicMock) -> None:
        result = _call_tool(mcp_server, "search_roles", query="test", limit=10, ctx=None)
        assert "Test Role" in result
        assert "test-role-id" in result

    def test_no_matching_roles(self, mcp_server: MCPServer) -> None:
        mcp_server._cache.cache.roles_by_id = {}  # type: ignore[union-attr]
        result = _call_tool(mcp_server, "search_roles", query="nonexistent", limit=10, ctx=None)
        assert "No roles found" in result

    def test_matches_by_description(
        self, mcp_server: MCPServer, mock_cached_role: MagicMock
    ) -> None:
        """Roles matching by description are included."""
        mock_cached_role.role_name = "Something Else"
        mock_cached_role.description = "unit testing description"
        result = _call_tool(mcp_server, "search_roles", query="unit testing", limit=10, ctx=None)
        assert "Something Else" in result

    def test_validation_error(self, mcp_server: MCPServer) -> None:
        result = _call_tool(mcp_server, "search_roles", query="x", limit=10, ctx=None)
        assert "at least" in result.lower()

    def test_long_description_truncated(
        self, mcp_server: MCPServer, mock_cached_role: MagicMock
    ) -> None:
        mock_cached_role.description = "A" * 200
        result = _call_tool(mcp_server, "search_roles", query="test", limit=10, ctx=None)
        assert "..." in result


# =============================================================================
# Tool Handler Tests — get_role
# =============================================================================


class TestGetRoleTool:
    """Tests for the get_role MCP tool handler."""

    def test_returns_role_details(self, mcp_server: MCPServer) -> None:
        result = _call_tool(mcp_server, "get_role", role_id_or_name="test-role-id", ctx=None)
        assert "Test Role" in result
        assert "test-role-id" in result

    def test_includes_actions(self, mcp_server: MCPServer) -> None:
        result = _call_tool(mcp_server, "get_role", role_id_or_name="test-role-id", ctx=None)
        assert "Actions" in result
        assert "Microsoft.Test/*/read" in result

    def test_role_not_found(self, mcp_server: MCPServer) -> None:
        mcp_server._cache.get_role_by_id.return_value = None  # type: ignore[union-attr]
        mcp_server._cache.get_all_roles.return_value = []  # type: ignore[union-attr]
        result = _call_tool(mcp_server, "get_role", role_id_or_name="nonexistent", ctx=None)
        assert "Role not found" in result

    def test_role_with_not_actions(
        self, mcp_server: MCPServer, mock_cached_role: MagicMock
    ) -> None:
        """NotActions are displayed when present."""
        mock_cached_role.definition.properties.permissions = [
            MagicMock(
                actions=["*/read"],
                not_actions=["Microsoft.Authorization/*/Delete"],
                data_actions=[],
                not_data_actions=[],
            )
        ]
        result = _call_tool(mcp_server, "get_role", role_id_or_name="test-role-id", ctx=None)
        assert "NotActions" in result
        assert "Microsoft.Authorization/*/Delete" in result

    def test_role_with_data_actions(
        self, mcp_server: MCPServer, mock_cached_role: MagicMock
    ) -> None:
        """DataActions are displayed when present."""
        mock_cached_role.definition.properties.permissions = [
            MagicMock(
                actions=[],
                not_actions=[],
                data_actions=[
                    "Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"
                ],
                not_data_actions=[],
            )
        ]
        result = _call_tool(mcp_server, "get_role", role_id_or_name="test-role-id", ctx=None)
        assert "DataActions" in result
        assert "blobs/read" in result


# =============================================================================
# Tool Handler Tests — get_role_permissions
# =============================================================================


class TestGetRolePermissionsTool:
    """Tests for the get_role_permissions MCP tool handler."""

    def test_returns_expanded_permissions(self, mcp_server: MCPServer) -> None:
        result = _call_tool(
            mcp_server, "get_role_permissions", role_id_or_name="test-role-id", ctx=None
        )
        assert "Expanded Permissions" in result
        assert "Control Plane" in result

    def test_role_not_found(self, mcp_server: MCPServer) -> None:
        mcp_server._cache.get_role_by_id.return_value = None  # type: ignore[union-attr]
        mcp_server._cache.get_all_roles.return_value = []  # type: ignore[union-attr]
        result = _call_tool(mcp_server, "get_role_permissions", role_id_or_name="missing", ctx=None)
        assert "Role not found" in result

    def test_no_coverage_available(self, mcp_server: MCPServer) -> None:
        mcp_server._cache.get_role_coverage.return_value = None  # type: ignore[union-attr]
        result = _call_tool(
            mcp_server, "get_role_permissions", role_id_or_name="test-role-id", ctx=None
        )
        assert "not available" in result

    def test_includes_data_actions_by_default(self, mcp_server: MCPServer) -> None:
        coverage = MagicMock()
        coverage.control = {"Microsoft.Compute/virtualMachines/read"}
        coverage.data = {"Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"}
        mcp_server._cache.get_role_coverage.return_value = coverage  # type: ignore[union-attr]
        result = _call_tool(
            mcp_server,
            "get_role_permissions",
            role_id_or_name="test-role-id",
            include_data_actions=True,
            ctx=None,
        )
        assert "Data Plane" in result

    def test_excludes_data_actions_when_false(self, mcp_server: MCPServer) -> None:
        coverage = MagicMock()
        coverage.control = {"Microsoft.Compute/virtualMachines/read"}
        coverage.data = {"Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read"}
        mcp_server._cache.get_role_coverage.return_value = coverage  # type: ignore[union-attr]
        result = _call_tool(
            mcp_server,
            "get_role_permissions",
            role_id_or_name="test-role-id",
            include_data_actions=False,
            ctx=None,
        )
        assert "Data Plane" not in result


# =============================================================================
# Tool Handler Tests — recommend_roles_tool
# =============================================================================


class TestRecommendRolesTool:
    """Tests for the recommend_roles_tool MCP tool handler."""

    def test_no_operations_provided(self, mcp_server: MCPServer) -> None:
        result = _call_tool(mcp_server, "recommend_roles_tool", ctx=None)
        assert "Provide operations" in result

    def test_too_many_operations(self, mcp_server: MCPServer) -> None:
        ops = [f"Microsoft.Test/op{i}/read" for i in range(60)]
        result = _call_tool(mcp_server, "recommend_roles_tool", operations=ops, ctx=None)
        assert "Maximum" in result

    @patch("azurerbac.mcp.server.recommend_roles")
    def test_returns_recommendations(
        self, mock_recommend: MagicMock, mcp_server: MCPServer
    ) -> None:
        match = MagicMock()
        match.role_name = "Reader"
        match.role_id = "acdd72a7"
        match.match_percentage = 100.0
        match.total_permissions = 5
        match.matched_operations_count = 2
        match.missing_operations = []
        mock_recommend.return_value = [match]
        result = _call_tool(
            mcp_server,
            "recommend_roles_tool",
            operations=["Microsoft.Test/resources/read"],
            ctx=None,
        )
        assert "Reader" in result
        assert "acdd72a7" in result
        assert "Coverage" in result

    @patch("azurerbac.mcp.server.recommend_roles")
    def test_no_matches(self, mock_recommend: MagicMock, mcp_server: MCPServer) -> None:
        mock_recommend.return_value = []
        result = _call_tool(
            mcp_server,
            "recommend_roles_tool",
            operations=["Microsoft.Nonexistent/action"],
            ctx=None,
        )
        assert "No roles found" in result

    @patch("azurerbac.mcp.server.recommend_roles")
    def test_shows_missing_operations(
        self, mock_recommend: MagicMock, mcp_server: MCPServer
    ) -> None:
        match = MagicMock()
        match.role_name = "Reader"
        match.role_id = "acdd72a7"
        match.match_percentage = 50.0
        match.total_permissions = 10
        match.matched_operations_count = 1
        match.missing_operations = ["Microsoft.Test/missing/action"]
        mock_recommend.return_value = [match]
        result = _call_tool(
            mcp_server,
            "recommend_roles_tool",
            operations=["Microsoft.Test/resources/read"],
            ctx=None,
        )
        assert "Missing" in result
        assert "Microsoft.Test/missing/action" in result

    def test_validation_error_in_operation(self, mcp_server: MCPServer) -> None:
        result = _call_tool(
            mcp_server,
            "recommend_roles_tool",
            operations=["bad\x00null"],
            ctx=None,
        )
        assert "Invalid" in result

    @patch("azurerbac.mcp.server.recommend_roles")
    def test_wildcards_control(self, mock_recommend: MagicMock, mcp_server: MCPServer) -> None:
        mock_recommend.return_value = []
        _call_tool(
            mcp_server,
            "recommend_roles_tool",
            wildcards_control=["Microsoft.Storage/*/read"],
            ctx=None,
        )
        # Verify recommend_roles was called with data_flags marking control plane
        call_kwargs = mock_recommend.call_args[1]
        assert call_kwargs["requested_ops_data_flags"]["Microsoft.Storage/*/read"] is False

    @patch("azurerbac.mcp.server.recommend_roles")
    def test_wildcards_data(self, mock_recommend: MagicMock, mcp_server: MCPServer) -> None:
        mock_recommend.return_value = []
        _call_tool(
            mcp_server,
            "recommend_roles_tool",
            wildcards_data=["Microsoft.Storage/*/read"],
            ctx=None,
        )
        call_kwargs = mock_recommend.call_args[1]
        assert call_kwargs["requested_ops_data_flags"]["Microsoft.Storage/*/read"] is True


# =============================================================================
# Tool Handler Tests — ai_recommend
# =============================================================================


class TestAiRecommendTool:
    """Tests for the ai_recommend MCP tool handler."""

    @patch("azurerbac.mcp.server.ai_recommend_roles")
    def test_returns_recommendations(self, mock_ai: MagicMock, mcp_server: MCPServer) -> None:
        mock_ai.return_value = (
            [
                {
                    "role_name": "Storage Blob Reader",
                    "description": "Read access to blobs",
                    "score": 0.95,
                    "matched_keywords": ["storage", "blob"],
                }
            ],
            "colbert",
        )
        result = _call_tool(
            mcp_server,
            "ai_recommend",
            query="I need to read blobs in Azure Storage",
            ctx=None,
        )
        assert "Storage Blob Reader" in result
        assert "colbert" in result
        assert "0.95" in result

    @patch("azurerbac.mcp.server.ai_recommend_roles")
    def test_no_recommendations(self, mock_ai: MagicMock, mcp_server: MCPServer) -> None:
        mock_ai.return_value = ([], "colbert")
        result = _call_tool(
            mcp_server, "ai_recommend", query="something very obscure query", ctx=None
        )
        assert "No roles found" in result

    def test_validation_error_short_query(self, mcp_server: MCPServer) -> None:
        result = _call_tool(mcp_server, "ai_recommend", query="hi", ctx=None)
        assert "at least" in result.lower()

    @patch("azurerbac.mcp.server.ai_recommend_roles")
    def test_ai_exception_handled(self, mock_ai: MagicMock, mcp_server: MCPServer) -> None:
        """Internal exception messages must not be echoed back to the MCP client.

        Leaking ``str(exception)`` over the wire can disclose internal paths,
        DB driver errors, or Ollama endpoint URLs. The handler should return
        a fixed, user-safe message and rely on server-side logging for detail.
        """
        mock_ai.side_effect = RuntimeError("Model not loaded")
        result = _call_tool(mcp_server, "ai_recommend", query="read blob storage data", ctx=None)
        assert "failed" in result.lower()
        # The exception message must NOT be present in the response
        assert "Model not loaded" not in result
        assert "RuntimeError" not in result


# =============================================================================
# Rate Limiting Integration Tests
# =============================================================================


class TestToolRateLimiting:
    """Tests for rate limiting across tool handlers."""

    def test_max_sessions_rejection(self, mcp_server: MCPServer) -> None:
        """When max sessions are reached, new sessions are rejected."""
        import time

        from azurerbac.mcp.constants import RATE_LIMIT_MAX_SESSIONS

        # Fill up all session slots
        now = time.monotonic()
        for i in range(RATE_LIMIT_MAX_SESSIONS):
            mcp_server._session_last_activity[f"session-{i}"] = now

        result = mcp_server._check_rate_limit("search_roles", "new-session")
        assert result is not None
        assert "capacity" in result.lower()

    def test_global_rate_limit_blocks(self, mcp_server: MCPServer) -> None:
        """Global rate limit blocks after capacity is exhausted."""
        # Exhaust global bucket
        for _ in range(100):
            res = mcp_server._global_limiter.is_allowed("global")
            if not res.allowed:
                break

        result = mcp_server._check_rate_limit("search_operations", "session-1")
        assert result is not None
        assert "load" in result.lower() or "wait" in result.lower()
