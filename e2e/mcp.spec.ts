/**
 * MCP Server E2E/Smoke Tests
 *
 * Tests the MCP Streamable HTTP endpoint availability and basic tool invocations.
 * Run with: npx playwright test e2e/mcp.spec.ts
 */
import { test, expect } from '@playwright/test';

const MCP_HTTP_URL = '/mcp';

test.describe('MCP Server', () => {
  test.describe('Streamable HTTP Endpoint', () => {
    test('should return valid response on HTTP endpoint', async ({ request }) => {
      // Streamable HTTP endpoint accepts POST requests with JSON-RPC messages
      const response = await request.post(MCP_HTTP_URL, {
        headers: { 
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/event-stream',
        },
        data: {
          jsonrpc: '2.0',
          id: 1,
          method: 'initialize',
          params: {
            protocolVersion: '2024-11-05',
            capabilities: {},
            clientInfo: { name: 'test-client', version: '1.0.0' }
          }
        },
        timeout: 5000,
      });
      // Should get a valid response (200) or method error, but not 404
      expect(response.status()).not.toBe(404);
    });

    test('should accept POST requests', async ({ request }) => {
      // Verify the HTTP endpoint accepts connections
      const response = await request.post(MCP_HTTP_URL, {
        headers: { 
          'Content-Type': 'application/json',
          'Accept': 'application/json, text/event-stream',
        },
        data: { jsonrpc: '2.0', id: 1, method: 'ping' },
        timeout: 2000,
      });
      // Should accept the connection (any status except 404 means route is mounted)
      expect([200, 400, 405, 500]).toContain(response.status());
    });
  });

  test.describe('Health Check', () => {
    test('should have MCP route mounted', async ({ request }) => {
      // Verify the MCP router is mounted by checking the endpoint
      const response = await request.post(MCP_HTTP_URL, {
        headers: { 'Content-Type': 'application/json' },
        data: {},
        timeout: 1000,
      });
      // Any response (including errors) means the route exists
      expect(response.status()).not.toBe(404);
    });
  });
});

test.describe('MCP Tools via API', () => {
  // These tests interact with the MCP server via HTTP
  // In a real MCP client scenario, tools are invoked via the SSE protocol
  // For smoke testing, we verify the underlying cache/API endpoints work

  test.describe('Operations Search', () => {
    test('should search operations via main API', async ({ request }) => {
      const response = await request.get('/api/operations/search?q=Microsoft.Storage&limit=5');
      expect(response.status()).toBe(200);
      const data = await response.json();
      expect(data.operations).toBeDefined();
    });
  });

  test.describe('Roles Search', () => {
    test('should search roles via dashboard', async ({ request }) => {
      const response = await request.get('/roles?q=contributor');
      expect(response.status()).toBe(200);
    });

    test('should get role detail page', async ({ request }) => {
      // Storage Blob Data Contributor - a well-known role
      const response = await request.get(
        '/roles/ba92f5b4-2d11-453d-a403-e96b0029c9fe'
      );
      expect(response.status()).toBe(200);
    });
  });

  test.describe('AI Recommendations', () => {
    test('should get AI recommendations', async ({ request }) => {
      const response = await request.post('/api/ai-recommend', {
        data: { query: 'read blob storage', mode: 'tfidf', top_k: 3 },
      });
      expect(response.status()).toBe(200);
      const data = await response.json();
      expect(data.recommendations).toBeDefined();
    });
  });
});

test.describe('MCP Rate Limiting', () => {
  test('should handle rapid requests gracefully', async ({ request }) => {
    // Send multiple rapid requests - should not crash
    const requests = Array.from({ length: 10 }, () =>
      request.get('/api/operations/search?q=read&limit=1')
    );

    const responses = await Promise.all(requests);
    // All should succeed or return rate limit response (429)
    for (const r of responses) {
      expect([200, 429]).toContain(r.status());
    }
  });
});
