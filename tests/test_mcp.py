"""Tests for MCP server implementation."""

from unittest.mock import MagicMock

import pytest

from azurerbac.mcp.server import MCPServer
from azurerbac.mcp.utils import InputValidator, ToolTimer, ValidationError

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
        assert timer._success is True  # pyright: ignore[reportPrivateUsage]

    def test_timer_records_failure_on_exception(self) -> None:
        timer: ToolTimer | None = None
        try:
            with ToolTimer("test_tool") as timer:
                raise ValueError("test error")
        except ValueError:
            pass
        assert timer is not None
        assert timer._success is False  # pyright: ignore[reportPrivateUsage]

    def test_timer_fail_method(self) -> None:
        with ToolTimer("test_tool") as timer:
            timer.fail()
        assert timer._success is False  # pyright: ignore[reportPrivateUsage]

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

    def test_server_initialization(self, mcp_server: MCPServer) -> None:
        assert mcp_server._mcp is not None  # pyright: ignore[reportPrivateUsage]
        assert mcp_server._mcp.name == "azure-rbac-catalog"  # pyright: ignore[reportPrivateUsage]

    def test_streamable_http_app_returns_starlette_app(self, mcp_server: MCPServer) -> None:
        from starlette.applications import Starlette

        assert isinstance(mcp_server.streamable_http_app(), Starlette)

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
        tools = mcp_server._mcp._tool_manager._tools  # pyright: ignore[reportPrivateUsage]
        assert tool_name in tools


class TestMCPServerClientKey:
    """Tests for _get_client_key method."""

    def test_returns_default_when_context_is_none(self) -> None:
        result = MCPServer._get_client_key(None)  # pyright: ignore[reportPrivateUsage]
        assert result == "default"

    def test_returns_session_based_key_with_context(self) -> None:
        mock_ctx = MagicMock()
        mock_ctx.session = object()  # Unique object
        mock_ctx.request_id = "123"
        result = MCPServer._get_client_key(mock_ctx)  # pyright: ignore[reportPrivateUsage]
        assert result.startswith("session_")
        assert str(id(mock_ctx.session)) in result


class TestMCPServerFindRole:
    """Tests for _find_role method."""

    def test_find_role_by_id(self, mcp_server: MCPServer, mock_cached_role: MagicMock) -> None:
        result = mcp_server._find_role("test-role-id")  # pyright: ignore[reportPrivateUsage]
        assert result == mock_cached_role

    def test_find_role_by_name_case_insensitive(
        self, mcp_server: MCPServer, mock_role: MagicMock, mock_cached_role: MagicMock
    ) -> None:
        # Configure mock to return None for ID lookup, forcing name search
        mcp_server._cache.get_role_by_id.side_effect = (  # type: ignore[union-attr]
            lambda x: mock_cached_role if x == "test-role-id" else None
        )
        result = mcp_server._find_role("TEST ROLE")  # pyright: ignore[reportPrivateUsage]
        assert result == mock_cached_role

    def test_find_role_returns_none_when_not_found(self, mcp_server: MCPServer) -> None:
        mcp_server._cache.get_role_by_id.return_value = None  # type: ignore[union-attr]
        mcp_server._cache.get_all_roles.return_value = []  # type: ignore[union-attr]
        result = mcp_server._find_role("nonexistent")  # pyright: ignore[reportPrivateUsage]
        assert result is None


class TestMCPServerFormatActionList:
    """Tests for _format_action_list static method."""

    def test_format_empty_list(self) -> None:
        result = MCPServer._format_action_list([])  # pyright: ignore[reportPrivateUsage]
        assert result == []

    def test_format_within_limit(self) -> None:
        actions = ["action1", "action2", "action3"]
        result = MCPServer._format_action_list(actions, limit=10)  # pyright: ignore[reportPrivateUsage]
        assert len(result) == 3
        assert all(line.startswith("  • ") for line in result)

    def test_format_exceeds_limit(self) -> None:
        actions = [f"action{i}" for i in range(100)]
        result = MCPServer._format_action_list(actions, limit=50)  # pyright: ignore[reportPrivateUsage]
        assert len(result) == 51  # 50 items + "and X more"
        assert "... and 50 more" in result[-1]


