import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { FlowTables } from './FlowTables';
import { PinStore } from '../lib/pins';

/**
 * The two sidecar-specific behaviours of the flow view (issue #155, ADR-0030),
 * kept in their own file rather than folded into `FlowTables.test.tsx`:
 *
 * 1. **the infrastructure filter** — MCP lifecycle / tool-discovery interactions
 *    are hidden by default behind a counted affordance, because on a real trace
 *    they are most of the rows and none of the signal;
 * 2. **the sidecar detail-panel rows** — `kind` / `destination` / `http` /
 *    `user` / `session` / `trace_id`, plus the span `name` column on the
 *    evidence table.
 *
 * Both read `kinds` and the other read-time derivations the API serves only for
 * sidecar-derived interactions, so every fixture here carries them; an
 * interaction with `kinds: null` (streaming/graph-derived) must behave as
 * not-infrastructure, and one case pins exactly that.
 */

const ENTITIES = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
];

/**
 * Main's tree table renders no summary column — its columns are Started / pin /
 * Caller / Callee / Status / Spans — so a row is identified here by its
 * **Started** time, which `formatTime24Utc` renders as `HH:MM:SS`. Each fixture
 * therefore gets a distinct second, and `startedAt(n)` is how a case names one.
 */
function startedAt(n: number): string {
  return `12:00:${String(n).padStart(2, '0')}`;
}

function leg(type: 'request' | 'response', seq: number, second: number) {
  return {
    leg_type: type,
    occurred_at: `2026-05-01T12:00:${String(second).padStart(2, '0')}Z`,
    payload_hash: null,
    error: false,
    seq,
  };
}

/** One interaction with both legs, its request leg starting at `second`.
 *  `kinds.request_content_kind` is what the infrastructure filter reads, so it
 *  is the interesting knob. */
function ixRow(
  id: string,
  second: number,
  over: Record<string, unknown> = {},
  requestContentKind: string | null = 'a2a_request',
) {
  return {
    id,
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    summary: `summary-${id}`,
    parent_interaction_id: null,
    legs: [leg('request', second * 2, second), leg('response', second * 2 + 1, second + 30)],
    duration_seconds: 1,
    any_error: false,
    span_count: 2,
    anchor_count: 1,
    kinds: {
      protocol: requestContentKind?.startsWith('mcp') || requestContentKind?.startsWith('tool')
        ? 'mcp'
        : 'a2a',
      mcp_method: null,
      request_content_kind: requestContentKind,
      response_content_kind: null,
    },
    destination: null,
    http: null,
    principal_sub: null,
    session_id: null,
    ...over,
  };
}

const INTERACTION_EVIDENCE = [
  {
    span_id: 'span-xyz',
    role: 'anchor',
    parent_id: 'span-parent',
    kind: 'CLIENT',
    name: 'weather-service a2a message/send',
    service_name: 'authbridge',
  },
];

function mockFetch(interactions: unknown[]) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.includes('/data-lineage-summary')) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ sources: [], destinations: [], status: 'complete', stopped_at_seq: null }),
      };
    }
    if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions }) };
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
    if (url.includes('/interactions/')) return { ok: true, status: 200, json: async () => ({ spans: INTERACTION_EVIDENCE }) };
    return { ok: true, status: 200, json: async () => ({ spans: [] }) };
  });
}

/** The rendered Started cells of the interactions table, in order — the visible row set. */
async function visibleRows(label = 'Interactions'): Promise<string[]> {
  const table = await screen.findByRole('grid', { name: label });
  return within(table)
    .getAllByRole('row')
    .slice(1) // drop the header row
    .map((tr) => tr.querySelector('td')?.textContent?.trim() ?? '');
}

