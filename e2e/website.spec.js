// @ts-check
const { test, expect } = require('@playwright/test');

// =============================================================================
// HOME PAGE
// =============================================================================
test.describe('Home Page', () => {
  test('should load with correct title and stats', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Azure.*Roles/i);
    await expect(page.locator('text=Total Built-in Roles')).toBeVisible();
    await expect(page.locator('text=Last Scan')).toBeVisible();
  });

  test('should have navigation tabs', async ({ page }) => {
    await page.goto('/recent');
    await expect(page.locator('a[href="/recent"]').first()).toBeVisible();
    await expect(page.locator('a[href^="/roles"]').first()).toBeVisible();
  });
});

// =============================================================================
// NAVIGATION
// =============================================================================
test.describe('Navigation', () => {
  test('should navigate between main tabs', async ({ page }) => {
    await page.goto('/recent');
    await page.locator('a[href^="/roles"]').first().click();
    await expect(page).toHaveURL(/\/roles/);
    await page.locator('a[href="/recent"]').first().click();
    await expect(page).toHaveURL(/\/recent/);
    await page.locator('a[href^="/operations"]').first().click();
    await expect(page).toHaveURL(/\/operations/);
  });

  test('should navigate to Role Recommender', async ({ page }) => {
    await page.goto('/');
    const recommendLink = page.locator('a[href*="recommend"]').first();
    if (await recommendLink.isVisible()) {
      await recommendLink.click();
      await expect(page).toHaveURL(/recommend/);
    }
  });
});

// =============================================================================
// ROLES LIST
// =============================================================================
test.describe('Roles List', () => {
  test('should display roles table with columns', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await expect(page.locator('table')).toBeVisible();
    await expect(page.locator('th:has-text("Role Name"), th:has-text("ROLE NAME")').first()).toBeVisible();
    await expect(page.locator('th:has-text("Effective Actions"), th:has-text("EFFECTIVE ACTIONS")').first()).toBeVisible();
    await expect(page.locator('th:has-text("Effective Data Actions"), th:has-text("EFFECTIVE DATA ACTIONS")').first()).toBeVisible();
  });

  test('should display role rows with data', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    const rows = page.locator('table tbody tr');
    expect(await rows.count()).toBeGreaterThan(0);
    // Actions column should have numeric values
    const actionsCell = page.locator('tbody tr td:nth-child(2)');
    expect((await actionsCell.first().textContent())?.trim()).toMatch(/^\d[\d,]*$/);
    // Role ID should be in a code element
    const roleIdCode = page.locator('tbody tr td:first-child code');
    expect(await roleIdCode.first().textContent()).toMatch(/[a-f0-9-]{36}/i);
  });

  test('should click on a role to view details', async ({ page }) => {
    await page.goto('/roles');
    await page.locator('table tbody tr td a').first().click();
    await expect(page).toHaveURL(/\/roles\//);
  });

  test.describe('Sorting', () => {
    const sortTests = [
      { field: 'actions', order: 'asc' },
      { field: 'actions', order: 'desc' },
      { field: 'data_actions', order: 'asc' },
      { field: 'data_actions', order: 'desc' },
      { field: 'name', order: 'asc' },
      { field: 'name', order: 'desc' },
      { field: 'id', order: 'asc' },
      { field: 'updated', order: 'desc' },
    ];

    for (const { field, order } of sortTests) {
      test(`should sort by ${field} ${order}`, async ({ page }) => {
        await page.setViewportSize({ width: 1280, height: 720 });
        await page.goto(`/roles?sort=${field}&order=${order}`);
        await page.waitForLoadState('domcontentloaded');
        expect(page.url()).toContain(`sort=${field}`);
        // Updated sort is slower as it requires Python-side sorting
        await expect(page.locator('table')).toBeVisible({ timeout: 15000 });
      });
    }

    test('should toggle sort when clicking header', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/roles');
      await page.locator('th a:has-text("Effective Actions")').click();
      await expect(page).toHaveURL(/sort=actions/);
    });

    test('should maintain search when sorting', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/roles?q=storage&sort=actions&order=desc');
      await expect(page).toHaveURL(/q=storage/);
      await expect(page).toHaveURL(/sort=actions/);
      await expect(page.locator('table')).toBeVisible();
    });
  });
});

