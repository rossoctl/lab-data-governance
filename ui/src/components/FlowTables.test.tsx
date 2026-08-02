import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { FlowTables, type LegViewKey } from './FlowTables';
import { PinStore } from '../lib/pins';

/**
 * The Interactions tab (`legView`) AND the Lineage tab's traced data source
 * (`lineageSource`) are controlled props — TraceDetailPage owns both as the `?legs` and
 * `?src` URL params. This harness stands in for that owner so a test can click the tab
 * and see the table swap, and can pick a source and see the reads fire, exactly as the
 * page does.
 *
 * `initialSource` seeds `?src` the way a deep link or a reload does. It defaults to
 * `null` — NOT to a source — because that is the honest first-open state: there is no
 * source the app is entitled to pick on the reader's behalf (see
 * `lineageReachability.resolveSourceChoice`), so a test wanting an ANSWER has to supply
 * one, exactly as a reader has to choose one.
 */
function FlowTablesWithLegTabs({
  initialSource = null,
  ...props
}: Omit<
  React.ComponentProps<typeof FlowTables>,
  'legView' | 'onLegViewChange' | 'lineageSource' | 'onLineageSourceChange'
> & { initialSource?: string | null }) {
  const [legView, setLegView] = React.useState<LegViewKey>('tree');
  const [source, setSource] = React.useState<string | null>(initialSource);
  return (
    <FlowTables
      {...props}
      legView={legView}
      onLegViewChange={setLegView}
      lineageSource={source}
      onLineageSourceChange={setSource}
    />
  );
}

/**
 * The natural key of `e1` in the `ENTITIES` fixture below — the source the Lineage-tab
 * cases trace.
 *
 * The reachability read is `fanin(entity, source)` with `source` REQUIRED
 * (`docs/data_lineage_alg.md`'s `## API`), so a case asserting an answer must name one
 * or the tab correctly asks nothing at all. It also has to be a member of the summary's
 * `sources` (see `mockFetch`'s `sources` default), or `resolveSourceChoice` reports it
 * `'stale'` and still asks nothing.
 */
const SOURCE_E1 = 'agent:(p,a)';

/**
 * Deadline for awaiting the lazily-imported Execution Flow graph past its Suspense
 * boundary. `findBy*`'s 1000ms default is not enough for the first `?legs=graph`
 * render in a file: `React.lazy` has to transform and evaluate PF topology (plus
 * d3 / dagre / mobx) before the component exists — the very ~387kB the production
 * build now keeps out of the main bundle. The waits below still poll the real
 * condition; only the deadline is raised.
 */
const GRAPH_CHUNK_TIMEOUT = 10_000;

const ENTITIES = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
];
const INTERACTIONS = [
  {
    id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2',
    summary: 'agent calls search', parent_interaction_id: null,
    legs: [
      { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: false, seq: 1 },
      { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: 2 },
    ],
    duration_seconds: 1, any_error: false,
    span_count: 2, anchor_count: 1,
  },
];

/** Replace one interaction's leg payload hashes (the test helper the payload
 *  cases below use, now that hashes live on legs — ADR-0025). */
function withLegHashes(reqHash: string | null, respHash: string | null) {
  return {
    ...INTERACTIONS[0],
    legs: [
      { ...INTERACTIONS[0].legs[0], payload_hash: reqHash },
      { ...INTERACTIONS[0].legs[1], payload_hash: respHash },
    ],
  };
}

// Evidence rows returned for the entity/interaction /spans sub-resources.
const ENTITY_EVIDENCE = [
  { span_id: 'span-abc', role: 'discovered_via', parent_id: 'span-parent', kind: 'SERVER', service_name: 'svc' },
  { span_id: 'span-def', role: 'identified_via', parent_id: null, kind: 'CLIENT', service_name: 'svc' },
];
const INTERACTION_EVIDENCE = [
  { span_id: 'span-xyz', role: 'anchor', parent_id: 'span-parent', kind: 'CLIENT', service_name: 'svc' },
];

/**
 * A reachability response for either direction, defaulting to the non-claiming
 * `no-adjacent` (ADR-0028 D15) so a test that does not care about lineage does not
 * accidentally assert an answer.
 */
function mkReach(direction: 'fanin' | 'fanout') {
  return {
    direction,
    seed_entity_id: 'e2',
    entities: [] as unknown[],
    legs: [] as unknown[],
    state: 'no-adjacent',
    pending_frontier: [] as string[],
    truncated: false,
    status: 'complete',
    stopped_at_seq: null,
  };
}

function mockFetch(over: { sources?: string[]; fanin?: unknown; fanout?: unknown } = {}) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    // THE TWO LINEAGE-TAB READS FIRST, and the order is load-bearing rather than
    // stylistic: the reachability URL is `/entities/<eid>/data-lineage-graph`, which
    // also matches the `/entities/` evidence branch below. Matched after it, a
    // reachability request would be answered with `{spans: […]}` — and because the
    // entity-evidence branch is what the selection's fetch uses, the mis-ordering
    // showed up as the ENTITY SELECTION silently failing rather than as a lineage bug.
    if (url.includes('/data-lineage-graph')) {
      // A SOURCE-LESS REQUEST IS A 400 ON THE REAL SERVER, so it is one here. Answering
      // it with data instead would let a regression that dropped the required `source`
      // parameter keep every one of these tests green while shipping a tab that 400s on
      // every read.
      if (!/[?&]source=/.test(url)) return { ok: false, status: 400, json: async () => ({}) };
      const wantFanin = url.includes('direction=fanin');
      const chosen = wantFanin ? over.fanin : over.fanout;
      return {
        ok: true,
        status: 200,
        json: async () => chosen ?? mkReach(wantFanin ? 'fanin' : 'fanout'),
      };
    }
    if (url.includes('/data-lineage-summary')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({
          // Defaults to OFFERING `SOURCE_E1`, because this array is now also the
          // choosable source list: a summary that did not contain the source a case
          // traces would make that case silently exercise the `'stale'` state.
          sources: over.sources ?? [SOURCE_E1],
          destinations: [],
          status: 'complete',
          stopped_at_seq: null,
        }),
      };
    }
    if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: INTERACTIONS }) };
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
    if (url.includes('/entities/')) return { ok: true, status: 200, json: async () => ({ spans: ENTITY_EVIDENCE }) };
    if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

