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

function mockFetch() {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.endsWith('/interactions')) return { ok: true, status: 200, json: async () => ({ interactions: INTERACTIONS }) };
    if (url.endsWith('/entities')) return { ok: true, status: 200, json: async () => ({ entities: ENTITIES }) };
    // span-evidence sub-resources.
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

  it('selects an interaction row on click and shows its summary in the detail panel', async () => {
    mockFetch();
    renderWithProviders(
      <FlowTables traceId="T1" pins={new PinStore()} onPinsChange={() => {}} />,
    );
    // Click the interaction row via its unique span-count cell.
    await waitFor(() => expect(screen.getByText(/2 \(1 anchor\)/)).toBeInTheDocument());
    await userEvent.click(screen.getByText(/2 \(1 anchor\)/));
    // The detail panel opens and echoes the interaction summary.
    await waitFor(() =>
      expect(screen.getByText(/Interaction details/i)).toBeInTheDocument(),
    );
    expect(screen.getByText('agent calls search')).toBeInTheDocument();
  });
});
