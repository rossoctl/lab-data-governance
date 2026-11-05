import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../../test/renderWithProviders';
import { LocationProbe } from '../../test/LocationProbe';
import { RiskRuleDetailPage } from './RiskRuleDetailPage';

// Mounted under a real :ruleId route so useParams resolves, mirroring
// TraceDetailPage.test.tsx's harness() convention for the same requirement.
function harness() {
  return (
    <Routes>
      <Route path="/risk/rules/:ruleId" element={<RiskRuleDetailPage />} />
    </Routes>
  );
}

describe('RiskRuleDetailPage', () => {
  it('renders its title with the rule id from the route', () => {
    renderWithProviders(harness(), { route: '/risk/rules/r1' });
    expect(screen.getByRole('heading', { name: /risk rule/i })).toBeInTheDocument();
    expect(screen.getByText(/r1/)).toBeInTheDocument();
  });
});

// New coverage for issue #171: the real rule detail view — key-value block,
// explanation, sources, conditions, allowed actions, and an explicit 404
// not-found state. The stub test above is untouched: with fetch unmocked
// (returns undefined, which react-query treats as a pending promise
// forever), the page still renders its loading heading without throwing.
describe('RiskRuleDetailPage content (#171)', () => {
  function ruleBody(overrides: Record<string, unknown> = {}) {
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

  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('renders the key-value block, explanation, sources, conditions, and allowed actions', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ruleBody(),
    });
    renderWithProviders(harness(), { route: '/risk/rules/DG-001' });

    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());
    // DG-001 appears twice by design: once in the breadcrumb crumb, once in
    // the key-value block's Rule ID row.
    expect(screen.getAllByText(/DG-001/).length).toBeGreaterThan(0);
    expect(screen.getByText(/data_exfiltration/)).toBeInTheDocument();
    expect(screen.getByText(/pii_exposure/)).toBeInTheDocument();
    expect(screen.getByText(/critical/i)).toBeInTheDocument();
    expect(screen.getByText(/block/i)).toBeInTheDocument();
    expect(
      screen.getByText(/PII detected in payload sent to an untrusted external destination/),
    ).toBeInTheDocument();
    expect(screen.getByText(/OWASP Top 10 for LLM Applications/)).toBeInTheDocument();
    expect(screen.getByText(/redact/)).toBeInTheDocument();
    // Conditions table: derived from event_type/data_items/data_destinations,
    // since the API has no literal `conditions` array (catalog.py's
    // structural-predicate design) — the UI still must show them as
    // condition-type/value rows per the mockup's Conditions table.
    expect(screen.getByText(/external_sharing/)).toBeInTheDocument();
    expect(screen.getByText(/regulatory tags: PII/)).toBeInTheDocument();
    expect(screen.getByText(/categories: external/)).toBeInTheDocument();
  });

  it('renders an explicit not-found state for a 404, not a blank page or crash', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({
        error: 'not found',
        detail: "no rule with id 'nope'",
        timestamp: '2026-08-02T00:00:00Z',
      }),
    });
    renderWithProviders(harness(), { route: '/risk/rules/nope' });

    await waitFor(() => expect(screen.getByText(/not found/i)).toBeInTheDocument());
  });

  it('renders empty-state sections for a rule with zero sources, conditions, or allowed actions', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () =>
        ruleBody({
          rule_sources: [],
          allowed_actions: [],
          data_items: [],
          data_destinations: [],
          event_type: null,
        }),
    });
    renderWithProviders(harness(), { route: '/risk/rules/DG-001' });

    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());
    expect(screen.getAllByText(/none/i).length).toBeGreaterThan(0);
  });

  it('renders a breadcrumb back to the rules table', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ruleBody(),
    });
    renderWithProviders(
      <>
        {harness()}
        <LocationProbe />
      </>,
      { route: '/risk/rules/DG-001' },
    );

    await waitFor(() => expect(screen.getByText(/pii_to_untrusted_external/)).toBeInTheDocument());
    await userEvent.click(screen.getByRole('link', { name: /risk rules/i }));

    expect(screen.getByTestId('location')).toHaveTextContent('/risk/rules');
  });

  it('renders correctly on a cold deep link with no prior navigation', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ruleBody({ rule_id: 'DG-004', rule_name: 'cold_open_rule' }),
    });
    renderWithProviders(harness(), { route: '/risk/rules/DG-004' });

    await waitFor(() => expect(screen.getByText(/cold_open_rule/)).toBeInTheDocument());
    expect(screen.getAllByText(/DG-004/).length).toBeGreaterThan(0);
  });
});
