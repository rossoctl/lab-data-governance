import { test, expect } from '@playwright/test';

/**
 * Smoke coverage for the SPA's routing + shell (ADR-0019, and the URL-as-state
 * contract in ADR-0017's addendum). These run against the production `vite
 * preview` build (or a live DG_UI_URL) and assert the chrome that renders
 * without a backend — the recent-traces landing shell and the trace-detail
 * two-way switcher on a deep link — plus that every UI state has its own URL
 * (reload restores the tab; bare paths canonicalise). Data-dependent behaviour
 * is covered by Vitest with mocked fetches; end-to-end-with-data is exercised
 * by the cluster verification in the repo CLAUDE.md.
 *
 * The app is mounted under /ui/ (React Router basename), matching how the
 * backend serves it.
 */

test('root /ui/ redirects to the canonical /ui/traces list', async ({ page }) => {
  await page.goto('/ui/');
  await expect(page).toHaveURL(/\/ui\/traces$/);
  await expect(page.getByRole('heading', { name: 'Recent traces' })).toBeVisible();
});

test('recent-traces list renders the app shell', async ({ page }) => {
  await page.goto('/ui/traces');
  // Masthead brand + page heading render regardless of the /api/traces result.
  await expect(page.getByRole('heading', { name: 'Recent traces' })).toBeVisible();
  await expect(page.getByText('Data Governance')).toBeVisible();
  // The time-window control is part of the static shell.
  await expect(page.getByLabel('Time window')).toBeVisible();
});

test('a non-default time window round-trips through the URL and survives reload', async ({ page }) => {
  await page.goto('/ui/traces');
  await page.getByLabel('Time window').selectOption('all');
  await expect(page).toHaveURL(/\/ui\/traces\?window=all$/);
  await page.reload();
  // Reload restores the window from the URL — the select still shows "all".
  await expect(page.getByLabel('Time window')).toHaveValue('all');
  await expect(page).toHaveURL(/\/ui\/traces\?window=all$/);
});

test('deep link /ui/traces/:tid/spans resolves to the trace-detail switcher', async ({ page }) => {
  // A pasted/bookmarked deep link must resolve client-side (catch-all →
  // index.html → React Router), not 404.
  await page.goto('/ui/traces/some-trace-id/spans');
  await expect(page).toHaveURL(/\/ui\/traces\/some-trace-id\/spans/);
  // The two-way switcher renders even before (or without) trace data.
  await expect(page.getByRole('tab', { name: 'Span tree' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Interaction flow' })).toBeVisible();
  // Span tree is the active tab for the /spans segment.
  await expect(page.getByRole('tab', { name: 'Span tree' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('bare /ui/traces/:tid canonicalises to the /spans tab', async ({ page }) => {
  await page.goto('/ui/traces/some-trace-id');
  await expect(page).toHaveURL(/\/ui\/traces\/some-trace-id\/spans$/);
  await expect(page.getByRole('tab', { name: 'Span tree' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('the active tab is a URL segment and survives reload', async ({ page }) => {
  await page.goto('/ui/traces/some-trace-id/spans');
  await page.getByRole('tab', { name: 'Interaction flow' }).click();
  await expect(page).toHaveURL(/\/ui\/traces\/some-trace-id\/flow$/);
  await expect(page.getByRole('tab', { name: 'Interaction flow' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  // The whole point: reload lands back on the same tab because the URL says so.
  await page.reload();
  await expect(page).toHaveURL(/\/ui\/traces\/some-trace-id\/flow$/);
  await expect(page.getByRole('tab', { name: 'Interaction flow' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('the browser back button walks the tab history', async ({ page }) => {
  await page.goto('/ui/traces/some-trace-id/spans');
  await page.getByRole('tab', { name: 'Interaction flow' }).click();
  await expect(page).toHaveURL(/\/flow$/);
  await page.goBack();
  await expect(page).toHaveURL(/\/spans$/);
  await expect(page.getByRole('tab', { name: 'Span tree' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
});
