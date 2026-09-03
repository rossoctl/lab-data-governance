import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../../test/renderWithProviders';
import { LocationProbe } from '../../test/LocationProbe';
import { RiskRulesPage } from './RiskRulesPage';

describe('RiskRulesPage', () => {
  it('renders its title without throwing', () => {
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });
    expect(screen.getByRole('heading', { name: /risk rules/i })).toBeInTheDocument();
  });
});

// New coverage for issue #171: the real rules table — every catalog field
// (or a link to the detail page for verbose ones), category/risk-level
// filters, cursor pagination, and row navigation. The stub test above is
// untouched — its heading assertion still holds against the real content.
describe('RiskRulesPage content (#171)', () => {
  function rule(overrides: Record<string, unknown> = {}) {
    return {
      rule_id: 'DG-001',
      rule_name: 'pii_to_untrusted_external',
      categories: ['data_exfiltration', 'pii_exposure'],
      risk_level: 'critical',
      enforcement: 'block',
      explanation: 'PII detected in payload sent to an untrusted external destination.',
      event_type: 'external_sharing',
      data_items: [{ regulatory_tags: ['PII'] }],
      data_destinations: [{ data_destination_categories: ['external'] }],
      allowed_actions: ['redact'],
      rule_sources: [
        {
          document_name: 'OWASP Top 10 for LLM Applications',
          version: '2025',
          section: 'LLM02',
          'article/clause': 'Sensitive Information Disclosure',
        },
      ],
      ...overrides,
    };
  }

  function mockFetchRouter(overrides: {
    rules?: Record<string, unknown>;
    categories?: Record<string, unknown>;
  } = {}) {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/risk/rules/categories')) {
        return {
          ok: true,
          status: 200,
          json: async () =>
            overrides.categories ?? {
              items: [
                { category: 'data_exfiltration', rule_count: 1 },
                { category: 'pii_exposure', rule_count: 1 },
              ],
            },
        };
      }
      if (url.includes('/risk/rules')) {
        return {
          ok: true,
          status: 200,
          json: async () => overrides.rules ?? { items: [rule()], next_cursor: null },
        };
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
  }

  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('renders every rule with its name+id, categories, risk badge, enforcement, and source', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });

    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());
    const table = screen.getByRole('grid', { name: /risk rules/i });
    expect(within(table).getByText(/DG-001/)).toBeInTheDocument();
    expect(within(table).getByText(/data_exfiltration, pii_exposure/)).toBeInTheDocument();
    expect(within(table).getByText(/critical/i)).toBeInTheDocument();
    expect(within(table).getByText(/block/i)).toBeInTheDocument();
    expect(within(table).getByText(/OWASP Top 10 for LLM Applications/)).toBeInTheDocument();
  });

  it('shows an empty state when the catalog has no matching rules', async () => {
    mockFetchRouter({ rules: { items: [], next_cursor: null } });
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });

    await waitFor(() => expect(screen.getByText(/no rules/i)).toBeInTheDocument());
  });

  it('shows an empty state, not a crash, when a cursor lands past the end', async () => {
    mockFetchRouter({ rules: { items: [], next_cursor: null } });
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules?cursor=PAST_END' });

    await waitFor(() => expect(screen.getByText(/no rules/i)).toBeInTheDocument());
  });

  it('renders a fallback for a rule with no sources, e.g. "none" rather than omitting the cell', async () => {
    mockFetchRouter({
      rules: { items: [rule({ rule_sources: [] })], next_cursor: null },
    });
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });

    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());
    expect(screen.getAllByText(/none/i).length).toBeGreaterThan(0);
  });

  it('navigates to the rule detail page when a row is clicked', async () => {
    mockFetchRouter();
    renderWithProviders(
      <>
        <RiskRulesPage />
        <LocationProbe />
      </>,
      { route: '/risk/rules' },
    );

    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/pii_to_untrusted_external/));

    expect(screen.getByTestId('location')).toHaveTextContent('/risk/rules/DG-001');
  });

  it('populates the category filter from GET /risk/rules/categories', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });

    await waitFor(() => expect(screen.getByRole('option', { name: /data_exfiltration/ })).toBeInTheDocument());
    expect(screen.getByRole('option', { name: /pii_exposure/ })).toBeInTheDocument();
  });

  it('requests GET /risk/rules with the selected category filter, matching server-side filtering', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });
    await waitFor(() => expect(screen.getByRole('option', { name: /data_exfiltration/ })).toBeInTheDocument());

    await userEvent.selectOptions(screen.getByLabelText(/category/i), 'data_exfiltration');

    await waitFor(() => {
      const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
      expect(urls.some((u) => u.includes('/risk/rules?') && u.includes('category=data_exfiltration'))).toBe(
        true,
      );
    });
  });

  it('requests GET /risk/rules with the selected risk-level filter', async () => {
    mockFetchRouter();
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });
    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());

    await userEvent.selectOptions(screen.getByLabelText(/risk level/i), 'critical');

    await waitFor(() => {
      const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
      expect(urls.some((u) => u.includes('/risk/rules?') && u.includes('risk_level=critical'))).toBe(true);
    });
  });

  it('shows a "Load more" control when next_cursor is present, and pages on click', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/risk/rules/categories')) {
        return { ok: true, status: 200, json: async () => ({ items: [] }) };
      }
      if (url.includes('cursor=CURSOR1')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ items: [rule({ rule_id: 'DG-002', rule_name: 'second_rule' })], next_cursor: null }),
        };
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ items: [rule()], next_cursor: 'CURSOR1' }),
      };
    });
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });

    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /load more/i }));

    await waitFor(() => expect(screen.getByText(/second_rule/)).toBeInTheDocument());
  });

  it('renders an error state without throwing on a fetch failure', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockRejectedValue(new TypeError('network error'));
    renderWithProviders(<RiskRulesPage />, { route: '/risk/rules' });

    await waitFor(() => expect(screen.getByText(/failed to load/i)).toBeInTheDocument());
  });
});