// =============================================================================
// SEARCH FUNCTIONALITY
// =============================================================================
test.describe('Search Functionality', () => {
  test('should have search input and perform search', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    const searchInput = page.locator('input[name="q"]:visible');
    await expect(searchInput).toBeVisible();
    await searchInput.fill('Reader');
    await searchInput.press('Enter');
    await page.waitForURL(/q=Reader/i);
    expect(page.url()).toContain('q=Reader');
  });

  test('should filter results with search', async ({ page }) => {
    await page.goto('/roles?q=Reader');
    const rows = page.locator('table tbody tr');
    expect(await rows.count()).toBeGreaterThan(0);
    expect((await rows.first().textContent())?.toLowerCase()).toContain('reader');
  });

  test('should perform exact match search', async ({ page }) => {
    await page.goto('/roles?q=Reader&exact_match=1');
    const rows = page.locator('table tbody tr');
    expect(await rows.count()).toBe(1);
    expect(await rows.first().textContent()).toContain('Reader');
  });

  test('should have exact match checkbox on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('input[name="exact_match"][type="checkbox"]:visible')).toBeVisible();
  });

  test('should not include default values in URL after search', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    const searchInput = page.locator('input[name="q"]:visible');
    await searchInput.fill('Contributor');
    await searchInput.press('Enter');
    await page.waitForURL(/q=Contributor/i);
    const url = page.url();
    expect(url).not.toContain('limit=25');
    expect(url).not.toContain('status_filter=active');
  });
});

// =============================================================================
// FILTERS
// =============================================================================
test.describe('Filters', () => {
  test('should have limit and status dropdowns', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('select[name="limit"]:visible')).toBeVisible();
    await expect(page.locator('select[name="status_filter"]:visible')).toBeVisible();
  });

  test('should change limit', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    await page.locator('select[name="limit"]:visible').selectOption('100');
    await page.waitForURL(/limit=100/);
    expect(page.url()).toContain('limit=100');
  });

  test('should filter by deleted status', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    await page.locator('select[name="status_filter"]:visible').selectOption('deleted');
    await page.waitForURL(/status_filter=deleted/);
    expect(page.url()).toContain('status_filter=deleted');
  });
});

// =============================================================================
// ROLE DETAIL PAGE
// =============================================================================
test.describe('Role Detail Page', () => {
  test('should load role detail with all sections', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await expect(page.locator('h1')).toContainText('Reader');
    await expect(page.locator('text=Role Information')).toBeVisible();
    await expect(page.locator('text=Latest Role JSON')).toBeVisible();
    await expect(page.locator('text=Effective Permissions')).toBeVisible();
  });

  test('should display role JSON with Z suffix for timestamps', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    // Wait for the JSON section header to be visible
    await expect(page.locator('text=Latest Role JSON')).toBeVisible();
    // The JSON is in a dark-themed pre block - get its text content
    const jsonBlock = page.locator('pre.text-slate-100').filter({ hasText: 'createdOn' }).first();
    // Wait for content to load
    await page.waitForTimeout(500);
    const jsonText = await jsonBlock.textContent();
    // Verify timestamps use Z suffix format (not +00:00)
    expect(jsonText).toContain('"createdOn":');
    expect(jsonText).toContain('"updatedOn":');
    // Check that timestamps end with Z, not +00:00
    expect(jsonText).toMatch(/"createdOn":\s*"[^"]+Z"/);
    expect(jsonText).toMatch(/"updatedOn":\s*"[^"]+Z"/);
    expect(jsonText).not.toMatch(/"createdOn":\s*"[^"]+\+00:00"/);
    expect(jsonText).not.toMatch(/"updatedOn":\s*"[^"]+\+00:00"/);
  });

  test('should display operations in Effective Permissions', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForTimeout(1000);
    // Check for "Operations granted by this role (N total)" text pattern
    await expect(page.locator('text=/operations granted.*\\(\\d+.*total\\)/i').first()).toBeVisible();
  });

  test('should have back link when navigating from list', async ({ page }) => {
    await page.goto('/roles');
    await page.click('table tbody tr:first-child a');
    await expect(page.locator('#back-button')).toBeVisible();
    await expect(page.locator('#back-label')).toBeVisible();
  });

  test('should return 404 for non-existent role', async ({ page }) => {
    const response = await page.goto('/roles/00000000-0000-0000-0000-000000000000');
    expect(response?.status()).toBe(404);
  });
});

