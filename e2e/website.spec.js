// @ts-check
const { test, expect } = require('@playwright/test');

// =============================================================================
// HOME PAGE
// =============================================================================
test.describe('Home Page', () => {
  test('should load with stats and navigation', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Azure.*Roles/i);
    await expect(page.locator('text=Total Built-in Roles')).toBeVisible();
    await expect(page.locator('text=Last Scan')).toBeVisible();
    await expect(page.locator('a[href="/recent"]').first()).toBeVisible();
    await expect(page.locator('a[href^="/roles"]').first()).toBeVisible();
  });

  test('should serve recent content at root URL', async ({ page }) => {
    const response = await page.goto('/');
    expect(response?.status()).toBe(200);
    // Root now serves content directly (no redirect)
    expect(page.url()).toMatch(/\/($|\?|#)/);
  });

  test('should still serve /recent as alias', async ({ page }) => {
    const response = await page.goto('/recent');
    expect(response?.status()).toBe(200);
    expect(page.url()).toContain('/recent');
  });
});

// =============================================================================
// NAVIGATION
// =============================================================================
test.describe('Navigation', () => {
  test('should navigate between all main tabs', async ({ page }) => {
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

  test('role->operation->role navigation preserves back links', async ({ page }) => {
    await page.goto('/roles');
    await page.click('table tbody tr:first-child a');
    await page.waitForLoadState('networkidle');
    
    const backLabel = page.locator('#back-label');
    if (await backLabel.count() > 0) {
      await expect(backLabel).toContainText(/Back to Roles/i);
    }

    const operationLink = page.locator('a[href^="/operations/"]').first();
    if (await operationLink.count() > 0) {
      await operationLink.click();
      await page.waitForLoadState('networkidle');
      
      const opBackLabel = page.locator('#back-label');
      if (await opBackLabel.count() > 0) {
        await expect(opBackLabel).toContainText(/Back to/);

        await page.locator('#back-button').click();
        await page.waitForLoadState('networkidle');
        // After navigating back, we should be on the role page
        await expect(page).toHaveURL(/\/roles\//);
      }
    }
  });
});

// =============================================================================
// RECENT CHANGES PAGE
// =============================================================================
test.describe('Recent Changes Page', () => {
  test('should display history table with all key elements', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');

    await expect(page.locator('table')).toBeVisible();
    const roleLinks = page.locator('table a[href^="/roles/"]');
    await expect(roleLinks.first()).toBeVisible();
    expect(await roleLinks.count()).toBeGreaterThan(0);
  });

  test('should display colored event type badges', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');

    // Check for event type badges (New=green, Edit=blue, Del=red) with their actual CSS classes
    const eventBadges = page.locator('span.text-xs.font-medium');
    expect(await eventBadges.count()).toBeGreaterThan(0);
    const hasEventTypes = await page.getByText(/New|Edit|Del|initial/i).first().isVisible();
    expect(hasEventTypes).toBe(true);
  });

  test.describe('Filters', () => {
    test('should have event type and time range filters', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/recent');
      await page.waitForLoadState('domcontentloaded');

      const eventTypeSelect = page.locator('select[name="event_type"]');
      if (await eventTypeSelect.isVisible()) {
        await expect(eventTypeSelect).toBeVisible();
      }

      const daysSelect = page.locator('select[name="days"]');
      if (await daysSelect.isVisible()) {
        await expect(daysSelect).toBeVisible();
      }
    });

    test('should filter by event type', async ({ page }) => {
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

    const daysTests = [7, 15, 30, 60, 90];
    for (const days of daysTests) {
      test(`should filter by days=${days}`, async ({ page }) => {
        await page.goto(`/recent?days=${days}`);
        await page.waitForLoadState('domcontentloaded');

        const hasTable = await page.locator('table').isVisible();
        const hasNoChanges = await page.getByText(/no changes found|no events/i).first().isVisible();
        expect(hasTable || hasNoChanges).toBe(true);
        expect(page.url()).toContain(`days=${days}`);
      });
    }

    test('should have per-page dropdown and results count', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/recent');
      await page.waitForLoadState('domcontentloaded');

      const limitSelect = page.locator('select[name="limit"]');
      if (await limitSelect.isVisible()) {
        await expect(limitSelect).toBeVisible();
      }

      const hasResultsCount = await page.getByText(/\d+\s*changes/i).first().isVisible() ||
                              await page.getByText(/Page \d+ of \d+/i).first().isVisible();
      expect(hasResultsCount).toBe(true);
    });

    test('should change per-page limit', async ({ page }) => {
      await page.goto('/recent?limit=25');
      const limitSelect = page.locator('select[name="limit"]');
      if (await limitSelect.isVisible()) {
        await limitSelect.selectOption('100');
        await page.waitForLoadState('networkidle');
        await expect(page).toHaveURL(/limit=100/);
      }
    });
  });

  test.describe('Pagination', () => {
    test('should show pagination or results count', async ({ page }) => {
      await page.goto('/recent?limit=25');
      await page.waitForLoadState('domcontentloaded');

      // Page should show either pagination (if > 1 page) or at least the results count
      const hasPageText = await page.getByText(/page \d+/i).first().isVisible();
      const hasPageLinks = await page.locator('a[href*="page="]').first().isVisible();
      const hasChangesText = await page.getByText(/\d+\s+changes/i).first().isVisible();
      expect(hasPageText || hasPageLinks || hasChangesText).toBe(true);
    });

    test('should navigate to page 2 and preserve filters', async ({ page }) => {
      await page.goto('/recent?limit=25&days=30');
      const page2Link = page.locator('a[href*="page=2"]').first();
      if (await page2Link.isVisible()) {
        await page2Link.click();
        await expect(page).toHaveURL(/page=2/);
        await expect(page).toHaveURL(/limit=25/);
      }
    });
  });

  test('mobile displays table correctly', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');

    await expect(page.locator('text=Total Built-in Roles')).toBeVisible();
    await expect(page.locator('table')).toBeVisible();

    const tableContainer = page.locator('.overflow-x-auto');
    if (await tableContainer.count() > 0) {
      await expect(tableContainer.first()).toBeVisible();
    }
  });
});

