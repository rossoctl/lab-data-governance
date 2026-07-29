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

  // --- Data lineage (issue #119, ADR-0027) ------------------------------------
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

  it('shows the payload lineage (data sources, per-source transformations, entities) on expand', async () => {
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Request: reqhash0/i }));

    // The Data lineage block renders in the expanded payload, beside the
    // Classification block it mirrors.
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
    // Only the request leg has lineage in the fixture, so expanding the
    // response payload must report "not yet computed" — the leg key is
    // (interaction_id, leg_type), never the payload/content hash (ADR-0027 D5).
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Response: resphash/i }));
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
    await userEvent.click(await screen.findByRole('button', { name: /Request: reqhash0/i }));
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
    await userEvent.click(await screen.findByRole('button', { name: /Request: reqhash0/i }));
    // The payload body still renders; lineage states its absence.
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
    expect(screen.getByText(/lineage not yet computed/i)).toBeInTheDocument();
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
    await userEvent.click(await screen.findByRole('button', { name: /Request: reqhash0/i }));
    // The payload body itself loaded fine — only lineage failed.
    await waitFor(() => expect(screen.getByText(/"flights"/)).toBeInTheDocument());
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

  it('reads the trace-scoped lineage resource exactly once for many payload expansions', async () => {
    mockFetchWithLineage(LINEAGE_LEGS);
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    await userEvent.click(await screen.findByRole('button', { name: /Request: reqhash0/i }));
    await userEvent.click(await screen.findByRole('button', { name: /Response: resphash/i }));
    await waitFor(() => expect(screen.getByLabelText('Data sources')).toBeInTheDocument());
    // One trace-level fetch serves every leg — NOT one per payload expansion.
    const lineageCalls = (fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
      (c) => String(c[0]).endsWith('/data-lineage'),
    );
    expect(lineageCalls).toHaveLength(1);
  });

  // --- trace-level coverage (issue #120, ADR-0027 D6) ------------------------
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
