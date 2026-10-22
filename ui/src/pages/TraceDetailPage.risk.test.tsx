import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Routes, Route, useNavigate } from 'react-router-dom';
import { renderWithProviders } from '../test/renderWithProviders';
import { LocationProbe } from '../test/LocationProbe';
import { TraceDetailPage } from './TraceDetailPage';

/**
 * THE RISK LAYER on the trace page: a Risk toggle beside the
 * view tabs, `?risk=1` in the URL (absent = off), one state for every view, the
 * span tree included. On, the alerts section renders above the views, the flow
 * view reads and paints the risk (`FlowTables.risk.test.tsx` pins the contract
 * there), the live keys gain the risk prefixes, and the cross link to the risk
 * trace page — which stays for a transition period — carries the open event and
 * the selection. Off, the page is as it was: `TraceDetailPage.test.tsx` runs
 * unchanged beside this file.
 */
function harness() {
  return (
    <>
      <Routes>
        <Route path="/traces/:traceId/:view" element={<TraceDetailPage />} />
      </Routes>
      <LocationProbe />
    </>
  );
}

/** Back, for the MemoryRouter (window.history.back() never reaches it). */
function BackButton() {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(-1)}>
      test-back
    </button>
  );
}

const GRAPH_CHUNK_TIMEOUT = 10_000;