describe('FlowTables', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('renders the Entities and Interactions tables from the trace resources', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    // The Entities table lists both entities (agent-a appears in the caller
    // cell too, so scope to the Entities table by its aria-label).
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    const entitiesTable = screen.getByLabelText('Entities');
    expect(within(entitiesTable).getByText('agent-a')).toBeInTheDocument();
    expect(within(entitiesTable).getByText('search')).toBeInTheDocument();
    // Interaction row shows the span count summary.
    expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument();
  });

  it('shows an empty-state hint when the derived graph is empty', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
      ok: true, status: 200, json: async () => ({ interactions: [], entities: [] }),
    }));
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() =>
      expect(screen.getByText(/No interaction data for this trace yet/i)).toBeInTheDocument(),
    );
  });

  it('shows a pin dot on a row after it is added to highlights (no stale memo)', async () => {
    mockFetch();
    const pins = new PinStore();
    // A parent that re-renders FlowTables on pin change, like TraceDetailPage.
    function Host() {
      const [, force] = React.useReducer((n: number) => n + 1, 0);
      return <FlowTables traceId="T1" pins={pins} onPinsChange={force} />;
    }
    renderWithProviders(<Host />);
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    // Select the 'search' entity (unique to the entities table), then Add to highlights.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    await userEvent.click(await screen.findByRole('button', { name: /Add to highlights/i }));
    // The entity row now carries a pin dot (aria-label="pinned").
    await waitFor(() => expect(screen.getAllByLabelText('pinned').length).toBeGreaterThan(0));
  });

  it('fires onRevealSpans with the evidence span ids when Add-to-highlights is clicked', async () => {
    mockFetch();
    const onRevealSpans = vi.fn();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} onRevealSpans={onRevealSpans} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    // Select the interaction (its evidence is INTERACTION_EVIDENCE: span-xyz).
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Add to highlights/i }));
    expect(onRevealSpans).toHaveBeenCalledWith(['span-xyz']);
  });

  it('shows the highlight color swatch on the pin button (next-free color unpinned, the pin color once pinned)', async () => {
    mockFetch();
    const pins = new PinStore();
    function Host() {
      const [, force] = React.useReducer((n: number) => n + 1, 0);
      return <FlowTables traceId="T1" pins={pins} onPinsChange={force} />;
    }
    renderWithProviders(<Host />);
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));

    // Unpinned: the swatch previews the color the pin WOULD get (slot 0 =
    // first palette color #ffd479).
    const swatch = await screen.findByTestId('highlight-swatch');
    expect(swatch).toHaveStyle({ background: '#ffd479' });
    // The swatch carries its own right margin so it doesn't crowd the label
    // text (PF's default icon gap is too tight for a color chip).
    expect(swatch).toHaveStyle({ marginRight: '0.375rem' });

    // After pinning, the swatch shows the color the pin actually HAS.
    await userEvent.click(screen.getByRole('button', { name: /Add to highlights/i }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Unpin/i })).toBeInTheDocument(),
    );
    expect(screen.getByTestId('highlight-swatch')).toHaveStyle({ background: '#ffd479' });
  });

  it('selects an interaction row on click and shows its summary under a promoted "Interaction" header', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    // Click the interaction row via its unique span-count cell.
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // The panel caption is now the selection's own name ('Interaction'), which
    // folds in what used to be a separate 'Details' header + section header.
    // A string `name` is an exact (normalized) accessible-name match, so this
    // targets the 'Interaction' caption and not the 'Interactions' table title.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument(),
    );
    // The generic 'Details' caption is gone once something is selected.
    expect(screen.queryByRole('heading', { name: 'Details' })).not.toBeInTheDocument();
    expect(screen.getByText('agent calls search')).toBeInTheDocument();
  });

  it('selects an entity row on click and shows it under a promoted "Entity" header', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    // The caption is the entity's own name; the generic 'Details' is gone.
    // A string `name` is an exact (normalized) match, so this targets the
    // 'Entity' caption and not the 'Entities' table title.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('heading', { name: 'Details' })).not.toBeInTheDocument();
  });

  it('floats the detail panel only after a selection, and dismisses it on close', async () => {
    mockFetch();
    const onSelectionChange = vi.fn();
    renderWithProviders(
      <FlowTables
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        onSelectionChange={onSelectionChange}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    // Nothing floats before a click — no panel caption, no placeholder hint.
    expect(screen.queryByRole('heading', { name: 'Details' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Select an entity or interaction/i)).not.toBeInTheDocument();
    // Selecting a row floats the panel; its × close button clears the selection.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument(),
    );
    await userEvent.click(screen.getByRole('button', { name: /Close details/i }));
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Entity' })).not.toBeInTheDocument(),
    );
    expect(onSelectionChange).toHaveBeenLastCalledWith(null);
  });

  it('flat view lists each leg as its own row, ordered by seq, ignoring the tree', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    // Tree view first: one interaction row, no per-leg breakdown.
    expect(screen.queryByLabelText('Interactions (flat)')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: 'Flat' }));
    // The single interaction's two legs become two rows in seq order.
    const flat = await screen.findByLabelText('Interactions (flat)');
    const body = within(flat).getAllByRole('row').slice(1); // drop the header row
    expect(body).toHaveLength(2);
    expect(within(body[0]).getByText('request')).toBeInTheDocument();
    expect(within(body[1]).getByText('response')).toBeInTheDocument();

    // Direction is per-leg (ADR-0025): the interaction is caller=agent-a (e1),
    // callee=search (e2). The request leg keeps that direction; the response
    // leg flows callee → caller, so its Caller/Callee are swapped.
    const cell = (row: HTMLElement, label: string) =>
      row.querySelector(`[data-label="${label}"]`) as HTMLElement;
    expect(within(cell(body[0], 'Caller')).getByText('agent-a')).toBeInTheDocument();
    expect(within(cell(body[0], 'Callee')).getByText('search')).toBeInTheDocument();
    // Response leg: swapped.
    expect(within(cell(body[1], 'Caller')).getByText('search')).toBeInTheDocument();
    expect(within(cell(body[1], 'Callee')).getByText('agent-a')).toBeInTheDocument();
  });

  // --- The Execution Flow graph as the last `?legs` tab. It moved here from the
  // top-level `/graph` view segment because it presents the SAME two reads these
  // tables do. The URL-level contract is pinned in TraceDetailPage.test.tsx (which
  // owns `?legs`); these are the cases only this component can state — that the
  // tab set really is one row, that the graph replaces the interactions table but
  // not the Entities table, and that the swap costs no extra fetch.

  it('offers Execution Flow in the same tab row as Tree and Flat', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    // One tab row, five tabs, in this ORDER — not a separate control elsewhere.
    // `Interaction diagram` sits between Flat and Execution Flow deliberately: it
    // renders the Flat list's own rows as a picture, so it belongs next to the tab
    // whose order it reproduces rather than after the graph.
    // `Lineage` sits immediately AFTER Execution Flow, and the adjacency is the
    // claim: it draws the identical graph (one component, one deriveGraph) and adds
    // only a highlight, so it is the graph tab's reading rather than a peer of it.
    const row = screen.getByRole('tablist');
    expect(within(row).getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Tree',
      'Flat',
      'Interaction diagram',
      'Execution Flow',
      'Lineage',
    ]);
  });

  it('swaps the interactions table for the sequence diagram on the Interaction diagram tab', async () => {
    // The diagram stands in for the interactions TABLE, like the graph — and for
    // the same reason keeps the Entities table, which is a fact of the flow view
    // rather than a part of that table. Unlike the graph it is NOT lazily imported
    // (hand-rolled SVG, no new dependency), so no chunk-load await is needed here.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Interaction diagram' }));

    expect(await screen.findByTestId('interaction-diagram')).toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Entities')).toBeInTheDocument();
    // One lifeline per participant and one arrow per LEG of the single fixture
    // interaction — the same two rows the Flat tab lists.
    expect(screen.getAllByTestId('dg-seq-lifeline')).toHaveLength(2);
    expect(screen.getAllByTestId('dg-seq-message')).toHaveLength(2);
  });

  it('opens the interaction detail panel when a diagram message is clicked', async () => {
    // The diagram honours the same `onSelect(ix)` contract the tables do: a click
    // on either leg selects the parent INTERACTION, since legs have no selection of
    // their own. Asserted here rather than only in the component's own test because
    // this is where the callback is actually wired to `selectInteraction`.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Interaction diagram' }));
    await userEvent.click((await screen.findAllByTestId('dg-seq-message'))[1]);

    // The same panel a Flat/Tree row click opens, with the interaction's fields.
    await waitFor(() => expect(screen.getByText('agent calls search')).toBeInTheDocument());
    expect(screen.getByText('interaction_id')).toBeInTheDocument();
  });

  it('swaps the interactions table for the graph, keeping the Entities table', async () => {
    // The graph stands in for the interactions TABLE only. The Entities table is a
    // fact of the flow view rather than a part of that table — and the graph's
    // nodes ARE those entities, so the table stays to carry the columns
    // (kind / natural key / detected from) and the click target the nodes lack.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Execution Flow/i }));

    // The graph is lazily imported (React.lazy + Suspense keeps PF topology out of
    // the main bundle), so the first render of this tab has to be awaited past the
    // chunk load — polling the real condition, not sleeping.
    const graph = await screen.findByTestId('execution-flow-graph', undefined, {
      timeout: GRAPH_CHUNK_TIMEOUT,
    });
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Interactions (flat)')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Entities')).toBeInTheDocument();

    // The graph is a third presentation of the tables' OWN two reads, not a new
    // endpoint: it hits the same two query keys, so no third resource is fetched
    // for it. (It does re-observe those keys, and this test's QueryClient sets no
    // staleTime, so TanStack may revalidate them on the graph's mount — hence the
    // assertion is on the set of URLs touched, not on a call count.)
    const reads = new Set(
      (fetch as ReturnType<typeof vi.fn>).mock.calls
        .map((c) => String(c[0]))
        .filter((u) => /\/(entities|interactions)$/.test(u)),
    );
    expect([...reads].sort()).toEqual([
      '/api/traces/T1/entities',
      '/api/traces/T1/interactions',
    ]);

    // Back to Tree restores the table and drops the graph.
    await userEvent.click(screen.getByRole('tab', { name: 'Tree' }));
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    expect(graph).not.toBeInTheDocument();
  });

  // --- Clicking a graph EDGE opens the interaction detail panel. The end-to-end path
  // through the real component, which is what only this file can state: the graph
  // reports an interaction id, THIS component resolves it and calls the very
  // `selectInteraction` a Flat/Tree row click calls, and the panel plus the `?iid`
  // mirroring follow from that single path. The graph-side mechanics (which element
  // carries the click, the per-interaction treatment, composition with the lineage
  // highlight) are pinned in ExecutionFlowGraph.test.tsx.
  //
  // `fireEvent.click`, NOT `userEvent.click`, for the edge: `userEvent` sends a
  // `mousedown` too, which reaches the pan/zoom behavior's d3-zoom listener, and
  // d3-zoom reads `svg.width.baseVal` — unimplemented in jsdom, so it throws an
  // unhandled error for the whole file. PF binds the edge handler to `onClick` alone,
  // so dispatching exactly that is both sufficient and honest. (Every other click in
  // this file stays on `userEvent` — they are all real HTML controls.)

  /** The `<g>` PF binds the edge's click handler to. Note PF's hyphenated attribute. */
  const edgeHandler = (id: string) =>
    document.querySelector(`[data-id="${id}"] [data-test-id="edge-handler"]`)!;

  it('opens the interaction detail panel when a graph EDGE is clicked', async () => {
    // The requirement, end to end: an edge IS one leg, clicking it selects the leg's
    // parent INTERACTION, and the panel that opens is the SAME one a Flat-table row
    // click opens — same evidence fetch, same fields.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Execution Flow/i }));
    await screen.findByTestId('execution-flow-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await waitFor(() => expect(document.querySelectorAll('[data-kind="edge"]')).toHaveLength(2));

    fireEvent.click(edgeHandler('i1:request'));

    // The interaction's own panel: its summary and the promoted section caption, which
    // is exactly what a row click produces (asserted for the diagram case above with
    // the same two strings — one panel, one path).
    await waitFor(() => expect(screen.getByText('agent calls search')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument();
    expect(screen.getByText('interaction_id')).toBeInTheDocument();
    // The evidence really was fetched through the shared `selectInteraction`, not
    // faked: the anchor span id from the interaction /spans stub is on screen. `getAll`
    // because it legitimately appears twice — once as the `anchor span(s)` field and
    // once as a row in the evidence list — which is itself the shape a row click
    // produces.
    expect(screen.getAllByText(/span-xyz/).length).toBeGreaterThan(0);
  });

  it('mirrors the clicked edge\'s interaction into ?iid', async () => {
    // The URL half of the same path. `onSelectionChange` is what the page turns into
    // `?iid`, so an edge click must fire it with the INTERACTION's id — not the leg's
    // `i1:request` edge id, which is not a thing the URL knows about.
    mockFetch();
    const onSelectionChange = vi.fn();
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        onSelectionChange={onSelectionChange}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Execution Flow/i }));
    await screen.findByTestId('execution-flow-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await waitFor(() => expect(document.querySelectorAll('[data-kind="edge"]')).toHaveLength(2));

    fireEvent.click(edgeHandler('i1:response'));

    await waitFor(() => expect(onSelectionChange).toHaveBeenCalledWith({ iid: 'i1' }));
  });

  it('gives BOTH legs of the clicked interaction the selected treatment', async () => {
    // The feedback the panel alone cannot give: the reader must see WHICH arrow they
    // picked. Both legs, because the selection is the interaction — and this is the
    // round trip, so it also proves the id the graph reported came back down as
    // `selectedInteractionId` rather than the graph marking itself locally.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Execution Flow/i }));
    await screen.findByTestId('execution-flow-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await waitFor(() => expect(document.querySelectorAll('[data-kind="edge"]')).toHaveLength(2));
    // Nothing selected yet, so no treatment anywhere.
    expect(document.querySelector('.dg-graph-edge--selected')).toBeNull();

    fireEvent.click(edgeHandler('i1:request'));

    await waitFor(() =>
      expect(document.querySelectorAll('.dg-graph-edge--selected')).toHaveLength(2),
    );
    expect(document.querySelector('[data-id="i1:response"] .dg-graph-edge--selected')).not.toBeNull();
  });

  it('selects the SAME interaction from an edge as from a Flat row — one path', async () => {
    // The anti-duplication guard, and the whole reason `selectInteractionById` is a
    // three-line adapter rather than a second selection implementation: the panel a
    // reader gets from an arrow must be indistinguishable from the one they get from a
    // row. Asserted by producing both and comparing the rendered field list.
    mockFetch();
    const { unmount } = renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    // Via the FLAT table's row.
    await userEvent.click(screen.getByRole('tab', { name: 'Flat' }));
    await userEvent.click(screen.getAllByText('request')[0]);
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument());
    // `.dg-detail-panel` is the panel's root (global.css). Read by class rather than
    // by a testid added for this one comparison: the panel is already addressable and a
    // production attribute existing only for a test is the wrong trade.
    const viaRow = document.querySelector('.dg-detail-panel')!.textContent;
    unmount();

    // Via the GRAPH's edge.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Execution Flow/i }));
    await screen.findByTestId('execution-flow-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await waitFor(() => expect(document.querySelectorAll('[data-kind="edge"]')).toHaveLength(2));
    fireEvent.click(edgeHandler('i1:request'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument());

    expect(document.querySelector('.dg-detail-panel')!.textContent).toBe(viaRow);
  });

  it('closes the panel when the graph BACKGROUND is clicked', async () => {
    // Deselection, consistent with the panel's own close button: same `setSelection(null)`
    // and the same `onSelectionChange(null)` that drops `?iid`.
    //
    // NOTE THE JSDOM CAVEAT, stated rather than hidden: PF sizes this backdrop `<rect>`
    // from `graph.getBounds()`, which is zero on an unmeasured surface — so this passes
    // only because jsdom dispatches without hit-testing. That the rect is actually
    // REACHABLE by a pointer needs a real layout and is a by-hand / Playwright fact.
    mockFetch();
    const onSelectionChange = vi.fn();
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        onSelectionChange={onSelectionChange}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Execution Flow/i }));
    await screen.findByTestId('execution-flow-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await waitFor(() => expect(document.querySelectorAll('[data-kind="edge"]')).toHaveLength(2));

    fireEvent.click(edgeHandler('i1:request'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument());

    fireEvent.click(document.querySelector('[data-kind="graph"] > rect')!);

    // Panel gone, treatment gone, URL cleared — all three, since a partial deselect
    // would leave the reader with one of the three still claiming a selection.
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Interaction' })).not.toBeInTheDocument(),
    );
    expect(document.querySelector('.dg-graph-edge--selected')).toBeNull();
    expect(onSelectionChange).toHaveBeenLastCalledWith(null);
  });

  it('renders the graph inside the detail gutter so the floating panel never covers it', async () => {
    // Why the graph is a CHILD of the `dg-detail-gutter` div rather than a sibling:
    // the detail panel floats fixed over the right of the content, and that class
    // is what reserves the gutter the content shrinks into. Outside it, the graph
    // would be overlapped by the panel on every row selection.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: /Execution Flow/i }));
    const graph = await screen.findByTestId('execution-flow-graph', undefined, {
      timeout: GRAPH_CHUNK_TIMEOUT,
    });
    // Nothing selected → the gutter class is off and the graph uses full width.
    expect(graph.closest('.dg-detail-gutter')).toBeNull();
    // Select an entity (the Entities table is still there while the graph is up),
    // which floats the panel…
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('agent-a'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument());
    // …and the graph is now inside the gutter, so it shrinks instead of hiding
    // behind the panel. Same container the tables use — one rule, not two.
    const gutter = screen.getByTestId('execution-flow-graph').closest('.dg-detail-gutter');
    expect(gutter).not.toBeNull();
    expect(gutter).toContainElement(screen.getByLabelText('Entities'));
  });

  // --- The Lineage tab: the SAME graph with the selected entity's data sources
  // highlighted. These are the cases only this component can state — that the tab
  // exists in the right place, that it rides the graph's own lazy chunk, that it is
  // driven by the EXISTING `?eid` selection rather than a second notion of one, and
  // that it costs no fetch beyond the three this view already makes. The highlight's
  // own logic is proven in lib/lineageGraph.test.ts (jsdom cannot measure an SVG, so
  // the shape of the answer is not assertable here — see ExecutionFlowGraph.test.tsx's
  // header for the same reasoning).

  it('offers Lineage as the last ?legs tab, riding the graph\'s own lazy chunk', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));

    // Its own testid, distinct from the Execution Flow tab's, so the two tabs are
    // separately addressable even though they are one component underneath.
    const lineage = await screen.findByTestId('lineage-graph', undefined, {
      timeout: GRAPH_CHUNK_TIMEOUT,
    });
    expect(lineage).toBeInTheDocument();
    // It stands in for the interactions TABLE, like the graph and the diagram — and
    // keeps the Entities table, which here is not merely retained but load-bearing:
    // it is the ONLY way to select the entity this tab answers about.
    expect(screen.queryByLabelText('Interactions')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Entities')).toBeInTheDocument();
  });

  it('instructs the reader to select an entity, claiming nothing, when none is selected', async () => {
    // The first of the three absence states. With no entity selected the graph is
    // drawn at full strength and asserts nothing about anyone's sources — an
    // instruction, not a verdict.
    //
    // `initialSource` IS SUPPLIED, and that is the change: the walk is
    // `fanin(entity, source)`, so a chosen source is now the other required half of the
    // question and this case is specifically about the ENTITY half being missing.
    // Without one, the tab correctly shows "choose a data source" instead (asserted in
    // its own case below), and this test would be checking the wrong prompt.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        initialSource={SOURCE_E1}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });

    // WORDING CHANGED faithfully: the prompt names the source, because the answer it
    // promises is source-relative.
    expect(
      screen.getByText(/Select an entity to trace this source’s data in and out/i),
    ).toBeInTheDocument();
    // Nothing is dimmed: "no question asked" must not be painted as "not part of the
    // answer" (see ExecutionFlowGraph's HighlightRole on why `'none'` and `'dimmed'`
    // are different values). Asserted as the ABSENCE of the class — a DOM fact jsdom
    // can honestly check, unlike the visual dimming itself, which needs a browser.
    //
    // EDGES, not nodes. jsdom gives the topology surface zero dimensions, so PF culls
    // every node's CONTENT (`Graph.isNodeInView` — see ExecutionFlowGraph.test.tsx's
    // long note) and each `<g data-kind="node">` renders EMPTY: the node's own
    // className never reaches the DOM here and asserting on it would silently pass
    // for the wrong reason. Edge content is not culled, so the edge classes are the
    // honest observable for the highlight in this environment.
    expect(document.querySelector('.dg-graph-edge--dimmed')).toBeNull();
    expect(document.querySelector('.dg-graph-edge--carrier')).toBeNull();
    // The graph IS mounted — so the absence above is "no highlight", not "no graph".
    expect(document.querySelectorAll('[data-kind="edge"]').length).toBeGreaterThan(0);
  });

  it('drives the highlight from the EXISTING entity selection, not a second one', async () => {
    // Requirement: one notion of "selected entity". Clicking the Entities table row
    // both opens the detail panel (the pre-existing behaviour) and drives this tab's
    // highlight, because both read the same `selection` state.
    //
    // agent-a (e1) calls search (e2). The served FAN-IN for e2 reaches e1 over the
    // request leg, so selecting `search` must mark e2 selected and light e1 upstream.
    //
    // Driven through the reachability endpoint rather than the per-leg `byLeg` map,
    // because that is what the tab now reads (ADR-0028 D14/D15): the server derives the
    // reachability claim, so the UI renders a served answer instead of composing one.
    mockFetch({
      fanin: {
        direction: 'fanin',
        seed_entity_id: 'e2',
        entities: [
          { id: 'e1', natural_key: 'agent:(p,a)', kind: 'agent', display_name: 'agent-a', hops: 1 },
        ],
        legs: [
          {
            interaction_id: 'i1',
            leg_type: 'request',
            from_entity_id: 'e1',
            to_entity_id: 'e2',
            seq: 1,
          },
        ],
        state: 'derived',
        pending_frontier: [],
        truncated: false,
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        // A chosen source is now required before the tab asks anything (`fanin(entity, source)`).
        initialSource={SOURCE_E1}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });

    // Select `search` (e2) in the Entities table — the same click that selects a row
    // anywhere else in this view.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));

    // The instruction is gone (a question has now been asked)…
    await waitFor(() =>
      expect(screen.queryByText(/Select an entity to trace its data in and out/i)).toBeNull(),
    );
    // …and the same click also opened the entity detail panel, proving both read one
    // selection rather than each holding their own.
    expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument();

    // The highlight, asserted through the EDGES — the honest observable in jsdom (node
    // content is culled; see the note in the "instructs the reader" case above). The
    // request leg (e1 → e2) is the traversed leg of the fan-in answer, so it carries the
    // upstream route class; the response leg is on neither route, so it is dimmed.
    //
    // This is a MODEL/CLASS assertion, not a visual one: jsdom applies no stylesheet
    // rules to computed style, so the actual dimming is Playwright/by-hand territory
    // and is deliberately not claimed here.
    await waitFor(() =>
      expect(
        document.querySelector('[data-id="i1:request"] .dg-graph-edge--upstream'),
      ).not.toBeNull(),
    );
    expect(
      document.querySelector('[data-id="i1:response"] .dg-graph-edge--dimmed'),
    ).not.toBeNull();
    // The lit leg is not ALSO dimmed — the two roles are exclusive, so a reader can
    // never be shown an arrow that is both the answer and not the answer.
    expect(
      document.querySelector('[data-id="i1:request"] .dg-graph-edge--dimmed'),
    ).toBeNull();
    // The seq tag follows its own arrow, so a bright number never floats over a faded
    // line as the most eye-catching thing on screen.
    expect(
      document.querySelector('[data-id="i1:request"] .dg-graph-edge-tag--upstream'),
    ).not.toBeNull();
  });

  it('says "not yet computed" — not "nothing flowed" — for a PENDING direction', async () => {
    // The second absence state, and the one this whole feature is disciplined around:
    // adjacency EXISTS but has no derived row. An empty highlight with no words would
    // read as "we checked and found nothing".
    mockFetch({
      fanin: {
        direction: 'fanin',
        seed_entity_id: 'e2',
        entities: [],
        legs: [],
        state: 'pending',
        pending_frontier: ['e1'],
        truncated: false,
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        // A chosen source is required before the tab asks anything (`fanin(entity, source)`).
        initialSource={SOURCE_E1}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));

    await waitFor(() =>
      expect(screen.getByText(/Upstream lineage not yet computed/i)).toBeInTheDocument(),
    );
    // The sentence the whole tri-state discipline hangs on.
    expect(screen.getByText(/this is not "nothing flowed"/i)).toBeInTheDocument();
    // The frontier is named, so "not yet" is visible rather than looking like a dead end.
    expect(screen.getByText(/1 entity is on the pending frontier/i)).toBeInTheDocument();
    // Emphatically NOT the derived-empty verdict, which is a different fact.
    expect(screen.queryByText(/derived, not missing/i)).toBeNull();
  });

  it('states "derived, not missing" for a DERIVED but empty direction', async () => {
    // The third absence state: a real derived answer (ADR-0028 D3/D15). An
    // unhighlighted graph looks identical to the pending case above, so the difference
    // has to be words — which is exactly what is asserted.
    mockFetch({
      fanin: {
        direction: 'fanin',
        seed_entity_id: 'e2',
        entities: [],
        legs: [],
        state: 'derived',
        pending_frontier: [],
        truncated: false,
        status: 'complete',
        stopped_at_seq: null,
      },
    });
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        // A chosen source is required before the tab asks anything (`fanin(entity, source)`).
        initialSource={SOURCE_E1}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));

    await waitFor(() =>
      expect(screen.getByText(/No upstream entities — derived, not missing/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Upstream lineage not yet computed/i)).toBeNull();
  });

  it('marks the trace data sources with nothing selected, and discloses undrawable ones', async () => {
    // JOB 1 of the tab, end to end through this component: the sources come from
    // `data-lineage-summary` and paint with NO selection. An origin the graph cannot
    // draw is disclosed — "2 sources, 1 marked" with no notice is exactly the silent
    // under-report a governance reader must never have to discover for themselves.
    mockFetch({ sources: ['agent:(p,a)', 'service:(elsewhere,crm)'] });
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });

    // No selection was made, and the roll-up is already stated.
    await waitFor(() =>
      expect(screen.getByText(/1 of 2 data sources marked on the graph/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/1 data source not shown as nodes/i)).toBeInTheDocument();
    // Named, not merely counted — and stated as still real.
    expect(screen.getByText(/service:\(elsewhere,crm\)/)).toBeInTheDocument();
    expect(screen.getByText(/not the full set/i)).toBeInTheDocument();
    // The legend is present, so a marked node is never unexplained.
    expect(screen.getByRole('group', { name: /Lineage graph legend/i })).toBeInTheDocument();
  });

  it('reports a failed SOURCES read as UNKNOWN, not as an absence, and asks nothing further', async () => {
    // THIS TEST WAS SPLIT IN TWO, and the split is a consequence of the new contract
    // rather than a weakening. It used to assert that a wholly-broken lineage stack
    // produced BOTH the "sources unknown" notice AND each direction's failure notice.
    // The second half is no longer reachable from this fixture, and correctly so: the
    // reachability read requires a `source`, and the only authority on which sources
    // this trace has is the summary — which failed here. With no roll-up to confirm the
    // requested source against, `resolveSourceChoice` cannot make the request, so there
    // is no directional failure to report.
    //
    // Asking anyway was considered and REJECTED: the request would very likely 400, and
    // the reader would then be shown "upstream lineage could not be loaded" — pointing
    // at the wrong failure entirely, when what actually broke is the sources read the
    // notice above already names. One failure, one notice.
    //
    // The per-direction failure notices are still asserted, in the next case, against a
    // GOOD summary and broken directional reads — which is the situation they actually
    // describe.
    mockFetchWithLineageError();
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        initialSource={SOURCE_E1}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });

    // The SOURCES read failed, and that is reported as unknown before any selection.
    await waitFor(() =>
      expect(screen.getByText(/data sources could not be loaded/i)).toBeInTheDocument(),
    );
    expect(screen.getAllByText(/unknown/i).length).toBeGreaterThan(0);

    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));

    // No reachability request was made at all — see the note above.
    const urls = () => (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(urls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);
    // …and none of the four per-direction states is claimed, because none was asked.
    expect(screen.queryByText(/not yet computed/i)).toBeNull();
    expect(screen.queryByText(/derived, not missing/i)).toBeNull();
    expect(screen.queryByText(/Upstream lineage could not be loaded/i)).toBeNull();
    // The "no sources" wording is emphatically NOT used: the read failed, so which
    // sources exist is unknown rather than none.
    expect(screen.queryByText(/No data sources attributed/i)).toBeNull();
  });

  it('reports each DIRECTION’s own failed read as unknown, separately from the sources', async () => {
    // The other half of the split above: the sources read SUCCEEDED (so a source is
    // choosable and the question is askable) and the two directional reads failed. This
    // is the situation the per-direction failure notices actually describe, and the
    // reason each direction keeps its own error flag — "we could not ask downstream"
    // must not look like "we know nothing at all".
    mockFetch();
    // Both directions fail, on top of the otherwise-healthy fixture.
    const base = fetch as ReturnType<typeof vi.fn>;
    const inner = base.getMockImplementation()!;
    base.mockImplementation(async (url: string) => {
      if (url.includes('/data-lineage-graph'))
        return { ok: false, status: 500, json: async () => ({ detail: 'boom' }) };
      return inner(url);
    });
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        initialSource={SOURCE_E1}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));

    await waitFor(() =>
      expect(screen.getByText(/Upstream lineage could not be loaded/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Downstream lineage could not be loaded/i)).toBeInTheDocument();
    // Not the pending wording, and not the derived-empty verdict.
    expect(screen.queryByText(/not yet computed/i)).toBeNull();
    expect(screen.queryByText(/derived, not missing/i)).toBeNull();
    // The sources read was fine, so its own notice is the count — not the failure.
    expect(screen.queryByText(/data sources could not be loaded/i)).toBeNull();
  });

  it('adds exactly the SUMMARY read on open, and the two directions only once BOTH halves are supplied', async () => {
    // THIS ASSERTION CHANGED TWICE, and both changes are faithful rather than
    // weakenings.
    //
    // FIRST it claimed the tab "adds no new resource read", which was true when it
    // composed its answer from the per-leg map and stopped being true when it started
    // rendering the SERVED reachability reads (ADR-0028 D14/D15).
    //
    // NOW the gate has a second condition. `source` became REQUIRED
    // (docs/data_lineage_alg.md's `## API`: `fanin(entity, source)`), so an entity
    // selection is no longer a complete question and the previous version of this
    // case — select an entity, expect two requests — would be asserting a request the
    // server refuses with a 400. What is pinned instead:
    //
    //   - the trace-level SUMMARY is ungated, because the trace's data sources are a
    //     standing fact that must paint on first open AND are the picker's only source
    //     of options;
    //   - the two DIRECTIONS need BOTH a selection and a chosen source. Either missing
    //     → no request, because an unavoidable 400 on first paint is not a loading
    //     state.
    //
    // The whole gesture is driven through the real UI — click the tab, pick a source in
    // the picker, click an entity — so this is also the end-to-end proof that the
    // control writes the value the reads then carry.
    mockFetch();
    renderWithProviders(
      // No `initialSource`: the honest first-open state, since nothing may be chosen on
      // the reader's behalf.
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });

    const urls = () => (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    // Asserted on the SET of resource URLs touched rather than a call count, for the
    // same reason the graph tab's case is (no staleTime here, so TanStack may
    // revalidate).
    await waitFor(() =>
      expect(urls().some((u) => u.includes('/data-lineage-summary'))).toBe(true),
    );
    // NOT yet asked: neither half of the question is supplied.
    expect(urls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);
    // The tab says which half it wants first.
    expect(screen.getByText(/Choose a data source to trace/i)).toBeInTheDocument();

    // ONE half: an entity, still no source. Deliberately in this order, because it is
    // the order that would have fired a 400 under the old gate.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument());
    expect(urls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);

    // THE OTHER HALF, through the real control: open the picker and choose the source.
    // `fireEvent`, not `userEvent`, on the option — this file's rule for anything inside
    // the topology surface's subtree (see the graph cases), and harmless here.
    fireEvent.click(screen.getByRole('button', { name: /Tracing data source/i }));
    fireEvent.click(await screen.findByRole('option', { name: /agent-a/ }));

    // Both directions, because `direction` is required and single-valued on the wire —
    // "both" is necessarily two requests — and both now carrying the chosen `source`.
    const sourceParam = new URLSearchParams({ source: SOURCE_E1 }).toString();
    await waitFor(() =>
      expect(urls().some((u) => u.includes('direction=fanin') && u.includes(sourceParam))).toBe(
        true,
      ),
    );
    expect(
      urls().some((u) => u.includes('direction=fanout') && u.includes(sourceParam)),
    ).toBe(true);
    // …and both scoped to the entity the reader actually selected.
    expect(
      urls().some((u) => u.includes('/entities/e2/data-lineage-graph') && u.includes('direction=fanin')),
    ).toBe(true);
  });

  it('says a ZERO-SOURCE trace has nothing to trace, with no picker and no read', async () => {
    // A real state of its own — nothing derived to trace — and kept apart from "still
    // loading" and "the read failed", which are the two it is easiest to conflate with.
    // No picker is drawn either: a dropdown over an empty list is a control that looks
    // broken.
    mockFetch({ sources: [] });
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });

    await waitFor(() =>
      expect(screen.getByText(/No data sources attributed in this trace yet/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/nothing to trace/i)).toBeInTheDocument();
    // Distinct from a failed read and from a load, in words.
    expect(screen.getByText(/neither a failed read nor a pending one/i)).toBeInTheDocument();
    expect(screen.queryByText(/data sources could not be loaded/i)).toBeNull();
    // No control, and no instruction to use one — an instruction to choose from nothing
    // would be a dead end.
    expect(screen.queryByRole('button', { name: /Tracing data source/i })).toBeNull();
    expect(screen.queryByText(/Choose a data source to trace/i)).toBeNull();

    // Selecting an entity still asks nothing, because there is no source to ask about.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument());
    const urls = () => (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    expect(urls().some((u) => u.includes('/data-lineage-graph'))).toBe(false);
  });

  it('restores a bookmarked ?src and asks with it, without the reader touching the picker', async () => {
    // The reason the choice went into the URL at all: a reload or a shared link has to
    // restore the whole reading of the trace, not just the tab. Modelled the way the page
    // supplies it — a controlled prop seeded from `?src`.
    mockFetch();
    renderWithProviders(
      <FlowTablesWithLegTabs
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        initialSource={SOURCE_E1}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    await screen.findByTestId('lineage-graph', undefined, { timeout: GRAPH_CHUNK_TIMEOUT });

    // The restored choice is VISIBLE on the closed control — a reader arriving by link
    // must be able to see which source the highlight is about.
    expect(await screen.findByRole('button', { name: /Tracing data source/i })).toHaveTextContent(
      'agent-a',
    );
    // …and no "choose a source" prompt, because one is chosen.
    expect(screen.queryByText(/Choose a data source to trace/i)).toBeNull();

    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    const urls = () => (fetch as ReturnType<typeof vi.fn>).mock.calls.map((c) => String(c[0]));
    await waitFor(() =>
      expect(
        urls().some((u) => u.includes(new URLSearchParams({ source: SOURCE_E1 }).toString())),
      ).toBe(true),
    );
  });

  it('renders the Lineage graph inside the detail gutter so the panel never covers it', async () => {
    // Same reason as the graph tab: the detail panel floats fixed over the right, and
    // `dg-detail-gutter` is what reserves the space the content shrinks into. This
    // matters MORE here than on the graph tab, because selecting an entity is how the
    // tab is used at all — so the panel is open whenever there is an answer to read.
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Lineage' }));
    const graph = await screen.findByTestId('lineage-graph', undefined, {
      timeout: GRAPH_CHUNK_TIMEOUT,
    });
    expect(graph.closest('.dg-detail-gutter')).toBeNull();

    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('agent-a'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Entity' })).toBeInTheDocument());
    const gutter = screen.getByTestId('lineage-graph').closest('.dg-detail-gutter');
    expect(gutter).not.toBeNull();
    expect(gutter).toContainElement(screen.getByLabelText('Entities'));
  });

  it('flat view links a request row to its response row with a shared connector (id + color)', async () => {
    // Two interactions whose legs interleave in seq order so a request and its
    // response are NOT adjacent: i1.req(1), i2.req(2), i1.resp(3), i2.resp(4).
    // The connector must tie i1's request row to its response row (same id +
    // color) and pass through the i2.req row that sits between them.
    const INTERLEAVED = [
      {
        id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2',
        summary: 'i1', parent_interaction_id: null,
        legs: [
          { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: false, seq: 1 },
          { leg_type: 'response', occurred_at: '2026-05-01T12:00:03Z', payload_hash: null, error: false, seq: 3 },
        ],
        duration_seconds: 3, any_error: false, span_count: 2, anchor_count: 1,
      },
      {
        id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e2',
        summary: 'i2', parent_interaction_id: null,
        legs: [
          { leg_type: 'request', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: 2 },
          { leg_type: 'response', occurred_at: '2026-05-01T12:00:04Z', payload_hash: null, error: false, seq: 4 },
        ],
        duration_seconds: 3, any_error: false, span_count: 2, anchor_count: 1,
      },
    ];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: INTERLEAVED }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Flat' }));
    const flat = await screen.findByLabelText('Interactions (flat)');
    const body = within(flat).getAllByRole('row').slice(1); // drop header
    expect(body).toHaveLength(4); // i1.req, i2.req, i1.resp, i2.resp

    // Each row's connector cell carries a <g> per bracket covering it, tagged
    // with the interaction id + drawing role (top/through/bottom).
    const segsOf = (row: HTMLElement) =>
      Array.from(row.querySelectorAll('[data-connector-id]')).map((g) => ({
        id: g.getAttribute('data-connector-id'),
        role: g.getAttribute('data-connector-role'),
      }));

    // Row 0 (i1.request) opens i1's bracket going down.
    expect(segsOf(body[0])).toContainEqual({ id: 'i1', role: 'top' });
    // Row 1 (i2.request) opens i2 AND passes i1's line through it.
    expect(segsOf(body[1])).toContainEqual({ id: 'i2', role: 'top' });
    expect(segsOf(body[1])).toContainEqual({ id: 'i1', role: 'through' });
    // Row 2 (i1.response) closes i1's bracket — same id ties it to row 0.
    expect(segsOf(body[2])).toContainEqual({ id: 'i1', role: 'bottom' });
    // Row 3 (i2.response) closes i2's bracket.
    expect(segsOf(body[3])).toContainEqual({ id: 'i2', role: 'bottom' });

    // The request row and its response row share the SAME connector color
    // (deterministic per interaction id), visually linking them.
    const colorOf = (row: HTMLElement, id: string) =>
      row.querySelector(`[data-connector-id="${id}"]`)?.getAttribute('stroke');
    expect(colorOf(body[0], 'i1')).toBe(colorOf(body[2], 'i1'));
    expect(colorOf(body[0], 'i1')).toBeTruthy();
  });

  it('flat view draws no connector line for a single-leg interaction (response in flight)', async () => {
    // One interaction with only a request leg — no partner, so no line.
    const SINGLE = [
      {
        id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2',
        summary: 'i1', parent_interaction_id: null,
        legs: [
          { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: false, seq: 1 },
        ],
        duration_seconds: null, any_error: false, span_count: 1, anchor_count: 1,
      },
    ];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: SINGLE }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTablesWithLegTabs traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Flat' }));
    const flat = await screen.findByLabelText('Interactions (flat)');
    const body = within(flat).getAllByRole('row').slice(1);
    expect(body).toHaveLength(1);
    // The connector cell renders (empty svg) but draws no bracket segment.
    expect(body[0].querySelectorAll('[data-connector-id]')).toHaveLength(0);
  });

  it('maps entity-evidence roles to glyph+words and links the Parent span id', async () => {
    mockFetch();
    const onNavigateToSpan = vi.fn();
    renderWithProviders(
      <FlowTables
        traceId="T1"
        pins={new PinStore()}
        onPinsChange={() => {}}
        onNavigateToSpan={onNavigateToSpan}
      />,
    );
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    // Select the 'search' entity → its evidence loads.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    // Role rendered as an icon with a hover tooltip + accessible role text:
    // discovered_via → the "key" glyph (shares it with anchor), identified_via
    // → the "dot" glyph. The human role text is present (visually-hidden).
    await waitFor(() => expect(screen.getByText('discovered via')).toBeInTheDocument());
    expect(screen.getByText('identified via')).toBeInTheDocument();
    expect(screen.getByTitle('discovered via')).toHaveClass('dg-role-icon--key');
    expect(screen.getByTitle('identified via')).toHaveClass('dg-role-icon--dot');
    // The Parent column renders a clickable span link (parent id span-parent).
    const parentLink = screen.getByRole('button', { name: /span-parent/i });
    await userEvent.click(parentLink);
    expect(onNavigateToSpan).toHaveBeenCalledWith('span-parent');
  });

  it('highlights only the latest-selected row (entity or interaction), clearing the previous one', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    // Select an entity first → its row is the sole highlight.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    const entityRow = within(screen.getByLabelText('Entities')).getByText('search').closest('tr')!;
    await waitFor(() => expect(entityRow.getAttribute('data-dg-selected')).toBe('active'));

    // Now select an interaction → it becomes the sole highlight; the entity
    // row's highlight is dropped entirely (no muted breadcrumb).
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    const interactionRow = screen.getByText(/2 \(1 anchor\)/).closest('tr')!;
    await waitFor(() => expect(interactionRow.getAttribute('data-dg-selected')).toBe('active'));
    expect(entityRow.getAttribute('data-dg-selected')).toBeNull();

    // Selecting the entity again flips the highlight back and clears the
    // interaction row — only the latest selection is ever highlighted.
    await userEvent.click(within(screen.getByLabelText('Entities')).getByText('search'));
    await waitFor(() => expect(entityRow.getAttribute('data-dg-selected')).toBe('active'));
    expect(interactionRow.getAttribute('data-dg-selected')).toBeNull();
  });

  it('lazily fetches and renders an interaction request payload when its tab is active', async () => {
    // An interaction that carries payload hashes (the common HTTP/MCP case) —
    // now on the request/response legs (ADR-0025).
    const withPayload = [withLegHashes('reqhash0deadbeef', 'resphash0feedface')];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: withPayload }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/payloads/reqhash0deadbeef'))
        return { ok: true, status: 200, json: async () => ({ content_hash: 'reqhash0deadbeef', content_kind: 'json', content: { q: 'flights' }, byte_size: 42 }) };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // Each leg gets an outer tab, `Request` active by default, and its inner
    // Payload tab still names the hash's first 8 chars ('reqhash0').
    expect(await screen.findByRole('tab', { name: 'Request' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('tab', { name: 'Response' })).toHaveAttribute(
      'aria-selected',
      'false',
    );
    const reqPayloadTab = screen.getByRole('tab', { name: /Request: Payload reqhash0/i });
    expect(reqPayloadTab).toHaveAttribute('aria-selected', 'true');
    // Payload is the active leg's default section, so the decoded content +
    // kind/hash/bytes render for the ACTIVE leg...
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    expect(screen.getByText('json')).toBeInTheDocument();
    // ...and the inactive leg's inner tabs are not even on screen, so nothing
    // there could have been read (the fetch assertion is in the gating block).
    expect(screen.queryByRole('tab', { name: /Response: Payload resphash/i })).toBeNull();
    // Switching legs brings the Response leg's inner tabs up, again on Payload.
    await userEvent.click(screen.getByRole('tab', { name: 'Response' }));
    expect(
      await screen.findByRole('tab', { name: /Response: Payload resphash/i }),
    ).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: /Request: Payload reqhash0/i })).toBeNull();
  });

  it('shows the payload Classification verdict (sensitivity + tags + identity bundle + findings) on expand', async () => {
    const withPayload = [
      withLegHashes('reqhash0deadbeef', null),
    ];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: withPayload }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/payloads/reqhash0deadbeef'))
        return {
          ok: true, status: 200,
          json: async () => ({
            content_hash: 'reqhash0deadbeef', content_kind: 'json',
            content: { note: 'contact jo@example.com' }, byte_size: 42,
            classification: {
              sensitivity_level: 'CONFIDENTIAL',
              regulatory_tags: ['PII'],
              contains_identity_bundle: true,
              is_personalized: true,
              primary_domain: 'person',
              findings: [{ entity_type: 'EMAIL', start: 8, end: 22, text: 'jo@example.com' }],
              model_version: 1,
            },
          }),
        };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // Classification is its own inner tab now — activating it (and nothing else)
    // both triggers the payload read it rides on and renders the verdict.
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Classification/i }));
    // The Classification verdict renders inside the active tab's pane.
    await waitFor(() => expect(screen.getByText('CONFIDENTIAL')).toBeInTheDocument());
    expect(screen.getByText('PII')).toBeInTheDocument();
    expect(screen.getByText(/identity bundle/i)).toBeInTheDocument();
    // The finding's detected type + flagged text region.
    const findings = screen.getByLabelText('Findings');
    expect(within(findings).getByText('EMAIL')).toBeInTheDocument();
    expect(within(findings).getByText('jo@example.com')).toBeInTheDocument();
  });

  it('shows "not yet classified" in the payload cell when classification is null (eventual-consistency window)', async () => {
    const withPayload = [
      withLegHashes('reqhash0deadbeef', null),
    ];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: withPayload }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/payloads/reqhash0deadbeef'))
        return {
          ok: true, status: 200,
          json: async () => ({
            content_hash: 'reqhash0deadbeef', content_kind: 'json',
            content: { q: 'flights' }, byte_size: 42, classification: null,
          }),
        };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Classification/i }));
    // Null classification renders as the distinct eventual-consistency note,
    // not as a PUBLIC verdict.
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(screen.queryByText('PUBLIC')).toBeNull();
  });

  it('omits both leg tabs for an interaction that carried no payloads', async () => {
    mockFetch(); // INTERACTIONS[0] has null request/response hashes
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument());
    // Neither leg tab, and none of the three section tabs — a payload-less leg
    // has no payload, no verdict, and no derivable lineage to offer. (The flow
    // view's own Tree/Flat/Execution Flow tabs are the only tabs left on screen.)
    expect(screen.queryByRole('tab', { name: 'Request' })).toBeNull();
    expect(screen.queryByRole('tab', { name: 'Response' })).toBeNull();
    expect(screen.queryByRole('tab', { name: /Data lineage/i })).toBeNull();
    expect(screen.queryByRole('tab', { name: /Classification/i })).toBeNull();
    expect(screen.queryByRole('tab', { name: /Payload/i })).toBeNull();
  });

  it('shows only the Request tab when only the request leg carried a payload', async () => {
    // The per-leg guard survives the restructure: one hash → one outer tab, and
    // the absent leg contributes no tab and no inner sections. The one leg
    // present is also the ACTIVE one, so the pane is never blank.
    const reqOnly = [withLegHashes('reqhash0deadbeef', null)];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: reqOnly }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'Request' })).toBeInTheDocument(),
    );
    expect(screen.getByRole('tab', { name: 'Request' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.queryByRole('tab', { name: 'Response' })).toBeNull();
    expect(screen.getByRole('tab', { name: /Request: Data lineage/i })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Response: Data lineage/i })).toBeNull();
  });

  // --- Data lineage (issue #119, ADR-0028) ------------------------------------
  // Lineage is read once per trace and keyed per LEG (interaction_id, leg_type),
  // so a payload's block is found by its leg identity, never by content hash.

  const LINEAGE_LEGS = [
    {
      interaction_id: 'i1',
      leg_type: 'request',
      payload_hash: 'reqhash0deadbeef',
      lineage: {
        data_sources: ['agent-a', 'user'],
        source_transformations: { 'agent-a': ['summarization'], user: ['anonymization'] },
        entities: ['agent-a', 'search', 'user'],
        seq: 1,
      },
    },
  ];

  /**
   * Mock with payload hashes on both legs plus a data-lineage response. The
   * trace-level coverage (issue #120) defaults to `complete` so the existing
   * per-leg cases stay unaffected by the banner; the banner cases pass their own.
   */
  function mockFetchWithLineage(
    legs: unknown[],
    coverage: { status?: string | null; stopped_at_seq?: number | null } = {
      status: 'complete',
      stopped_at_seq: null,
    },
  ) {
    const withPayload = [withLegHashes('reqhash0deadbeef', 'resphash0feedface')];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      // The Lineage TAB's two reads, matched BEFORE the `/entities/` evidence branch
      // for the reason `mockFetch` states at length: `/entities/<eid>/data-lineage-graph`
      // also matches that branch, and answering it with `{spans: […]}` breaks the
      // entity SELECTION rather than the lineage. These default to the non-claiming
      // answers — this helper's cases are about the PER-LEG lineage, so the tab's
      // reachability must not accidentally assert anything.
      if (url.includes('/data-lineage-graph')) {
        const wantFanin = url.includes('direction=fanin');
        return {
          ok: true,
          status: 200,
          json: async () => mkReach(wantFanin ? 'fanin' : 'fanout'),
        };
      }
      if (url.includes('/data-lineage-summary'))
        return {
          ok: true,
          status: 200,
          json: async () => ({
            sources: [],
            destinations: [],
            status: 'complete',
            stopped_at_seq: null,
          }),
        };
      if (url.endsWith('/data-lineage'))
        return { ok: true, status: 200, json: async () => ({ legs, ...coverage }) };
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: withPayload }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/payloads/'))
        return {
          ok: true, status: 200,
          json: async () => ({
            content_hash: url.split('/').pop(), content_kind: 'json',
            content: { q: 'flights' }, byte_size: 42, classification: null,
          }),
        };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
  }

  it('shows the payload lineage (data sources, per-source transformations, entities) on its tab', async () => {
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Data lineage/i }));

    // The Data lineage block renders in its own tab pane, beside the
    // Classification tab it mirrors.
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    const sources = screen.getByLabelText('Data sources');
    expect(within(sources).getByText('agent-a')).toBeInTheDocument();
    expect(within(sources).getByText('user')).toBeInTheDocument();
    // Per-source transformations sit with their source.
    const agentRow = within(sources).getByText('agent-a').closest('tr')!;
    expect(within(agentRow).getByText('summarization')).toBeInTheDocument();
    // And the entities traversed — membership only; the set is unordered.
    const entities = screen.getByLabelText('Entities traversed');
    expect(within(entities).getByText('user')).toBeInTheDocument();
    expect(within(entities).getByText('agent-a')).toBeInTheDocument();
    expect(within(entities).getByText('search')).toBeInTheDocument();
  });

  it('keys lineage per leg: the response leg does not inherit the request leg’s lineage', async () => {
    // Only the request leg has lineage in the fixture, so the response leg's
    // lineage tab must report "not yet computed" — the leg key is
    // (interaction_id, leg_type), never the payload/content hash (ADR-0028 D5).
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // Reaching it means switching legs first — the outer tab — then picking the
    // inner section.
    await userEvent.click(await screen.findByRole('tab', { name: 'Response' }));
    await userEvent.click(await screen.findByRole('tab', { name: /Response: Data lineage/i }));
    await waitFor(() =>
      expect(screen.getByText(/lineage not yet computed/i)).toBeInTheDocument(),
    );
    expect(screen.queryByLabelText('Data sources')).toBeNull();
  });

  it('shows "not yet computed" when the leg is present but its lineage is null', async () => {
    mockFetchWithLineage([{ ...LINEAGE_LEGS[0], lineage: null }]);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Data lineage/i }));
    await waitFor(() =>
      expect(screen.getByText(/lineage not yet computed/i)).toBeInTheDocument(),
    );
  });

  it('handles the empty-legs lineage response (unmigrated DB) without breaking the payload view', async () => {
    mockFetchWithLineage([]);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // Payload is the default tab, so the body lands first; then the lineage tab
    // states the absence. Switching back proves the shared read still satisfies
    // the body without a refetch.
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Data lineage/i }));
    expect(screen.getByText(/lineage not yet computed/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('tab', { name: /Request: Payload reqhash0/i }));
    expect(screen.getByText(/"flights"/)).toBeInTheDocument();
  });

  /**
   * Same fixture, but the data-lineage read itself fails. Everything else in the
   * trace still resolves, so the flow view renders and only the lineage block is
   * affected — which is exactly the case that used to masquerade as "not yet
   * computed".
   */
  function mockFetchWithLineageError() {
    const withPayload = [withLegHashes('reqhash0deadbeef', 'resphash0feedface')];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      // The reachability reads fail too, which is the honest shape of this fixture:
      // it exists to prove a broken lineage read is reported as UNKNOWN rather than as
      // an absence, and a version where only the per-leg read broke while the tab's
      // own reads succeeded would be testing a different (and easier) situation.
      if (url.includes('/data-lineage-graph') || url.includes('/data-lineage-summary'))
        return { ok: false, status: 500, json: async () => ({ detail: 'boom' }) };
      if (url.endsWith('/data-lineage'))
        return { ok: false, status: 500, json: async () => ({ detail: 'boom' }) };
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: withPayload }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/payloads/'))
        return {
          ok: true, status: 200,
          json: async () => ({
            content_hash: url.split('/').pop(), content_kind: 'json',
            content: { q: 'flights' }, byte_size: 42, classification: null,
          }),
        };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
  }

  it('reports a failed lineage read as an error, not as "not yet computed"', async () => {
    // A failed request and the eventual-consistency window are different facts
    // and prompt different actions (retry vs wait). Collapsing them — as the
    // `DataLineage | null` prop did — leaves a reader waiting on a dead request.
    mockFetchWithLineageError();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // The payload body itself loaded fine (the default tab) — only lineage
    // failed, which its own tab reports.
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Data lineage/i }));
    expect(screen.getByText(/failed to load lineage/i)).toBeInTheDocument();
    expect(screen.queryByText(/lineage not yet computed/i)).toBeNull();
    expect(screen.queryByLabelText('Data sources')).toBeNull();
  });

  it('says coverage is unknown — not fine — when the lineage read fails', async () => {
    // The absence of a banner is this view's "no truncation" statement (see the
    // `complete` case below). A failed read established NOTHING, so staying
    // silent would assert full coverage the view never obtained. It must still
    // not claim a truncation it cannot establish either — hence "unknown",
    // worded distinctly from the `partial` prefix warning.
    mockFetchWithLineageError();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/could not be loaded|unknown/i);
    // No fabricated truncation: the prefix claim belongs to `partial` alone.
    expect(alert.textContent).not.toMatch(/not the complete set of data sources/i);
    expect(alert.textContent).not.toMatch(/derived only up to/i);
  });

  // --- the two-level tabs (leg → section) and the shared payload read --------
  // Classification is INLINED on the payload read (ADR-0024); Data lineage comes
  // from the trace-scoped read (ADR-0028 D5). The tabs' fetch behaviour has to
  // follow that split, or a reader either waits on a request nobody made or pays
  // for one they did not need.

  /** Payload-read calls the mock has seen, for the gating assertions below. */
  const payloadCalls = () =>
    (fetch as ReturnType<typeof vi.fn>).mock.calls.filter((c) =>
      String(c[0]).includes('/payloads/'),
    );

  /** Select the interaction and wait for its leg tabs to appear. */
  async function selectInteraction() {
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await screen.findByRole('tab', { name: /Request: Payload reqhash0/i });
  }

  it('issues no payload read while nothing that needs one is on screen', async () => {
    // A payload-less interaction contributes no leg tabs at all, so an opened
    // detail panel is free. The tab set fetching on mount regardless would undo
    // that for every selection.
    // Same lineage fixture as the rest of this block, but the selected
    // interaction carries no payload hashes — so there is no leg tab at all and
    // nothing to read.
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/data-lineage'))
        return { ok: true, status: 200, json: async () => ({ legs: LINEAGE_LEGS, status: 'complete', stopped_at_seq: null }) };
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: INTERACTIONS }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument(),
    );
    expect(screen.queryByRole('tab', { name: /Payload/i })).toBeNull();
    expect(payloadCalls()).toHaveLength(0);
  });

  it('triggers the payload read when Classification alone is the active tab', async () => {
    // The verdict is an inlined field of GET /api/payloads/{hash} (ADR-0024), so
    // this tab cannot render without that read even though it shows no body.
    //
    // Payload is a leg's default tab, so to attribute a read to Classification
    // *alone* the reader has to be parked somewhere that fetches nothing first:
    // Data lineage. From there the next click is Classification, and the request
    // that appears is unambiguously the one it rides on.
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await selectInteraction();
    // Reselect the row so the leg pane remounts with a fresh (unfetched) state,
    // then go straight to Data lineage — proving the read below is not a
    // leftover of the default Payload tab.
    await userEvent.click(screen.getByRole('tab', { name: /Request: Data lineage/i }));
    (fetch as ReturnType<typeof vi.fn>).mockClear();
    expect(payloadCalls()).toHaveLength(0);
    await userEvent.click(screen.getByRole('tab', { name: /Request: Classification/i }));
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    // And the fetched verdict (null in this fixture) is what renders — the
    // eventual-consistency note, reached only once the read landed. The read
    // itself is either the newly-issued request or the one this leg already had
    // in cache; either way every payload URL seen is this leg's.
    expect(payloadCalls().every((c) => String(c[0]).includes('reqhash0deadbeef'))).toBe(true);
  });

  it('adds no payload read when Data lineage is the active tab', async () => {
    // Lineage is keyed on the leg, not the content hash, and is already in hand
    // from the one trace-scoped read — fetching the payload to show it would be
    // pure waste (and would drag a multi-KB body over the wire for provenance).
    //
    // Stated here as "the lineage tab adds nothing": the reader lands on the
    // leg's default Payload tab (one read), then sits on Data lineage while the
    // whole provenance triple renders, and the read count does not move. The
    // *stronger* form — a leg whose payload-backed tabs are never activated and
    // whose hash is therefore never requested at all — is asserted in
    // LegTabs.test.tsx, on the leg that is not the default one.
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await selectInteraction();
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    const before = payloadCalls().length;
    await userEvent.click(screen.getByRole('tab', { name: /Request: Data lineage/i }));
    // It renders straight away from the prop...
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    // ...and cost no request to do it, on this leg or the other.
    expect(payloadCalls()).toHaveLength(before);
    expect(payloadCalls().every((c) => String(c[0]).includes('reqhash0deadbeef'))).toBe(true);
  });

  it('does not fetch the inactive leg’s payload while the other leg’s tab is active', async () => {
    // The regression tabs introduce over collapsibles: PF keeps inactive tab
    // content mounted-but-hidden by default, and a mounted leg pane runs its
    // `usePayload`. LegTabs therefore renders ONLY the active leg's pane, so the
    // Response body is never dragged over the wire for a reader looking at the
    // Request. Both legs carry hashes here, so both COULD be read.
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await selectInteraction();
    // The Request leg is active, so its payload is read (Payload is its default
    // section) — and the Response leg's is NOT, on any tab of the active leg.
    await waitFor(() => expect(payloadCalls().length).toBeGreaterThan(0));
    expect(payloadCalls().every((c) => String(c[0]).includes('reqhash0deadbeef'))).toBe(true);
    await userEvent.click(screen.getByRole('tab', { name: /Request: Classification/i }));
    await userEvent.click(screen.getByRole('tab', { name: /Request: Data lineage/i }));
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    expect(payloadCalls().every((c) => String(c[0]).includes('reqhash0deadbeef'))).toBe(true);
    expect(payloadCalls().some((c) => String(c[0]).includes('resphash0feedface'))).toBe(false);
    // Only once the Response leg is actually selected is its payload read.
    await userEvent.click(screen.getByRole('tab', { name: 'Response' }));
    await waitFor(() =>
      expect(payloadCalls().some((c) => String(c[0]).includes('resphash0feedface'))).toBe(true),
    );
  });

  it('does not render "not yet classified" while the payload read is still in flight', async () => {
    // The load-bearing distinction: *unfetched* is not *unclassified*. A pending
    // read must show its own loading state, because "Not yet classified" is a
    // claim about the SERVER's state (ADR-0024) that nobody has checked yet.
    let releasePayload: (() => void) | null = null;
    const gate = new Promise<void>((resolve) => {
      releasePayload = resolve;
    });
    const withPayload = [withLegHashes('reqhash0deadbeef', null)];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/data-lineage'))
        return { ok: true, status: 200, json: async () => ({ legs: [], status: 'complete', stopped_at_seq: null }) };
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: withPayload }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/payloads/')) {
        await gate;
        return {
          ok: true, status: 200,
          json: async () => ({
            content_hash: 'reqhash0deadbeef', content_kind: 'json',
            content: { q: 'flights' }, byte_size: 42, classification: null,
          }),
        };
      }
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await selectInteraction();
    await userEvent.click(screen.getByRole('tab', { name: /Request: Classification/i }));
    // In flight: a loading state, and emphatically NOT the eventual-consistency
    // note, which would assert the server has no verdict.
    await waitFor(() =>
      expect(screen.getByLabelText(/Loading Request payload/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/not yet classified/i)).toBeNull();
    // Once it lands, the fetched null verdict does render as that note.
    releasePayload!();
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
  });

  it('shows the payload-read failure inside Classification, not "not yet classified"', async () => {
    // Same discipline on the error arm: a failed read established nothing about
    // the verdict, so it gets the sibling "Failed to load payload." treatment.
    const withPayload = [withLegHashes('reqhash0deadbeef', null)];
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
      if (url.endsWith('/data-lineage'))
        return { ok: true, status: 200, json: async () => ({ legs: [], status: 'complete', stopped_at_seq: null }) };
      if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: withPayload }) };
      if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
      if (url.includes('/payloads/')) return { ok: false, status: 500, json: async () => ({ detail: 'boom' }) };
      if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
      return { ok: true, status: 200, json: async () => ({ spans: [] }) };
    });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await selectInteraction();
    await userEvent.click(screen.getByRole('tab', { name: /Request: Classification/i }));
    await waitFor(() =>
      expect(screen.getByText(/failed to load payload/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/not yet classified/i)).toBeNull();
  });

  it('switches the three section tabs independently of each other, sharing one payload read', async () => {
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await selectInteraction();
    const payloadTab = () => screen.getByRole('tab', { name: /Request: Payload reqhash0/i });
    const classTab = () => screen.getByRole('tab', { name: /Request: Classification/i });
    const lineageTab = () => screen.getByRole('tab', { name: /Request: Data lineage/i });

    // Payload is the default active section: its body shows, the other two panes
    // do not — exactly one section at a time is the whole point of tabs.
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    expect(payloadTab()).toHaveAttribute('aria-selected', 'true');
    expect(classTab()).toHaveAttribute('aria-selected', 'false');
    expect(lineageTab()).toHaveAttribute('aria-selected', 'false');
    expect(screen.queryByText(/not yet classified/i)).toBeNull();
    expect(screen.queryByLabelText('Data sources')).toBeNull();

    // Switch to Classification — no SECOND payload request: one hoisted
    // usePayload per leg, and TanStack keys the cache on the hash. The verdict
    // renders straight from the satisfied read, never flickering to loading.
    const callsAfterPayload = payloadCalls().length;
    await userEvent.click(classTab());
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(payloadCalls()).toHaveLength(callsAfterPayload);
    expect(classTab()).toHaveAttribute('aria-selected', 'true');
    expect(payloadTab()).toHaveAttribute('aria-selected', 'false');
    expect(screen.queryByText(/"flights"/)).toBeNull();

    // Data lineage next — it needs no payload read at all, and swaps the pane
    // out from under Classification.
    await userEvent.click(lineageTab());
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    expect(lineageTab()).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByText(/not yet classified/i)).toBeNull();
    expect(screen.queryByText(/"flights"/)).toBeNull();
    expect(payloadCalls()).toHaveLength(callsAfterPayload);

    // And back to Payload: still the same one read, still no refetch.
    await userEvent.click(payloadTab());
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    expect(payloadCalls()).toHaveLength(callsAfterPayload);
  });

  it('reads the trace-scoped lineage resource exactly once for many tab activations', async () => {
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Data lineage/i }));
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('tab', { name: 'Response' }));
    await userEvent.click(await screen.findByRole('tab', { name: /Response: Data lineage/i }));
    await userEvent.click(screen.getByRole('tab', { name: 'Request' }));
    await userEvent.click(await screen.findByRole('tab', { name: /Request: Data lineage/i }));
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    // One trace-level fetch serves every leg — NOT one per tab activation.
    const lineageCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c) => String(c[0]).endsWith('/data-lineage'),
    );
    expect(lineageCalls).toHaveLength(1);
  });

  // --- trace-level coverage (issue #120, ADR-0028 D6) ------------------------
  // A partial trace must be UNMISTAKABLE: the flow view shows its tables before
  // anything is selected, so a warning that only appeared inside an expanded
  // payload would let a reader take the visible rows for the whole picture.

  it('reads lineage up front, not only once a row is selected', async () => {
    // #119 gated this read on there being a selection, because lineage only
    // rendered inside an expanded payload. #120 moved the trace-level status to
    // the top of the view, where it has to be on screen from the first render —
    // so the laziness is deliberately gone. One fetch per trace either way.
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() =>
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.filter((c) =>
          String(c[0]).endsWith('/data-lineage'),
        ),
      ).toHaveLength(1),
    );
  });

  it('warns that lineage is incomplete, and from where, on a partial trace', async () => {
    mockFetchWithLineage(LINEAGE_LEGS, { status: 'partial', stopped_at_seq: 2 });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/incomplete/i);
    // The stop position is named, so the reader knows where the picture ends
    // rather than only that it does.
    expect(alert.textContent).toMatch(/2/);
  });

  it('says the sources shown are not the full set on a partial trace', async () => {
    // The acceptance criterion is about MEANING, not just a coloured box: the
    // warning has to state that what is listed is a prefix. "Fewer rows" read as
    // "fewer sources" is the exact failure this flag exists to prevent.
    mockFetchWithLineage(LINEAGE_LEGS, { status: 'partial', stopped_at_seq: 2 });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/not the complete set of data sources/i);
  });

  it('shows no coverage warning on a complete trace', async () => {
    mockFetchWithLineage(LINEAGE_LEGS, { status: 'complete', stopped_at_seq: null });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await waitFor(() =>
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.filter((c) =>
          String(c[0]).endsWith('/data-lineage'),
        ),
      ).toHaveLength(1),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('shows no coverage warning while the status is unknown', async () => {
    // `status: null` is "not derived yet". Warning about a truncation nobody has
    // established would train the reader to ignore the banner — and the per-leg
    // blocks already say "lineage not yet computed" for this state.
    mockFetchWithLineage(LINEAGE_LEGS, { status: null, stopped_at_seq: null });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await waitFor(() =>
      expect(
        (fetch as ReturnType<typeof vi.fn>).mock.calls.filter((c) =>
          String(c[0]).endsWith('/data-lineage'),
        ),
      ).toHaveLength(1),
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('keeps the partial warning visible while a row is selected', async () => {
    // The detail panel floats over the tables; the warning must not be what it
    // covers, or the truncation becomes invisible exactly when a reader is
    // drilling into a payload's sources.
    mockFetchWithLineage(LINEAGE_LEGS, { status: 'partial', stopped_at_seq: 2 });
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await screen.findByRole('alert');
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument(),
    );
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('scopes the active-row highlight selector to out-specify PF clickable rows', () => {
    // jsdom does not apply CSS-file rules to computed style, so the highlight's
    // *visibility* cannot be asserted here (see project_dg_react_ui memory). The
    // failure mode this guards is a CSS-specificity regression: PF paints
    // clickable rows via `.pf-v5-c-table tr:where(...).pf-m-clickable`
    // (specificity 0-2-1). A bare `tr[data-dg-selected='active']` (0-1-0)
    // loses, leaving the row visually un-highlighted while the attribute is set.
    // So assert the shipped selector is scoped through the table + row class.
    // Vitest runs from the ui/ package root, so resolve from cwd.
    const css = readFileSync(resolve('src/styles/global.css'), 'utf8');
    expect(css).toMatch(
      /\.pf-v5-c-table\s+tr\.pf-v5-c-table__tr\[data-dg-selected='active'\]/,
    );
    // And it must NOT be the too-weak bare selector on its own.
    expect(css).not.toMatch(/(^|\})\s*tr\[data-dg-selected='active'\]\s*\{/);
  });
});
