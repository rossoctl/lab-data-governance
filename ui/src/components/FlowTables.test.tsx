import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { FlowTables } from './FlowTables';
import { PinStore } from '../lib/pins';

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

// A trace whose exchanges carry server-classified `kinds`: one real root, two
// MCP infrastructure children (lifecycle + discovery — hidden by default), one
// real child, and a real grandchild under it. span_count is distinct per row so
// tests can target rows by their unique "N (1 anchor)" Spans cell. Rows are in
// the ADR-0025 legs shape (the leg seqs give the flat view a stable order).
const kindsOf = (protocol: string, mcp_method: string | null, req: string, resp: string) => ({
  protocol, mcp_method, request_content_kind: req, response_content_kind: resp,
});
let ixSeq = 0;
const ixRow = (over: Record<string, unknown>) => {
  const reqSeq = (ixSeq += 2) - 1;
  return {
    caller_entity_id: 'e1', callee_entity_id: 'e2',
    summary: null, parent_interaction_id: null, anchor_count: 1,
    legs: [
      { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: null, seq: reqSeq },
      { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: reqSeq + 1 },
    ],
    duration_seconds: 1, any_error: false,
    ...over,
  };
};
const INFRA_INTERACTIONS = [
  ixRow({ id: 'i-root', span_count: 2,
    kinds: kindsOf('a2a', null, 'agent_request', 'agent_response') }),
  ixRow({ id: 'i-life', span_count: 3, parent_interaction_id: 'i-root',
    kinds: kindsOf('mcp', 'initialize', 'mcp_lifecycle_request', 'mcp_lifecycle_result') }),
  ixRow({ id: 'i-disc', span_count: 4, parent_interaction_id: 'i-root',
    kinds: kindsOf('mcp', 'tools/list', 'tool_discovery_request', 'tool_discovery_result') }),
  ixRow({ id: 'i-call', span_count: 5, parent_interaction_id: 'i-root',
    kinds: kindsOf('mcp', 'tools/call', 'tool_call_request', 'tool_call_result') }),
  ixRow({ id: 'i-deep', span_count: 6, parent_interaction_id: 'i-call',
    kinds: kindsOf('http', null, 'http_request', 'http_response') }),
];

function mockFetchInfra(interactions: unknown[] = INFRA_INTERACTIONS) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions }) };
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

// Evidence rows returned for the entity/interaction /spans sub-resources.
const ENTITY_EVIDENCE = [
  { span_id: 'span-abc', role: 'discovered_via', parent_id: 'span-parent', kind: 'SERVER', service_name: 'svc' },
  { span_id: 'span-def', role: 'identified_via', parent_id: null, kind: 'CLIENT', service_name: 'svc' },
];
const INTERACTION_EVIDENCE = [
  { span_id: 'span-xyz', role: 'anchor', parent_id: 'span-parent', kind: 'CLIENT', service_name: 'svc' },
];