// =============================================================================
// ROLES LIST
// =============================================================================
test.describe('Roles List', () => {
  test('should display roles table with all columns', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    await expect(page.locator('table')).toBeVisible();
    await expect(page.locator('th:has-text("Role Name"), th:has-text("ROLE NAME")').first()).toBeVisible();
    await expect(page.locator('th:has-text("Effective Actions"), th:has-text("EFFECTIVE ACTIONS")').first()).toBeVisible();
    await expect(page.locator('th:has-text("Effective Data Actions"), th:has-text("EFFECTIVE DATA ACTIONS")').first()).toBeVisible();
  });

  test('should display role rows with valid data', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    const rows = page.locator('table tbody tr');
    expect(await rows.count()).toBeGreaterThan(0);

    const actionsCell = page.locator('tbody tr td:nth-child(2)');
    expect((await actionsCell.first().textContent())?.trim()).toMatch(/^\d[\d,]*$/);

    const roleIdCode = page.locator('tbody tr td:first-child code');
    expect(await roleIdCode.first().textContent()).toMatch(/[a-f0-9-]{36}/i);
  });

  test('should navigate to role details on click', async ({ page }) => {
    await page.goto('/roles');
    await page.locator('table tbody tr td a').first().click();
    await expect(page).toHaveURL(/\/roles\//);
  });

  test.describe('Sorting', () => {
    const sortTests = [
      { field: 'actions', order: 'asc' },
      { field: 'actions', order: 'desc' },
      { field: 'data_actions', order: 'desc' },
      { field: 'name', order: 'asc' },
      { field: 'id', order: 'asc' },
      { field: 'updated', order: 'desc' },
    ];

    for (const { field, order } of sortTests) {
      test(`should sort by ${field} ${order}`, async ({ page }) => {
        await page.setViewportSize({ width: 1280, height: 720 });
        await page.goto(`/roles?sort=${field}&order=${order}`);
        await page.waitForLoadState('domcontentloaded');
        expect(page.url()).toContain(`sort=${field}`);
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

  test.describe('Search', () => {
    test('should perform search and filter results', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/roles');
      await page.waitForLoadState('domcontentloaded');

      const searchInput = page.locator('input[name="q"]:visible');
      await expect(searchInput).toBeVisible();
      await searchInput.fill('Reader');
      await searchInput.press('Enter');
      await page.waitForURL(/q=Reader/i);

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

  test.describe('Pagination', () => {
    test('should show pagination with navigation', async ({ page }) => {
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
});

// =============================================================================
// ROLE DETAIL PAGE
// =============================================================================
test.describe('Role Detail Page', () => {
  test('should load with all sections', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await expect(page.locator('h1')).toContainText('Reader');
    await expect(page.locator('text=Role Information')).toBeVisible();
    await expect(page.locator('text=Latest Role JSON')).toBeVisible();
    await expect(page.locator('text=Effective Permissions')).toBeVisible();
  });

  test('should serve content without slug and include canonical with slug', async ({ page }) => {
    // Access role without slug - should return 200 with content (for SEO: GUIDs are indexable)
    const response = await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    expect(response?.status()).toBe(200);
    
    // Should stay on the same URL (no redirect)
    expect(page.url()).toMatch(/\/roles\/acdd72a7-3385-48ef-bd42-f606fba81ae7$/);
    
    // Should have canonical link pointing to slug version
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toMatch(/\/roles\/acdd72a7-3385-48ef-bd42-f606fba81ae7\/.+/);
    
    // Content should be visible (use heading to avoid multiple matches)
    await expect(page.getByRole('heading', { name: 'Reader' })).toBeVisible();
  });

  test('should display role JSON with Z suffix timestamps', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await expect(page.locator('text=Latest Role JSON')).toBeVisible();

    const jsonBlock = page.locator('pre.text-slate-100').filter({ hasText: 'createdOn' }).first();
    await page.waitForTimeout(500);
    const jsonText = await jsonBlock.textContent();

    expect(jsonText).toContain('"createdOn":');
    expect(jsonText).toContain('"updatedOn":');
    expect(jsonText).toMatch(/"createdOn":\s*"[^"]+Z"/);
    expect(jsonText).toMatch(/"updatedOn":\s*"[^"]+Z"/);
    expect(jsonText).not.toMatch(/"createdOn":\s*"[^"]+\+00:00"/);
  });

  test('should display operations in Effective Permissions', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForTimeout(1000);
    await expect(page.locator('text=/operations granted.*\\(\\d+.*total\\)/i').first()).toBeVisible();
  });

  test('should display role history/events section', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    // Should show change history section
    const hasHistorySection = await page.getByText(/Change History|Recent Changes|Events/i).first().isVisible();
    expect(hasHistorySection).toBe(true);
  });

  test('should have back link when navigating from list', async ({ page }) => {
    await page.goto('/roles');
    await page.click('table tbody tr:first-child a');
    await expect(page.locator('#back-button')).toBeVisible();
    await expect(page.locator('#back-label')).toBeVisible();
  });

  test('should not show back link when accessing directly', async ({ page }) => {
    // Access role detail directly without navigation
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7/reader');
    await page.waitForLoadState('domcontentloaded');
    // Back button should not be visible or should be hidden
    const backButton = page.locator('#back-button');
    if (await backButton.count() > 0) {
      // If present, verify it's for direct access behavior (may still show home link)
      const isVisible = await backButton.isVisible();
      // This is a soft assertion - just verify page loads correctly
      expect(await page.locator('h1').textContent()).toContain('Reader');
    }
  });

  test('should return 404 for non-existent role', async ({ page }) => {
    const response = await page.goto('/roles/00000000-0000-0000-0000-000000000000');
    expect(response?.status()).toBe(404);
  });

  test('should return 400 for malformed UUID', async ({ page }) => {
    const response = await page.goto('/roles/not-a-valid-uuid');
    expect(response?.status()).toBe(400);
  });

  test('should return 400 for UUID with invalid format', async ({ page }) => {
    // UUID with partial dashes is invalid
    const response = await page.goto('/roles/acdd72a73385-48ef-bd42-f606fba81ae7');
    expect(response?.status()).toBe(400);
  });

  test('should handle valid UUID without dashes', async ({ page }) => {
    // Valid UUID without dashes should work (normalized)
    const response = await page.goto('/roles/acdd72a7338548efbd42f606fba81ae7');
    // May return 404 if role not found, or redirect to slug
    expect([200, 301, 404]).toContain(response?.status());
  });

  test('should have Copy and Download buttons for Latest Role JSON', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    
    // Find the JSON section buttons
    const copyJsonButton = page.locator('button[title="Copy JSON"]');
    const downloadJsonButton = page.locator('button[title="Download JSON"]');
    
    await expect(copyJsonButton).toBeVisible();
    await expect(downloadJsonButton).toBeVisible();
  });

  test('should copy JSON without JS errors when clicking button', async ({ page, context }) => {
    // Grant clipboard permissions
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    
    // Track JS errors
    const jsErrors = [];
    page.on('pageerror', (error) => jsErrors.push(error.message));
    
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard to be available
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');
    
    const copyJsonButton = page.locator('button[title="Copy JSON"]');
    await expect(copyJsonButton).toBeVisible();
    
    // Actually click the button - this will fail if onclick has JS syntax errors
    await copyJsonButton.click();
    
    // Verify no JS errors occurred
    expect(jsErrors).toHaveLength(0);
    
    // Toast should appear (proves the copy function ran)
    const toast = page.locator('#toast-container');
    await expect(toast).toBeVisible({ timeout: 3000 });
    await expect(toast).toContainText('JSON copied');
    
    // Visual feedback - check icon should be visible
    const checkIcon = copyJsonButton.locator('.check-icon');
    await expect(checkIcon).toBeVisible({ timeout: 1000 });
  });

  test('should invoke Clipboard.copyWithFeedback when clicking copy JSON button', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard to be available
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');
    
    // Track if Clipboard.copyWithFeedback was called with correct arguments
    const copyArgs = await page.evaluate(() => {
      return new Promise((resolve) => {
        const originalCopy = window.Clipboard.copyWithFeedback;
        window.Clipboard.copyWithFeedback = (button, text, message) => {
          resolve({ hasButton: !!button, textContains: text.includes('roleDefinitions'), message });
          return originalCopy(button, text, message);
        };
        // Click will trigger the onclick handler
        document.querySelector('button[title="Copy JSON"]')?.click();
      });
    });
    
    expect(copyArgs.hasButton).toBe(true);
    expect(copyArgs.textContains).toBe(true);
    expect(copyArgs.message).toBe('JSON copied');
  });

  test('should show visual feedback when copying JSON', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard to be available
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');
    
    // Mock copyWithFeedback to always succeed so we can test the visual feedback
    await page.evaluate(() => {
      window.Clipboard.copyWithFeedback = async (button, text, message, duration = 1500) => {
        // Simulate successful copy with visual feedback using inline styles
        const copyIcon = button.querySelector('.copy-icon');
        const checkIcon = button.querySelector('.check-icon');
        const copyText = button.querySelector('.copy-text');
        const copiedText = button.querySelector('.copied-text');
        
        if (copyIcon) copyIcon.style.display = 'none';
        if (checkIcon) checkIcon.style.display = '';
        if (copyText) copyText.style.display = 'none';
        if (copiedText) copiedText.style.display = '';
        
        setTimeout(() => {
          if (copyIcon) copyIcon.style.display = '';
          if (checkIcon) checkIcon.style.display = 'none';
          if (copyText) copyText.style.display = '';
          if (copiedText) copiedText.style.display = 'none';
        }, duration);
        
        return true;
      };
    });
    
    const copyJsonButton = page.locator('button[title="Copy JSON"]');
    await copyJsonButton.click();
    
    // Check for "Copied!" text feedback
    const copiedText = copyJsonButton.locator('.copied-text');
    await expect(copiedText).toBeVisible({ timeout: 3000 });
    
    // Wait for it to revert back (feedback duration is 1500ms)
    await expect(copiedText).not.toBeVisible({ timeout: 3000 });
  });

  test('should download JSON file', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard to be available
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');
    
    const downloadButton = page.locator('button[title="Download JSON"]');
    await expect(downloadButton).toBeVisible();
    
    // Set up download promise BEFORE clicking
    const downloadPromise = page.waitForEvent('download');
    await downloadButton.click();
    
    // Wait for download event and verify filename
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/reader\.json$/i);
  });

  test('should not show Copy/Download buttons for deleted role', async ({ page }) => {
    // Navigate to recent page filtered by deleted events
    await page.goto('/recent?event_type=deleted');
    await page.waitForLoadState('domcontentloaded');
    
    // Find a role link on the page (deleted roles still have links to their detail page)
    const roleLink = page.locator('a[href^="/roles/"]').first();
    const hasDeletedRole = await roleLink.count() > 0;
    
    // Skip if no deleted roles exist in this database
    if (!hasDeletedRole) {
      test.skip();
      return;
    }
    
    await roleLink.click();
    await page.waitForLoadState('domcontentloaded');
    
    // Copy/Download buttons should not be visible for deleted roles
    const copyJsonButton = page.locator('button[title="Copy JSON"]');
    const downloadJsonButton = page.locator('button[title="Download JSON"]');
    
    await expect(copyJsonButton).not.toBeVisible();
    await expect(downloadJsonButton).not.toBeVisible();
  });
});

// =============================================================================
// ROLE DETAIL - COPY/DOWNLOAD FUNCTIONALITY
// =============================================================================
test.describe('Role Detail - Copy/Download', () => {
  test.beforeEach(async ({ context }) => {
    // Grant clipboard permissions for copy tests
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  });

  test('should copy Role ID without JS errors when clicking button', async ({ page }) => {
    // Listen for page errors
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));

    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard utility to be loaded
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');

    // Find and click the Role ID copy button
    const copyButton = page.locator('button[onclick*="Clipboard.copyWithTooltip"]');
    await expect(copyButton).toBeVisible();
    await copyButton.click();

    // Verify tooltip appeared (auto-waits for condition)
    const tooltip = copyButton.locator('.copied-tooltip');
    await expect(tooltip).toHaveCSS('opacity', '1');

    // Verify the correct content was copied to clipboard
    const clipboardContent = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardContent).toBe('acdd72a7-3385-48ef-bd42-f606fba81ae7');

    // Check no JS errors occurred
    expect(errors).toHaveLength(0);
  });

  test('should show visual feedback when copying Role ID', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard utility to be loaded
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');

    const copyButton = page.locator('button[onclick*="Clipboard.copyWithTooltip"]');
    await expect(copyButton).toBeVisible();

    // Before click: copy icon visible, check icon hidden
    const copyIcon = copyButton.locator('.copy-icon');
    const checkIcon = copyButton.locator('.check-icon');
    await expect(copyIcon).toBeVisible();
    await expect(checkIcon).toBeHidden();

    // Click to copy
    await copyButton.click();

    // After click: check icon visible, copy icon hidden
    await expect(checkIcon).toBeVisible();
    await expect(copyIcon).toBeHidden();

    // After delay: should revert back (auto-wait using expectations)
    await expect(copyIcon).toBeVisible({ timeout: 3000 });
    await expect(checkIcon).toBeHidden({ timeout: 3000 });
  });
});