/** Stands in for TraceDetailPage, which owns `?showInfra` as a controlled prop. */
function Host(props: Partial<React.ComponentProps<typeof FlowTables>> = {}) {
  const [showInfra, setShowInfra] = React.useState(false);
  return (
    <FlowTables
      traceId="t1"
      pins={new PinStore()}
      onPinsChange={() => {}}
      legView="tree"
      showInfra={showInfra}
      onShowInfraChange={setShowInfra}
      {...props}
    />
  );
}

describe('FlowTables · infrastructure filter', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('hides infrastructure interactions by default and names the hidden count', async () => {
    mockFetch([
      ixRow('i-real', 1),
      ixRow('i-life', 2, {}, 'mcp_lifecycle_request'),
      ixRow('i-disc', 3, {}, 'tool_discovery_request'),
    ]);
    renderWithProviders(<Host />);

    expect(await visibleRows()).toEqual([startedAt(1)]);
    expect(
      screen.getByRole('button', { name: /2 infrastructure interactions hidden — show/i }),
    ).toBeTruthy();
  });

  it('reveals the infra rows via the affordance, which flips to "Hide N"', async () => {
    mockFetch([
      ixRow('i-real', 1),
      ixRow('i-life', 2, {}, 'mcp_lifecycle_request'),
      ixRow('i-disc', 3, {}, 'tool_discovery_request'),
    ]);
    renderWithProviders(<Host />);

    await userEvent.click(await screen.findByRole('button', { name: /hidden — show/i }));

    expect(await visibleRows()).toEqual([startedAt(1), startedAt(2), startedAt(3)]);
    expect(
      screen.getByRole('button', { name: /Hide 2 infrastructure interactions/i }),
    ).toBeTruthy();
  });

  it('uses the singular form for exactly one hidden row', async () => {
    mockFetch([ixRow('i-real', 1), ixRow('i-life', 2, {}, 'mcp_lifecycle_request')]);
    renderWithProviders(<Host />);

    expect(
      await screen.findByRole('button', { name: /1 infrastructure interaction hidden — show/i }),
    ).toBeTruthy();
  });

  it('offers no affordance when the trace has no infrastructure rows', async () => {
    mockFetch([ixRow('i-real', 1), ixRow('i-other', 2)]);
    renderWithProviders(<Host />);

    expect(await visibleRows()).toEqual([startedAt(1), startedAt(2)]);
    expect(screen.queryByRole('button', { name: /infrastructure/i })).toBeNull();
  });

  it('treats an interaction with no kinds at all as not-infrastructure', async () => {
    // Streaming/graph-derived interactions carry `kinds: null`. Never fabricate a
    // default that would hide them.
    mockFetch([ixRow('i-real', 1), ixRow('i-nokinds', 2, { kinds: null })]);
    renderWithProviders(<Host />);

    expect(await visibleRows()).toEqual([startedAt(1), startedAt(2)]);
    expect(screen.queryByRole('button', { name: /infrastructure/i })).toBeNull();
  });

  it('keeps an infra row visible when a visible row parents through it', async () => {
    // A hidden parent of a visible child must stay, or the tree renders a child
    // with no parent — and it is then NOT counted as hidden.
    mockFetch([
      ixRow('i-root', 1),
      ixRow('i-mid', 2, { parent_interaction_id: 'i-root' }, 'mcp_lifecycle_request'),
      ixRow('i-leaf', 3, { parent_interaction_id: 'i-mid' }),
      ixRow('i-hidden-leaf', 4, { parent_interaction_id: 'i-root' }, 'mcp_lifecycle_request'),
    ]);
    renderWithProviders(<Host />);

    // i-mid survives because it parents a visible row; i-hidden-leaf does not.
    expect(await visibleRows()).toEqual([startedAt(1), startedAt(2), startedAt(3)]);
    expect(
      screen.getByRole('button', { name: /1 infrastructure interaction hidden — show/i }),
    ).toBeTruthy();
  });

  it('keeps visible-row indentation stable while infra siblings are hidden', async () => {
    // Depths come from the FULL list: i-deep is depth 2 (root → mid → deep) and
    // must still read as depth 2 with its depth-1 infra sibling hidden.
    mockFetch([
      ixRow('i-root', 1),
      ixRow('i-mid', 2, { parent_interaction_id: 'i-root' }),
      ixRow('i-deep', 3, { parent_interaction_id: 'i-mid' }),
      ixRow('i-infra-sib', 4, { parent_interaction_id: 'i-root' }, 'tool_discovery_request'),
    ]);
    renderWithProviders(<Host />);

    const table = await screen.findByRole('grid', { name: 'Interactions' });
    const rows = within(table).getAllByRole('row').slice(1);
    const deepRow = rows.find((tr) => tr.querySelector('td')?.textContent?.trim() === startedAt(3));
    expect(deepRow).toBeTruthy();
    // depth 2 renders one '│ ' before the '└─' guide; depth 1 renders none.
    expect(deepRow!.textContent).toContain('│ └─');
  });

  it('drops both legs of a hidden interaction in the flat view', async () => {
    // The filter runs on interactions BEFORE the per-leg flatMap, so a hidden
    // interaction never contributes a half-bracket to the flat rows: the visible
    // interaction's two legs are all that remain.
    mockFetch([ixRow('i-real', 1), ixRow('i-life', 2, {}, 'mcp_lifecycle_request')]);
    renderWithProviders(<Host legView="flat" />);

    expect(await screen.findByRole('button', { name: /hidden — show/i })).toBeTruthy();
    const flat = await visibleRows('Interactions (flat)');
    expect(flat).toHaveLength(2); // one request leg + one response leg, both i-real's
  });
});