const ROOT = {
  seq: 1, trace_id: 'T1', span_id: 'root', parent_id: null, name: 'root-span',
  started_at: '2026-05-01T12:00:00Z', service_name: 'svc', kind: 'SERVER', error: null, attributes: {},
};
const ENTITIES = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
  { id: 'e3', kind: 'external', natural_key: 'external:(p,partner)', display_name: 'partner', detected_from: 'span' },
];
function legs(seq: number) {
  return [
    { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: 'h1', error: false, seq },
    { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: seq + 1 },
  ];
}
const INTERACTIONS = [
  { id: 'i0', caller_entity_id: 'e1', callee_entity_id: 'e2', summary: 'agent calls search', parent_interaction_id: null, legs: legs(1), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
  { id: 'i1', caller_entity_id: 'e2', callee_entity_id: 'e3', summary: 'search calls partner', parent_interaction_id: 'i0', legs: legs(3), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
  { id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3', summary: 'agent calls partner', parent_interaction_id: null, legs: legs(5), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
];

function risk(interactionId: string, overrides: Record<string, unknown> = {}) {
  return {
    interaction_risk_id: `ir-${interactionId}`, interaction_id: interactionId, trace_id: 'T1', parent_interaction_id: null,
    caller_entity_id: 'e1', callee_entity_id: 'e2', version: 1, computed_at: '2026-05-01T12:00:00Z',
    risk_level: 'low', enforcement_type: null, policy_event_count: 0, triggered_rule_ids: [], classification_summary: null,
    opa_policy_versions_used: ['v1'], overall_confidence: 0.9, ...overrides,
  };
}
function forestOf(ix: (typeof INTERACTIONS)[number], riskRecord: Record<string, unknown> | null) {
  return {
    interaction_id: ix.id, trace_id: 'T1', parent_interaction_id: ix.parent_interaction_id,
    caller_entity_id: ix.caller_entity_id, callee_entity_id: ix.callee_entity_id, summary: ix.summary,
    legs: ix.legs.map(({ leg_type, occurred_at, payload_hash, error }) => ({ leg_type, occurred_at, payload_hash, error })),
    risk: riskRecord, span_count: ix.span_count, anchor_count: ix.anchor_count,
  };
}
const EVENT_I1 = risk('i1', { risk_level: 'critical', enforcement_type: 'block', policy_event_count: 1, triggered_rule_ids: ['DG-001'] });
const EVENT_I2 = risk('i2', { risk_level: 'high', enforcement_type: 'flag', policy_event_count: 1, triggered_rule_ids: ['DG-002'] });
/** i0 fired no rule; i1 and i2 are policy events 1 and 2. */
const FOREST = [forestOf(INTERACTIONS[0], risk('i0')), forestOf(INTERACTIONS[1], EVENT_I1), forestOf(INTERACTIONS[2], EVENT_I2)];
/** A new risk version for i0 (low → medium) that fired no rule: the events stay [i1, i2]. */
const FOREST_RECOLOURED = [forestOf(INTERACTIONS[0], risk('i0', { risk_level: 'medium', version: 2 })), forestOf(INTERACTIONS[1], EVENT_I1), forestOf(INTERACTIONS[2], EVENT_I2)];
/** The outer call i0 gained its first rule: it is now event 1, i1 and i2 are 2 and 3. */
const FOREST_SHIFTED = [
  forestOf(INTERACTIONS[0], risk('i0', { risk_level: 'medium', policy_event_count: 1, triggered_rule_ids: ['DG-003'] })),
  forestOf(INTERACTIONS[1], EVENT_I1),
  forestOf(INTERACTIONS[2], EVENT_I2),
];

function rule(id: string) {
  return {
    rule_id: id, rule_name: `Rule ${id}`, categories: ['privacy'], risk_level: 'critical', enforcement: 'block',
    explanation: `${id} explains.`, event_type: 'tool_call', data_items: [], data_destinations: [], allowed_actions: ['block'], rule_sources: [],
  };
}
function alert(overrides: Record<string, unknown> = {}) {
  return {
    alert_id: 'a1', trace_id: 'T1', risk_level: 'critical', enforcement_type: 'block', title: 'PII left the boundary',
    description: 'desc', status: 'open', duplicate_count: 0, superseded_by: null, triggered_rule_ids: ['DG-001'],
    timestamp: '2026-05-01T12:00:02Z', ...overrides,
  };
}

/** The base routing, reusable by a case that overrides one read. */
let mockFetchBase: (url: string) => Promise<unknown> = async () => ({ ok: true, status: 200, json: async () => ({}) });
function mockFetch(over: { forest?: unknown[]; alerts?: unknown[] } = {}) {
  mockFetchBase = async (url: string) => {
    if (url.startsWith('/risk/rules')) return { ok: true, status: 200, json: async () => ({ items: [rule('DG-001'), rule('DG-002'), rule('DG-003')], next_cursor: null }) };
    if (url.startsWith('/risk/alerts')) return { ok: true, status: 200, json: async () => ({ items: over.alerts ?? [], next_cursor: null }) };
    if (url.startsWith('/risk/traces/')) return { ok: true, status: 200, json: async () => ({ trace_risk: { trace_id: 'T1' }, interactions: over.forest ?? FOREST }) };
    if (url === '/api/traces/T1') {
      return { ok: true, status: 200, json: async () => ({ trace_id: 'T1', listing_root: ROOT, counts: { total: 1, in_window: 1, error_count: 0 }, in_time_window: true }) };
    }
    if (url.includes('/labels')) return { ok: true, status: 200, json: async () => ({ legs: [], lineage_status: 'complete' }) };
    if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: INTERACTIONS }) };
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
    if (url.includes('/data-lineage')) return { ok: true, status: 200, json: async () => ({ legs: [], status: 'complete', stopped_at_seq: null }) };
    if (url.includes('/interactions/i0/spans')) {
      return { ok: true, status: 200, json: async () => ({ spans: [{ span_id: 'ev-span', role: 'anchor', parent_id: 'root', kind: 'CLIENT', service_name: 'svc' }] }) };
    }
    // The reveal fetches the pinned span by id and then selects it: a real span,
    // or the tree would select a row with no id (see `handleSpanSelect`).
    if (url.endsWith('/spans/ev-span')) {
      return { ok: true, status: 200, json: async () => ({ ...ROOT, seq: 2, span_id: 'ev-span', parent_id: 'root', name: 'ev', kind: 'CLIENT', observed_at: '2026-05-01T12:00:01Z', arrival_seq: 2, in_time_window: true, status_message: null, events: null, links: null, ended_at: null, otlp: null, scope: null, resource_attributes: null }) };
    }
    return { ok: true, status: 200, json: async () => ({ spans: [], interactions: [], entities: [] }) };
  };
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(mockFetchBase);
}

