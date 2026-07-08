import { test, expect } from '@playwright/test';

/**
 * Smoke coverage for the SPA's routing + shell (ADR-0019). These run against
 * the production `vite preview` build (or a live DG_UI_URL) and assert the
 * chrome that renders without a backend — the recent-traces landing shell and
 * the trace-detail three-way switcher on a deep link. Data-dependent behaviour
 * is covered by Vitest with mocked fetches; end-to-end-with-data is exercised
 * by the cluster verification in the repo CLAUDE.md.
 *
 * The app is mounted under /ui/ (React Router basename), matching how the
 * backend serves it.
 */

test('recent-traces landing renders the app shell', async ({ page }) => {
  await page.goto('/ui/');
  // Masthead brand + page heading render regardless of the /api/traces result.
  await expect(page.getByRole('heading', { name: 'Recent traces' })).toBeVisible();
  await expect(page.getByText('Data Governance')).toBeVisible();
  // The time-window control is part of the static shell.
  await expect(page.getByLabel('Time window')).toBeVisible();
});

test('deep link /ui/traces/:tid resolves to the trace-detail switcher', async ({ page }) => {
  // A pasted/bookmarked deep link must resolve client-side (catch-all →
  // index.html → React Router), not 404.
  await page.goto('/ui/traces/some-trace-id');
  // The three-way switcher renders even before (or without) trace data.
  await expect(page.getByRole('tab', { name: 'Span tree' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Interaction flow' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Graph' })).toBeVisible();
  // Span tree is the default active tab.
  await expect(page.getByRole('tab', { name: 'Span tree' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('switching to the Graph tab activates it', async ({ page }) => {
  await page.goto('/ui/traces/some-trace-id');
  await page.getByRole('tab', { name: 'Graph' }).click();
  await expect(page.getByRole('tab', { name: 'Graph' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});
