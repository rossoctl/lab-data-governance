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
    started_at: '2026-05-01T12:00:00Z', ended_at: '2026-05-01T12:00:01Z',
    error: false, request_payload_hash: null, response_payload_hash: null,
    summary: 'agent calls search', parent_interaction_id: null,
    span_count: 2, anchor_count: 1,
  },
];

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

  it('selects an interaction row on click and shows its summary under a Details / Interaction header', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    // Click the interaction row via its unique span-count cell.
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // The detail panel is titled "Details" with a leading "Interaction" section.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Details' })).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'Interaction' })).toBeInTheDocument();
    expect(screen.getByText('agent calls search')).toBeInTheDocument();
  });

  it('shows an always-visible detail panel with a placeholder before any selection', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    await waitFor(() => expect(screen.getByLabelText('Entities')).toBeInTheDocument());
    // Panel header present, plus a "select something" hint, before any click.
    expect(screen.getByRole('heading', { name: 'Details' })).toBeInTheDocument();
    expect(screen.getByText(/Select an entity or interaction/i)).toBeInTheDocument();
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
    // An interaction that carries payload hashes (the common HTTP/MCP case).
    const withPayload = [
      {
        ...INTERACTIONS[0],
        request_payload_hash: 'reqhash0deadbeef',
        response_payload_hash: 'resphash0feedface',
      },
    ];
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
      { ...INTERACTIONS[0], request_payload_hash: 'reqhash0deadbeef', response_payload_hash: null },
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
      { ...INTERACTIONS[0], request_payload_hash: 'reqhash0deadbeef', response_payload_hash: null },
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
