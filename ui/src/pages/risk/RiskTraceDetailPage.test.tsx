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
    alerts?: Record<string, unknown>;
    labels?: Record<string, unknown>;
  } = {}) {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/labels')) {
        return { ok: true, status: 200, json: async () => overrides.labels ?? { legs: [], lineage_status: 'complete' } };
      }
      if (url.startsWith('/risk/alerts')) {
        return { ok: true, status: 200, json: async () => overrides.alerts ?? { items: [], next_cursor: null } };
      }
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

    await waitFor(() => expect(screen.getByText('Policy events')).toBeInTheDocument());

    const detailCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(([url]) =>
      String(url).includes('/risk/traces/'),
    );
    expect(detailCalls).toHaveLength(1);
  });

  const alert = (over: Record<string, unknown>) => ({
    alert_id: 'a', timestamp: '2026-10-14T10:00:00Z', trace_id: 't1',
    trace_risk_record_id: 'r', risk_level: 'low', enforcement_type: null,
    title: 'T', description: 'D', involved_entities: [], triggered_rule_ids: [],
    status: 'new', alert_type: 'standard', assigned_to: null, dedup_key: null,
    duplicate_count: 0, superseded_by: null, ...over,
  });

  it('reads the trace alerts from the alert API, superseded included, one full page', async () => {
    mockFetchRouter();
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await screen.findByText('No alerts');
    const alertCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.startsWith('/risk/alerts'));
    expect(alertCalls).toHaveLength(1);
    const params = new URL(alertCalls[0], 'http://ui').searchParams;
    expect(params.get('trace_id')).toBe('t1');
    expect(params.get('include_superseded')).toBe('true');
    expect(params.get('limit')).toBe('200');
  });

  it('lists the trace alerts oldest first, naming the current one and the superseder', async () => {
    // The API serves newest first; the panel shows the chain in the order it was written.
    mockFetchRouter({
      alerts: {
        items: [
          alert({ alert_id: 'a2', title: 'second', risk_level: 'critical', duplicate_count: 3 }),
          alert({ alert_id: 'a1', title: 'first', superseded_by: 'a2' }),
        ],
        next_cursor: null,
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    const table = await screen.findByLabelText('Trace alerts');
    const rows = within(table).getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('first')).toBeInTheDocument();
    expect(within(rows[0]).getByText('#2')).toBeInTheDocument();
    expect(within(rows[1]).getByText('second')).toBeInTheDocument();
    expect(within(rows[1]).getByText('current')).toBeInTheDocument();
    expect(within(rows[1]).getByText('3')).toBeInTheDocument();
    expect(screen.queryByText('The oldest alerts of this trace are not shown.')).not.toBeInTheDocument();
  });

  it('says when the oldest alerts were cut', async () => {
    mockFetchRouter({ alerts: { items: [alert({ alert_id: 'a1' })], next_cursor: 'more' } });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await screen.findByLabelText('Trace alerts');
    expect(screen.getByText('The oldest alerts of this trace are not shown.')).toBeInTheDocument();
  });

  it('says so when the trace has no alert', async () => {
    mockFetchRouter();
    renderWithProviders(harness(), { route: '/risk/traces/t1' });
    expect(await screen.findByText('No alerts')).toBeInTheDocument();
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
    expect(screen.getByTestId('location')).not.toHaveTextContent('interaction=');
  });

  // Issue #295: a click on ANY edge shows that interaction's own data in the
  // interaction panel; only a policy event's edge also moves the stepper.
  describe('edge click shows the interaction (issue #295)', () => {
    const TWO = [
      interaction({ interaction_id: 'i1', summary: 'only violation' }),
      interaction({
        interaction_id: 'i2',
        summary: 'agent calls weather',
        risk: risk({
          interaction_id: 'i2',
          risk_level: 'low',
          enforcement_type: null,
          triggered_rule_ids: [],
          classification_summary: {
            request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PCI', 'PI'] },
            response: { payload: null },
          },
        }),
      }),
      interaction({
        interaction_id: 'i3',
        summary: 'plain call',
        risk: risk({
          interaction_id: 'i3',
          risk_level: 'low',
          enforcement_type: null,
          triggered_rule_ids: [],
          classification_summary: { request: { classification_pending: true } },
        }),
      }),
    ];

    /** The `<dd>` text beside a `<dt>` term — pins WHICH leg carries a verdict, not just that both strings appear. */
    function rowText(panel: HTMLElement, term: string) {
      return within(panel).getByText(term).closest('.pf-v5-c-description-list__group')!.textContent;
    }

    it('clicking an edge that triggered no rule shows its per-leg tags, mirrors ?interaction=, and leaves the open policy event as-is', async () => {
      mockFetchRouter({ detail: { trace_risk: { trace_id: 't1' }, interactions: TWO } });
      renderWithProviders(
        <>
          <LocationProbe />
          {harness()}
        </>,
        { route: '/risk/traces/t1' },
      );

      await waitFor(() => expect(screen.getByText('1 of 1')).toBeInTheDocument());
      // Default selection = the open policy event's interaction.
      expect(within(screen.getByTestId('interaction-panel')).getByText('only violation')).toBeInTheDocument();

      clickEdge('i2:request');

      const panel = await screen.findByTestId('interaction-panel');
      await waitFor(() => expect(within(panel).getByText('agent calls weather')).toBeInTheDocument());
      expect(rowText(panel, 'Request leg')).toContain('INTERNAL · PCI, PI');
      expect(rowText(panel, 'Response leg')).toContain('no payload'); // `{payload: null}`
      expect(within(panel).getByText('low')).toBeInTheDocument();
      expect(within(panel).getByRole('link', { name: 'Open in trace view' })).toHaveAttribute(
        'href',
        '/traces/t1/flow?iid=i2',
      );

      expect(screen.getByTestId('location')).toHaveTextContent('interaction=i2');
      expect(screen.getByTestId('location')).not.toHaveTextContent('violation=');
      expect(screen.getByText('1 of 1')).toBeInTheDocument();
      expect(within(screen.getByTestId('policy-decision-panel')).getByText('only violation')).toBeInTheDocument();

      // A second click REPLACES the param (one `interaction=`, the new one), never appends.
      clickEdge('i3:request');
      await waitFor(() => expect(within(panel).getByText('plain call')).toBeInTheDocument());
      expect(rowText(panel, 'Request leg')).toContain('pending'); // `{classification_pending: true}`
      expect(screen.getByTestId('location')).toHaveTextContent('interaction=i3');
      expect(screen.getByTestId('location')).not.toHaveTextContent('interaction=i2');
    });

    // Label lineage's `inherited` block, shaped on the live partner leg
    // (trace e06cd67d…, notify-partner → api.travel-partner.example:8000):
    // the classifier read PI / INTERNAL here; PI, PII / RESTRICTED came in.
    it('splits a leg with carried-in labels into Found here and Carried in, a pending leg included, with no count', async () => {
      const inherited = { count: 5, kinds: ['whole'], max_level: 'RESTRICTED', recombined: false, regulatory_tags: ['PI', 'PII'] };
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [
            interaction({
              interaction_id: 'i1',
              summary: 'travel-advisor/notify-partner → api.travel-partner.example:8000',
              risk: risk({
                triggered_rule_ids: ['DG-001', 'DG-004'],
                classification_summary: {
                  request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited },
                  response: { payload: null },
                },
              }),
            }),
            interaction({
              interaction_id: 'i3',
              summary: 'plain call',
              risk: risk({
                interaction_id: 'i3',
                risk_level: 'low',
                enforcement_type: null,
                triggered_rule_ids: [],
                classification_summary: { request: { classification_pending: true, inherited } },
              }),
            }),
          ],
        },
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1' });

      const panel = await screen.findByTestId('interaction-panel');
      await waitFor(() => expect(rowText(panel, 'Request leg')).toContain('Found here'));
      expect(rowText(panel, 'Request leg')).toBe('Request legFound here INTERNAL · PICarried in RESTRICTED · PI, PII');
      // A leg without the block reads as it always did.
      expect(rowText(panel, 'Response leg')).toBe('Response legno payload');
      expect(within(panel).getByRole('link', { name: 'Open in trace view — see where each label came from' })).toHaveAttribute(
        'href',
        '/traces/t1/flow?iid=i1',
      );

      clickEdge('i3:request');
      await waitFor(() => expect(within(panel).getByText('plain call')).toBeInTheDocument());
      expect(rowText(panel, 'Request leg')).toBe('Request legFound here pendingCarried in RESTRICTED · PI, PII');
      // `count` (edges, not labels) never reaches the screen.
      expect(panel).not.toHaveTextContent('5');
    });

    it('a plain-edge click keeps the policy event that a saved ?interaction= had opened', async () => {
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [
            interaction({ interaction_id: 'i1', summary: 'first violation' }),
            interaction({ interaction_id: 'i2', summary: 'second violation', risk: risk({ interaction_id: 'i2' }) }),
            interaction({ interaction_id: 'i3', summary: 'plain call', risk: null }),
          ],
        },
      });
      renderWithProviders(
        <>
          <LocationProbe />
          {harness()}
        </>,
        { route: '/risk/traces/t1?interaction=i2' },
      );

      await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());

      clickEdge('i3:request');

      await waitFor(() => expect(within(screen.getByTestId('interaction-panel')).getByText('plain call')).toBeInTheDocument());
      expect(screen.getByText('2 of 2')).toBeInTheDocument();
      expect(within(screen.getByTestId('policy-decision-panel')).getByText('second violation')).toBeInTheDocument();
      expect(screen.getByTestId('location')).toHaveTextContent('violation=2');
      expect(screen.getByTestId('location')).toHaveTextContent('interaction=i3');
    });

    it('clicking a policy event edge moves the stepper and deletes ?interaction= (its interaction is the default)', async () => {
      mockFetchRouter({ detail: { trace_risk: { trace_id: 't1' }, interactions: TWO } });
      renderWithProviders(
        <>
          <LocationProbe />
          {harness()}
        </>,
        { route: '/risk/traces/t1?interaction=i2' },
      );

      await waitFor(() =>
        expect(within(screen.getByTestId('interaction-panel')).getByText('agent calls weather')).toBeInTheDocument(),
      );

      clickEdge('i1:request');

      await waitFor(() => expect(screen.getByTestId('location')).not.toHaveTextContent('interaction='));
      expect(within(screen.getByTestId('interaction-panel')).getByText('only violation')).toBeInTheDocument();
      expect(screen.getByText('1 of 1')).toBeInTheDocument();
    });

    it('says "not yet computed" for an interaction whose risk is null', async () => {
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [interaction({ interaction_id: 'i2', summary: 'pending', risk: null })],
        },
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1?interaction=i2' });

      const panel = await screen.findByTestId('interaction-panel');
      expect(within(panel).getByText('pending')).toBeInTheDocument();
      expect(within(panel).getByText('not yet computed')).toBeInTheDocument();
      expect(within(panel).getByText('not classified')).toBeInTheDocument();
    });

    it('falls back to the open policy event for a stale ?interaction= that names no interaction of the trace', async () => {
      mockFetchRouter({ detail: { trace_risk: { trace_id: 't1' }, interactions: TWO } });
      renderWithProviders(harness(), { route: '/risk/traces/t1?interaction=gone' });

      await waitFor(() => expect(screen.getByText('1 of 1')).toBeInTheDocument());
      expect(within(screen.getByTestId('interaction-panel')).getByText('only violation')).toBeInTheDocument();
    });

    it('renders no panel for a stale ?interaction= when the trace has no policy event either', async () => {
      mockFetchRouter({
        detail: { trace_risk: { trace_id: 't1' }, interactions: [interaction({ risk: null })] },
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1?interaction=gone' });

      await waitFor(() => expect(screen.getByText('No policy events')).toBeInTheDocument());
      expect(screen.queryByTestId('interaction-panel')).not.toBeInTheDocument();
    });

    it('a cold-opened ?interaction= naming a policy event moves the stepper to it, so both panels agree', async () => {
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [
            interaction({ interaction_id: 'i1', summary: 'first violation' }),
            interaction({ interaction_id: 'i2', summary: 'second violation', risk: risk({ interaction_id: 'i2' }) }),
          ],
        },
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1?interaction=i2' });

      await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());
      expect(within(screen.getByTestId('policy-decision-panel')).getByText('second violation')).toBeInTheDocument();
      expect(within(screen.getByTestId('interaction-panel')).getByText('second violation')).toBeInTheDocument();
    });

    it('Previous/Next clear ?interaction=, so the stepped-to event is the selection', async () => {
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [
            interaction({ interaction_id: 'i1', summary: 'first violation' }),
            interaction({ interaction_id: 'i2', summary: 'second violation', risk: risk({ interaction_id: 'i2' }) }),
            interaction({ interaction_id: 'i3', summary: 'plain call', risk: null }),
          ],
        },
      });
      renderWithProviders(
        <>
          <LocationProbe />
          {harness()}
        </>,
        { route: '/risk/traces/t1?interaction=i3' },
      );

      await waitFor(() => expect(within(screen.getByTestId('interaction-panel')).getByText('plain call')).toBeInTheDocument());

      await userEvent.click(screen.getByRole('button', { name: /next/i }));

      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('violation=2'));
      expect(screen.getByTestId('location')).not.toHaveTextContent('interaction=');
      expect(within(screen.getByTestId('interaction-panel')).getByText('second violation')).toBeInTheDocument();
    });
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

  // Issue #222 terminology. The header count label had no coverage before, so
  // these are new tests rather than edits: singular and plural are both
  // asserted because the label pluralizes ("1 policy event" vs "2 policy
  // events"), and the retired "violation(s)" wording is asserted absent so a
  // partial rename cannot pass.
  it('labels the header count "policy events", pluralized, not "violations"', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [
          interaction({ interaction_id: 'i1' }),
          interaction({ interaction_id: 'i2', risk: risk({ interaction_id: 'i2' }) }),
        ],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText('2 policy events')).toBeInTheDocument());
    expect(screen.queryByText(/violation/i)).not.toBeInTheDocument();
  });

  it('uses the singular "1 policy event" for a trace with exactly one', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [interaction({ interaction_id: 'i1' })],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText('1 policy event')).toBeInTheDocument());
  });

  it('says "0 policy events" and shows no retired wording when none triggered', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [interaction({ risk: null })],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText('No policy events')).toBeInTheDocument());
    expect(screen.getByText('0 policy events')).toBeInTheDocument();
    expect(screen.queryByText(/violation/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Policy decisions')).not.toBeInTheDocument();
  });

  it('shows the diagram and an empty state, with no stepper, when there are zero violations', async () => {
    mockFetchRouter({
      detail: {
        trace_risk: { trace_id: 't1' },
        interactions: [interaction({ risk: null })],
      },
    });
    renderWithProviders(harness(), { route: '/risk/traces/t1' });

    await waitFor(() => expect(screen.getByText('No policy events')).toBeInTheDocument());
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

    await waitFor(() => expect(screen.getByText('Policy events')).toBeInTheDocument());
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

  // Issue #170 follow-up: regulatory tags as edge labels. Proves the wiring
  // end-to-end — adapter (`classificationByInteraction`) -> page -> prop ->
  // the graph's rendered tag — without re-covering the adapter's own
  // omission rules (`riskForestAdapter.test.ts`) or the label/precedence
  // logic (`ExecutionFlowGraph.test.tsx`), which is exactly what those two
  // files already exercise directly.
  describe('classification tags on the execution-flow graph (issue #170 follow-up)', () => {
    it("shows the interaction's regulatory tags on its edge, proving adapter -> prop -> tag", async () => {
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [
            interaction({
              risk: risk({
                classification_summary: {
                  request: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII', 'GDPR'] },
                },
              }),
            }),
          ],
        },
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1' });

      await waitFor(() => expect(screen.getByText('Policy events')).toBeInTheDocument());
      expect(
        document.querySelector('[data-id="i1:request"] .pf-topology__edge__tag text')?.textContent,
      ).toBe('PII, GDPR');
    });

    it('keeps the visible tag the classifier\'s own and puts carried-in labels in the hover title only', async () => {
      const inherited = { count: 5, kinds: ['whole'], max_level: 'RESTRICTED', recombined: false, regulatory_tags: ['PI', 'PII'] };
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [
            interaction({
              risk: risk({
                classification_summary: {
                  request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited },
                  response: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited },
                },
              }),
            }),
          ],
        },
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1' });

      await waitFor(() => expect(screen.getByText('Policy events')).toBeInTheDocument());
      expect(document.querySelector('[data-id="i1:request"] .pf-topology__edge__tag text')?.textContent).toBe('PI');
      expect(document.querySelector('[data-id="i1:request"] title')?.textContent).toMatch(
        / — PI \(INTERNAL\) · carried in: PI, PII \(RESTRICTED\)$/,
      );
    });

    it("shows no tag anywhere in the graph, and never the word 'none', when risk is null throughout", async () => {
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [interaction({ risk: null })],
        },
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1' });

      await waitFor(() => expect(screen.getByText('No policy events')).toBeInTheDocument());
      const graphSection = screen.getByText('Execution flow').closest('div')?.parentElement ?? document.body;
      expect(document.querySelector('[data-id="i1:request"] .pf-topology__edge__tag')).toBeNull();
      expect(within(graphSection as HTMLElement).queryByText(/\bnone\b/i)).not.toBeInTheDocument();
    });
  });

  /**
   * THE PAGE IS LIVE (ADR-0034), on the trace page's stream. jsdom has no
   * EventSource, so the hook is off in every other test in this file; here a
   * stand-in stream drives it, the way `TraceDetailPage.test.tsx` does.
   */
  describe('live: the page follows the trace change stream', () => {
    class FakeEventSource {
      static readonly CONNECTING = 0;
      static readonly CLOSED = 2;
      static last: FakeEventSource | null = null;
      readyState = FakeEventSource.CONNECTING;
      onerror: ((e: Event) => void) | null = null;
      private listeners = new Map<string, Set<(e: Event) => void>>();
      constructor(public readonly url: string) {
        FakeEventSource.last = this;
      }
      addEventListener(type: string, fn: (e: Event) => void) {
        if (!this.listeners.has(type)) this.listeners.set(type, new Set());
        this.listeners.get(type)!.add(fn);
      }
      removeEventListener() {}
      close() {}
      change(id: string) {
        this.listeners.get('change')?.forEach((fn) => fn(new MessageEvent('change', { lastEventId: id })));
      }
    }

    beforeEach(() => {
      vi.stubGlobal('EventSource', FakeEventSource);
      FakeEventSource.last = null;
    });

    /** How many times the page has read each of its sources so far. */
    function reads() {
      const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map(([url]) => String(url));
      return {
        detail: urls.filter((u) => u.startsWith('/risk/traces/')).length,
        alerts: urls.filter((u) => u.startsWith('/risk/alerts')).length,
        entities: urls.filter((u) => u.includes('/entities')).length,
        rules: urls.filter((u) => u.startsWith('/risk/rules')).length,
        labels: urls.filter((u) => u.includes('/labels')).length,
      };
    }

    const forest = (weatherSummary: string) => ({
      trace_risk: { trace_id: 't1' },
      interactions: [
        interaction({ interaction_id: 'i1', summary: 'only violation' }),
        interaction({
          interaction_id: 'i2',
          summary: weatherSummary,
          risk: risk({ interaction_id: 'i2', risk_level: 'low', enforcement_type: null, triggered_rule_ids: [] }),
        }),
      ],
    });

    it('re-reads detail, alerts and entities on a change, keeping ?interaction=, the panel and the viewport', async () => {
      mockFetchRouter({ detail: forest('agent calls weather') });
      renderWithProviders(
        <>
          <LocationProbe />
          {harness()}
        </>,
        { route: '/risk/traces/t1?interaction=i2' },
      );
      const panel = await screen.findByTestId('interaction-panel');
      await waitFor(() => expect(within(panel).getByText('agent calls weather')).toBeInTheDocument());
      await screen.findByText('No alerts');
      expect(FakeEventSource.last?.url).toBe('/api/traces/t1/events');
      expect(screen.queryByTestId('live-mode')).toBeNull();

      // The reader zoomed in; a re-read must not undo it.
      const transform = () => document.querySelector('[data-surface="true"]')?.getAttribute('transform');
      const fitted = transform();
      await userEvent.click(screen.getByRole('button', { name: /^Zoom In$/i }));
      await waitFor(() => expect(transform()).not.toBe(fitted));
      const zoomed = transform();
      const edgeBefore = document.querySelector('[data-id="i2:request"]');
      expect(edgeBefore).not.toBeNull();

      await act(async () => FakeEventSource.last!.change('v1'));
      expect(screen.getByTestId('live-mode')).toHaveTextContent('Live');
      await waitFor(() => expect(reads().detail).toBe(2)); // the first token re-reads once
      const before = reads();

      // The engine writes a new risk version and raises an alert.
      mockFetchRouter({
        detail: forest('agent calls weather, recomputed'),
        alerts: { items: [alert({ alert_id: 'a1', title: 'raised mid-run' })], next_cursor: null },
      });
      await act(async () => FakeEventSource.last!.change('v2'));

      await waitFor(() => expect(within(panel).getByText('agent calls weather, recomputed')).toBeInTheDocument());
      expect(await screen.findByText('raised mid-run')).toBeInTheDocument();
      expect(reads()).toEqual({
        detail: before.detail + 1,
        alerts: before.alerts + 1,
        entities: before.entities + 1,
        rules: before.rules, // the catalog is not per trace
        labels: before.labels, // no values panel is open, so no labels read to repeat
      });
      expect(screen.getByTestId('location')).toHaveTextContent('interaction=i2');
      expect(screen.getByTestId('interaction-panel')).toBe(panel);
      expect(transform()).toBe(zoomed);
      expect(document.querySelector('[data-id="i2:request"]')).toBe(edgeBefore);

      // Same token again (a reconnect): nothing is re-read.
      await act(async () => FakeEventSource.last!.change('v2'));
      expect(reads().detail).toBe(before.detail + 1);
    });

    it('keeps a picked policy event when an earlier interaction gains its first rule', async () => {
      // The outer call (i0) comes first in request order but its rule lands
      // last, after the reader picked i2 — the second policy event so far.
      const traced = (outerRuled: boolean) => ({
        trace_risk: { trace_id: 't1' },
        interactions: [
          interaction({
            interaction_id: 'i0',
            summary: 'outer call',
            risk: outerRuled
              ? risk({ interaction_id: 'i0' })
              : risk({ interaction_id: 'i0', risk_level: 'low', enforcement_type: null, triggered_rule_ids: [] }),
          }),
          interaction({ interaction_id: 'i1', summary: 'first inner', risk: risk({ interaction_id: 'i1' }) }),
          interaction({ interaction_id: 'i2', summary: 'picked event', risk: risk({ interaction_id: 'i2' }) }),
        ],
      });
      mockFetchRouter({ detail: traced(false) });
      renderWithProviders(
        <>
          <LocationProbe />
          {harness()}
        </>,
        { route: '/risk/traces/t1?violation=2' },
      );
      await waitFor(() => expect(screen.getByText('2 of 2')).toBeInTheDocument());
      expect(within(screen.getByTestId('policy-decision-panel')).getByText('picked event')).toBeInTheDocument();

      mockFetchRouter({ detail: traced(true) });
      await act(async () => FakeEventSource.last!.change('v1'));

      // Same event, now third of three: the URL follows it, nothing else moves.
      await waitFor(() => expect(screen.getByText('3 of 3')).toBeInTheDocument());
      expect(within(screen.getByTestId('policy-decision-panel')).getByText('picked event')).toBeInTheDocument();
      expect(within(screen.getByTestId('interaction-panel')).getByText('picked event')).toBeInTheDocument();
      expect(document.querySelector('[data-id="i2:request"] .dg-graph-edge--selected')).not.toBeNull();
      expect(screen.getByTestId('location')).toHaveTextContent('violation=3');
      expect(screen.getByTestId('location')).not.toHaveTextContent('interaction=');

      // The stepper still steps from there.
      await userEvent.click(screen.getByRole('button', { name: /previous/i }));
      await waitFor(() => expect(screen.getByText('2 of 3')).toBeInTheDocument());
      expect(within(screen.getByTestId('policy-decision-panel')).getByText('first inner')).toBeInTheDocument();
    });

    it('re-reads the open values panel\'s labels on a change', async () => {
      const inherited = { count: 1, kinds: ['whole'], max_level: 'RESTRICTED', recombined: false, regulatory_tags: ['PII'] };
      const labels = (seq: number) => ({
        lineage_status: 'complete',
        legs: [{
          leg_type: 'request', payload_hash: 'h1', seq,
          found: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] },
          carried: { max_level: 'RESTRICTED', regulatory_tags: ['PII'], origin_count: 1 },
          labels: [],
        }],
      });
      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [interaction({ risk: risk({ classification_summary: { request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited } } }) })],
        },
        labels: labels(7),
      });
      renderWithProviders(harness(), { route: '/risk/traces/t1' });
      await userEvent.click(await screen.findByRole('button', { name: /Show the values/ }));
      await screen.findByRole('heading', { name: 'Request #7' });
      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2));
      const before = reads();

      mockFetchRouter({
        detail: {
          trace_risk: { trace_id: 't1' },
          interactions: [interaction({ risk: risk({ classification_summary: { request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited } } }) })],
        },
        labels: labels(8),
      });
      await act(async () => FakeEventSource.last!.change('v2'));
      await screen.findByRole('heading', { name: 'Request #8' });
      expect(reads().labels).toBe(before.labels + 1);
    });

    it('shows Polling while the stream errors, and Live again when it returns', async () => {
      mockFetchRouter();
      renderWithProviders(harness(), { route: '/risk/traces/t1' });
      await screen.findByText('Policy events');

      await act(async () => FakeEventSource.last!.change('v1'));
      expect(screen.getByTestId('live-mode')).toHaveTextContent('Live');
      await act(async () => FakeEventSource.last!.onerror?.(new Event('error')));
      expect(screen.getByTestId('live-mode')).toHaveTextContent('Polling');
      await act(async () => FakeEventSource.last!.change('v1'));
      expect(screen.getByTestId('live-mode')).toHaveTextContent('Live');
    });

    it('keeps what it shows when a background re-read fails', async () => {
      mockFetchRouter();
      renderWithProviders(harness(), { route: '/risk/traces/t1' });
      await screen.findByText('Policy events');

      mockFetchRouter({ detail: { status: 500, body: { error: 'boom' } } });
      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2));
      await act(async () => {
        await new Promise((r) => setTimeout(r, 20));
      });
      expect(screen.getByText('Policy events')).toBeInTheDocument();
      expect(screen.queryByText('Failed to load')).not.toBeInTheDocument();
    });

    it('fills in by itself when it was opened before the trace had a risk record', async () => {
      mockFetchRouter({ detail: { status: 404, body: { error: 'not found' } } });
      renderWithProviders(harness(), { route: '/risk/traces/t1' });
      await screen.findByText('Trace not found');

      mockFetchRouter();
      await act(async () => FakeEventSource.last!.change('v1'));
      expect(await screen.findByText('Policy events')).toBeInTheDocument();
      expect(screen.queryByText('Trace not found')).not.toBeInTheDocument();
    });

    it('keeps saying "Trace not found" through re-reads, with no spinner in between', async () => {
      mockFetchRouter({ detail: { status: 404, body: { error: 'not found' } } });
      renderWithProviders(harness(), { route: '/risk/traces/t1' });
      await screen.findByText('Trace not found');

      let spun = false;
      const observer = new MutationObserver(() => {
        if (document.querySelector('[aria-label="Loading risk trace"]')) spun = true;
      });
      observer.observe(document.body, { childList: true, subtree: true });
      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2));
      await act(async () => FakeEventSource.last!.change('v2'));
      await waitFor(() => expect(reads().detail).toBe(3));
      observer.disconnect();

      expect(spun).toBe(false);
      expect(screen.getByText('Trace not found')).toBeInTheDocument();
    });

    it('lands on a linked ?violation=N once event N arrives', async () => {
      const traced = (n: number) => ({
        trace_risk: { trace_id: 't1' },
        interactions: ['i1', 'i2', 'i3'].slice(0, n).map((id) =>
          interaction({ interaction_id: id, summary: `event ${id}`, risk: risk({ interaction_id: id }) }),
        ),
      });
      mockFetchRouter({ detail: traced(2) });
      renderWithProviders(
        <>
          <LocationProbe />
          {harness()}
        </>,
        { route: '/risk/traces/t1?violation=3' },
      );
      await waitFor(() => expect(screen.getByText('1 of 2')).toBeInTheDocument());

      mockFetchRouter({ detail: traced(3) });
      await act(async () => FakeEventSource.last!.change('v1'));

      await waitFor(() => expect(screen.getByText('3 of 3')).toBeInTheDocument());
      expect(within(screen.getByTestId('policy-decision-panel')).getByText('event i3')).toBeInTheDocument();
      expect(screen.getByTestId('location')).toHaveTextContent('violation=3');
    });
  });
});