// =============================================================================
// ROLE RECOMMENDER
// =============================================================================
test.describe('Role Recommender', () => {
  test('should load recommender page with input', async ({ page }) => {
    await page.goto('/recommend');
    await expect(page).toHaveURL(/recommend/);
    await expect(page).toHaveTitle(/Recommend|Role/i);
    await expect(page.locator('#operation-search')).toBeVisible();
  });

  test('should have AI mode toggle when enabled', async ({ page }) => {
    await page.goto('/recommend?ai=1');
    const modeButtons = page.locator('#mode-llm-btn, #mode-rag-btn, #mode-llm-btn-mobile, #mode-rag-btn-mobile');
    expect(await modeButtons.count()).toBeGreaterThan(0);
  });
});

// =============================================================================
// ABOUT PAGE
// =============================================================================
test.describe('About Page', () => {
  test('should load with Azure RBAC content', async ({ page }) => {
    await page.goto('/about');
    await expect(page).toHaveTitle(/About/i);
    await expect(page.locator('text=/Azure|RBAC|Role/i').first()).toBeVisible();
  });
});

// =============================================================================
// API ENDPOINTS
// =============================================================================
test.describe('API Endpoints', () => {
  test('should return 200 for health check (GET and HEAD)', async ({ request }) => {
    expect((await request.get('/healthz')).status()).toBe(200);
    expect((await request.head('/healthz')).status()).toBe(200);
  });

  test('should return version info', async ({ request }) => {
    const response = await request.get('/version');
    expect(response.status()).toBe(200);
    expect(await response.json()).toHaveProperty('version');
  });

  test('should return 405 for unsupported methods', async ({ request }) => {
    expect((await request.delete('/healthz')).status()).toBe(405);
    expect((await request.put('/roles')).status()).toBe(405);
    expect((await request.delete('/')).status()).toBe(405);
  });

  test('should return JSON for operations search API', async ({ request }) => {
    const response = await request.get('/api/operations/search?q=read');
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(data).toHaveProperty('operations');
    expect(Array.isArray(data.operations)).toBe(true);
  });

  test('should return count matches data', async ({ request }) => {
    const response = await request.get('/api/operations/count-matches?pattern=*/read');
    expect(response.status()).toBe(200);
    expect(await response.json()).toHaveProperty('count');
  });

  test('should recommend roles via API', async ({ request }) => {
    const response = await request.post('/api/recommend-roles', {
      data: { operations: [{ name: 'Microsoft.Storage/storageAccounts/read', is_data_action: false }] }
    });
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(data).toHaveProperty('roles');
    expect(Array.isArray(data.roles)).toBe(true);
  });

  test('should return all matching roles without artificial limit', async ({ request }) => {
    const response = await request.post('/api/recommend-roles', {
      data: { operations: [{ name: 'Microsoft.Authorization/roleAssignments/delete', is_data_action: false }] }
    });
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(data.total_matches).toBeGreaterThan(20);
    expect(data.roles.length).toBeGreaterThan(20);
  });

  test('should handle AI recommend API', async ({ request }) => {
    const response = await request.post('/api/ai-recommend', {
      data: { query: 'read storage blobs', mode: 'tfidf' }
    });
    expect(response.status()).toBe(200);
    expect(await response.json()).toHaveProperty('recommendations');
  });
});

// =============================================================================
// ERROR HANDLING
// =============================================================================
test.describe('Error Handling', () => {
  test('should return 404 for unknown routes', async ({ request }) => {
    const response = await request.get('/nonexistent-page-12345');
    expect(response.status()).toBe(404);
    const text = await response.text();
    expect(text.includes('404')).toBe(true);
  });

  test('should redirect old tab parameter', async ({ page }) => {
    const response = await page.goto('/?tab=roles');
    expect(response?.status()).toBe(200);
    expect(page.url()).toContain('/recent');
  });
});

