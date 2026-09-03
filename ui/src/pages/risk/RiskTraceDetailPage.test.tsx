import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route } from 'react-router-dom';
import { renderWithProviders } from '../../test/renderWithProviders';
import { LocationProbe } from '../../test/LocationProbe';
import { RiskTraceDetailPage } from './RiskTraceDetailPage';

/**
 * Click one edge, the way a reader does — same target and same `act` wrap as
 * `ExecutionFlowGraph.test.tsx`'s own `clickEdge` helper (see that file's
 * header for why `fireEvent` rather than `userEvent`, and why the `act` wrap
 * is needed for PF's mobx selection state).
 */
function clickEdge(id: string) {
  const handler = document.querySelector(`[data-id="${id}"] [data-test-id="edge-handler"]`);
  if (!handler) throw new Error(`no clickable handler on edge ${id}`);
  act(() => {
    fireEvent.click(handler);
  });
}

// Mounted under a real :traceId route so useParams resolves, mirroring
// TraceDetailPage.test.tsx's harness() convention for the same requirement.
function harness() {
  return (
    <Routes>
      <Route path="/risk/traces/:traceId" element={<RiskTraceDetailPage />} />
    </Routes>
  );
}

describe('RiskTraceDetailPage', () => {
  it('renders its title with the trace id from the route', () => {
    renderWithProviders(harness(), { route: '/risk/traces/abc123' });
    expect(screen.getByRole('heading', { name: /risk trace/i })).toBeInTheDocument();
    expect(screen.getByText(/abc123/)).toBeInTheDocument();
  });
});

