import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { onlineManager } from '@tanstack/react-query';
import { renderWithProviders } from '../test/renderWithProviders';
import { FlowTables, type FlowSelection, type LegViewKey } from './FlowTables';
import { PinStore } from '../lib/pins';

/**
 * THE RISK LAYER on the flow view: with `risk` on, this
 * component reads the trace's risk, puts the risk bar (count and stepper) above
 * the views, colours the edges, adds the selected interaction's risk — and a
 * policy event's decision card — to the detail panel, and keeps the
 * `?violation=` / selection contract with `?iid=` as the selection. See
 * `FlowTablesProps.risk`.
 *
 * The harness below stands in for `TraceDetailPage`, the owner of the URL: it
 * holds `?iid` / `?eid` / `?violation` in state, writes them the way the page
 * does (`FlowSelection.violation` in the same write as the selection, deleted at
 * 1), and prints them, so each case can assert the URL the page would show.
 */
function Harness({
  initial = {},
  legView = 'graph',
  risk = true,
  initialEdges = false,
  initialSource = null,
}: {
  initial?: { iid?: string; eid?: string; violation?: string };
  legView?: LegViewKey;
  risk?: boolean;
  initialEdges?: boolean;
  initialSource?: string | null;
}) {
  const [params, setParams] = React.useState(initial);
  const [edges, setEdges] = React.useState(initialEdges);
  const [source, setSource] = React.useState<string | null>(initialSource);
  const writeViolation = (next: typeof params, n: number) => {
    if (n === 1) delete next.violation;
    else next.violation = String(n);
  };
  return (
    <>
      <div data-testid="params">{JSON.stringify({ ...params, ...(edges ? { edges: 'interaction' } : {}) })}</div>
      <FlowTables
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        legView={legView}
        showLegTabs={false}
        risk={risk}
        violation={params.violation ?? null}
        edgesPerInteraction={edges}
        onEdgesPerInteractionChange={setEdges}
        lineageSource={source}
        onLineageSourceChange={setSource}
        initialSelection={{ iid: params.iid, eid: params.eid }}
        onSelectionChange={(sel: FlowSelection | null) =>
          setParams((prev) => {
            const next = { ...prev };
            delete next.iid;
            delete next.eid;
            if (sel?.iid) next.iid = sel.iid;
            else if (sel?.eid) next.eid = sel.eid;
            if (sel?.violation != null) writeViolation(next, sel.violation);
            return next;
          })
        }
        onViolationChange={(n) =>
          setParams((prev) => {
            const next = { ...prev };
            writeViolation(next, n);
            return next;
          })
        }
      />
    </>
  );
}

const GRAPH_CHUNK_TIMEOUT = 10_000;

const ENTITIES = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
  { id: 'e3', kind: 'external', natural_key: 'external:(p,partner)', display_name: 'partner', detected_from: 'span' },
];

function flowLegs(seq: number) {
  return [
    { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: 'h1', error: false, seq },
    { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: seq + 1 },
  ];
}