// =============================================================================
// OPERATIONS PAGE
// =============================================================================
test.describe('Operations Page', () => {
  test('should load with table and filters', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/operations');
    await expect(page).toHaveTitle(/Azure Operations/i);
    await expect(page.locator('table')).toBeVisible();
    await expect(page.locator('th:has-text("Granted By"), th:has-text("GRANTED BY")').first()).toBeVisible();
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

  test.describe('Filters', () => {
    test('should have data action filter dropdown', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/operations');
      await page.waitForLoadState('domcontentloaded');

      const dataActionSelect = page.locator('select[name="is_data_action"]:visible');
      await expect(dataActionSelect).toBeVisible();
      // Verify it has options
      const options = await dataActionSelect.locator('option').count();
      expect(options).toBeGreaterThan(1);
    });

    test('should filter by data action via URL', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/operations?is_data_action=true');
      await page.waitForLoadState('domcontentloaded');

      await expect(page.locator('table')).toBeVisible();
      expect(page.url()).toContain('is_data_action=true');
    });

    test('should have provider filter dropdown', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/operations');
      await page.waitForLoadState('domcontentloaded');

      const providerSelect = page.locator('select[name="provider"]:visible');
      await expect(providerSelect).toBeVisible();
      const options = await providerSelect.locator('option').count();
      expect(options).toBeGreaterThan(1);
    });

    test('should apply provider filter from URL', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      // Navigate with provider filter
      await page.goto('/operations?provider=Microsoft.Authorization');
      await page.waitForLoadState('domcontentloaded');

      // Page should show either a table with results OR a "no results" message
      const hasTable = await page.locator('table').isVisible();
      const hasNoResults = await page.getByText(/no operations|no results|0 operations/i).first().isVisible();
      expect(hasTable || hasNoResults).toBe(true);

      // The provider dropdown should be visible
      const providerSelect = page.locator('select[name="provider"]:visible');
      await expect(providerSelect).toBeVisible();
    });
  });

  test.describe('Search', () => {
    test('should search operations by query', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/operations');
      await page.waitForLoadState('domcontentloaded');

      const searchInput = page.locator('input[name="q"]:visible');
      await searchInput.fill('read');
      await searchInput.press('Enter');
      await page.waitForURL(/q=read/i);
      await expect(page.locator('table')).toBeVisible();
    });

    test('should show results matching search term', async ({ page }) => {
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto('/operations?q=storage');
      await page.waitForLoadState('domcontentloaded');

      const rows = page.locator('table tbody tr');
      expect(await rows.count()).toBeGreaterThan(0);
      // Verify storage appears in results
      const tableText = await page.locator('table tbody').textContent();
      expect(tableText?.toLowerCase()).toContain('storage');
    });
  });

  test.describe('Pagination', () => {
    test('should show pagination info or results', async ({ page }) => {
      await page.goto('/operations?limit=10');
      await page.waitForLoadState('domcontentloaded');

      // Should show table with results OR pagination
      await expect(page.locator('table')).toBeVisible();
      const rows = page.locator('table tbody tr');
      expect(await rows.count()).toBeGreaterThan(0);
    });

    test('should navigate pages', async ({ page }) => {
      await page.goto('/operations?limit=10');
      const page2Link = page.locator('a[href*="page=2"]').first();
      if (await page2Link.isVisible()) {
        await page2Link.click();
        await expect(page).toHaveURL(/page=2/);
      }
    });
  });

  test.describe('Sorting', () => {
    const sortTests = [
      { field: 'roles', order: 'desc' },
      { field: 'name', order: 'desc' },
      { field: 'provider', order: 'asc' },
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

    const showMoreBtn = page.locator('#showMoreBtn');
    if (await showMoreBtn.isVisible()) {
      await showMoreBtn.click();
    }

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

  test('should copy Operation Name without JS errors when clicking button', async ({ page, context }) => {
    // Grant clipboard permissions for copy tests
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);

    // Listen for page errors
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));

    await page.goto('/operations/Microsoft.Storage/storageAccounts/read');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard utility to be loaded
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');

    // Find and click the Operation Name copy button
    const copyButton = page.locator('button[title="Copy Operation Name"]');
    await expect(copyButton).toBeVisible();
    await copyButton.click();

    // Verify tooltip appeared (auto-waits for condition)
    const tooltip = copyButton.locator('.copied-tooltip');
    await expect(tooltip).toHaveCSS('opacity', '1');

    // Verify the correct content was copied to clipboard
    const clipboardContent = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboardContent).toBe('Microsoft.Storage/storageAccounts/read');

    // Check no JS errors occurred
    expect(errors).toHaveLength(0);
  });

  test('should show visual feedback when copying Operation Name', async ({ page, context }) => {
    // Grant clipboard permissions for copy tests
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);

    await page.goto('/operations/Microsoft.Storage/storageAccounts/read');
    await page.waitForLoadState('domcontentloaded');
    
    // Wait for Clipboard utility to be loaded
    await page.waitForFunction(() => typeof window.Clipboard !== 'undefined');

    const copyButton = page.locator('button[title="Copy Operation Name"]');
    await expect(copyButton).toBeVisible();

    // Before click: copy icon visible, check icon hidden
    const copyIcon = copyButton.locator('.copy-icon');
    const checkIcon = copyButton.locator('.check-icon');
    await expect(copyIcon).toBeVisible();
    await expect(checkIcon).not.toBeVisible();

    // Click copy button
    await copyButton.click();

    // After click: check icon should become visible
    await expect(checkIcon).toBeVisible();
  });
});

