#!/usr/bin/env python3
"""Smoke tests for Azure RBAC Catalog.

This script runs after deployment to the staging slot and must pass
before promoting to production via slot swap.

Tests are organized into categories:
1. Page Availability - All pages load with 200 status
2. Content Validation - Pages contain expected elements
3. API Functionality - APIs return valid JSON responses
4. Search & Filters - Query parameters work correctly
5. AI Recommender - All recommender modes function
6. Security Headers - CSP, X-Frame-Options present
7. Edge Cases - 404s, empty queries, invalid inputs
8. Performance - Response times are acceptable

Usage:
    python scripts/smoke_tests.py
    python scripts/smoke_tests.py --url https://custom-url.azurewebsites.net
    python scripts/smoke_tests.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Final

import httpx

# Default timeout for requests
TIMEOUT: Final = 30.0

# Maximum acceptable response time for critical pages (seconds)
MAX_RESPONSE_TIME: Final = 5.0

# ─── Test Configurations ──────────────────────────────────────────────────────

# Pages that should return 200 and contain specific content
PAGES_WITH_CONTENT: Final = [
    ("Homepage", "/", ["Total Built-in Roles", "Last Scan"]),
    ("Roles list", "/roles", ["Role Name", "Effective Actions"]),
    ("Recent changes", "/recent", ["changes", "per page"]),
    ("Operations list", "/operations", ["Azure Operations", "Granted By"]),
    ("Recommend page", "/recommend", ["Find Least-Privilege"]),
    ("About page", "/about", ["Azure RBAC"]),
    ("Analytics page", "/analytics", ["Analytics"]),
    (
        "Compare page",
        "/compare",
        ["Choose Two Roles to Compare", "How Role Comparison Works"],
    ),
    (
        "Role detail (Reader)",
        "/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7",
        ["Reader", "Role Information"],
    ),
    (
        "Operation detail",
        "/operations/Microsoft.Storage/storageAccounts/read",
        ["storageAccounts/read"],
    ),
]

# Static assets that should return 200
STATIC_ASSETS: Final = [
    ("Favicon ICO", "/favicon.ico"),
    ("Favicon SVG", "/favicon.svg"),
    ("Robots.txt", "/robots.txt"),
    ("Sitemap.xml", "/sitemap.xml"),
    ("Tailwind CSS", "/static/css/tailwind.min.css"),
    ("Dark mode JS", "/static/js/darkmode.js"),
]

# API endpoints with expected JSON structure
API_ENDPOINTS_JSON: Final = [
    ("API: Search operations", "/api/operations/search?q=read", ["operations"]),
    ("API: Search with wildcard", "/api/operations/search?q=Microsoft.Storage/*", ["operations"]),
    ("API: Search with limit", "/api/operations/search?q=blob&limit=5", ["operations"]),
    ("API: Count matches", "/api/operations/count-matches?pattern=Microsoft.Storage/*", ["count"]),
    (
        "API: Count data actions",
        "/api/operations/count-matches?pattern=Microsoft.Storage/*&is_data_action=true",
        ["count"],
    ),
]

# Search and filter parameter tests
SEARCH_FILTER_TESTS: Final = [
    ("Roles search", "/roles?q=storage", ["storage"]),
    ("Roles exact match", "/roles?q=Reader&exact_match=1", ["Reader"]),
    ("Roles sort by actions", "/roles?sort=actions&order=desc", ["Role Name"]),
    ("Roles limit 50", "/roles?limit=50", ["Role Name"]),
    ("Roles page 2", "/roles?page=2&limit=25", ["Role Name"]),
    ("Operations search", "/operations?q=storage", ["storage"]),
    ("Operations data actions", "/operations?is_data_action=1", ["Azure Operations"]),
    ("Recent days=7", "/recent?days=7", ["changes"]),
    ("Recent days=90", "/recent?days=90", ["changes"]),
    ("Recent limit=100", "/recent?limit=100", ["changes"]),
]

# Edge cases - expected to return specific status codes
EDGE_CASES: Final = [
    ("Non-existent page", "/this-page-does-not-exist", (403, 404)),
    ("Invalid role ID", "/roles/00000000-0000-0000-0000-000000000000", (404,)),
    ("Invalid operation", "/operations/NonExistent.Provider/nonExistentAction", (404,)),
    ("API: Empty search query", "/api/operations/search?q=", (200,)),
    ("API: Short query", "/api/operations/search?q=a", (200,)),
    ("API: Invalid count pattern", "/api/operations/count-matches?pattern=", (200,)),
]

# Input validation - expected to return 400 for invalid parameters
INPUT_VALIDATION_TESTS: Final = [
    ("Roles: negative page", "/roles?page=-1", (400,)),
    ("Roles: huge page", "/roles?page=999999999", (400,)),
    ("Roles: zero limit", "/roles?limit=0", (400,)),
    ("Roles: excessive limit", "/roles?limit=99999", (400,)),
    ("Operations: negative page", "/operations?page=-1", (400,)),
    ("Operations: excessive limit", "/operations?limit=99999", (400,)),
    ("Recent: zero days", "/recent?days=0", (400,)),
    ("Recent: excessive days", "/recent?days=1000", (400,)),
    ("Recent: negative page", "/recent?page=-1", (400,)),
    ("API search: query too long", "/api/operations/search?q=" + "A" * 200, (400,)),
]

# AI Recommender POST endpoint tests (different modes)
AI_RECOMMENDER_TESTS: Final = [
    ("AI: TFIDF mode", {"query": "read storage blobs", "top_k": 3, "recommender_mode": "tfidf"}),
    (
        "AI: Semantic mode",
        {"query": "manage virtual machines", "top_k": 3, "recommender_mode": "semantic"},
    ),
    ("AI: ColBERT mode", {"query": "backup databases", "top_k": 3, "recommender_mode": "colbert"}),
    (
        "AI: CrossEncoder mode",
        {"query": "manage kubernetes", "top_k": 3, "recommender_mode": "crossencoder"},
    ),
    ("AI: RAG mode", {"query": "deploy applications", "top_k": 3, "recommender_mode": "rag"}),
    ("AI: Hybrid mode", {"query": "monitor resources", "top_k": 3, "recommender_mode": "hybrid"}),
    ("AI: LLM mode", {"query": "manage clusters", "top_k": 3, "recommender_mode": "llm"}),
]

# Role recommend API tests
ROLE_RECOMMEND_TESTS: Final = [
    (
        "Recommend: Storage read",
        [{"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False}],
    ),
    (
        "Recommend: Authorization write",
        [{"name": "Microsoft.Authorization/roleAssignments/write", "is_data_action": False}],
    ),
    (
        "Recommend: Multiple operations",
        [
            {"name": "Microsoft.Storage/storageAccounts/read", "is_data_action": False},
            {"name": "Microsoft.Resources/subscriptions/read", "is_data_action": False},
        ],
    ),
]

# Security headers that should be present (set by our app)
# Note: Strict-Transport-Security (HSTS) is added by Cloudflare, not tested here
REQUIRED_SECURITY_HEADERS: Final = [
    ("Content-Security-Policy", "default-src"),
    ("X-Frame-Options", "DENY"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Permissions-Policy", "accelerometer=()"),
]

# RSS/Atom feed endpoints - (name, path, expected_content_type, xml_root_element)
FEED_ENDPOINTS: Final = [
    ("Feed: Atom changelog", "/feeds/changelog.atom", "application/atom+xml", "feed"),
    ("Feed: RSS changelog", "/feeds/changelog.rss", "application/rss+xml", "rss"),
]


@dataclass(slots=True)
class TestResult:
    """Result of a single smoke test."""

    name: str
    passed: bool
    status_code: int | None = None
    expected: int = 200
    message: str = ""
    response_time: float = 0.0
    details: dict = field(default_factory=dict)


async def test_page_with_content(
    client: httpx.AsyncClient,
    name: str,
    path: str,
    expected_content: list[str],
) -> TestResult:
    """Test a page returns 200 and contains expected content."""
    start = time.monotonic()
    try:
        response = await client.get(path)
        elapsed = time.monotonic() - start

        if response.status_code != 200:
            return TestResult(
                name=name,
                passed=False,
                status_code=response.status_code,
                expected=200,
                response_time=elapsed,
            )

        text = response.text.lower()
        missing = [c for c in expected_content if c.lower() not in text]

        if missing:
            # Include a snippet of the response for debugging
            body_preview = response.text[:300].replace("\n", " ")[:150]
            return TestResult(
                name=name,
                passed=False,
                status_code=200,
                message=f"Missing content: {missing}",
                response_time=elapsed,
                details={"body_preview": body_preview},
            )

        return TestResult(
            name=name,
            passed=True,
            status_code=200,
            response_time=elapsed,
            details={"content_checks": len(expected_content)},
        )
    except httpx.RequestError as e:
        return TestResult(name=name, passed=False, message=str(e))


async def test_api_json(
    client: httpx.AsyncClient,
    name: str,
    path: str,
    expected_keys: list[str],
) -> TestResult:
    """Test an API endpoint returns valid JSON with expected keys."""
    start = time.monotonic()
    try:
        response = await client.get(path)
        elapsed = time.monotonic() - start

        if response.status_code != 200:
            return TestResult(
                name=name,
                passed=False,
                status_code=response.status_code,
                expected=200,
                response_time=elapsed,
            )

        try:
            data = response.json()
        except Exception:
            return TestResult(
                name=name,
                passed=False,
                status_code=200,
                message="Invalid JSON response",
                response_time=elapsed,
            )

        missing = [k for k in expected_keys if k not in data]
        if missing:
            return TestResult(
                name=name,
                passed=False,
                status_code=200,
                message=f"Missing JSON keys: {missing}",
                response_time=elapsed,
            )

        return TestResult(
            name=name,
            passed=True,
            status_code=200,
            response_time=elapsed,
            details={"keys": list(data.keys())},
        )
    except httpx.RequestError as e:
        return TestResult(name=name, passed=False, message=str(e))


async def test_static_asset(
    client: httpx.AsyncClient,
    name: str,
    path: str,
) -> TestResult:
    """Test a static asset returns 200."""
    start = time.monotonic()
    try:
        response = await client.get(path)
        elapsed = time.monotonic() - start

        passed = response.status_code == 200
        return TestResult(
            name=name,
            passed=passed,
            status_code=response.status_code,
            expected=200,
            response_time=elapsed,
            details={"content_length": len(response.content)},
        )
    except httpx.RequestError as e:
        return TestResult(name=name, passed=False, message=str(e))


async def test_edge_case(
    client: httpx.AsyncClient,
    name: str,
    path: str,
    expected_codes: tuple[int, ...],
) -> TestResult:
    """Test edge cases return expected status codes."""
    start = time.monotonic()
    try:
        response = await client.get(path)
        elapsed = time.monotonic() - start

        passed = response.status_code in expected_codes
        return TestResult(
            name=name,
            passed=passed,
            status_code=response.status_code,
            expected=expected_codes[0],
            response_time=elapsed,
            message="" if passed else f"Expected one of {expected_codes}",
        )
    except httpx.RequestError as e:
        return TestResult(name=name, passed=False, message=str(e))


async def test_feed_endpoint(
    client: httpx.AsyncClient,
    name: str,
    path: str,
    expected_content_type: str,
    expected_root_element: str,
) -> TestResult:
    """Test an RSS/Atom feed endpoint returns valid XML with correct content type."""
    start = time.monotonic()
    try:
        response = await client.get(path)
        elapsed = time.monotonic() - start

        if response.status_code != 200:
            return TestResult(
                name=name,
                passed=False,
                status_code=response.status_code,
                expected=200,
                response_time=elapsed,
            )

        # Check content type
        content_type = response.headers.get("content-type", "")
        if expected_content_type not in content_type:
            return TestResult(
                name=name,
                passed=False,
                status_code=200,
                response_time=elapsed,
                message=f"Wrong content-type: {content_type}",
                details={"expected": expected_content_type, "actual": content_type},
            )

        # Check for valid XML with expected root element
        content = response.text
        has_xml_decl = content.strip().startswith("<?xml")
        has_root_element = f"<{expected_root_element}" in content

        if not has_xml_decl or not has_root_element:
            return TestResult(
                name=name,
                passed=False,
                status_code=200,
                response_time=elapsed,
                message="Invalid XML structure",
                details={"has_xml_decl": has_xml_decl, "has_root": has_root_element},
            )

        # Check cache headers
        cache_control = response.headers.get("cache-control", "")
        has_cache = "max-age" in cache_control

        return TestResult(
            name=name,
            passed=True,
            status_code=200,
            response_time=elapsed,
            details={"has_cache_header": has_cache, "content_length": len(content)},
        )
    except httpx.RequestError as e:
        return TestResult(name=name, passed=False, message=str(e))


def print_result(result: TestResult, verbose: bool = False) -> None:
    """Print a test result with color."""
    if result.passed:
        status = "\033[92m✅ PASS\033[0m"
        detail = f"({result.response_time:.2f}s)" if result.response_time else ""
        if verbose and result.details:
            detail += f" {result.details}"
    else:
        status = "\033[91m❌ FAIL\033[0m"
        details_parts = []
        if result.message:
            details_parts.append(result.message)
        if result.status_code and result.status_code != result.expected:
            details_parts.append(f"HTTP {result.status_code}")
        if verbose and result.details:
            details_parts.append(str(result.details))
        detail = f"({', '.join(details_parts)})" if details_parts else ""

    print(f"{status} - {result.name} {detail}")


async def test_ai_recommender(
    client: httpx.AsyncClient,
    name: str,
    request_body: dict,
) -> TestResult:
    """Test AI recommender endpoint with a specific mode."""
    start = time.monotonic()
    try:
        response = await client.post("/api/ai-recommend", json=request_body)
        elapsed = time.monotonic() - start

        if response.status_code == 429:
            return TestResult(
                name=name,
                passed=True,
                status_code=429,
                message="Rate limited (expected)",
                response_time=elapsed,
            )

        if response.status_code != 200:
            # Try to extract error message from response body
            error_detail = ""
            try:
                error_data = response.json()
                error_detail = error_data.get("detail", error_data.get("error", ""))
            except Exception:
                error_detail = response.text[:200] if response.text else ""
            return TestResult(
                name=name,
                passed=False,
                status_code=response.status_code,
                expected=200,
                response_time=elapsed,
                message=error_detail,
                details={"request": request_body},
            )

        try:
            data = response.json()

            # Check for error response (engine unavailable, etc.)
            if data.get("error"):
                return TestResult(
                    name=name,
                    passed=False,
                    status_code=200,
                    message=data["error"],
                    response_time=elapsed,
                    details={"request": request_body},
                )

            rec_count = len(data.get("recommendations", []))
            engine_info = data.get("engine") or {}
            engine_mode = engine_info.get("mode", "unknown")
            engine_time = engine_info.get("processing_time_ms", 0)
            return TestResult(
                name=name,
                passed=True,
                status_code=200,
                response_time=elapsed,
                details={
                    "recommendations": rec_count,
                    "engine": engine_mode,
                    "engine_ms": engine_time,
                },
            )
        except Exception as e:
            # Include response body preview for debugging
            body_preview = response.text[:500] if response.text else "<empty>"
            return TestResult(
                name=name,
                passed=False,
                status_code=200,
                message=f"Invalid JSON: {e}",
                response_time=elapsed,
                details={"body_preview": body_preview},
            )
    except httpx.RequestError as e:
        return TestResult(name=name, passed=False, message=f"Request failed: {e}")


async def test_role_recommend(
    client: httpx.AsyncClient,
    name: str,
    operations: list[dict],
) -> TestResult:
    """Test role recommend endpoint."""
    start = time.monotonic()
    try:
        response = await client.post("/api/recommend-roles", json={"operations": operations})
        elapsed = time.monotonic() - start

        if response.status_code != 200:
            return TestResult(
                name=name,
                passed=False,
                status_code=response.status_code,
                expected=200,
                response_time=elapsed,
            )

        try:
            data = response.json()
            # Accept either 'recommended_roles' or 'roles' key
            roles_key = "recommended_roles" if "recommended_roles" in data else "roles"
            if roles_key not in data:
                return TestResult(
                    name=name,
                    passed=False,
                    status_code=200,
                    message="Missing 'recommended_roles' or 'roles' key",
                    response_time=elapsed,
                )
            return TestResult(
                name=name,
                passed=True,
                status_code=200,
                response_time=elapsed,
                details={"role_count": len(data[roles_key])},
            )
        except Exception:
            return TestResult(
                name=name,
                passed=False,
                status_code=200,
                message="Invalid JSON response",
                response_time=elapsed,
            )
    except httpx.RequestError as e:
        return TestResult(name=name, passed=False, message=str(e))


async def test_security_headers(
    client: httpx.AsyncClient,
) -> list[TestResult]:
    """Test security headers are present on responses."""
    results = []
    try:
        response = await client.get("/")
        for header_name, expected_value in REQUIRED_SECURITY_HEADERS:
            header_value = response.headers.get(header_name, "")
            passed = expected_value.lower() in header_value.lower()
            results.append(
                TestResult(
                    name=f"Security: {header_name}",
                    passed=passed,
                    message="" if passed else f"Missing or invalid {header_name}",
                )
            )
    except httpx.RequestError as e:
        for header_name, _ in REQUIRED_SECURITY_HEADERS:
            results.append(
                TestResult(
                    name=f"Security: {header_name}",
                    passed=False,
                    message=str(e),
                )
            )
    return results


async def test_method_restriction(
    client: httpx.AsyncClient,
    path: str,
    method: str,
) -> TestResult:
    """Test that invalid HTTP methods return 405."""
    try:
        if method == "DELETE":
            response = await client.delete(path)
        elif method == "PUT":
            response = await client.put(path, content=b"")
        else:
            return TestResult(
                name=f"Method {method} {path}",
                passed=False,
                message=f"Unknown method: {method}",
            )

        # Accept 405 Method Not Allowed, 400 Bad Request, or 403 Forbidden (Cloudflare WAF)
        passed = response.status_code in (400, 403, 405)
        return TestResult(
            name=f"Method: {method} {path}",
            passed=passed,
            status_code=response.status_code,
            expected=405,
            message="" if passed else f"Expected 405, got {response.status_code}",
        )
    except httpx.RequestError as e:
        return TestResult(
            name=f"Method: {method} {path}",
            passed=False,
            message=str(e),
        )


async def _run_page_tests(
    client: httpx.AsyncClient, verbose: bool
) -> tuple[list[TestResult], list[tuple[str, float]]]:
    """Run page availability tests with content validation."""
    print("\n📄 Testing Pages with Content Validation...")
    print("-" * 40)
    results: list[TestResult] = []
    slow_responses: list[tuple[str, float]] = []
    for name, path, expected_content in PAGES_WITH_CONTENT:
        result = await test_page_with_content(client, name, path, expected_content)
        print_result(result, verbose)
        results.append(result)
        if result.response_time > MAX_RESPONSE_TIME:
            slow_responses.append((name, result.response_time))
    return results, slow_responses


async def _run_static_tests(client: httpx.AsyncClient, verbose: bool) -> list[TestResult]:
    """Run static asset tests."""
    print("\n📦 Testing Static Assets...")
    print("-" * 40)
    results: list[TestResult] = []
    for name, path in STATIC_ASSETS:
        result = await test_static_asset(client, name, path)
        print_result(result, verbose)
        results.append(result)
    return results


async def _run_api_tests(client: httpx.AsyncClient, verbose: bool) -> list[TestResult]:
    """Run API and search/filter tests."""
    results: list[TestResult] = []

    print("\n🔌 Testing API Endpoints...")
    print("-" * 40)
    for name, path, expected_keys in API_ENDPOINTS_JSON:
        result = await test_api_json(client, name, path, expected_keys)
        print_result(result, verbose)
        results.append(result)

    print("\n🔍 Testing Search & Filters...")
    print("-" * 40)
    for name, path, expected_content in SEARCH_FILTER_TESTS:
        result = await test_page_with_content(client, name, path, expected_content)
        print_result(result, verbose)
        results.append(result)

    print("\n📊 Testing Role Recommend API...")
    print("-" * 40)
    for name, operations in ROLE_RECOMMEND_TESTS:
        result = await test_role_recommend(client, name, operations)
        print_result(result, verbose)
        results.append(result)

    return results


async def _run_feed_tests(client: httpx.AsyncClient, verbose: bool) -> list[TestResult]:
    """Run RSS/Atom feed endpoint tests."""
    print("\n📡 Testing RSS/Atom Feeds...")
    print("-" * 40)
    results: list[TestResult] = []
    for name, path, content_type, root_element in FEED_ENDPOINTS:
        result = await test_feed_endpoint(client, name, path, content_type, root_element)
        print_result(result, verbose)
        results.append(result)
    return results


async def _run_ai_tests(client: httpx.AsyncClient, verbose: bool) -> list[TestResult]:
    """Run AI recommender tests for all modes."""
    print("\n🤖 Testing AI Recommender Modes...")
    print("-" * 40)
    results: list[TestResult] = []
    for name, request_body in AI_RECOMMENDER_TESTS:
        result = await test_ai_recommender(client, name, request_body)
        print_result(result, verbose)
        results.append(result)
    return results


async def _run_edge_and_security_tests(
    client: httpx.AsyncClient, verbose: bool
) -> list[TestResult]:
    """Run edge case and security tests."""
    results: list[TestResult] = []

    print("\n⚠️  Testing Edge Cases...")
    print("-" * 40)
    for name, path, expected_codes in EDGE_CASES:
        result = await test_edge_case(client, name, path, expected_codes)
        print_result(result, verbose)
        results.append(result)

    print("\n�️  Testing Input Validation...")
    print("-" * 40)
    for name, path, expected_codes in INPUT_VALIDATION_TESTS:
        result = await test_edge_case(client, name, path, expected_codes)
        print_result(result, verbose)
        results.append(result)

    print("\n�🔒 Testing Security Headers...")
    print("-" * 40)
    security_results = await test_security_headers(client)
    for result in security_results:
        print_result(result, verbose)
        results.append(result)

    return results


async def _run_infra_tests(client: httpx.AsyncClient, verbose: bool) -> list[TestResult]:
    """Run infrastructure tests (version, health, method restrictions)."""
    results: list[TestResult] = []

    print("\n📌 Testing Version Endpoint...")
    print("-" * 40)
    try:
        response = await client.get("/version")
        version = response.text.strip()
        version_match = re.match(r"^[a-f0-9]{7,40}$", version)
        result = TestResult(
            name="Version endpoint",
            passed=response.status_code == 200,
            status_code=response.status_code,
            details={"version": version if version_match else "non-sha-format"},
        )
    except httpx.RequestError as e:
        result = TestResult(name="Version endpoint", passed=False, message=str(e))
    print_result(result, verbose)
    results.append(result)

    print("\n❤️  Testing Health Endpoint...")
    print("-" * 40)
    try:
        response = await client.get("/healthz")
        if response.status_code == 200:
            result = TestResult(name="Health endpoint", passed=True, status_code=200)
        elif response.status_code == 404:
            result = TestResult(
                name="Health endpoint",
                passed=True,
                status_code=404,
                message="Not implemented (optional)",
            )
        else:
            result = TestResult(
                name="Health endpoint",
                passed=False,
                status_code=response.status_code,
                expected=200,
            )
    except httpx.RequestError as e:
        result = TestResult(name="Health endpoint", passed=False, message=str(e))
    print_result(result, verbose)
    results.append(result)

    print("\n🚫 Testing Method Restrictions...")
    print("-" * 40)
    for path in ["/", "/roles", "/operations"]:
        for method in ["DELETE", "PUT"]:
            result = await test_method_restriction(client, path, method)
            print_result(result, verbose)
            results.append(result)

    return results


def _print_summary(results: list[TestResult], slow_responses: list[tuple[str, float]]) -> bool:
    """Print test summary and return True if all tests passed."""
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print("\n" + "=" * 60)
    print(f"📋 Summary: {passed} passed, {failed} failed out of {len(results)} tests")

    if slow_responses:
        print(f"\n⏱️  Slow responses (>{MAX_RESPONSE_TIME}s):")
        for name, elapsed in slow_responses:
            print(f"   - {name}: {elapsed:.2f}s")

    print("=" * 60)

    if failed > 0:
        print("\033[91m❌ Smoke tests FAILED\033[0m")
        print("\nFailed tests:")
        for r in results:
            if not r.passed:
                msg = r.message if r.message else f"HTTP {r.status_code}"
                print(f"   - {r.name}: {msg}")
                if r.details:
                    for key, value in r.details.items():
                        val_str = str(value)[:200]
                        print(f"       {key}: {val_str}")
        return False
    else:
        print("\033[92m🎉 All smoke tests PASSED\033[0m")
        return True


async def run_smoke_tests(base_url: str, verbose: bool = False) -> bool:
    """Run all smoke tests against the given base URL.

    All tests run to completion regardless of failures.
    Returns True only if all tests pass.
    """
    print(f"🧪 Running smoke tests against: {base_url}")
    print("=" * 60)

    results: list[TestResult] = []
    slow_responses: list[tuple[str, float]] = []

    async with httpx.AsyncClient(
        base_url=base_url, timeout=TIMEOUT, follow_redirects=True
    ) as client:
        page_results, page_slow = await _run_page_tests(client, verbose)
        results.extend(page_results)
        slow_responses.extend(page_slow)

        results.extend(await _run_static_tests(client, verbose))
        results.extend(await _run_api_tests(client, verbose))
        results.extend(await _run_feed_tests(client, verbose))
        results.extend(await _run_ai_tests(client, verbose))
        results.extend(await _run_edge_and_security_tests(client, verbose))
        results.extend(await _run_infra_tests(client, verbose))

    return _print_summary(results, slow_responses)


async def main() -> int:
    """Parse arguments and run smoke tests."""
    parser = argparse.ArgumentParser(description="Smoke tests for Azure RBAC Catalog")
    parser.add_argument(
        "--url",
        default="https://azurerbac-builtinroles-staging.azurewebsites.net",
        help="Base URL to test (default: staging slot)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show verbose output with response details",
    )
    args = parser.parse_args()

    success = await run_smoke_tests(args.url.rstrip("/"), verbose=args.verbose)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