function mockFetch() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
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
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    // Tree view first: one interaction row, no per-leg breakdown.
    expect(screen.queryByLabelText('Interactions (flat)')).not.toBeInTheDocument();
    await userEvent.click(screen.getByLabelText('Flat view'));
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
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByLabelText('Flat view'));
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
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Interactions')).toBeInTheDocument());
    await userEvent.click(screen.getByLabelText('Flat view'));
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

  it('lazily fetches and renders an interaction request payload on expand', async () => {
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
    // The Payloads section offers Request/Response links (hash prefix shown).
    // Links show the hash's first 8 chars: 'reqhash0' / 'resphash'.
    const reqToggle = await screen.findByRole('button', { name: /Request: reqhash0/i });
    expect(screen.getByRole('button', { name: /Response: resphash/i })).toBeInTheDocument();
    // The body is NOT fetched until the link is expanded (lazy).
    expect(screen.queryByText(/"flights"/)).toBeNull();
    await userEvent.click(reqToggle);
    // On expand, the decoded content + kind/hash/bytes render.
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    expect(screen.getByText('json')).toBeInTheDocument();
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
    const reqToggle = await screen.findByRole('button', { name: /Request: reqhash0/i });
    await userEvent.click(reqToggle);
    // The Classification verdict renders inside the expanded Req cell.
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
    await userEvent.click(await screen.findByRole('button', { name: /Request: reqhash0/i }));
    // Null classification renders as the distinct eventual-consistency note,
    // not as a PUBLIC verdict.
    await waitFor(() => expect(screen.getByText(/not yet classified/i)).toBeInTheDocument());
    expect(screen.queryByText('PUBLIC')).toBeNull();
  });

  it('omits the Payloads section for an interaction that carried no payloads', async () => {
    mockFetch(); // INTERACTIONS[0] has null request/response hashes
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument());
    // No Payloads header when both hashes are null.
    expect(screen.queryByRole('heading', { name: 'Payloads' })).toBeNull();
  });

  // --- Infrastructure filter: MCP plumbing rows (lifecycle / tool discovery,
  // per the server classifier's `kinds`) are hidden by default; an inline
  // affordance names the hidden count and toggles them (?showInfra=1 on the
  // page, `showInfra` prop here).

  it('hides infrastructure interactions by default and offers a count affordance', async () => {
    mockFetchInfra();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    // The lifecycle + discovery rows are hidden…
    expect(screen.queryByText(/3 \(1 anchor\)/)).toBeNull();
    expect(screen.queryByText(/4 \(1 anchor\)/)).toBeNull();
    // …the real rows stay…
    expect(screen.getByText(/5 \(1 anchor\)/)).toBeInTheDocument();
    expect(screen.getByText(/6 \(1 anchor\)/)).toBeInTheDocument();
    // …and the affordance names the hidden count.
    expect(
      screen.getByRole('button', { name: /2 infrastructure interactions hidden — show/i }),
    ).toBeInTheDocument();
  });

  it('reveals the infra rows via the affordance, which flips to "Hide N …"', async () => {
    mockFetchInfra();
    const pins = new PinStore();
    // A host owning showInfra, as TraceDetailPage does (mirroring ?showInfra=1).
    function Host() {
      const [showInfra, setShowInfra] = React.useState(false);
      return (
        <FlowTables
          traceId="T1" pins={pins} onPinsChange={() => {}}
          showInfra={showInfra} onShowInfraChange={setShowInfra}
        />
      );
    }
    renderWithProviders(<Host />);
    await userEvent.click(await screen.findByRole('button', { name: /hidden — show/i }));
    // Both infra rows appear, and the affordance flips to the hide form.
    await waitFor(() => expect(screen.getByText(/3 \(1 anchor\)/)).toBeInTheDocument());
    expect(screen.getByText(/4 \(1 anchor\)/)).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole('button', { name: /Hide 2 infrastructure interactions/i }),
    );
    await waitFor(() => expect(screen.queryByText(/3 \(1 anchor\)/)).toBeNull());
  });

  it('flat view filters whole infra interactions, keeping the surviving bracket intact', async () => {
    // An infra interaction whose legs interleave BETWEEN the real interaction's
    // request and response (seqs 1,4 vs 2,3). Filtering must drop both infra
    // legs together, leaving the real pair adjacent with an intact top/bottom
    // connector — the filter runs on interactions BEFORE the per-leg flatMap.
    const real = ixRow({ id: 'i-real', span_count: 2,
      kinds: kindsOf('a2a', null, 'agent_request', 'agent_response') });
    real.legs = [
      { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error: null, seq: 1 },
      { leg_type: 'response', occurred_at: '2026-05-01T12:00:03Z', payload_hash: null, error: false, seq: 4 },
    ];
    const infra = ixRow({ id: 'i-infra', span_count: 3,
      kinds: kindsOf('mcp', 'initialize', 'mcp_lifecycle_request', 'mcp_lifecycle_result') });
    infra.legs = [
      { leg_type: 'request', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: null, seq: 2 },
      { leg_type: 'response', occurred_at: '2026-05-01T12:00:02Z', payload_hash: null, error: false, seq: 3 },
    ];
    mockFetchInfra([real, infra]);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await userEvent.click(await screen.findByLabelText('Flat view'));
    const flat = await screen.findByLabelText('Interactions (flat)');
    const body = within(flat).getAllByRole('row').slice(1); // drop header
    // Only the real interaction's two legs remain, adjacent.
    expect(body).toHaveLength(2);
    expect(body[0]).toHaveTextContent('request');
    expect(body[1]).toHaveTextContent('response');
    // Its connector bracket survives whole: a top edge and a bottom edge.
    const markers = within(flat).getAllByTestId('flat-connector');
    expect(markers).toHaveLength(2);
  });

  it('keeps visible-row indentation stable while infra siblings are hidden (depths from the full list)', async () => {
    mockFetchInfra();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    // i-deep is depth 2 (root → i-call → i-deep). With its depth-1 infra
    // siblings hidden, its indent prefix must still read depth 2 ('│ └─'),
    // not reflow as if the tree had shrunk.
    await waitFor(() => expect(screen.getByText(/6 \(1 anchor\)/)).toBeInTheDocument());
    const deepRow = screen.getByText(/6 \(1 anchor\)/).closest('tr')!;
    expect(deepRow.textContent).toContain('│ └─');
    const callRow = screen.getByText(/5 \(1 anchor\)/).closest('tr')!;
    expect(callRow.textContent).toContain('└─');
    expect(callRow.textContent).not.toContain('│');
  });

  it('keeps an infra row visible when a visible row parents through it (no dangling child)', async () => {
    // Lifecycle hops are leaves so this shouldn't occur, but the guard must
    // hold: a hidden parent of a visible child stays visible (and is then not
    // counted in the hidden-row affordance).
    mockFetchInfra([
      ixRow({ id: 'g-parent', span_count: 7,
        kinds: kindsOf('mcp', 'initialize', 'mcp_lifecycle_request', 'mcp_lifecycle_result') }),
      ixRow({ id: 'g-child', span_count: 8, parent_interaction_id: 'g-parent',
        kinds: kindsOf('mcp', 'tools/call', 'tool_call_request', 'tool_call_result') }),
      ixRow({ id: 'g-leaf', span_count: 9, parent_interaction_id: 'g-child',
        kinds: kindsOf('mcp', 'ping', 'mcp_lifecycle_request', 'mcp_lifecycle_result') }),
    ]);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    // The infra parent survives (its child is visible); the infra leaf hides.
    await waitFor(() => expect(screen.getByText(/8 \(1 anchor\)/)).toBeInTheDocument());
    expect(screen.getByText(/7 \(1 anchor\)/)).toBeInTheDocument();
    expect(screen.queryByText(/9 \(1 anchor\)/)).toBeNull();
    // The child still renders indented under its (kept) parent.
    const childRow = screen.getByText(/8 \(1 anchor\)/).closest('tr')!;
    expect(childRow.textContent).toContain('└─');
    // Only the leaf counts as hidden (singular form).
    expect(
      screen.getByRole('button', { name: /1 infrastructure interaction hidden — show/i }),
    ).toBeInTheDocument();
  });

  it('shows no infra affordance when the trace has no infrastructure rows', async () => {
    mockFetch(); // INTERACTIONS carry no `kinds` at all (pre-kinds rows)
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /infrastructure/i })).toBeNull();
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