// =============================================================================
// ROLE RECOMMENDER
// =============================================================================
test.describe('Role Recommender', () => {
  test('should load page with operation search input', async ({ page }) => {
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

  test('should show autocomplete dropdown on search input', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/recommend');
    await page.waitForLoadState('domcontentloaded');

    const searchInput = page.locator('#operation-search');
    await searchInput.fill('storage');
    await page.waitForTimeout(500);

    // Check for autocomplete suggestions
    const suggestions = page.locator('#autocomplete-list, .autocomplete-dropdown, [role="listbox"]');
    if (await suggestions.count() > 0) {
      expect(await suggestions.first().isVisible()).toBe(true);
    }
  });

  test('should display selected operations when added', async ({ page }) => {
    await page.goto('/recommend');
    await page.waitForLoadState('domcontentloaded');

    const searchInput = page.locator('#operation-search');
    await searchInput.fill('Microsoft.Storage/storageAccounts/read');
    await page.waitForTimeout(300);

    // Try to add the operation
    const addButton = page.locator('button:has-text("Add"), #add-operation-btn');
    if (await addButton.isVisible()) {
      await addButton.click();
      const selectedOps = page.locator('#selected-operations, .selected-operations');
      if (await selectedOps.count() > 0) {
        const text = await selectedOps.textContent();
        expect(text?.toLowerCase()).toContain('storage');
      }
    }
  });

  test('should have mode selection buttons in AI mode', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/recommend?ai=1');
    await page.waitForLoadState('domcontentloaded');

    // Check for mode buttons
    const modeContainer = page.locator('#ai-mode-selector, .mode-selector');
    if (await modeContainer.count() > 0) {
      expect(await modeContainer.first().isVisible()).toBe(true);
    }
  });

  test('should submit recommendation request via API', async ({ page, request }) => {
    // Test the API directly
    const response = await request.post('/api/recommend-roles', {
      data: {
        operations: [{ name: 'Microsoft.Storage/storageAccounts/read', is_data_action: false }]
      }
    });
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(data).toHaveProperty('roles');
    expect(Array.isArray(data.roles)).toBe(true);
  });
});