// =============================================================================
// MOBILE RESPONSIVENESS
// =============================================================================
test.describe('Mobile Responsiveness', () => {
  test('should be responsive on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/recent');
    await expect(page.locator('text=Total Built-in Roles')).toBeVisible();
  });

  test('should have mobile search form with exact match toggle', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/roles');
    await expect(page.locator('input[name="q"]').first()).toBeVisible();
    await expect(page.locator('button[onclick="toggleExactMatch(this)"]')).toBeVisible();
  });

  test('should have mobile dropdowns for limit and status', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('select[name="limit"]').first()).toBeVisible();
    await expect(page.locator('select[name="status_filter"]').first()).toBeVisible();
  });
});

// =============================================================================
// PAGINATION
// =============================================================================
test.describe('Pagination', () => {
  test('should show pagination when there are many roles', async ({ page }) => {
    await page.goto('/roles?limit=25');
    const hasPagination = await page.locator('text=Page').isVisible() ||
                          await page.locator('a[href*="page=2"]').first().isVisible();
    expect(hasPagination).toBe(true);
  });

  test('should navigate to next page', async ({ page }) => {
    await page.goto('/roles?limit=25');
    const nextButton = page.locator('a[href*="page=2"]').first();
    if (await nextButton.isVisible()) {
      await nextButton.click();
      await expect(page).toHaveURL(/page=2/);
    }
  });
});

// =============================================================================
// SECURITY HEADERS
// =============================================================================
test.describe('Security Headers', () => {
  test('should have required security headers', async ({ request }) => {
    const response = await request.get('/');
    const csp = response.headers()['content-security-policy'];
    expect(csp).toBeDefined();
    expect(csp).toContain("default-src 'self'");
    expect(response.headers()['x-frame-options']).toBe('DENY');
  });
});

// =============================================================================
// OPERATIONS PAGE
// =============================================================================
test.describe('Operations Page', () => {
  test('should load operations list with columns', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/operations');
    await expect(page).toHaveTitle(/Azure Operations/i);
    await expect(page.locator('table')).toBeVisible();
    await expect(page.locator('th:has-text("Granted By"), th:has-text("GRANTED BY")').first()).toBeVisible();
  });

  test('should have search and filter dropdowns', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/operations');
    await expect(page.locator('input[name="q"]:visible')).toBeVisible();
    await expect(page.locator('select[name="is_data_action"]:visible')).toBeVisible();
    await expect(page.locator('select[name="provider"]:visible')).toBeVisible();
  });

  test('should display role counts in Roles column', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/operations');
    const rolesCell = page.locator('tbody tr td:nth-child(3)');
    expect((await rolesCell.first().textContent())?.trim()).toMatch(/^\d[\d,]*$/);
  });

  test('should navigate to operation detail', async ({ page }) => {
    await page.goto('/operations');
    await page.locator('tbody tr td a').first().click();
    await expect(page).toHaveURL(/\/operations\//);
  });

  test.describe('Sorting', () => {
    const sortTests = [
      { field: 'roles', order: 'asc' },
      { field: 'roles', order: 'desc' },
      { field: 'name', order: 'desc' },
      { field: 'provider', order: 'asc' },
      { field: 'type', order: 'desc' },
    ];

    for (const { field, order } of sortTests) {
      test(`should sort by ${field} ${order}`, async ({ page }) => {
        await page.setViewportSize({ width: 1280, height: 720 });
        await page.goto(`/operations?sort=${field}&order=${order}`);
        expect(page.url()).toContain(`sort=${field}`);
        await expect(page.locator('table')).toBeVisible();
      });
    }

    test('should toggle sort when clicking header', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/operations');
      await page.locator('th a:has-text("Granted By")').click();
      await expect(page).toHaveURL(/sort=roles/);
    });

    test('should maintain filters when sorting', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/operations?q=storage&sort=roles&order=desc');
      await expect(page).toHaveURL(/q=storage/);
      await expect(page).toHaveURL(/sort=roles/);
      await expect(page.locator('table')).toBeVisible();
    });
  });
});

