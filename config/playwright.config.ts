import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '../e2e',
  outputDir: '../test-results',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,

  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,

  timeout: process.env.CI ? 20000 : 10000,
  expect: { timeout: 5000 },

  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: '../playwright-report' }]]
    : 'list',

  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:8000',
    headless: true,
    trace: 'on-first-retry',
    actionTimeout: process.env.CI ? 10000 : 5000,
  },

  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
  ],
});
