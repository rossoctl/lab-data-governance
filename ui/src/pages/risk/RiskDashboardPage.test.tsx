import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/renderWithProviders';
import { LocationProbe } from '../../test/LocationProbe';
import { RiskDashboardPage } from './RiskDashboardPage';

describe('RiskDashboardPage', () => {
  it('renders its title without throwing', () => {
    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });
    expect(screen.getByRole('heading', { name: /risk dashboard/i })).toBeInTheDocument();
  });
});

// New coverage for issue #169's dashboard content: all five cards, URL-driven
// window state (mirroring RecentTracesPage's convention), and error/network
// resilience. The test above is untouched — its heading assertion still holds
// against the real content.
describe('RiskDashboardPage content (#169)', () => {
  function summaryBody(overrides: Record<string, unknown> = {}) {
    return {
      window: '24h',
      from: '2026-08-01T00:00:00Z',
      to: '2026-08-02T00:00:00Z',
      agents: { total: 4, risky: 1, risky_pct: 25 },
      users: { total: 2, risky: 0, risky_pct: 0 },
      workflows: { total: 10, risky: 3, risky_pct: 30 },
      evaluated_interactions: { total: 100, risky: 5, risky_pct: 5 },
      rules_fired: { total: 6, critical: 2 },
      computed_at: '2026-08-02T00:00:00Z',
      ...overrides,
    };
  }

  function mockFetchRouter(
    overrides: {
      summary?: Record<string, unknown>;
      // Issue #214: lets a test serve its own /risk/traces page (e.g. an
      // all-none one) without duplicating the whole router.
      traces?: Record<string, unknown>;
    } = {},
  ) {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/risk/metrics/summary')) {
        return { ok: true, status: 200, json: async () => overrides.summary ?? summaryBody() };
      }
      if (url.includes('/risk/metrics/risk-distribution')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            window: '24h',
            distribution: { critical: 1, high: 2, medium: 3, low: 4, none: 90, total: 100 },
            computed_at: '2026-08-02T00:00:00Z',
          }),
        };
      }
      if (url.includes('/risk/metrics/risk-by-category')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ window: '24h', items: [{ category: 'privacy', count: 3 }] }),
        };
      }
      if (url.includes('/risk/metrics/top-rules')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            window: '24h',
            items: [
              {
                rule_id: 'rule-1',
                rule_name: 'Excessive tool use',
                count: 9,
                trace_count: 4,
                risk_level: 'high',
                risk_level_distribution: { high: 9 },
              },
            ],
          }),
        };
      }
      if (url.includes('/risk/traces')) {
        if (overrides.traces) {
          return { ok: true, status: 200, json: async () => overrides.traces };
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            items: [
              {
                trace_risk_id: 'trr-1',
                trace_id: 'trace-1',
                version: 1,
                computed_at: '2026-08-02T00:00:00Z',
                trace_risk_level: 'high',
                trace_enforcement_type: 'block',
                risk_compounding_mode: 'max',
                enforcement_aggregation_mode: 'strictest',
                interaction_count: 3,
                policy_event_count: 2,
                all_entity_ids: [],
                triggered_rule_ids: ['rule-1'],
                overall_confidence: 0.9,
                contributing_interaction_risk_ids: [],
              },
            ],
            next_cursor: null,
          }),
        };
      }
      throw new Error(`Unexpected fetch: ${url}`);
    });
  }

  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('renders all cards once data loads, under their two pane titles', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });

    await waitFor(() => expect(screen.getByText('Agents')).toBeInTheDocument());
    expect(screen.getByText('Risk Analysis')).toBeInTheDocument();
    expect(screen.getByText('Policies and Alerts')).toBeInTheDocument();
    expect(screen.getByText('Risk Distribution')).toBeInTheDocument();
    expect(screen.getByText('Top rules')).toBeInTheDocument();
    expect(screen.getByText('Risk by category')).toBeInTheDocument();
    expect(screen.getByText('Alerts')).toBeInTheDocument();
    expect(screen.getByLabelText(/critical: 1 \(1%\)/i)).toBeInTheDocument();
    expect(screen.queryByText('Users')).not.toBeInTheDocument();
  });

  it('places Top rules in the right pane, above Alerts', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });

    await waitFor(() => expect(screen.getByText('Agents')).toBeInTheDocument());
    const rightPane = screen.getByText('Policies and Alerts').closest('.pf-v5-l-grid__item')!;
    expect(rightPane).toContainElement(screen.getByText('Top rules'));
    expect(rightPane).toContainElement(screen.getByText('Alerts'));

    const topRulesTitle = screen.getByText('Top rules');
    const alertsTitle = screen.getByText('Alerts');
    expect(topRulesTitle.compareDocumentPosition(alertsTitle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const leftPane = screen.getByText('Risk Analysis').closest('.pf-v5-l-grid__item')!;
    expect(leftPane).not.toContainElement(screen.getByText('Top rules'));
  });

  it('changing the window toggle writes ?window=7d and re-requests every endpoint with it', async () => {
    mockFetchRouter();
    renderWithProviders(
      <>
        <RiskDashboardPage />
        <LocationProbe />
      </>,
      { route: '/risk' },
    );

    await waitFor(() => expect(screen.getByText('Agents')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Last 7 days' }));

    await waitFor(() =>
      expect(screen.getByTestId('location')).toHaveTextContent('/risk?window=7d'),
    );
    await waitFor(() => {
      const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
      expect(calls.some((u) => u.includes('/risk/metrics/summary') && u.includes('window=7d'))).toBe(
        true,
      );
      expect(
        calls.some((u) => u.includes('/risk/metrics/risk-distribution') && u.includes('window=7d')),
      ).toBe(true);
      expect(
        calls.some((u) => u.includes('/risk/metrics/risk-by-category') && u.includes('window=7d')),
      ).toBe(true);
      expect(calls.some((u) => u.includes('/risk/metrics/top-rules') && u.includes('window=7d'))).toBe(
        true,
      );
      expect(calls.some((u) => u.includes('/risk/traces') && u.includes('window=7d'))).toBe(true);
    });
  });

  // Issue #214 — end-to-end through the real hook + card, not just the units.

  it('asks /risk/traces for non-none risk levels only', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });

    await waitFor(() => expect(screen.getByText('Agents')).toBeInTheDocument());
    await waitFor(() => {
      const tracesUrl = (fetch as ReturnType<typeof vi.fn>).mock.calls
        .map((c) => c[0] as string)
        .find((u) => u.includes('/risk/traces'));
      expect(tracesUrl).toBeDefined();
      const levels =
        new URL(tracesUrl!, 'http://localhost').searchParams.get('risk_level')?.split(',') ?? [];
      expect(new Set(levels)).toEqual(new Set(['critical', 'high', 'medium', 'low']));
    });
  });

  it('shows no alert row for a none-risk trace, even if the API returns one', async () => {
    mockFetchRouter({
      traces: {
        items: [
          {
            trace_risk_id: 'trr-none',
            trace_id: 'trace-quiet',
            version: 1,
            computed_at: '2026-08-02T00:00:00Z',
            trace_risk_level: 'none',
            trace_enforcement_type: null,
            risk_compounding_mode: 'max',
            enforcement_aggregation_mode: 'strictest',
            interaction_count: 3,
            policy_event_count: 0,
            all_entity_ids: [],
            triggered_rule_ids: [],
            overall_confidence: 0.9,
            contributing_interaction_risk_ids: [],
          },
        ],
        next_cursor: null,
      },
    });
    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });

    await waitFor(() => expect(screen.getByText('Agents')).toBeInTheDocument());
    // The Alerts card still renders — it just has nothing to list.
    expect(screen.getByText('Alerts')).toBeInTheDocument();
    expect(screen.queryByTestId('alert-row-trace-quiet')).not.toBeInTheDocument();
    expect(screen.getByText(/no incidents/i)).toBeInTheDocument();
  });

  it('selecting 24h again deletes ?window rather than writing it', async () => {
    mockFetchRouter();
    renderWithProviders(
      <>
        <RiskDashboardPage />
        <LocationProbe />
      </>,
      { route: '/risk?window=7d' },
    );

    await waitFor(() => expect(screen.getByText('Agents')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Last 24 hours' }));

    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/risk'));
    expect(screen.getByTestId('location')).not.toHaveTextContent('window=24h');
  });

  it('offers only the three server-supported window options, none disabled', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });

    await waitFor(() => expect(screen.getByText('Agents')).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Last hour' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'All time' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Last 24 hours' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Last 7 days' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Last 30 days' })).toBeEnabled();
  });

  it('renders the error state when the summary request fails', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ error: 'internal', detail: 'boom', timestamp: '2026-08-02T00:00:00Z' }),
    });

    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });
    await waitFor(() => expect(screen.getByText(/failed to load/i)).toBeInTheDocument());
  });

  it('does not throw on a network failure', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new TypeError('network error'));

    renderWithProviders(<RiskDashboardPage />, { route: '/risk' });
    await waitFor(() => expect(screen.getByText(/failed to load/i)).toBeInTheDocument());
  });
});