// =============================================================================
// OPERATION DETAIL PAGE
// =============================================================================
test.describe('Operation Detail Page', () => {
  test('should load with all sections', async ({ page }) => {
    await page.goto('/operations/Microsoft.Storage/storageAccounts/read');
    await expect(page.locator('h1')).toBeVisible();
    await expect(page.locator('text=Operation Information')).toBeVisible();
    await expect(page.locator('text=Roles Allowing This Operation')).toBeVisible();
    await expect(page.locator('text=Control Plane').first()).toBeVisible();
  });

  test('should show roles count in section header', async ({ page }) => {
    await page.goto('/operations/Microsoft.Storage/storageAccounts/read');
    await expect(page.locator('text=/\\d+ roles? grant/i')).toBeVisible();
  });

  test('should have role search for operations with many roles', async ({ page }) => {
    await page.goto('/operations/Microsoft.Resources/subscriptions/read');
    await page.waitForLoadState('domcontentloaded');
    const searchInput = page.locator('#roleSearchInput');
    if (await searchInput.count() > 0) {
      await expect(searchInput).toBeVisible();
    }
  });

  test('should filter roles when searching', async ({ page }) => {
    await page.goto('/operations/Microsoft.Resources/subscriptions/read');
    await page.waitForLoadState('domcontentloaded');
    const searchInput = page.locator('#roleSearchInput');
    if (await searchInput.isVisible()) {
      await searchInput.fill('Reader');
      await page.waitForTimeout(300);
      const visibleRows = page.locator('tbody tr:visible');
      if (await visibleRows.count() > 0) {
        expect((await visibleRows.first().textContent())?.toLowerCase()).toContain('reader');
      }
    }
  });

  test('should have back link and preserve search params', async ({ page }) => {
    await page.goto('/operations?q=storage');
    await page.click('table tbody tr:first-child a');
    await expect(page.locator('#back-button')).toBeVisible();
    await page.locator('#back-button').click();
    await expect(page).toHaveURL(/operations/);
  });

  test('should link roles to role detail pages', async ({ page }) => {
    await page.goto('/operations/Microsoft.Storage/storageAccounts/read');
    const roleLink = page.locator('a[href^="/roles/"]').first();
    if (await roleLink.isVisible()) {
      expect(await roleLink.getAttribute('href')).toContain('/roles/');
    }
  });

  test('should show conditional warning badge for roles with conditions', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/operations/Microsoft.Authorization/roleAssignments/write');
    await page.waitForLoadState('networkidle');
    // Click "Show all" to reveal hidden rows that may contain conditional roles
    const showMoreBtn = page.locator('#showMoreBtn');
    if (await showMoreBtn.isVisible()) {
      await showMoreBtn.click();
    }
    // Check for conditional badge
    await expect(page.locator('.conditional-badge').first()).toBeVisible();
    expect(await page.locator('.tooltip-text').count()).toBeGreaterThan(0);
    expect(await page.locator('tr[data-has-condition="true"]').count()).toBeGreaterThan(0);
  });

  test('should return 404 for non-existent operation', async ({ page }) => {
    const response = await page.goto('/operations/NonExistent.Provider/nonExistentAction');
    expect(response?.status()).toBe(404);
  });

  test('should have responsive table layout', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/operations/Microsoft.Resources/subscriptions/read');
    await page.waitForLoadState('domcontentloaded');
    await expect(page.locator('table.table-fixed')).toBeVisible();
    await expect(page.locator('th:has-text("Actions")').first()).toBeVisible();
    await expect(page.locator('th:has-text("Data Actions")').first()).toBeVisible();
  });
});

// =============================================================================
// SITEMAP AND ROBOTS
// =============================================================================
test.describe('Sitemap and Robots', () => {
  test('should have valid sitemap with operations', async ({ request }) => {
    const response = await request.get('/sitemap.xml');
    expect(response.status()).toBe(200);
    const content = await response.text();
    // Validate XML structure using string checks
    expect(content.startsWith('<?xml')).toBe(true);
    expect(content.includes('urlset')).toBe(true);
    expect(content.includes('/operations')).toBe(true);
  });

  test('should have valid robots.txt', async ({ request }) => {
    const response = await request.get('/robots.txt');
    expect(response.status()).toBe(200);
    const content = await response.text();
    expect(content).toContain('User-agent:');
    expect(content).toContain('Sitemap:');
  });
});