class TestMCPServerRateLimiting:
    """Tests for rate limiting logic."""

    def test_check_rate_limit_allows_first_request(self, mcp_server: MCPServer) -> None:
        result = mcp_server._check_rate_limit("test_tool", "session_1")  # pyright: ignore[reportPrivateUsage]
        assert result is None  # No error = allowed

    def test_session_cleanup_removes_expired(self, mcp_server: MCPServer) -> None:
        import time

        # pyright: ignore[reportPrivateUsage]
        mcp_server._session_last_activity["old_session"] = time.monotonic() - 7200  # type: ignore[index]
        mcp_server._cleanup_expired_sessions()  # pyright: ignore[reportPrivateUsage]
        assert "old_session" not in mcp_server._session_last_activity  # pyright: ignore[reportPrivateUsage]


class TestCreateMCPServer:
    """Tests for create_mcp_server factory function."""

    def test_returns_starlette_app(self) -> None:
        from starlette.applications import Starlette

        from azurerbac.mcp.server import create_mcp_server

        mock_cache = MagicMock()
        mock_cache.get_all_roles.return_value = []
        mock_cache.get_all_operations.return_value = []

        assert isinstance(create_mcp_server(mock_cache), Starlette)


class TestValidationError:
    """Tests for ValidationError exception."""

    def test_validation_error_is_exception(self) -> None:
        assert issubclass(ValidationError, Exception)

    def test_validation_error_stores_message(self) -> None:
        err = ValidationError("test error message")
        assert str(err) == "test error message"


class TestTransportSecurity:
    """Tests for MCP transport security configuration."""

    def test_adds_production_domain_to_allowed_hosts(self, mock_cache: MagicMock) -> None:
        """Verify production domain is added to allowed_hosts when transport_security exists."""
        from azurerbac.web.constants import NEW_DOMAIN

        server = MCPServer(mock_cache)
        transport_security = server._mcp.settings.transport_security  # pyright: ignore[reportPrivateUsage]

        assert transport_security is not None
        assert NEW_DOMAIN in transport_security.allowed_hosts

    def test_adds_production_origin_to_allowed_origins(self, mock_cache: MagicMock) -> None:
        """Verify production origin is added to allowed_origins when transport_security exists."""
        from azurerbac.web.constants import SITE_URL

        server = MCPServer(mock_cache)
        transport_security = server._mcp.settings.transport_security  # pyright: ignore[reportPrivateUsage]

        assert transport_security is not None
        assert SITE_URL in transport_security.allowed_origins

    def test_no_duplicate_entries_on_multiple_instantiation(self, mock_cache: MagicMock) -> None:
        """Verify multiple MCPServer instances don't create duplicate entries."""
        from azurerbac.web.constants import NEW_DOMAIN, SITE_URL

        # Create first server
        MCPServer(mock_cache)
        # Create second server (would share settings if they're class-level)
        server2 = MCPServer(mock_cache)

        # Each server should have exactly one entry for production domain
        # (checking server2 since it's the latest instantiation)
        ts = server2._mcp.settings.transport_security  # pyright: ignore[reportPrivateUsage]
        assert ts is not None
        assert ts.allowed_hosts.count(NEW_DOMAIN) == 1
        assert ts.allowed_origins.count(SITE_URL) == 1


class TestValidateInput:
    """Tests for InputValidator.validate method."""

    def test_returns_sanitized_value_on_success(self) -> None:
        result = InputValidator.validate("  valid query  ", 100, 1, "Query")
        assert result == "valid query"  # Trimmed

    def test_raises_validation_error_on_too_short(self) -> None:
        with pytest.raises(ValidationError, match="at least 5 characters"):
            InputValidator.validate("ab", 100, 5, "Query")

    def test_raises_validation_error_on_too_long(self) -> None:
        with pytest.raises(ValidationError, match="Maximum 5 characters"):
            InputValidator.validate("too long string", 5, 1, "Query")

    def test_raises_validation_error_on_suspicious_input(self) -> None:
        with pytest.raises(ValidationError, match="Invalid query format"):
            InputValidator.validate("<script>alert(1)</script>", 100, 1, "Query")