describe('FlowTables · sidecar detail-panel rows (issue #155)', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()));
  afterEach(() => vi.unstubAllGlobals());

  it('shows the sidecar derivations and the span name for the selected interaction', async () => {
    mockFetch([
      ixRow('i-real', 1, {
        destination: {
          url: 'http://weather:8000/a2a',
          host: 'weather:8000',
          path: '/a2a',
          internal: true,
        },
        http: { method: 'POST', status_code: 200, outcome: 'ok' },
        principal_sub: 'alice',
        session_id: 'sess-42',
      }),
    ]);
    renderWithProviders(<Host />);

    await userEvent.click((await screen.findAllByText(startedAt(1)))[0]);

    // The panel is the only place these strings appear.
    expect(await screen.findByText('trace_id')).toBeTruthy();
    expect(screen.getByText('t1')).toBeTruthy();
    expect(screen.getByText('kind')).toBeTruthy();
    expect(screen.getByText('destination')).toBeTruthy();
    expect(screen.getByText('http://weather:8000/a2a (internal)')).toBeTruthy();
    expect(screen.getByText('http')).toBeTruthy();
    expect(screen.getByText('POST → 200 (ok)')).toBeTruthy();
    expect(screen.getByText('user')).toBeTruthy();
    expect(screen.getByText('alice')).toBeTruthy();
    expect(screen.getByText('session')).toBeTruthy();
    expect(screen.getByText('sess-42')).toBeTruthy();

    // …and the evidence table names the span, not just its id.
    const evidence = screen.getByRole('grid', { name: /span evidence/i });
    expect(within(evidence).getByText('weather-service a2a message/send')).toBeTruthy();
  });

  it('omits a row whose fact is absent rather than printing it empty', async () => {
    mockFetch([
      ixRow('i-bare', 1, { destination: null, http: null, principal_sub: null, session_id: null }),
    ]);
    renderWithProviders(<Host />);

    await userEvent.click((await screen.findAllByText(startedAt(1)))[0]);

    expect(await screen.findByText('trace_id')).toBeTruthy(); // always present
    expect(screen.queryByText('destination')).toBeNull();
    expect(screen.queryByText('http')).toBeNull();
    expect(screen.queryByText('user')).toBeNull();
    expect(screen.queryByText('session')).toBeNull();
  });
});