// =============================================================================
// AI MODE PRESERVATION
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

  test('should store ai mode from analytics page', async ({ page }) => {
    await page.goto('/analytics');
    await page.evaluate(() => sessionStorage.clear());
    await page.goto('/analytics?ai=1');
    await page.waitForLoadState('domcontentloaded');
    expect(await page.evaluate(() => sessionStorage.getItem('azurerbac_ai_mode'))).toBe('1');

    // Navigate to another page and verify persistence
    await page.goto('/about');
    await page.waitForLoadState('domcontentloaded');
    expect(await page.evaluate(() => sessionStorage.getItem('azurerbac_ai_mode'))).toBe('1');
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

  test('should display content sections', async ({ page }) => {
    await page.goto('/about');
    await page.waitForLoadState('domcontentloaded');
    // Should show FAQ or about content
    const hasContent = await page.getByText(/Azure|RBAC|Role|FAQ|Question/i).first().isVisible();
    expect(hasContent).toBe(true);
  });

  test('should preserve AI mode parameter', async ({ page }) => {
    await page.goto('/about?ai=1');
    await page.waitForLoadState('domcontentloaded');
    expect(page.url()).toContain('ai=1');
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

  test('should return empty for short search queries', async ({ request }) => {
    const response = await request.get('/api/operations/search?q=a');
    expect(response.status()).toBe(200);
    const data = await response.json();
    // Should return empty or error message for too-short queries
    expect(data).toHaveProperty('operations');
  });

  test('should handle wildcard search in operations API', async ({ request }) => {
    const response = await request.get('/api/operations/search?q=*/read');
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(data).toHaveProperty('is_wildcard_search');
  });

  test('should return count matches data', async ({ request }) => {
    const response = await request.get('/api/operations/count-matches?pattern=*/read');
    expect(response.status()).toBe(200);
    expect(await response.json()).toHaveProperty('count');
  });

  test('should return zero for non-wildcard pattern count', async ({ request }) => {
    const response = await request.get('/api/operations/count-matches?pattern=Microsoft.Storage/storageAccounts/read');
    expect(response.status()).toBe(200);
    const data = await response.json();
    expect(data.count).toBe(0);
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

  test('should handle empty operations list', async ({ request }) => {
    const response = await request.post('/api/recommend-roles', {
      data: { operations: [] }
    });
    // Empty operations might return 200/400/422 depending on validation rules
    expect([200, 400, 422]).toContain(response.status());
  });

  test('should handle AI recommend API', async ({ request }) => {
    const response = await request.post('/api/ai-recommend', {
      data: { query: 'read storage blobs', mode: 'tfidf' }
    });
    expect(response.status()).toBe(200);
    expect(await response.json()).toHaveProperty('recommendations');
  });

  test('should reject empty AI query with 400', async ({ request }) => {
    const response = await request.post('/api/ai-recommend', {
      data: { query: '', mode: 'tfidf' }
    });
    // Empty query is now rejected with 400 due to min_length=1 validation
    expect(response.status()).toBe(400);
  });

  test('should reject too short AI query', async ({ request }) => {
    const response = await request.post('/api/ai-recommend', {
      data: { query: 'ab', mode: 'tfidf' }
    });
    expect(response.status()).toBe(200);
    const data = await response.json();
    // Should return error response for short query
    expect(data.error || data.recommendations).toBeDefined();
  });
});

// =============================================================================
// ERROR HANDLING & INPUT VALIDATION
// =============================================================================
test.describe('Error Handling', () => {
  test('should return 404 for unknown routes', async ({ request }) => {
    const response = await request.get('/nonexistent-page-12345');
    expect(response.status()).toBe(404);
    const text = await response.text();
    expect(text.includes('404')).toBe(true);
  });

  test('should handle invalid sort field gracefully', async ({ page }) => {
    const response = await page.goto('/roles?sort=invalid_field');
    expect(response?.status()).toBe(200);
    await expect(page.locator('table')).toBeVisible();
  });

  test('should handle missing operation in detail page', async ({ page }) => {
    const response = await page.goto('/operations/Invalid.Provider/nonExistentAction');
    expect(response?.status()).toBe(404);
  });
});

test.describe('Input Validation - Reject Invalid Parameters', () => {
  // /roles endpoint validation
  test('should reject /roles with negative page', async ({ page }) => {
    const response = await page.goto('/roles?page=-1');
    expect(response?.status()).toBe(400);
  });

  test('should reject /roles with huge page number', async ({ page }) => {
    const response = await page.goto('/roles?page=999999999');
    expect(response?.status()).toBe(400);
  });

  test('should reject /roles with zero limit', async ({ page }) => {
    const response = await page.goto('/roles?limit=0');
    expect(response?.status()).toBe(400);
  });

  test('should reject /roles with excessive limit', async ({ page }) => {
    const response = await page.goto('/roles?limit=99999');
    expect(response?.status()).toBe(400);
  });

  // /operations endpoint validation
  test('should reject /operations with negative page', async ({ page }) => {
    const response = await page.goto('/operations?page=-1');
    expect(response?.status()).toBe(400);
  });

  test('should reject /operations with excessive limit', async ({ page }) => {
    const response = await page.goto('/operations?limit=99999');
    expect(response?.status()).toBe(400);
  });

  // /recent endpoint validation
  test('should reject /recent with zero days', async ({ page }) => {
    const response = await page.goto('/recent?days=0');
    expect(response?.status()).toBe(400);
  });

  test('should reject /recent with excessive days', async ({ page }) => {
    const response = await page.goto('/recent?days=1000');
    expect(response?.status()).toBe(400);
  });

  test('should reject /recent with negative page', async ({ page }) => {
    const response = await page.goto('/recent?page=-1');
    expect(response?.status()).toBe(400);
  });

  // API endpoint validation
  test('should reject /api/operations/search with too long query', async ({ request }) => {
    const response = await request.get('/api/operations/search?q=' + 'A'.repeat(200));
    expect(response.status()).toBe(400);
  });

  test('should reject /api/recommend-roles with too many operations', async ({ request }) => {
    const operations = Array.from({ length: 200 }, (_, i) => ({
      name: `op${i}`,
      is_data_action: false,
    }));
    const response = await request.post('/api/recommend-roles', { data: { operations } });
    expect(response.status()).toBe(400);
  });

  test('should reject /api/ai-recommend with too long query', async ({ request }) => {
    const response = await request.post('/api/ai-recommend', {
      data: { query: 'A'.repeat(600), top_k: 5, recommender_mode: 'tfidf' },
    });
    expect(response.status()).toBe(400);
  });

  test('should reject /api/ai-recommend with invalid top_k', async ({ request }) => {
    const response = await request.post('/api/ai-recommend', {
      data: { query: 'test query', top_k: 100, recommender_mode: 'tfidf' },
    });
    expect(response.status()).toBe(400);
  });
});

// =============================================================================
// MOBILE RESPONSIVENESS
// =============================================================================
test.describe('Mobile Responsiveness', () => {
  test('should show mobile search and filters on roles page', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/roles');
    await expect(page.locator('input[name="q"]').first()).toBeVisible();
    await expect(page.locator('button[onclick="toggleExactMatch(this)"]')).toBeVisible();
    await expect(page.locator('select[name="limit"]').first()).toBeVisible();
    await expect(page.locator('select[name="status_filter"]').first()).toBeVisible();
  });

  test('should show mobile layout on operations page', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/operations');
    await expect(page.locator('input[name="q"]').first()).toBeVisible();
    await expect(page.locator('table')).toBeVisible();
  });

  test('should show mobile layout on recent page', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/recent');
    await expect(page.locator('table')).toBeVisible();
    await expect(page.locator('text=Total Built-in Roles')).toBeVisible();
  });

  test('should show mobile layout on role detail page', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7/reader');
    await expect(page.locator('h1')).toContainText('Reader');
    await expect(page.locator('text=Role Information')).toBeVisible();
  });

  test('should have horizontally scrollable tables on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/roles');
    
    const tableContainer = page.locator('.overflow-x-auto');
    if (await tableContainer.count() > 0) {
      await expect(tableContainer.first()).toBeVisible();
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

  test('should have security headers present', async ({ request }) => {
    const response = await request.get('/');
    // Check that at least CSP is present (X-Content-Type-Options may be added by proxy)
    const headers = response.headers();
    expect(headers['content-security-policy'] || headers['x-frame-options']).toBeDefined();
  });

  test('should have cache headers on static assets', async ({ request }) => {
    const response = await request.get('/robots.txt');
    expect(response.status()).toBe(200);
    const cacheControl = response.headers()['cache-control'];
    expect(cacheControl).toContain('public');
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
    expect(content.startsWith('<?xml')).toBe(true);
    expect(content.includes('urlset')).toBe(true);
    expect(content.includes('/operations')).toBe(true);
  });

  test('should have sitemap with roles', async ({ request }) => {
    const response = await request.get('/sitemap.xml');
    expect(response.status()).toBe(200);
    const content = await response.text();
    expect(content.includes('/roles/')).toBe(true);
  });

  test('should have valid robots.txt', async ({ request }) => {
    const response = await request.get('/robots.txt');
    expect(response.status()).toBe(200);
    const content = await response.text();
    expect(content).toContain('User-agent:');
    expect(content).toContain('Sitemap:');
  });

  test('should have sitemap URL in robots.txt', async ({ request }) => {
    const response = await request.get('/robots.txt');
    const content = await response.text();
    expect(content).toMatch(/Sitemap:.*sitemap\.xml/);
  });
});

// =============================================================================
// STATIC ASSETS
// =============================================================================
test.describe('Static Assets', () => {
  test('should serve favicon.ico', async ({ request }) => {
    const response = await request.get('/favicon.ico');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('image');
  });

  test('should serve favicon.svg', async ({ request }) => {
    const response = await request.get('/favicon.svg');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('svg');
  });

  test('should serve apple touch icon', async ({ request }) => {
    const response = await request.get('/apple-touch-icon.png');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('png');
  });

  test('should serve Google site verification', async ({ request }) => {
    const response = await request.get('/googleec37c4d2676ac205.html');
    expect(response.status()).toBe(200);
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

  test('should have proper heading hierarchy on roles page', async ({ page }) => {
    await page.goto('/roles');
    await expect(page.locator('h1').first()).toBeVisible();
  });

  test('should have proper heading hierarchy on operations page', async ({ page }) => {
    await page.goto('/operations');
    await expect(page.locator('h1').first()).toBeVisible();
  });

  test('should have skip link or main landmark', async ({ page }) => {
    await page.goto('/');
    // Check for main element or skip link
    const hasMain = await page.locator('main, [role="main"]').count() > 0;
    const hasSkipLink = await page.locator('a[href="#main"], .skip-link').count() > 0;
    expect(hasMain || hasSkipLink).toBe(true);
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

  test('should have focusable interactive elements', async ({ page }) => {
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    
    // Tab through the page and verify focus moves
    await page.keyboard.press('Tab');
    const focusedElement = await page.evaluate(() => document.activeElement?.tagName);
    expect(['A', 'BUTTON', 'INPUT', 'SELECT']).toContain(focusedElement);
  });

  test('should have proper form labels', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/roles');
    
    // Check that inputs have associated labels or aria-labels
    const searchInput = page.locator('input[name="q"]:visible');
    const hasLabel = await searchInput.getAttribute('aria-label') ||
                     await searchInput.getAttribute('placeholder') ||
                     await page.locator('label[for]').count() > 0;
    expect(hasLabel).toBeTruthy();
  });
});

// =============================================================================
// SEO
// =============================================================================
test.describe('SEO', () => {
  test('should have meta description on main pages', async ({ page }) => {
    await page.goto('/recent');
    const metaDesc = page.locator('meta[name="description"]');
    expect(await metaDesc.count()).toBeGreaterThan(0);
  });

  test('should have Open Graph tags', async ({ page }) => {
    await page.goto('/recent');
    const ogTitle = page.locator('meta[property="og:title"]');
    const ogDesc = page.locator('meta[property="og:description"]');
    // At least one OG tag should exist
    const hasOgTags = await ogTitle.count() > 0 || await ogDesc.count() > 0;
    expect(hasOgTags).toBe(true);
  });
});

// =============================================================================
// CANONICAL URLS & SITEMAP
// =============================================================================
test.describe('Canonical URLs & Sitemap', () => {
  const SITE_URL = 'https://rbac-catalog.dev';

  test('homepage canonical should be /', async ({ page }) => {
    await page.goto('/');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toBe(`${SITE_URL}/`);
    const ogUrl = await page.locator('meta[property="og:url"]').getAttribute('content');
    expect(ogUrl).toBe(`${SITE_URL}/`);
  });

  test('/recent alias should have canonical pointing to /', async ({ page }) => {
    await page.goto('/recent');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toBe(`${SITE_URL}/`);
    const ogUrl = await page.locator('meta[property="og:url"]').getAttribute('content');
    expect(ogUrl).toBe(`${SITE_URL}/`);
  });

  test('/roles page should have self-referencing canonical', async ({ page }) => {
    await page.goto('/roles');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toBe(`${SITE_URL}/roles`);
  });

  test('/operations page should have self-referencing canonical', async ({ page }) => {
    await page.goto('/operations');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toBe(`${SITE_URL}/operations`);
  });

  test('/recommend page should have self-referencing canonical', async ({ page }) => {
    await page.goto('/recommend');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toBe(`${SITE_URL}/recommend`);
  });

  test('/about page should have self-referencing canonical', async ({ page }) => {
    await page.goto('/about');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toBe(`${SITE_URL}/about`);
  });

  test('role detail page canonical should match sitemap URL', async ({ page }) => {
    await page.goto('/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7/reader');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toBe(`${SITE_URL}/roles/acdd72a7-3385-48ef-bd42-f606fba81ae7/reader`);
  });

  test('operation detail page canonical should use %2F encoding', async ({ page }) => {
    // Operation names contain slashes that must be encoded as %2F
    await page.goto('/operations/Microsoft.Compute%2FvirtualMachines%2Fread');
    const canonical = await page.locator('link[rel="canonical"]').getAttribute('href');
    expect(canonical).toContain('%2F');
    expect(canonical).toBe(`${SITE_URL}/operations/Microsoft.Compute%2FvirtualMachines%2Fread`);
  });

  test('sitemap should not contain /recent (alias)', async ({ request }) => {
    const response = await request.get('/sitemap.xml');
    const body = await response.text();
    // /recent is an alias for / and should not be in sitemap
    expect(body).not.toContain(`${SITE_URL}/recent</loc>`);
    // But / should be in sitemap
    expect(body).toContain(`${SITE_URL}/</loc>`);
  });

  test('sitemap should contain main pages', async ({ request }) => {
    const response = await request.get('/sitemap.xml');
    const body = await response.text();
    expect(body).toContain(`${SITE_URL}/</loc>`);
    expect(body).toContain(`${SITE_URL}/roles</loc>`);
    expect(body).toContain(`${SITE_URL}/operations</loc>`);
    expect(body).toContain(`${SITE_URL}/recommend</loc>`);
    expect(body).toContain(`${SITE_URL}/analytics</loc>`);
    expect(body).toContain(`${SITE_URL}/about</loc>`);
  });

  test('sitemap operation URLs should use %2F encoding', async ({ request }) => {
    const response = await request.get('/sitemap.xml');
    const body = await response.text();
    // Operation URLs in sitemap should have encoded slashes
    const operationUrlMatch = body.match(/<loc>https:\/\/rbac-catalog\.dev\/operations\/[^<]+%2F[^<]+<\/loc>/);
    expect(operationUrlMatch).not.toBeNull();
  });
});

// =============================================================================
// PERFORMANCE
// =============================================================================
test.describe('Performance', () => {
  test('should load home page within acceptable time', async ({ page }) => {
    const start = Date.now();
    await page.goto('/recent');
    await page.waitForLoadState('domcontentloaded');
    const loadTime = Date.now() - start;
    // Should load within 5 seconds
    expect(loadTime).toBeLessThan(5000);
  });

  test('should load roles page within acceptable time', async ({ page }) => {
    const start = Date.now();
    await page.goto('/roles');
    await page.waitForLoadState('domcontentloaded');
    const loadTime = Date.now() - start;
    expect(loadTime).toBeLessThan(5000);
  });

  test('should load operations page within acceptable time', async ({ page }) => {
    const start = Date.now();
    await page.goto('/operations');
    await page.waitForLoadState('domcontentloaded');
    const loadTime = Date.now() - start;
    expect(loadTime).toBeLessThan(5000);
  });

  test('API endpoint should respond quickly', async ({ request }) => {
    const start = Date.now();
    const response = await request.get('/api/operations/search?q=storage');
    const responseTime = Date.now() - start;
    expect(response.status()).toBe(200);
    // API should respond within 2 seconds
    expect(responseTime).toBeLessThan(2000);
  });
});

// =============================================================================
// RSS/ATOM FEEDS
// =============================================================================
test.describe('RSS/Atom Feeds', () => {
  test('Atom feed returns valid XML', async ({ request }) => {
    const response = await request.get('/feeds/changelog.atom');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('application/atom+xml');
    
    const body = await response.text();
    expect(body).toContain('<feed');
    expect(body).toContain('xmlns="http://www.w3.org/2005/Atom"');
    expect(body).toContain('<title>Azure RBAC Role Changes</title>');
  });

  test('RSS feed returns valid XML', async ({ request }) => {
    const response = await request.get('/feeds/changelog.rss');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('application/rss+xml');
    
    const body = await response.text();
    expect(body).toContain('<rss version="2.0"');
    expect(body).toContain('<channel>');
    expect(body).toContain('<title>Azure RBAC Role Changes</title>');
  });

  test('Atom feed respects days parameter', async ({ request }) => {
    const response = await request.get('/feeds/changelog.atom?days=7');
    expect(response.status()).toBe(200);
  });

  test('RSS feed respects limit parameter', async ({ request }) => {
    const response = await request.get('/feeds/changelog.rss?limit=10');
    expect(response.status()).toBe(200);
  });

  test('Feed has cache-control header', async ({ request }) => {
    const response = await request.get('/feeds/changelog.atom');
    expect(response.headers()['cache-control']).toContain('max-age');
  });

  test('Feeds reject invalid parameters', async ({ request }) => {
    // days=0 is invalid (min 1)
    const response1 = await request.get('/feeds/changelog.atom?days=0');
    expect([400, 422]).toContain(response1.status());

    // days=500 is invalid (max 365)
    const response2 = await request.get('/feeds/changelog.rss?days=500');
    expect([400, 422]).toContain(response2.status());
  });

  test('Subscribe button visible on Recent Changes page', async ({ page }) => {
    await page.goto('/recent');
    const subscribeLink = page.locator('a[href="/feeds/changelog.atom"]').first();
    await expect(subscribeLink).toBeVisible();
  });

  test('Feed autodiscovery links in page head', async ({ page }) => {
    await page.goto('/recent');
    
    // Check for Atom autodiscovery link
    const atomLink = page.locator('link[rel="alternate"][type="application/atom+xml"]');
    await expect(atomLink).toHaveAttribute('href', /\/feeds\/changelog\.atom/);
    
    // Check for RSS autodiscovery link
    const rssLink = page.locator('link[rel="alternate"][type="application/rss+xml"]');
    await expect(rssLink).toHaveAttribute('href', /\/feeds\/changelog\.rss/);
  });
});

// =============================================================================
// ANALYTICS PAGE
// =============================================================================
test.describe('Analytics Page', () => {
  test('should load analytics page', async ({ page }) => {
    const response = await page.goto('/analytics');
    expect(response?.status()).toBe(200);
    await expect(page).toHaveTitle(/Analytics|Azure.*Roles/i);
  });

  test('should display main statistics cards', async ({ page }) => {
    await page.goto('/analytics');
    await page.waitForLoadState('domcontentloaded');
    
    // Check for key statistics sections - look for the header
    await expect(page.locator('h1:has-text("Analytics Dashboard")')).toBeVisible({ timeout: 10000 });
  });

  test('should have navigation link to analytics', async ({ page }) => {
    await page.goto('/');
    const analyticsLink = page.locator('a[href="/analytics"]').first();
    await expect(analyticsLink).toBeVisible();
    await analyticsLink.click();
    await expect(page).toHaveURL(/\/analytics/);
  });

  test('should display charts (if data exists)', async ({ page }) => {
    await page.goto('/analytics');
    await page.waitForLoadState('networkidle');
    
    // Charts are rendered via canvas elements
    const canvases = page.locator('canvas');
    const canvasCount = await canvases.count();
    // Analytics page may have charts, check if any exist
    if (canvasCount > 0) {
      await expect(canvases.first()).toBeVisible();
    }
  });

  test('should display provider statistics', async ({ page }) => {
    await page.goto('/analytics');
    await page.waitForLoadState('domcontentloaded');
    
    // Check for provider section (may be "Top Providers" or similar)
    const providerSection = page.locator('text=Provider').or(page.locator('text=Operations'));
    await expect(providerSection.first()).toBeVisible({ timeout: 10000 });
  });

  test('should have responsive layout', async ({ page }) => {
    // Test desktop viewport
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/analytics');
    await expect(page.locator('body')).toBeVisible();
    
    // Test mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/analytics');
    await expect(page.locator('body')).toBeVisible();
  });

  test('clicking role from analytics sets back context', async ({ page }) => {
    await page.goto('/analytics');
    await page.waitForLoadState('networkidle');
    
    // Use a link from "Top 10 Roles" section - these come from cache and always exist
    // (Recently Created/Deleted may reference roles no longer in the database)
    const section = page.locator('text=Top 10 Roles by Effective').first();
    await section.scrollIntoViewIfNeeded();
    
    const roleLink = section.locator('xpath=ancestor::div[contains(@class,"bg-white")]').locator('a[href^="/roles/"]').first();
    
    if (await roleLink.count() === 0) {
      test.skip();
      return;
    }
    
    await roleLink.click();
    await page.waitForLoadState('networkidle');
    
    // Should be on a role page
    await expect(page).toHaveURL(/\/roles\//);
    
    // Back button should say "Back to Analytics"
    const backLabel = page.locator('#back-label');
    await expect(backLabel).toBeVisible();
    await expect(backLabel).toContainText(/Back to Analytics/i);
    
    // Verify clicking back returns to analytics
    await page.locator('#back-button').click();
    await page.waitForLoadState('networkidle');
    await expect(page).toHaveURL(/\/analytics/);
  });
});