function reads() {
  const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map(([u]) => String(u));
  return {
    detail: urls.filter((u) => u.startsWith('/risk/traces/')).length,
    alerts: urls.filter((u) => u.startsWith('/risk/alerts')).length,
    rules: urls.filter((u) => u.startsWith('/risk/rules')).length,
    interactions: urls.filter((u) => u.endsWith('/interactions')).length,
  };
}
const location = () => screen.getByTestId('location').textContent;
const toggle = () => screen.getByRole('checkbox', { name: 'Risk' });
async function findGraph() {
  return screen.findByTestId('execution-flow-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
}

describe('TraceDetailPage — the risk layer', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('is off by default: the toggle is there and unchecked, no risk read is made, no alerts section shows', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph' });
    await findGraph();
    expect(toggle()).not.toBeChecked();
    expect(reads()).toMatchObject({ detail: 0, alerts: 0, rules: 0 });
    expect(screen.queryByRole('heading', { name: 'Alerts' })).toBeNull();
    expect(location()).toBe('/traces/T1/graph');
  });

  it('switching it on writes ?risk=1, reads the risk, shows the alerts; switching it off deletes ?risk and ?violation', async () => {
    mockFetch({ alerts: [alert()] });
    renderWithProviders(harness(), { route: '/traces/T1/graph' });
    await findGraph();
    await userEvent.click(toggle());
    expect(location()).toBe('/traces/T1/graph?risk=1');
    expect(toggle()).toBeChecked();
    expect(await screen.findByText('PII left the boundary')).toBeInTheDocument();
    await waitFor(() => expect(reads()).toMatchObject({ detail: 1, alerts: 1, rules: 1 }));
    // The default selection opened event 1; step to event 2 so ?violation= is written…
    const layer = await screen.findByTestId('risk-layer');
    await userEvent.click(within(layer).getByRole('button', { name: /Next/ }));
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&violation=2'));
    // …and switching the layer off drops both params, the position meaning nothing then.
    await userEvent.click(toggle());
    expect(location()).toBe('/traces/T1/graph');
    await waitFor(() => expect(screen.queryByTestId('risk-layer')).toBeNull());
    expect(screen.queryByRole('heading', { name: 'Alerts' })).toBeNull();
  });

  it('is one state for every view: ?risk=1 crosses to the span tree and back, and the alerts show on the tree', async () => {
    mockFetch({ alerts: [alert()] });
    renderWithProviders(harness(), { route: '/traces/T1/spans?risk=1' });
    expect(await screen.findByText('PII left the boundary')).toBeInTheDocument();
    expect(toggle()).toBeChecked();
    await userEvent.click(screen.getByRole('tab', { name: 'Interactions and Entities' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/flow?risk=1'));
    await userEvent.click(screen.getByRole('tab', { name: 'Span tree' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/spans?risk=1'));
  });

  it('carries the layer and its position to the tree, dropping only the selection as before', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&violation=2&iid=i0' });
    await findGraph();
    await userEvent.click(screen.getByRole('tab', { name: 'Span tree' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/spans?risk=1&violation=2'));
  });

  it('?risk=1&violation=2 lands with the layer on and the stepper on event 2', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&violation=2' });
    await findGraph();
    const layer = await screen.findByTestId('risk-layer');
    expect(within(layer).getByText('2 of 2')).toBeInTheDocument();
    expect(within(within(layer).getByTestId('policy-decision-panel')).getByText('agent calls partner')).toBeInTheDocument();
    expect(toggle()).toBeChecked();
  });

  it('the reverse cross link carries the open event and the selection to the risk trace page', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&violation=2&iid=i0' });
    await findGraph();
    expect(screen.getByRole('link', { name: 'Risk page for this trace' })).toHaveAttribute(
      'href',
      '/risk/traces/T1?violation=2&interaction=i0',
    );
  });

  it('restores the graph edge mode from ?edges=interaction and writes it back, deleting the per-leg default', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph?edges=interaction' });
    await findGraph();
    await waitFor(() => expect(document.querySelector('[data-id="i1:request"]')).not.toBeNull());
    expect(document.querySelector('[data-id="i1:response"]')).toBeNull();
    expect(screen.getByRole('button', { name: 'Edges per interaction' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(screen.getByRole('button', { name: 'Edges per leg' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/graph'));
    await waitFor(() => expect(document.querySelector('[data-id="i1:response"]')).not.toBeNull());
    await userEvent.click(screen.getByRole('button', { name: 'Edges per interaction' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?edges=interaction'));
    // Carried to the lineage view like the other interaction-view params.
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/lineage?edges=interaction'));
  });

  // The second route into the tree (the detail panel's pin) keeps the layer.
  it('Add-to-highlights from the detail panel lands on the span tree with the layer and its position kept', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&violation=2&iid=i0' });
    await findGraph();
    const layer = await screen.findByTestId('risk-layer', undefined, { timeout: 5000 });
    // The panel is open on i0 and its evidence (the span the pin reveals) is on
    // screen before the pin is clicked: state, not time.
    await waitFor(() => expect(within(layer).getByText('agent calls search')).toBeInTheDocument(), { timeout: 5000 });
    await screen.findAllByText('ev-span', undefined, { timeout: 5000 });
    await userEvent.click(await screen.findByRole('button', { name: 'Add to highlights' }, { timeout: 5000 }));
    await waitFor(() => expect(location()).toBe('/traces/T1/spans?sel=ev-span&risk=1&violation=2'), { timeout: 5000 });
    expect(toggle()).toBeChecked();
    expect(await screen.findByRole('heading', { name: 'Alerts' })).toBeInTheDocument();
  }, 15000);

  // A selection only the layer made goes with the layer.
  it('switching the layer off closes the default selection it opened, so the URL and the screen agree', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1' });
    await findGraph();
    await within(await screen.findByTestId('risk-layer')).findByText('1 of 2');
    expect(location()).toBe('/traces/T1/graph?risk=1');
    await userEvent.click(toggle());
    await waitFor(() => expect(location()).toBe('/traces/T1/graph'));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Close details' })).toBeNull());
    expect(document.querySelector('.dg-graph-edge--selected')).toBeNull();
    // A pick the reader made survives the toggle: it is in the URL.
    await userEvent.click(toggle());
    await within(await screen.findByTestId('risk-layer')).findByText('1 of 2');
    act(() => {
      fireEvent.click(document.querySelector('[data-id="i0:request"] [data-test-id="edge-handler"]')!);
    });
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&iid=i0'));
    await userEvent.click(toggle());
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?iid=i0'));
    expect(screen.getByRole('button', { name: 'Close details' })).toBeInTheDocument();
  });

  // A dismissal made with the layer off is not a dismissal of the layer.
  it('closing the panel or clicking the background with the layer off does not keep the default from opening when it is switched on', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph' });
    await findGraph();
    await waitFor(() => expect(document.querySelector('[data-id="i0:request"] [data-test-id="edge-handler"]')).not.toBeNull());
    act(() => {
      fireEvent.click(document.querySelector('[data-id="i0:request"] [data-test-id="edge-handler"]')!);
    });
    // Each step waits on the state it produces, never on time.
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?iid=i0'), { timeout: 5000 });
    await userEvent.click(await screen.findByRole('button', { name: 'Close details' }, { timeout: 5000 }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Close details' })).toBeNull());
    await waitFor(() => expect(location()).toBe('/traces/T1/graph'));
    act(() => {
      fireEvent.click(document.querySelector('[data-kind="graph"] > rect')!);
    });
    await userEvent.click(toggle());
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1'));
    const layer = await screen.findByTestId('risk-layer', undefined, { timeout: 5000 });
    await within(layer).findByText('1 of 2', undefined, { timeout: 5000 });
  }, 15000);

  // A policy event picked by its edge is named by ?violation= only, which the toggle deletes — it goes too.
  it('switching the layer off closes a policy event picked by its edge, since nothing in the URL names it', async () => {
    mockFetch();
    renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1' });
    await findGraph();
    await within(await screen.findByTestId('risk-layer')).findByText('1 of 2');
    act(() => {
      fireEvent.click(document.querySelector('[data-id="i2:request"] [data-test-id="edge-handler"]')!);
    });
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&violation=2'));
    await userEvent.click(toggle());
    await waitFor(() => expect(location()).toBe('/traces/T1/graph'));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Close details' })).toBeNull());
    expect(document.querySelector('.dg-graph-edge--selected')).toBeNull();
  });

  // A dismissal must not drop a later ?iid= restore — the URL owns a selection it names.
  it('after a dismissal, Back to a URL naming ?iid= reopens the panel on it', async () => {
    mockFetch();
    renderWithProviders(
      <>
        {harness()}
        <BackButton />
      </>,
      { route: '/traces/T1/graph?risk=1' },
    );
    await findGraph();
    await within(await screen.findByTestId('risk-layer')).findByText('1 of 2');
    act(() => {
      fireEvent.click(document.querySelector('[data-id="i0:request"] [data-test-id="edge-handler"]')!);
    });
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&iid=i0'));
    await userEvent.click(screen.getByRole('tab', { name: 'Interactions and Entities' })); // a push: Back has somewhere to go
    await waitFor(() => expect(location()).toBe('/traces/T1/flow?risk=1&iid=i0'));
    await userEvent.click(screen.getByRole('button', { name: 'Close details' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/flow?risk=1'));
    await userEvent.click(screen.getByRole('button', { name: 'test-back' }));
    await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&iid=i0'));
    const layer = await screen.findByTestId('risk-layer');
    await waitFor(() => expect(within(layer).getByText('agent calls search')).toBeInTheDocument());
  });

  describe('live, with the layer on (ADR-0034)', () => {
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

    it('re-reads the risk detail and the alerts on a change, keeping the selection, the panel and the viewport; off, it re-reads no risk', async () => {
      mockFetch();
      renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&iid=i0' });
      await findGraph();
      const layer = await screen.findByTestId('risk-layer');
      await waitFor(() => expect(within(layer).getByText('agent calls search')).toBeInTheDocument());
      await screen.findByText('No alerts');

      const transform = () => document.querySelector('[data-surface="true"]')?.getAttribute('transform');
      const fitted = transform();
      await userEvent.click(screen.getByRole('button', { name: /^Zoom In$/i }));
      await waitFor(() => expect(transform()).not.toBe(fitted));
      const zoomed = transform();

      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2)); // the first token re-reads once
      const before = reads();

      // The engine writes a new risk version (i0 low → medium) and raises an alert:
      // the re-read must reach the panel and recolour the edge without refitting.
      mockFetch({ forest: FOREST_RECOLOURED, alerts: [alert({ title: 'raised mid-run' })] });
      await act(async () => FakeEventSource.last!.change('v2'));
      expect(await screen.findByText('raised mid-run')).toBeInTheDocument();
      await waitFor(() => expect(within(layer).getAllByText('medium').length).toBeGreaterThan(0));
      await waitFor(() => expect(document.querySelector('[data-id="i0:request"]')?.innerHTML).toContain('gold-400'));
      await waitFor(() => expect(reads()).toMatchObject({ detail: before.detail + 1, alerts: before.alerts + 1, interactions: before.interactions + 1 }));
      expect(reads().rules).toBe(before.rules); // the catalog is not per trace
      expect(location()).toBe('/traces/T1/graph?risk=1&iid=i0');
      expect(screen.getByTestId('risk-layer')).toBe(layer);
      expect(transform()).toBe(zoomed);

      // The layer off: the same change re-reads the trace, not the risk.
      await userEvent.click(toggle());
      await waitFor(() => expect(location()).toBe('/traces/T1/graph?iid=i0'));
      const off = reads();
      await act(async () => FakeEventSource.last!.change('v3'));
      await waitFor(() => expect(reads().interactions).toBe(off.interactions + 1));
      expect(reads()).toMatchObject({ detail: off.detail, alerts: off.alerts });
    });

    it('keeps the open policy event when an earlier interaction gains its first rule, rewriting ?violation=', async () => {
      mockFetch();
      renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&violation=2' });
      await findGraph();
      const layer = await screen.findByTestId('risk-layer');
      await within(layer).findByText('2 of 2');
      expect(within(within(layer).getByTestId('policy-decision-panel')).getByText('agent calls partner')).toBeInTheDocument();

      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2));
      mockFetch({ forest: FOREST_SHIFTED });
      await act(async () => FakeEventSource.last!.change('v2'));

      await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&violation=3'));
      const after = await screen.findByTestId('risk-layer');
      expect(within(after).getByText('3 of 3')).toBeInTheDocument();
      expect(within(within(after).getByTestId('policy-decision-panel')).getByText('agent calls partner')).toBeInTheDocument();
      // One evidence fetch for the held interaction, none for the one that moved into its old position.
      const evidence = (fetch as ReturnType<typeof vi.fn>).mock.calls.map(([u]) => String(u)).filter((u) => /\/interactions\/i[0-9]\/spans/.test(u));
      expect(evidence.filter((u) => u.includes('/i1/'))).toHaveLength(0);
    });

    it('lands on a linked ?violation=N once event N arrives', async () => {
      mockFetch();
      renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&violation=3' });
      await findGraph();
      const layer = await screen.findByTestId('risk-layer');
      await within(layer).findByText('1 of 2'); // event 3 has not arrived: event 1 for now
      expect(within(within(layer).getByTestId('policy-decision-panel')).getByText('search calls partner')).toBeInTheDocument();
      expect(location()).toBe('/traces/T1/graph?risk=1&violation=3'); // not held, not rewritten

      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2));
      mockFetch({ forest: FOREST_SHIFTED });
      await act(async () => FakeEventSource.last!.change('v2'));

      const after = await screen.findByTestId('risk-layer');
      await waitFor(() => expect(within(after).getByText('3 of 3')).toBeInTheDocument());
      expect(within(within(after).getByTestId('policy-decision-panel')).getByText('agent calls partner')).toBeInTheDocument();
      expect(location()).toBe('/traces/T1/graph?risk=1&violation=3');
    });

    // A hold that fired under a `?iid=` must not pull a later step back to the held event.
    it('after a hold under a cold ?iid= naming the event, Previous still moves to the event before it', async () => {
      mockFetch();
      renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&iid=i2' });
      await findGraph();
      const layer = await screen.findByTestId('risk-layer');
      await within(layer).findByText('2 of 2');

      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2));
      mockFetch({ forest: FOREST_SHIFTED });
      await act(async () => FakeEventSource.last!.change('v2'));
      await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&iid=i2&violation=3'));
      await waitFor(() => expect(within(screen.getByTestId('risk-layer')).getByText('3 of 3')).toBeInTheDocument());

      await userEvent.click(within(screen.getByTestId('risk-layer')).getByRole('button', { name: /Previous/ }));
      await waitFor(() => expect(location()).toBe('/traces/T1/graph?risk=1&violation=2'));
      await waitFor(() => expect(within(screen.getByTestId('risk-layer')).getByText('2 of 3')).toBeInTheDocument());
      expect(within(within(screen.getByTestId('risk-layer')).getByTestId('policy-decision-panel')).getByText('search calls partner')).toBeInTheDocument();
    });

    // A plain `?iid=` is not a picked event, so an out-of-range `?violation=N` still lands on N.
    it('a linked ?violation=N with a plain ?iid= is not rewritten to 1, and lands on N once N arrives', async () => {
      mockFetch();
      renderWithProviders(harness(), { route: '/traces/T1/graph?risk=1&violation=3&iid=i0' });
      await findGraph();
      const layer = await screen.findByTestId('risk-layer');
      await waitFor(() => expect(within(layer).getByText('agent calls search')).toBeInTheDocument());
      expect(within(layer).queryByTestId('policy-decision-panel')).toBeNull();

      await act(async () => FakeEventSource.last!.change('v1'));
      await waitFor(() => expect(reads().detail).toBe(2));
      mockFetch({ forest: FOREST_SHIFTED });
      await act(async () => FakeEventSource.last!.change('v2'));
      await waitFor(() => expect(reads().detail).toBe(3));
      await act(() => new Promise((r) => setTimeout(r, 50)));
      expect(location()).toBe('/traces/T1/graph?risk=1&violation=3&iid=i0');
      // The selection is still the reader's plain interaction; the stepper is at the linked event 3 when an event edge is clicked next.
      act(() => {
        fireEvent.click(document.querySelector('[data-id="i2:request"] [data-test-id="edge-handler"]')!);
      });
      await waitFor(() => expect(within(screen.getByTestId('risk-layer')).getByText('3 of 3')).toBeInTheDocument());
    });
  });

  // A reveal without a span id must not navigate.
  it('a pin whose evidence has no span id does not land the tree on ?sel=undefined', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.includes('/interactions/i0/spans')) {
        return { ok: true, status: 200, json: async () => ({ spans: [{ span_id: undefined, role: 'anchor', parent_id: null, kind: 'CLIENT', service_name: 'svc' }] }) };
      }
      return mockFetchBase(url);
    });
    renderWithProviders(harness(), { route: '/traces/T1/graph?iid=i0' });
    await findGraph();
    await userEvent.click(await screen.findByRole('button', { name: 'Add to highlights' }, { timeout: 5000 }));
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(location()).toBe('/traces/T1/graph?iid=i0');
    expect(location()).not.toContain('undefined');
  });

  it('the tree writes no ?sel= for a revealed span that has no id', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/spans/ev-span')) return { ok: true, status: 200, json: async () => ({ name: 'no id' }) };
      return mockFetchBase(url);
    });
    renderWithProviders(harness(), { route: '/traces/T1/graph?iid=i0' });
    await findGraph();
    await screen.findAllByText('ev-span', undefined, { timeout: 5000 });
    await userEvent.click(await screen.findByRole('button', { name: 'Add to highlights' }, { timeout: 5000 }));
    await waitFor(() => expect(location()).toBe('/traces/T1/spans?sel=ev-span'), { timeout: 5000 });
    await act(() => new Promise((r) => setTimeout(r, 100)));
    expect(location()).toBe('/traces/T1/spans?sel=ev-span');
  }, 15000);
});
