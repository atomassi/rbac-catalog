#!/usr/bin/env python3
"""Smoke tests for Azure RBAC Catalog.

This script runs after deployment to the staging slot and must pass
before promoting to production via slot swap.

Usage:
    python scripts/smoke_tests.py
    python scripts/smoke_tests.py --url https://custom-url.azurewebsites.net
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from typing import Final

import httpx

# Default timeout for requests
TIMEOUT: Final = 30.0

# ─── Test Configurations ──────────────────────────────────────────────────────

PAGES: Final = [
    ("Homepage", "/"),
    ("Roles list", "/roles"),
    ("Recent changes", "/recent"),
    ("Operations list", "/operations"),
    ("Recommend page", "/recommend"),
    ("About page", "/about"),
    ("Role detail", "/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7"),  # Reader
    ("Operation detail", "/operations/Microsoft.Storage/storageAccounts/read"),
]

STATIC_ASSETS: Final = [
    ("Favicon ICO", "/favicon.ico"),
    ("Favicon SVG", "/favicon.svg"),
    ("Robots.txt", "/robots.txt"),
    ("Sitemap.xml", "/sitemap.xml"),
]

API_ENDPOINTS: Final = [
    ("API: Search operations", "/api/operations/search?q=read"),
    ("API: Search with wildcard", "/api/operations/search?q=Microsoft.Storage/*"),
    ("API: Search with limit", "/api/operations/search?q=blob&limit=5"),
    ("API: Count matches", "/api/operations/count-matches?pattern=Microsoft.Storage/*"),
    (
        "API: Count data actions",
        "/api/operations/count-matches?pattern=Microsoft.Storage/*&is_data_action=true",
    ),
]

# Edge cases - expected to return specific status codes (tuple allows multiple valid codes)
EDGE_CASES: Final = [
    (
        "Non-existent page",
        "/this-page-does-not-exist",
        (403, 404),
    ),  # adding 403 - Cloudflare returns 403 due to security rules when used against public site
    ("Invalid role ID", "/roles/00000000-0000-0000-0000-000000000000", (404,)),
    ("API: Empty search query", "/api/operations/search?q=", (200,)),
    ("API: Short query", "/api/operations/search?q=a", (200,)),
]

# AI Recommender POST endpoint tests (different modes)
AI_RECOMMENDER_TESTS: Final = [
    ("AI: TFIDF mode", {"query": "read storage blobs", "top_k": 3, "recommender_mode": "tfidf"}),
    (
        "AI: Semantic mode",
        {"query": "manage virtual machines", "top_k": 3, "recommender_mode": "semantic"},
    ),
    (
        "AI: CrossEncoder mode",
        {"query": "backup databases", "top_k": 3, "recommender_mode": "crossencoder"},
    ),
    (
        "AI: ColBERT mode",
        {"query": "create resource groups", "top_k": 3, "recommender_mode": "colbert"},
    ),
    (
        "AI: LLM mode",
        {"query": "manage kubernetes clusters", "top_k": 3, "recommender_mode": "llm"},
    ),
]


@dataclass
class TestResult:
    """Result of a single smoke test."""

    name: str
    passed: bool
    status_code: int | None = None
    expected: int = 200
    message: str = ""


async def test_endpoint(
    client: httpx.AsyncClient,
    name: str,
    path: str,
    expected_status: int = 200,
    method: str = "GET",
    json_data: dict | None = None,
) -> TestResult:
    """Test an HTTP endpoint returns expected status code."""
    try:
        if method == "POST" and json_data:
            response = await client.post(path, json=json_data)
        else:
            response = await client.get(path)

        passed = response.status_code == expected_status
        return TestResult(
            name=name,
            passed=passed,
            status_code=response.status_code,
            expected=expected_status,
        )
    except httpx.RequestError as e:
        return TestResult(
            name=name,
            passed=False,
            message=str(e),
        )


def print_result(result: TestResult) -> None:
    """Print a test result with color."""
    if result.passed:
        status = "\033[92m✅ PASS\033[0m"
        detail = f"(HTTP {result.status_code})" if result.status_code else ""
    else:
        status = "\033[91m❌ FAIL\033[0m"
        if result.status_code:
            detail = f"(Expected {result.expected}, got {result.status_code})"
        else:
            detail = f"({result.message})"

    print(f"{status} - {result.name} {detail}")


async def run_test_batch(
    client: httpx.AsyncClient,
    section: str,
    tests: list[tuple[str, str]],
    results: list[TestResult],
) -> None:
    """Run a batch of endpoint tests and collect results."""
    print(f"\n{section}")
    print("-" * 40)

    tasks = [test_endpoint(client, name, path) for name, path in tests]
    batch_results = await asyncio.gather(*tasks)
    for result in batch_results:
        print_result(result)
        results.append(result)


async def run_ai_recommender_tests(
    client: httpx.AsyncClient,
    results: list[TestResult],
) -> None:
    """Run AI recommender tests with different modes."""
    print("\nTesting AI Recommender...")
    print("-" * 40)

    for test_name, request_body in AI_RECOMMENDER_TESTS:
        try:
            response = await client.post("/api/ai-recommend", json=request_body)
            if response.status_code == 200:
                data = response.json()
                rec_count = len(data.get("recommendations", []))
                engine = data.get("engine", {}).get("mode", "unknown")
                detail = f"(engine={engine}, results={rec_count})"
                print(f"\033[92m✅ PASS\033[0m - {test_name} {detail}")
                results.append(TestResult(name=test_name, passed=True, status_code=200))
            elif response.status_code == 429:
                print(f"\033[93m⚠️  SKIP\033[0m - {test_name} (rate limited)")
            else:
                print(f"\033[91m❌ FAIL\033[0m - {test_name} (HTTP {response.status_code})")
                results.append(
                    TestResult(name=test_name, passed=False, status_code=response.status_code)
                )
        except Exception as e:
            print(f"\033[91m❌ FAIL\033[0m - {test_name} ({e})")
            results.append(TestResult(name=test_name, passed=False, message=str(e)))


async def run_version_test(
    client: httpx.AsyncClient,
    results: list[TestResult],
) -> None:
    """Test the version endpoint."""
    print("\nTesting Version Endpoint...")
    print("-" * 40)

    try:
        response = await client.get("/version")
        version = response.text.strip()
        print(f"\033[92m✅ PASS\033[0m - Version endpoint (returned: {version})")
        results.append(TestResult(name="Version", passed=True, status_code=200))
    except Exception as e:
        print(f"\033[91m❌ FAIL\033[0m - Version endpoint ({e})")
        results.append(TestResult(name="Version", passed=False, message=str(e)))


async def run_edge_case_tests(
    client: httpx.AsyncClient,
    results: list[TestResult],
) -> None:
    """Test edge cases like 404 pages and empty queries."""
    print("\nTesting Edge Cases...")
    print("-" * 40)

    for name, path, expected_codes in EDGE_CASES:
        try:
            response = await client.get(path)
            if response.status_code in expected_codes:
                print(f"\033[92m✅ PASS\033[0m - {name} (got {response.status_code})")
                results.append(TestResult(name=name, passed=True, status_code=response.status_code))
            else:
                expected_str = "/".join(str(c) for c in expected_codes)
                print(
                    f"\033[91m❌ FAIL\033[0m - {name} "
                    f"(expected {expected_str}, got {response.status_code})"
                )
                results.append(
                    TestResult(name=name, passed=False, status_code=response.status_code)
                )
        except Exception as e:
            print(f"\033[91m❌ FAIL\033[0m - {name} ({e})")
            results.append(TestResult(name=name, passed=False, message=str(e)))


async def run_health_test(
    client: httpx.AsyncClient,
    results: list[TestResult],
) -> None:
    """Test the health endpoint (optional)."""
    print("\nTesting Health/Status...")
    print("-" * 40)

    try:
        response = await client.get("/healthz")
        if response.status_code == 200:
            print("\033[92m✅ PASS\033[0m - Health endpoint")
            results.append(TestResult(name="Health", passed=True, status_code=200))
        elif response.status_code == 404:
            print("\033[93m⚠️  SKIP\033[0m - Health endpoint (not implemented)")
        else:
            print(f"\033[91m❌ FAIL\033[0m - Health endpoint (HTTP {response.status_code})")
            results.append(
                TestResult(name="Health", passed=False, status_code=response.status_code)
            )
    except Exception as e:
        print(f"\033[91m❌ FAIL\033[0m - Health endpoint ({e})")
        results.append(TestResult(name="Health", passed=False, message=str(e)))


async def run_smoke_tests(base_url: str) -> bool:
    """Run all smoke tests against the given base URL.

    All tests run to completion regardless of failures.
    Returns True only if all tests pass.
    """
    print(f"🧪 Running smoke tests against: {base_url}")
    print("=" * 60)

    results: list[TestResult] = []

    async with httpx.AsyncClient(
        base_url=base_url, timeout=TIMEOUT, follow_redirects=True
    ) as client:
        await run_test_batch(client, "Testing Pages...", PAGES, results)
        await run_test_batch(client, "Testing Static Assets...", STATIC_ASSETS, results)
        await run_test_batch(client, "Testing API Endpoints...", API_ENDPOINTS, results)

        await run_ai_recommender_tests(client, results)
        await run_edge_case_tests(client, results)
        await run_version_test(client, results)
        await run_health_test(client, results)

    # ─── Summary ──────────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print("\n" + "=" * 60)
    print(f"📋 Summary: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        print("\033[91m❌ Smoke tests FAILED\033[0m")
        return False
    else:
        print("\033[92m🎉 All smoke tests PASSED\033[0m")
        return True


async def main() -> int:
    """Parse arguments and run smoke tests."""
    parser = argparse.ArgumentParser(description="Smoke tests for Azure RBAC Catalog")
    parser.add_argument(
        "--url",
        default="https://azurerbac-builtinroles-staging.azurewebsites.net",
        help="Base URL to test (default: staging slot)",
    )
    args = parser.parse_args()

    success = await run_smoke_tests(args.url.rstrip("/"))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
