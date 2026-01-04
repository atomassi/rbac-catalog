"""Tests for rate limiting utilities."""

from unittest.mock import MagicMock

from azurerbac.web.limiter import get_real_client_ip


class TestGetRealClientIP:
    """Tests for get_real_client_ip function."""

    def test_cloudflare_ip_header(self):
        """CF-Connecting-IP header takes priority."""
        request = MagicMock()
        request.headers = {"CF-Connecting-IP": "1.2.3.4"}
        request.client = MagicMock(host="10.0.0.1")

        result = get_real_client_ip(request)

        assert result == "1.2.3.4"

    def test_x_forwarded_for_header(self):
        """X-Forwarded-For is used when CF header missing."""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "5.6.7.8, 10.0.0.1, 10.0.0.2"}
        request.client = MagicMock(host="10.0.0.1")

        result = get_real_client_ip(request)

        # First IP in the chain is the original client
        assert result == "5.6.7.8"

    def test_x_forwarded_for_single_ip(self):
        """X-Forwarded-For with single IP."""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "9.10.11.12"}
        request.client = MagicMock(host="10.0.0.1")

        result = get_real_client_ip(request)

        assert result == "9.10.11.12"

    def test_x_forwarded_for_strips_whitespace(self):
        """X-Forwarded-For strips whitespace from IPs."""
        request = MagicMock()
        request.headers = {"X-Forwarded-For": "  13.14.15.16  , 10.0.0.1"}
        request.client = MagicMock(host="10.0.0.1")

        result = get_real_client_ip(request)

        assert result == "13.14.15.16"

    def test_cloudflare_takes_priority_over_xff(self):
        """CF-Connecting-IP is preferred over X-Forwarded-For."""
        request = MagicMock()
        request.headers = {
            "CF-Connecting-IP": "1.1.1.1",
            "X-Forwarded-For": "2.2.2.2",
        }
        request.client = MagicMock(host="10.0.0.1")

        result = get_real_client_ip(request)

        assert result == "1.1.1.1"

    def test_fallback_to_client_host(self):
        """Falls back to direct client IP when no headers."""
        request = MagicMock()
        request.headers = {}
        request.client = MagicMock(host="192.168.1.100")

        result = get_real_client_ip(request)

        assert result == "192.168.1.100"

    def test_no_client_returns_unknown(self):
        """Returns 'unknown' when no client and no headers."""
        request = MagicMock()
        request.headers = {}
        request.client = None

        result = get_real_client_ip(request)

        assert result == "unknown"

    def test_ipv6_address(self):
        """Handles IPv6 addresses correctly."""
        request = MagicMock()
        request.headers = {"CF-Connecting-IP": "2001:db8::1"}
        request.client = MagicMock(host="10.0.0.1")

        result = get_real_client_ip(request)

        assert result == "2001:db8::1"

    def test_empty_cf_header_uses_xff(self):
        """Empty CF header falls through to X-Forwarded-For."""
        request = MagicMock()
        request.headers = {
            "CF-Connecting-IP": "",
            "X-Forwarded-For": "3.3.3.3",
        }
        request.client = MagicMock(host="10.0.0.1")

        result = get_real_client_ip(request)

        # Empty string is falsy, so falls through to XFF
        assert result == "3.3.3.3"