// New coverage for issue #170: the real trace-detail view — execution-flow
// graph + sequence diagram + a policy-decision panel that steps through each
// triggered-rule violation. The stub test above is untouched — its heading
// and traceId assertions still hold against the real content because they
// exercise the loading branch (see the design-constraint comment in
// RiskTraceDetailPage.tsx's header): fetch is stubbed per-test below, so a
// test that never resolves the mock (none do — mockFetchRouter always
// resolves) never hits that branch here.
describe('RiskTraceDetailPage content (#170)', () => {
  function leg(overrides: Record<string, unknown> = {}) {
    return {
      leg_type: 'request',
      occurred_at: '2026-05-01T12:00:00Z',
      payload_hash: 'h1',
      error: false,
      ...overrides,
    };
  }

  function risk(overrides: Record<string, unknown> = {}) {
    return {
      interaction_risk_id: 'ir1',
      interaction_id: 'i1',
      trace_id: 't1',
      parent_interaction_id: null,
      caller_entity_id: 'e1',
      callee_entity_id: 'e2',
      version: 1,
      computed_at: '2026-05-01T12:00:00Z',
      risk_level: 'critical',
      enforcement_type: 'block',
      policy_event_count: 1,
      triggered_rule_ids: ['DG-001'],
      classification_summary: null,
      opa_policy_versions_used: ['v1'],
      overall_confidence: 0.9,
      ...overrides,
    };
  }

  function interaction(overrides: Record<string, unknown> = {}) {
    return {
      interaction_id: 'i1',
      trace_id: 't1',
      parent_interaction_id: null,
      caller_entity_id: 'e1',
      callee_entity_id: 'e2',
      summary: 'agent calls search',
      legs: [leg()],
      risk: risk(),
      span_count: 1,
      anchor_count: 1,
      ...overrides,
    };
  }

  function rule(overrides: Record<string, unknown> = {}) {
    return {
      rule_id: 'DG-001',
      rule_name: 'No PII to external services',
      categories: ['privacy'],
      risk_level: 'critical',
      enforcement: 'block',
      explanation: 'PII must not leave the trust boundary.',
      event_type: 'tool_call',
      data_items: [],
      data_destinations: [{ data_destination_categories: ['external'], data_destination_trust_level: 'UNTRUSTED_EXTERNAL' }],
      allowed_actions: ['redact', 'block'],
      rule_sources: [],
      ...overrides,
    };
  }

  const ENTITIES = [
    { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
    { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
  ];

  // Most-specific-URL-substring-match first (RiskRulesPage.test.tsx's
  // convention): `/entities` and `/risk/rules` are disjoint from
  // `/risk/traces/`, so order between them doesn't matter, but an unmatched
  // URL always throws rather than silently falling through.
  function mockFetchRouter(overrides: {
    detail?: Record<string, unknown> | { status: number; body: Record<string, unknown> };
    entities?: Record<string, unknown> | { status: number };
    rules?: Record<string, unknown>;
  } = {}) {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/traces/') && url.includes('/entities')) {
        if (overrides.entities && 'status' in overrides.entities) {
          return { ok: false, status: overrides.entities.status, json: async () => ({ error: 'failed' }) };
        }
        return {
          ok: true,
          status: 200,
          json: async () => overrides.entities ?? { entities: ENTITIES },
        };
      }
      if (url.includes('/risk/rules')) {
        return {
          ok: true,
          status: 200,
          json: async () => overrides.rules ?? { items: [rule()], next_cursor: null },
        };
      }
      if (url.includes('/risk/traces/')) {
        if (overrides.detail && 'status' in overrides.detail) {
          return {
            ok: false,
            status: overrides.detail.status,
            json: async () => overrides.detail!.body,
          };
        }
        return {
          ok: true,
          status: 200,
          json: async () =>
            overrides.detail ?? {
              trace_risk: { trace_id: 't1' },
              interactions: [interaction()],
            },
        };
      }
      throw new Error(`unexpected fetch: ${url}`);
    });
  }

  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('shows the heading, breadcrumb and a spinner while loading, not a blank screen', () => {
    mockFetchRouter();
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    expect(screen.getByRole('heading', { name: /risk trace/i })).toBeInTheDocument();
    expect(screen.getByText('Risk dashboard')).toBeInTheDocument();
    expect(screen.getByLabelText(/loading risk trace/i)).toBeInTheDocument();
  });

  it('fetches the trace detail exactly once — no N+1, no per-interaction refetch', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [interaction({ interaction_id: 'i1' }), interaction({ interaction_id: 'i2', risk: null })],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText('Policy decisions')).toBeInTheDocument());

    const detailCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([url]) =>
      String(url).includes('/risk/traces/'),
    );
    expect(detailCalls).toHaveLength(1);
  });

  it('cold-opens with ?violation=2 selecting violation 2, no click needed', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [
          interaction({ interaction_id: 'i1', summary: 'first violation' }),
          interaction({ interaction_id: 'i2', summary: 'second violation', risk: risk({ interaction_id: 'i2' }) }),
        ],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1?violation=2' });

    await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('second violation')).toBeInTheDocument();
  });

  it('keeps the stepper, the URL, the panel and the diagram selection in lockstep on Previous', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [
          interaction({ interaction_id: 'i1', summary: 'first violation' }),
          interaction({ interaction_id: 'i2', summary: 'second violation', risk: risk({ interaction_id: 'i2' }) }),
        ],
      },
    });
    renderWithProviders(
      <>
        <LocationProbe />
        {harness()}
      </>,
      { route: '/risk/traces/t1?violation=2' },
    );

    await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: /previous/i }));

    // URL: the default is written by deleting the param, never setting it.
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('/risk/traces/t1'));
    expect(screen.getByTestId('location')).not.toHaveTextContent('violation=');

    // Panel: now shows violation 1.
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('first violation')).toBeInTheDocument();

    // Stepper: now "1 of 2", Previous disabled.
    expect(screen.getByText('1 of 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /previous/i })).toHaveAttribute('aria-disabled', 'true');
  });

  it('clicking a violating edge selects that violation, the same way the stepper does (issue #170)', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [
          interaction({ interaction_id: 'i1', summary: 'first violation' }),
          interaction({ interaction_id: 'i2', summary: 'second violation', risk: risk({ interaction_id: 'i2' }) }),
        ],
      },
    });
    renderWithProviders(
      <>
        <LocationProbe />
        {harness()}
      </>,
      { route: '/risk/traces/t1' },
    );

    await waitFor(() => expect(screen.getByText('1 of 2')).toBeInTheDocument());

    clickEdge('i2:request');

    await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('second violation')).toBeInTheDocument();
    expect(screen.getByTestId('location')).toHaveTextContent('violation=2');
  });

  it('clicking an edge whose interaction triggered no rule is a no-op, leaving the open violation as-is', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [
          interaction({ interaction_id: 'i1', summary: 'only violation' }),
          interaction({ interaction_id: 'i2', summary: 'not a violation', risk: null }),
        ],
      },
    });
    renderWithProviders(
      <>
        <LocationProbe />
        {harness()}
      </>,
      { route: '/risk/traces/t1' },
    );

    await waitFor(() => expect(screen.getByText('1 of 1')).toBeInTheDocument());

    clickEdge('i2:request');

    expect(screen.getByText('1 of 1')).toBeInTheDocument();
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('only violation')).toBeInTheDocument();
    expect(screen.getByTestId('location')).not.toHaveTextContent('violation=');
  });

  it('clamps an out-of-range or non-numeric ?violation= to violation 1 without error', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [interaction({ interaction_id: 'i1', summary: 'only violation' })],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1?violation=99' });

    await waitFor(() => expect(screen.getByText('1 of 1')).toBeInTheDocument());
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('only violation')).toBeInTheDocument();
  });

  it('clamps a non-numeric ?violation= to violation 1 without error', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [interaction({ interaction_id: 'i1', summary: 'only violation' })],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1?violation=abc' });

    await waitFor(() => expect(screen.getByText('1 of 1')).toBeInTheDocument());
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('only violation')).toBeInTheDocument();
  });

  it("links a triggered rule to its detail page and the click lands there", async () => {
    mockFetchRouter();
    renderWithProviders(
      <Routes>
        <Route path="/risk/traces/:traceId" element={<RiskTraceDetailPage />} />
        <Route path="/risk/rules/:ruleId" element={<div>Rule detail page</div>} />
      </Routes>,
      { route: '/risk/traces/t1' },
    );

    const link = await screen.findByRole('link', { name: 'No PII to external services' });
    expect(link).toHaveAttribute('href', '/risk/rules/DG-001');

    await userEvent.click(link);
    expect(screen.getByText('Rule detail page')).toBeInTheDocument();
  });

  it('shows the diagram and an empty state, with no stepper, when there are zero violations', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [interaction({ risk: null })],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText('No policy violations')).toBeInTheDocument());
    expect(screen.getByText('Execution flow')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /previous/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId('policy-decision-panel')).not.toBeInTheDocument();
  });

  it('renders an explicit not-found state for a 404, keeping the breadcrumb visible', async () => {
    mockFetchRouter({
      detail: {
        status: 404,
        body: { error: 'not found', detail: "no trace with id 't1'", timestamp: '2026-08-02T00:00:00Z' },
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText(/not found/i)).toBeInTheDocument());
    expect(screen.getByText('Risk dashboard')).toBeInTheDocument();
  });

  it('still renders the diagram with degraded (unknown) participant labels when the entities read fails', async () => {
    // Per the codebase's established silent-fallback convention (no Alert
    // component exists anywhere for a "degraded labels" banner — see
    // toFlowEntities/PolicyDecisionPanel's own 'unknown' fallback), a failed
    // entities read degrades labels rather than surfacing a warning banner.
    mockFetchRouter({ entities: { status: 500 } });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText('Policy decisions')).toBeInTheDocument());
    expect(screen.getByText('Execution flow')).toBeInTheDocument();
    // entityKindsOf dedupes via a Set — caller and callee both degrade to the
    // same 'unknown' kind, so the row shows one 'unknown', not two.
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('unknown')).toBeInTheDocument();
  });

  it('fetches the rule catalog at most once across three distinct triggered rule ids', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [
          interaction({ interaction_id: 'i1', risk: risk({ interaction_id: 'i1', triggered_rule_ids: ['DG-001'] }) }),
          interaction({ interaction_id: 'i2', risk: risk({ interaction_id: 'i2', triggered_rule_ids: ['DG-002'] }) }),
          interaction({ interaction_id: 'i3', risk: risk({ interaction_id: 'i3', triggered_rule_ids: ['DG-003'] }) }),
        ],
      },
      rules: {
        items: [rule(), rule({ rule_id: 'DG-002' }), rule({ rule_id: 'DG-003' })],
        next_cursor: null,
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    // Cold open defaults to violation 1 of 3 (no ?violation= param).
    await waitFor(() => expect(screen.getByText('1 of 3')).toBeInTheDocument());

    const ruleCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([url]) =>
      String(url).includes('/risk/rules'),
    );
    expect(ruleCalls.length).toBeLessThanOrEqual(1);
  });
});