/** Three interactions: i0 fired no rule, i1 and i2 are the two policy events. */
const INTERACTIONS = [
  { id: 'i0', caller_entity_id: 'e1', callee_entity_id: 'e2', summary: 'agent calls search', parent_interaction_id: null, legs: flowLegs(1), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
  { id: 'i1', caller_entity_id: 'e2', callee_entity_id: 'e3', summary: 'search calls partner', parent_interaction_id: 'i0', legs: flowLegs(3), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
  { id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3', summary: 'agent calls partner', parent_interaction_id: null, legs: flowLegs(5), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
];

function risk(interactionId: string, overrides: Record<string, unknown> = {}) {
  return {
    interaction_risk_id: `ir-${interactionId}`,
    interaction_id: interactionId,
    trace_id: 'T1',
    parent_interaction_id: null,
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    version: 1,
    computed_at: '2026-05-01T12:00:00Z',
    risk_level: 'low',
    enforcement_type: null,
    policy_event_count: 0,
    triggered_rule_ids: [],
    classification_summary: null,
    opa_policy_versions_used: ['v1'],
    overall_confidence: 0.9,
    ...overrides,
  };
}

function forestOf(ix: (typeof INTERACTIONS)[number], riskRecord: Record<string, unknown> | null) {
  return {
    interaction_id: ix.id,
    trace_id: 'T1',
    parent_interaction_id: ix.parent_interaction_id,
    caller_entity_id: ix.caller_entity_id,
    callee_entity_id: ix.callee_entity_id,
    summary: ix.summary,
    legs: ix.legs.map(({ leg_type, occurred_at, payload_hash, error }) => ({ leg_type, occurred_at, payload_hash, error })),
    risk: riskRecord,
    span_count: ix.span_count,
    anchor_count: ix.anchor_count,
  };
}

const FOREST = [
  forestOf(INTERACTIONS[0], risk('i0')),
  forestOf(INTERACTIONS[1], risk('i1', {
    risk_level: 'critical', enforcement_type: 'block', policy_event_count: 1, triggered_rule_ids: ['DG-001'],
    classification_summary: { request: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII'] } },
  })),
  forestOf(INTERACTIONS[2], risk('i2', {
    risk_level: 'high', enforcement_type: 'flag', policy_event_count: 1, triggered_rule_ids: ['DG-002'],
    classification_summary: { request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] } },
  })),
];

function rule(id: string, overrides: Record<string, unknown> = {}) {
  return {
    rule_id: id, rule_name: `Rule ${id}`, categories: ['privacy'], risk_level: 'critical', enforcement: 'block',
    explanation: `${id} explains.`, event_type: 'tool_call', data_items: [], data_destinations: [], allowed_actions: ['block'], rule_sources: [],
    ...overrides,
  };
}
const RULES = [rule('DG-001', { data_items: [{ regulatory_tags: ['PII'] }] }), rule('DG-002', { data_items: [{ regulatory_tags: ['PI'] }] })];

/** A labels reply for i1 with two carried-in values: a PID (PII) and a DATE (PI). */
const hop = (seq: number, role: string, iid: string, leg: string) => ({
  interaction_id: iid, leg_type: leg, seq, trace_id: 'T1', caller: 'a', callee: 'b', text: 'v', start: 0, end: 1, role, excerpt: null,
});
const origin = (iid: string, chains: unknown[][]) => ({
  origin_interaction_id: iid, origin_leg_type: 'response', origin_seq: 0, caller: 'a', callee: 'b',
  unit_text: 'v', unit_value: 'v', match_kind: 'whole', origin_trace_id: null, chains,
});
const LABELS_I1 = {
  lineage_status: 'complete',
  legs: [{
    leg_type: 'request', payload_hash: 'h1', seq: 3, found: null,
    carried: { max_level: 'RESTRICTED', regulatory_tags: ['PI', 'PII'], origin_count: 2 },
    labels: [
      { text: 'v', start: 0, end: 1, entity_type: 'PID', sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII'], found_here: false,
        carried_in: [origin('i0', [[hop(2, 'origin', 'i0', 'response'), hop(3, 'here', 'i1', 'request')]])] },
      { text: 'd', start: 2, end: 3, entity_type: 'DATE', sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], found_here: true,
        carried_in: [origin('i2', [[hop(5, 'origin', 'i2', 'request'), hop(3, 'here', 'i1', 'request')]])] },
    ],
  }],
};

/** A reachability answer: `legs` is the served route, nodes the entities it reaches. */
function reach(direction: 'fanin' | 'fanout', legs: unknown[] = [], entities: unknown[] = []) {
  return { direction, seed_entity_id: 'e1', entities, legs, state: legs.length ? 'derived' : 'no-adjacent', pending_frontier: [], truncated: false, status: 'complete', stopped_at_seq: null };
}

function mockFetch(over: {
  /** `null`: no risk record (404). `'error'`: the risk read fails (500). */
  forest?: unknown[] | null | 'error';
  labels?: unknown;
  fanin?: unknown;
  sources?: string[];
  /** Gates named reads behind a promise the test resolves (the risk read; an interaction's evidence). */
  gate?: { risk?: Promise<void>; evidence?: Promise<void> };
} = {}) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.startsWith('/risk/rules')) return { ok: true, status: 200, json: async () => ({ items: RULES, next_cursor: null }) };
    if (url.startsWith('/risk/alerts')) return { ok: true, status: 200, json: async () => ({ items: [], next_cursor: null }) };
    if (url.startsWith('/risk/traces/')) {
      if (over.gate?.risk) await over.gate.risk;
      if (over.forest === null) return { ok: false, status: 404, json: async () => ({ error: 'not found', detail: '', timestamp: '' }) };
      if (over.forest === 'error') return { ok: false, status: 500, json: async () => ({ error: 'boom', detail: '', timestamp: '' }) };
      return { ok: true, status: 200, json: async () => ({ trace_risk: { trace_id: 'T1' }, interactions: over.forest ?? FOREST }) };
    }
    if (url.includes('/labels')) return { ok: true, status: 200, json: async () => over.labels ?? LABELS_I1 };
    if (url.includes('/data-lineage-graph')) {
      const fanin = url.includes('direction=fanin');
      return { ok: true, status: 200, json: async () => (fanin ? (over.fanin ?? reach('fanin')) : reach('fanout')) };
    }
    if (url.includes('/data-lineage-summary')) {
      return { ok: true, status: 200, json: async () => ({ sources: over.sources ?? [], destinations: [], status: 'complete', stopped_at_seq: null }) };
    }
    if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: INTERACTIONS }) };
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
    if (url.includes('/data-lineage')) return { ok: true, status: 200, json: async () => ({ legs: [], status: 'complete', stopped_at_seq: null }) };
    if (/\/interactions\/[^/]+\/spans/.test(url) && over.gate?.evidence) await over.gate.evidence;
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