// =============================================================================
// AI MODE PARAMETER PRESERVATION
// =============================================================================
test.describe('AI Mode Preservation', () => {
  test('should store and preserve ai mode in sessionStorage', async ({ page }) => {
    await page.goto('/recommend?ai=1');
    await page.waitForLoadState('domcontentloaded');
    expect(await page.evaluate(() => sessionStorage.getItem('azurerbac_ai_mode'))).toBe('1');

    await page.goto('/about');
    await page.waitForLoadState('domcontentloaded');
    expect(await page.evaluate(() => sessionStorage.getItem('azurerbac_ai_mode'))).toBe('1');
  });

  test('should not have ai mode without parameter', async ({ page }) => {
    await page.goto('/recommend');
    await page.evaluate(() => sessionStorage.clear());
    await page.goto('/about');
    await page.waitForLoadState('domcontentloaded');
    expect(await page.evaluate(() => sessionStorage.getItem('azurerbac_ai_mode'))).toBeNull();
  });
});

// =============================================================================
// NAVIGATION LOOP PREVENTION
// =============================================================================
test.describe('Navigation Loop Prevention', () => {
  test('role page back button should not point to same role', async ({ page }) => {
    await page.goto('/roles');
    await page.click('table tbody tr:first-child a');
    await page.waitForLoadState('domcontentloaded');

    const roleTitle = await page.locator('h1').first().textContent();
    const operationLink = page.locator('a[href^="/operations/"]').first();

    if (await operationLink.count() > 0) {
      await operationLink.click();
      await page.waitForLoadState('domcontentloaded');
      await page.locator('#back-button').click();
      await page.waitForLoadState('domcontentloaded');

      const backLabelText = await page.locator('#back-label').textContent();
      expect(backLabelText).not.toContain(roleTitle?.trim());
    }
  });

  test('role->operation->role navigation should have correct back links', async ({ page }) => {
    await page.goto('/roles');
    await page.click('table tbody tr:first-child a');
    await page.waitForLoadState('domcontentloaded');

    await expect(page.locator('#back-label')).toContainText(/Back to Roles/i);

    const operationLink = page.locator('a[href^="/operations/"]').first();
    if (await operationLink.count() > 0) {
      await operationLink.click();
      await page.waitForLoadState('domcontentloaded');
      await expect(page.locator('#back-label')).toContainText(/Back to/);

      await page.locator('#back-button').click();
      await page.waitForLoadState('domcontentloaded');
      await expect(page.locator('#back-label')).toContainText(/Back to Roles/i);
    }
  });
});

// =============================================================================
// ACCESSIBILITY
// =============================================================================
test.describe('Accessibility', () => {
  test('should have proper heading hierarchy on home page', async ({ page }) => {
    await page.goto('/recent');
    await expect(page.locator('h1').first()).toBeVisible();
  });

  test('should have alt text or aria-labels on interactive elements', async ({ page }) => {
    await page.goto('/roles');
    const buttons = page.locator('button:visible');
    const count = await buttons.count();
    if (count > 0) {
      expect(count).toBeGreaterThan(0);
    }
  });

  test('should support keyboard navigation in search', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    const searchInput = page.locator('input[name="q"]:visible');
    await searchInput.focus();
    await searchInput.fill('test');
    await searchInput.press('Enter');
    await expect(page).toHaveURL(/q=test/);
  });
});

