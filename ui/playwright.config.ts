import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for the data-governance SPA (ADR-0019).
 *
 * Auth is out of scope (open behind the Gateway), so there's no login setup —
 * a single chromium project. By default Playwright builds the SPA and serves
 * the production `dist/` with `vite preview` (base '/ui/'), so the specs
 * exercise the same asset layout + basename the backend serves. Point at a live
 * deployment instead with:
 *
 *   DG_UI_URL=http://dg.localtest.me:8080 npm run test:e2e
 */
const BASE = process.env.DG_UI_URL || 'http://localhost:4173';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['html', { outputFolder: 'playwright-report' }], ['list']],
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    ignoreHTTPSErrors: true,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  // Build + preview the production SPA unless pointed at a live deployment.
  // `vite preview` honours base '/ui/', so the app lives under /ui/ just like
  // in the backend-served image.
  webServer: process.env.DG_UI_URL
    ? undefined
    : {
        command: 'npm run build && npm run preview -- --port 4173 --strictPort',
        url: 'http://localhost:4173/ui/',
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
});
