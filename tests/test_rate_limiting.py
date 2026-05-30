"""Tests for rate limiting utilities."""

from unittest.mock import MagicMock

import pytest

from azurerbac.settings import Settings
from azurerbac.web.limiter import get_real_client_ip


@pytest.fixture
def _deployed(monkeypatch):
    """Force ``Settings.get().is_deployed`` to True for proxy-trust tests."""
    settings = MagicMock()
    settings.is_deployed = True
    monkeypatch.setattr(Settings, "get", classmethod(lambda cls: settings))


@pytest.fixture
def _local(monkeypatch):
    """Force ``Settings.get().is_deployed`` to False (local/self-hosted)."""
    settings = MagicMock()
    settings.is_deployed = False
    monkeypatch.setattr(Settings, "get", classmethod(lambda cls: settings))


class TestGetRealClientIP:
    """Tests for get_real_client_ip function."""

    @pytest.mark.usefixtures("_deployed")
    @pytest.mark.parametrize(
        ("headers", "client_host", "expected"),
        [
            pytest.param(
                {"CF-Connecting-IP": "1.2.3.4"},
                "10.0.0.1",
                "1.2.3.4",
                id="cloudflare_ip_header",
            ),
            pytest.param(
                {"X-Forwarded-For": "5.6.7.8, 10.0.0.1, 10.0.0.2"},
                "10.0.0.1",
                "5.6.7.8",
                id="x_forwarded_for_chain",
            ),
            pytest.param(
                {"X-Forwarded-For": "9.10.11.12"},
                "10.0.0.1",
                "9.10.11.12",
                id="x_forwarded_for_single",
            ),
            pytest.param(
                {"X-Forwarded-For": "  13.14.15.16  , 10.0.0.1"},
                "10.0.0.1",
                "13.14.15.16",
                id="x_forwarded_for_strips_whitespace",
            ),
            pytest.param(
                {"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"},
                "10.0.0.1",
                "1.1.1.1",
                id="cloudflare_priority_over_xff",
            ),
            pytest.param(
                {},
                "192.168.1.100",
                "192.168.1.100",
                id="fallback_to_client_host",
            ),
            pytest.param(
                {"CF-Connecting-IP": "2001:db8::1"},
                "10.0.0.1",
                "2001:db8::1",
                id="ipv6_address",
            ),
            pytest.param(
                {"CF-Connecting-IP": "", "X-Forwarded-For": "3.3.3.3"},
                "10.0.0.1",
                "3.3.3.3",
                id="empty_cf_header_uses_xff",
            ),
        ],
    )
    def test_get_real_client_ip(self, headers, client_host, expected):
        """Test IP extraction from various header combinations."""
        request = MagicMock()
        request.headers = headers
        request.client = MagicMock(host=client_host)
        assert get_real_client_ip(request) == expected

    def test_no_client_returns_unknown(self):
        """Returns 'unknown' when no client and no headers."""
        request = MagicMock()
        request.headers = {}
        request.client = None

        assert get_real_client_ip(request) == "unknown"

    @pytest.mark.usefixtures("_local")
    @pytest.mark.parametrize(
        "headers",
        [
            {"CF-Connecting-IP": "1.2.3.4"},
            {"X-Forwarded-For": "5.6.7.8, 10.0.0.1"},
            {"CF-Connecting-IP": "1.1.1.1", "X-Forwarded-For": "2.2.2.2"},
        ],
    )
    def test_local_ignores_spoofable_headers(self, headers):
        """Outside deployed envs, proxy headers are untrusted (anti-spoofing)."""
        request = MagicMock()
        request.headers = headers
        request.client = MagicMock(host="192.168.1.100")

        assert get_real_client_ip(request) == "192.168.1.100"