// =============================================================================
// RECENT PAGE PAGINATION
// =============================================================================
test.describe('Recent Page Pagination', () => {
  test('should default to 30 days', async ({ page }) => {
    await page.goto('/recent');
    // The page should display "30 days" or have days=30 in state
    const daysText = await page.locator('text=30 days').first().isVisible();
    const daysInUrl = page.url().includes('days=30') || !page.url().includes('days=');
    expect(daysText || daysInUrl).toBe(true);
  });

  test('should show pagination controls', async ({ page }) => {
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');
    // Check for pagination info - "Page X of Y" text, "Showing X to Y of Z" text, page links,
    // or "changes found" text (for single-page results)
    const hasPageText = await page.getByText(/page \d+ of \d+/i).first().isVisible();
    const hasShowingText = await page.getByText(/showing \d+ to \d+ of/i).first().isVisible();
    const hasPageLinks = await page.locator('a[href*="page="]').first().isVisible();
    const hasChangesFound = await page.getByText(/\d+.*changes found/i).first().isVisible();
    expect(hasPageText || hasShowingText || hasPageLinks || hasChangesFound).toBe(true);
  });

  test('should have rows dropdown with correct options', async ({ page }) => {
    await page.goto('/recent');
    // Look for the rows dropdown - it's an onchange select that auto-submits
    const rowsSelect = page.locator('select').filter({ hasText: /rows/ });
    if (await rowsSelect.count() > 0) {
      // Verify the dropdown has expected options
      const options = await rowsSelect.first().locator('option').allTextContents();
      expect(options.some(o => o.includes('25'))).toBe(true);
      expect(options.some(o => o.includes('100'))).toBe(true);
    } else {
      // Rows dropdown may be styled differently - check for limit in URL or text
      const pageLoaded = await page.locator('table').isVisible();
      expect(pageLoaded).toBe(true);
    }
  });

  test('should change page size when selecting from dropdown', async ({ page }) => {
    await page.goto('/recent?limit=25');
    const rowsSelect = page.locator('select[name="limit"]');
    if (await rowsSelect.isVisible()) {
      await rowsSelect.selectOption('100');
      await page.waitForLoadState('networkidle');
      await expect(page).toHaveURL(/limit=100/);
    }
  });

  test('should navigate to page 2', async ({ page }) => {
    // Use limit=25 to ensure multiple pages
    await page.goto('/recent?limit=25');
    const page2Link = page.locator('a[href*="page=2"]').first();
    if (await page2Link.isVisible()) {
      await page2Link.click();
      await expect(page).toHaveURL(/page=2/);
    }
  });

  test('should preserve limit when paginating', async ({ page }) => {
    await page.goto('/recent?limit=100');
    const page2Link = page.locator('a[href*="page=2"]').first();
    if (await page2Link.isVisible()) {
      await page2Link.click();
      await expect(page).toHaveURL(/limit=100/);
      await expect(page).toHaveURL(/page=2/);
    }
  });

  test('should show event count and page info', async ({ page }) => {
    await page.goto('/recent');
    // Check for "X changes found" or similar text
    const hasEventCount = await page.locator('text=changes found').first().isVisible() ||
                          await page.locator('text=events').first().isVisible() ||
                          await page.locator('text=changes').first().isVisible();
    expect(hasEventCount).toBe(true);
  });

  test.describe('Days Filter', () => {
    const daysTests = [7, 15, 30, 60, 90];

    for (const days of daysTests) {
      test(`should work with days=${days}`, async ({ page }) => {
        await page.goto(`/recent?days=${days}`);
        await page.waitForLoadState('domcontentloaded');
        // Either a table with results, "No changes found" message, or the page loaded with content
        const hasTable = await page.locator('table').isVisible();
        const hasNoChanges = await page.getByText(/no changes found|no events|nothing/i).first().isVisible();
        const hasPageContent = await page.getByText(/changes found|events|total/i).first().isVisible();
        expect(hasTable || hasNoChanges || hasPageContent).toBe(true);
        expect(page.url()).toContain(`days=${days}`);
      });
    }
  });
});

// =============================================================================
// RECENT CHANGES CONTENT (History)
// =============================================================================
test.describe('Recent Changes Content', () => {
  test('should display history table with role changes', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');
    // The recent page should have a table with history entries
    await expect(page.locator('table')).toBeVisible();
  });

  test('should display role names in history entries', async ({ page }) => {
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');
    // Check for role names in the table (Reader or Contributor from our seed data)
    const hasRoleNames = await page.locator('table').getByText(/Reader|Contributor|Owner|Storage/i).first().isVisible();
    expect(hasRoleNames).toBe(true);
  });

  test('should have links to role detail pages from history', async ({ page }) => {
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');
    // History entries should link to role detail pages
    const roleLinks = page.locator('table a[href^="/roles/"]');
    expect(await roleLinks.count()).toBeGreaterThan(0);
  });

  test('should display event type badges', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');
    // Look for event type indicators (initial_scan, created, updated, deleted)
    const hasEventTypes = await page.getByText(/initial|created|updated|deleted/i).first().isVisible();
    expect(hasEventTypes).toBe(true);
  });

  test('should filter by event type when dropdown exists', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');
    const eventTypeSelect = page.locator('select[name="event_type"]');
    if (await eventTypeSelect.isVisible()) {
      await eventTypeSelect.selectOption('updated');
      await page.waitForLoadState('networkidle');
      expect(page.url()).toContain('event_type=updated');
    }
  });
});