/** A promise a test resolves by hand. */
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => { resolve = r; });
  return { promise, resolve };
}

function riskReads() {
  const urls = (fetch as ReturnType<typeof vi.fn>).mock.calls.map(([u]) => String(u));
  return {
    detail: urls.filter((u) => u.startsWith('/risk/traces/')).length,
    rules: urls.filter((u) => u.startsWith('/risk/rules')).length,
    alerts: urls.filter((u) => u.startsWith('/risk/alerts')).length,
    any: urls.filter((u) => u.startsWith('/risk/')).length,
  };
}

async function findGraph() {
  return screen.findByTestId('execution-flow-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
}

/** Click an edge (PF's selection needs the `act`). */
function clickEdge(id: string) {
  const handler = document.querySelector(`[data-id="${id}"] [data-test-id="edge-handler"]`);
  if (!handler) throw new Error(`no clickable handler on edge ${id}`);
  act(() => {
    fireEvent.click(handler);
  });
}

const params = () => JSON.parse(screen.getByTestId('params').textContent ?? '{}');
const bar = () => screen.getByTestId('risk-bar');
/** The panel open on policy event `n` of 2: the bar's stepper says so and the decision card is in the panel. */
async function panelOnEvent(n: number) {
  await waitFor(() => {
    expect(within(bar()).getByText(`${n} of 2`)).toBeInTheDocument();
    expect(within(screen.getByTestId('risk-layer')).getByTestId('policy-decision-panel')).toBeInTheDocument();
  });
}
const edgeHtml = (id: string) => document.querySelector(`[data-id="${id}"]`)?.innerHTML ?? '';

describe('FlowTables — the risk layer', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('off: makes no risk read, paints no risk colour, and the detail panel has no risk card', async () => {
    mockFetch();
    renderWithProviders(<Harness risk={false} initial={{ iid: 'i1' }} />);
    await findGraph();
    await screen.findByText('search calls partner');
    expect(riskReads().any).toBe(0);
    expect(edgeHtml('i1:request')).not.toContain('danger-color');
    expect(screen.queryByTestId('risk-bar')).toBeNull();
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    expect(screen.queryByTestId('interaction-panel')).toBeNull();
  });

  it('on: reads the risk detail and the catalog once each, colours both legs by risk level and tags the edges', async () => {
    mockFetch();
    renderWithProviders(<Harness initial={{ iid: 'i0' }} />);
    await findGraph();
    await waitFor(() => expect(edgeHtml('i1:request')).toContain('danger-color')); // critical
    expect(edgeHtml('i1:response')).toContain('danger-color'); // the response leg too
    expect(edgeHtml('i2:request')).toContain('orange-300'); // high
    expect(edgeHtml('i0:request')).toContain('success-color'); // low
    expect(document.querySelector('[data-id="i1:request"] .pf-topology__edge__tag text')?.textContent).toBe('PII');
    expect(riskReads()).toEqual({ detail: 1, rules: 1, alerts: 1, any: 3 });
    expect(screen.getByRole('heading', { name: 'Alerts' })).toBeInTheDocument();
  });

  it('a cold ?violation=2 opens the panel on event 2 with the stepper and the decision card, writing no ?iid=', async () => {
    mockFetch();
    renderWithProviders(<Harness initial={{ violation: '2' }} />);
    await findGraph();
    const layer = await screen.findByTestId('risk-layer');
    expect(within(bar()).getByText('2 of 2')).toBeInTheDocument();
    const card = within(layer).getByTestId('policy-decision-panel');
    expect(within(card).getByText('agent calls partner')).toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'Rule DG-002' })).toHaveAttribute('href', '/risk/rules/DG-002');
    // The interaction's risk card.
    const panel = within(layer).getByTestId('interaction-panel');
    expect(within(panel).getByText('high')).toBeInTheDocument();
    expect(params()).toEqual({ violation: '2' });
    // The default selection is the event's interaction: its edges carry the selected treatment.
    expect(document.querySelector('[data-id="i2:request"] .dg-graph-edge--selected, [data-id="i2:request"].pf-m-selected')).not.toBeNull();
  });

  it('Previous moves the stepper, the URL and the panel together, deleting the default position', async () => {
    mockFetch();
    renderWithProviders(<Harness initial={{ violation: '2' }} />);
    await findGraph();
    await panelOnEvent(2);
    await userEvent.click(within(bar()).getByRole('button', { name: /Previous/ }));
    await waitFor(() => expect(params()).toEqual({}));
    await panelOnEvent(1);
    expect(within(screen.getByTestId('policy-decision-panel')).getByText('search calls partner')).toBeInTheDocument();
    // Previous on the first event wraps to the last.
    await userEvent.click(within(bar()).getByRole('button', { name: /Previous/ }));
    await waitFor(() => expect(params()).toEqual({ violation: '2' }));
    await panelOnEvent(2);
  });

  it('a policy event edge writes ?violation= and drops ?iid=; a plain edge writes ?iid= and pins the open event', async () => {
    mockFetch();
    renderWithProviders(<Harness />);
    await findGraph();
    await panelOnEvent(1);

    clickEdge('i2:request');
    await waitFor(() => expect(params()).toEqual({ violation: '2' }));
    await panelOnEvent(2);

    clickEdge('i0:request');
    await waitFor(() => expect(params()).toEqual({ iid: 'i0', violation: '2' }));
    const layer = await screen.findByTestId('risk-layer');
    await waitFor(() => expect(within(layer).getByText('agent calls search')).toBeInTheDocument());
    // No rule fired on i0: its risk shows, no stepper, no decision card.
    expect(within(layer).getByText('low')).toBeInTheDocument();
    expect(within(layer).queryByTestId('policy-decision-panel')).toBeNull();
    expect(within(layer).queryByText(/of 2/)).toBeNull();

    clickEdge('i1:request');
    await waitFor(() => expect(params()).toEqual({}));
    await panelOnEvent(1);
  });

  it('a cold ?iid= naming a policy event moves the stepper to it', async () => {
    mockFetch();
    renderWithProviders(<Harness initial={{ iid: 'i2' }} />);
    await findGraph();
    await panelOnEvent(2);
    expect(params()).toEqual({ iid: 'i2' });
  });

  it('says "not yet computed" for a selected interaction the risk read has no record of, and for a trace with none', async () => {
    mockFetch({ forest: [FOREST[1]] });
    renderWithProviders(<Harness initial={{ iid: 'i0' }} />);
    await findGraph();
    const layer = await screen.findByTestId('risk-layer');
    await waitFor(() => expect(within(layer).getByText('not yet computed')).toBeInTheDocument());
    expect(within(layer).getByText('not classified')).toBeInTheDocument();
    expect(within(layer).queryByTestId('policy-decision-panel')).toBeNull();
  });

  it('a trace with no risk record yet shows the layer as "not yet computed", no error', async () => {
    mockFetch({ forest: null });
    renderWithProviders(<Harness initial={{ iid: 'i1' }} />);
    await findGraph();
    const layer = await screen.findByTestId('risk-layer');
    await waitFor(() => expect(within(layer).getByText('not yet computed')).toBeInTheDocument());
    expect(edgeHtml('i1:request')).not.toContain('danger-color');
  });

  it('closing the panel keeps it closed for that event until another is picked', async () => {
    mockFetch();
    renderWithProviders(<Harness />);
    await findGraph();
    await panelOnEvent(1);
    await userEvent.click(screen.getByRole('button', { name: 'Close details' }));
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    // Nothing reopens it on its own.
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    clickEdge('i2:request');
    await panelOnEvent(2);
  });

  it('the right-click default is the decision-derived one with the layer on, every carried-in value with it off', async () => {
    mockFetch();
    const { unmount } = renderWithProviders(<Harness />);
    await findGraph();
    await waitFor(() => expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge-focus')).not.toBeNull());
    act(() => {
      fireEvent.contextMenu(document.querySelector('[data-id="i1:request"] .dg-graph-edge-focus')!);
    });
    const banner = await screen.findByTestId('track-banner');
    // DG-001 names PII: the PID is tracked, the DATE (PI) is not.
    expect(banner).toHaveTextContent('Tracking 1 value');
    expect(banner).toHaveTextContent('v PID');
    expect(banner).not.toHaveTextContent('DATE');
    // Both legs of the PID's route are lit on this graph, the response leg it was born on included.
    await waitFor(() => expect(document.querySelector('[data-id="i0:response"] .dg-graph-edge--carrier')).not.toBeNull());
    expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--carrier')).not.toBeNull();
    expect(document.querySelector('[data-id="i2:request"] .dg-graph-edge--dimmed')).not.toBeNull();
    unmount();

    renderWithProviders(<Harness risk={false} />);
    await findGraph();
    await waitFor(() => expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge-focus')).not.toBeNull());
    act(() => {
      fireEvent.contextMenu(document.querySelector('[data-id="i1:request"] .dg-graph-edge-focus')!);
    });
    const all = await screen.findByTestId('track-banner');
    await waitFor(() => expect(all).toHaveTextContent('Tracking 2 values'));
    expect(all).toHaveTextContent('d DATE');
  });

  it('colours the sequence diagram and the lineage graph too', async () => {
    mockFetch();
    const { unmount } = renderWithProviders(<Harness legView="diagram" initial={{ iid: 'i0' }} />);
    const rows = await screen.findAllByTestId('dg-seq-message');
    await waitFor(() => expect(rows.find((r) => r.getAttribute('data-seq') === '3')?.innerHTML).toContain('danger-color'));
    unmount();

    renderWithProviders(<Harness legView="lineage" initial={{ iid: 'i0' }} />);
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await waitFor(() => expect(edgeHtml('i1:request')).toContain('danger-color'));
  });

  it('"Edges per interaction" draws one arrow per call, and the overlay lights it when the value sat on the response leg', async () => {
    mockFetch();
    renderWithProviders(<Harness />); // the layer on: the right-click tracks the PID only (DG-001 names PII)
    await findGraph();
    await waitFor(() => expect(document.querySelector('[data-id="i1:response"]')).not.toBeNull());
    expect(screen.getByRole('button', { name: 'Edges per leg' })).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(screen.getByRole('button', { name: 'Edges per interaction' }));
    await waitFor(() => expect(document.querySelector('[data-id="i1:response"]')).toBeNull());
    expect(document.querySelector('[data-id="i1:request"]')).not.toBeNull();
    expect(document.querySelector('[data-id="i0:response"]')).toBeNull();
    expect(params()).toEqual({ edges: 'interaction' });
    expect(screen.queryByText(/entity pair/)).toBeNull(); // the parallel-groups notice is off in this mode

    // The PID was born on i0's RESPONSE: with one arrow per call, that arrow is i0's request edge, and it lights.
    await waitFor(() => expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge-focus')).not.toBeNull());
    act(() => {
      fireEvent.contextMenu(document.querySelector('[data-id="i1:request"] .dg-graph-edge-focus')!);
    });
    await screen.findByTestId('track-banner');
    await waitFor(() => expect(document.querySelector('[data-id="i0:request"] .dg-graph-edge--carrier')).not.toBeNull());
    expect(document.querySelector('[data-id="i1:request"] .dg-graph-edge--carrier')).not.toBeNull();
    expect(document.querySelector('[data-id="i2:request"] .dg-graph-edge--dimmed')).not.toBeNull();

    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edges per leg' }));
    await waitFor(() => expect(document.querySelector('[data-id="i1:response"]')).not.toBeNull());
    expect(params()).toEqual({}); // the default selection writes no ?iid=, the per-leg default no ?edges=
  });

  it('offers the edge mode on the lineage graph too, and on no other view', async () => {
    mockFetch();
    const { unmount } = renderWithProviders(<Harness risk={false} legView="lineage" />);
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await userEvent.click(screen.getByRole('button', { name: 'Edges per interaction' }));
    await waitFor(() => expect(document.querySelector('[data-id="i1:response"]')).toBeNull());
    expect(document.querySelector('[data-id="i1:request"]')).not.toBeNull();
    unmount();
    renderWithProviders(<Harness risk={false} legView="diagram" />);
    await screen.findAllByTestId('dg-seq-message');
    expect(screen.queryByRole('button', { name: 'Edges per interaction' })).toBeNull();
  });

  // A dismissal is a dismissal whatever was shown.
  it('closing the panel on a plain interaction, or clicking the graph background, keeps it closed', async () => {
    mockFetch();
    renderWithProviders(<Harness />);
    await findGraph();
    await panelOnEvent(1);
    clickEdge('i0:request');
    await waitFor(() => expect(params()).toEqual({ iid: 'i0' }));
    await waitFor(() => expect(within(screen.getByTestId('risk-layer')).getByText('agent calls search')).toBeInTheDocument());

    await userEvent.click(screen.getByRole('button', { name: 'Close details' }));
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    expect(params()).toEqual({});

    // Reopen on the event by stepping to it through a click, then dismiss by the background.
    clickEdge('i2:request');
    await panelOnEvent(2);
    act(() => {
      fireEvent.click(document.querySelector('[data-kind="graph"] > rect')!);
    });
    await waitFor(() => expect(screen.queryByTestId('risk-layer')).toBeNull());
    await act(() => new Promise((r) => setTimeout(r, 50)));
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    expect(params()).toEqual({ violation: '2' }); // the open event is kept in the URL, the panel stays closed
  });

  // The default never supersedes a reader's click that is still fetching its evidence.
  it('a click made before the risk read lands keeps its selection when the read arrives mid-fetch', async () => {
    const riskGate = deferred();
    const evidenceGate = deferred();
    mockFetch({ gate: { risk: riskGate.promise, evidence: evidenceGate.promise } });
    renderWithProviders(<Harness />);
    await findGraph();
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    clickEdge('i0:request'); // the evidence fetch is now pending
    await act(async () => {
      riskGate.resolve(); // the forest lands first: two policy events, nothing selected yet
      await new Promise((r) => setTimeout(r, 20));
    });
    await act(async () => {
      evidenceGate.resolve();
      await new Promise((r) => setTimeout(r, 20));
    });
    const layer = await screen.findByTestId('risk-layer');
    await waitFor(() => expect(within(layer).getByText('agent calls search')).toBeInTheDocument());
    expect(params()).toEqual({ iid: 'i0' });
    expect(within(layer).queryByTestId('policy-decision-panel')).toBeNull();
  });

  // On the lineage graph with edges per interaction, a served route through a response leg lights its interaction's one arrow.
  it('the lineage route through a response leg lights the interaction\'s arrow with edges per interaction', async () => {
    const route = {
      fanin: reach('fanin', [{ interaction_id: 'i0', leg_type: 'response', from_entity_id: 'e2', to_entity_id: 'e1', seq: 2 }],
        [{ id: 'e2', natural_key: 'tool:(p,svc)', kind: 'tool', display_name: 'search', hops: 1 }]),
      sources: ['tool:(p,svc)'],
    };
    mockFetch(route);
    const { unmount } = renderWithProviders(<Harness risk={false} legView="lineage" initial={{ eid: 'e1' }} initialSource="tool:(p,svc)" />);
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    // Per leg: the response arrow itself is upstream.
    await waitFor(() => expect(document.querySelector('[data-id="i0:response"] .dg-graph-edge--upstream')).not.toBeNull());
    unmount();

    mockFetch(route);
    renderWithProviders(<Harness risk={false} legView="lineage" initial={{ eid: 'e1' }} initialSource="tool:(p,svc)" initialEdges />);
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await waitFor(() => expect(document.querySelector('[data-id="i0:request"]')).not.toBeNull());
    expect(document.querySelector('[data-id="i0:response"]')).toBeNull();
    await waitFor(() => expect(document.querySelector('[data-id="i0:request"] .dg-graph-edge--upstream')).not.toBeNull());
    expect(document.querySelector('[data-id="i0:request"] .dg-graph-edge--dimmed')).toBeNull();
  });

  // An explicit step beats a click still fetching its evidence.
  it('Next pressed while an earlier plain click is still loading wins — the panel lands on the stepped-to event', async () => {
    mockFetch();
    renderWithProviders(<Harness />);
    await findGraph();
    await panelOnEvent(1);
    // A plain click whose evidence does not return in time, then Next.
    const laterGate = deferred();
    mockFetch({ gate: { evidence: laterGate.promise } });
    clickEdge('i0:request');
    await userEvent.click(within(bar()).getByRole('button', { name: /Next/ }));
    await waitFor(() => expect(params()).toEqual({ violation: '2' }));
    await act(async () => {
      laterGate.resolve();
      await new Promise((r) => setTimeout(r, 50));
    });
    await panelOnEvent(2);
    expect(params()).toEqual({ violation: '2' });
    expect(within(screen.getByTestId('risk-layer')).queryByText('agent calls search')).toBeNull();
  });

  // A dismissal while the default is still fetching its evidence holds.
  it('a background click while the default selection is still loading keeps the panel closed', async () => {
    const evidenceGate = deferred();
    mockFetch({ gate: { evidence: evidenceGate.promise } });
    renderWithProviders(<Harness />);
    await findGraph();
    await waitFor(() => expect(document.querySelector('[data-kind="graph"] > rect')).not.toBeNull());
    await act(() => new Promise((r) => setTimeout(r, 30))); // the default's silent select is now in flight
    act(() => {
      fireEvent.click(document.querySelector('[data-kind="graph"] > rect')!);
    });
    await act(async () => {
      evidenceGate.resolve();
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Close details' })).toBeNull();
  });

  // A second Next inside one evidence fetch must not drop the step's own select.
  it('Next pressed twice before the first step\'s evidence returns still lands on event 2', async () => {
    mockFetch();
    renderWithProviders(<Harness />);
    await findGraph();
    await panelOnEvent(1);
    const gate = deferred();
    mockFetch({ gate: { evidence: gate.promise } });
    const next = () => within(bar()).getByRole('button', { name: /Next/ });
    await userEvent.click(next());
    await waitFor(() => expect(params()).toEqual({ violation: '2' }));
    await userEvent.click(next()); // the stepper still reads 1: the first step's select is in flight
    await act(async () => {
      gate.resolve();
      await new Promise((r) => setTimeout(r, 50));
    });
    await panelOnEvent(2);
    expect(params()).toEqual({ violation: '2' });
  });

  // The bar is the stepper's place: on screen whatever the panel shows.
  it('the risk bar counts the policy events beside the stepper, stays with the panel closed, and a step reopens the panel', async () => {
    mockFetch();
    renderWithProviders(<Harness />);
    await findGraph();
    await panelOnEvent(1);
    expect(within(bar()).getByText('2 policy events')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Close details' }));
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    expect(within(bar()).getByText('1 of 2')).toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole('button', { name: /Next/ }));
    await panelOnEvent(2);
    expect(params()).toEqual({ violation: '2' });
  });

  // One event: the stepper still steps (it wraps), so the bar can reopen a closed panel on it.
  it('with one policy event, a step reopens the closed panel on it', async () => {
    mockFetch({ forest: [FOREST[0], FOREST[1]] });
    renderWithProviders(<Harness />);
    await findGraph();
    await waitFor(() => expect(within(screen.getByTestId('risk-layer')).getByTestId('policy-decision-panel')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'Close details' }));
    expect(screen.queryByTestId('risk-layer')).toBeNull();
    expect(within(bar()).getByText('1 of 1')).toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole('button', { name: /Next/ }));
    await waitFor(() => expect(within(screen.getByTestId('risk-layer')).getByTestId('policy-decision-panel')).toBeInTheDocument());
    expect(params()).toEqual({});
  });

  it('a step from a plain interaction moves the panel to the stepped-to event', async () => {
    mockFetch();
    renderWithProviders(<Harness />);
    await findGraph();
    await panelOnEvent(1);
    clickEdge('i0:request');
    await waitFor(() => expect(within(screen.getByTestId('risk-layer')).getByText('agent calls search')).toBeInTheDocument());
    expect(within(bar()).getByText('1 of 2')).toBeInTheDocument();
    await userEvent.click(within(bar()).getByRole('button', { name: /Next/ }));
    await waitFor(() => expect(params()).toEqual({ violation: '2' }));
    await panelOnEvent(2);
    expect(within(screen.getByTestId('risk-layer')).queryByText('agent calls search')).toBeNull();
  });

  it('says "1 policy event" for one, "0 policy events" and why for none, and that there is no risk record without one', async () => {
    mockFetch({ forest: [FOREST[0], FOREST[1]] });
    const one = renderWithProviders(<Harness />);
    await findGraph();
    await waitFor(() => expect(within(bar()).getByText('1 policy event')).toBeInTheDocument());
    expect(within(bar()).getByText('1 of 1')).toBeInTheDocument();
    one.unmount();

    mockFetch({ forest: [FOREST[0]] });
    const none = renderWithProviders(<Harness />);
    await findGraph();
    await waitFor(() => expect(within(bar()).getByText('0 policy events')).toBeInTheDocument());
    expect(within(bar()).getByText('No rule was triggered by any interaction in this trace.')).toBeInTheDocument();
    expect(within(bar()).queryByRole('button', { name: /Next/ })).toBeNull();
    none.unmount();

    mockFetch({ forest: null });
    renderWithProviders(<Harness />);
    await findGraph();
    await waitFor(() => expect(within(bar()).getByText('No risk record for this trace yet.')).toBeInTheDocument());
    expect(within(bar()).queryByText(/policy event/)).toBeNull();
  });

  // Nor is a read paused offline (no data, not fetching): the layer switched on with the network gone.
  it('keeps the spinner on the bar while the risk read is paused offline', async () => {
    mockFetch();
    const { rerender } = renderWithProviders(<Harness risk={false} />);
    await findGraph();
    onlineManager.setOnline(false);
    try {
      rerender(<Harness risk />);
      await waitFor(() => expect(within(bar()).getByLabelText('Loading risk')).toBeInTheDocument());
      await act(() => new Promise((r) => setTimeout(r, 50)));
      expect(within(bar()).getByLabelText('Loading risk')).toBeInTheDocument();
      expect(within(bar()).queryByText(/policy event/)).toBeNull();
    } finally {
      onlineManager.setOnline(true);
    }
  });

  // A failed read is never shown as a trace with no risk.
  it('says the risk read failed on the bar, with no count and no "no rule" words', async () => {
    mockFetch({ forest: 'error' });
    renderWithProviders(<Harness />);
    await findGraph();
    await waitFor(() => expect(within(bar()).getByText('The risk read failed.')).toBeInTheDocument());
    expect(within(bar()).queryByText(/policy event/)).toBeNull();
    expect(within(bar()).queryByText(/No rule was triggered/)).toBeNull();
  });
});
