import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { screen, waitFor, within } from '@testing-library/react';
import { renderWithProviders } from '../test/renderWithProviders';
import { DataLineageView } from './DataLineageView';
import type { DataLineage, Entity, LineageState } from '../types';

// The view resolves a lineage natural key to the entity's friendly display_name
// off the trace-scoped entities query, and takes the traceId from the route — so
// every render here needs a Router + QueryClient (renderWithProviders) mounted at
// the real trace path, and a stubbed `GET /api/traces/T1/entities`.
const ROUTE = '/traces/T1/flow';

/** Render under the trace route, where `:traceId` resolves to `T1`. */
function render(ui: Parameters<typeof renderWithProviders>[0]) {
  return renderWithProviders(ui, { route: ROUTE });
}

function entity(over: Partial<Entity> = {}): Entity {
  return {
    id: 'e1',
    kind: 'agent',
    natural_key: 'agent-one',
    display_name: 'agent-one',
    detected_from: 'span',
    ...over,
  };
}

/**
 * Stub the entities read this view now consumes. Defaults to an entity set whose
 * natural keys match the fixture lineage below, so the pre-existing assertions
 * (which name sources by their bare keys) keep passing unchanged.
 */
function mockEntities(entities: Entity[] = [entity(), entity({ id: 'e2', natural_key: 'user', kind: 'user', display_name: 'user' }), entity({ id: 'e3', natural_key: 'llm-x', kind: 'llm', display_name: 'llm-x' })]) {
  (fetch as ReturnType<typeof vi.fn>).mockImplementation(async (url: string) => {
    if (url.startsWith('/api/traces/T1/entities')) {
      return { ok: true, status: 200, json: async () => ({ entities }) };
    }
    throw new Error(`unexpected fetch: ${url}`);
  });
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn());
  mockEntities();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A fully-populated lineage triple; individual tests override slices of it. */
function lineage(over: Partial<DataLineage> = {}): DataLineage {
  return {
    data_sources: ['agent-one', 'user'],
    source_transformations: {
      'agent-one': ['summarization'],
      user: ['anonymization', 'summarization'],
    },
    entities: ['agent-one', 'llm-x', 'user'],
    seq: 2,
    ...over,
  };
}

/** The `derived` arm — a real triple the backend actually produced. */
function derived(over: Partial<DataLineage> = {}): LineageState {
  return { kind: 'derived', lineage: lineage(over) };
}

describe('DataLineageView', () => {
  it('lists the data sources the payload originated from', () => {
    render(<DataLineageView state={derived()} />);
    const sources = screen.getByLabelText('Data sources');
    expect(within(sources).getByText('agent-one')).toBeInTheDocument();
    expect(within(sources).getByText('user')).toBeInTheDocument();
  });

  it('lists the transformations applied per data source', () => {
    render(<DataLineageView state={derived()} />);
    const sources = screen.getByLabelText('Data sources');
    // Each source's own transformation set sits with that source, so the
    // "what happened to MY data" question is answered per origin.
    const agentRow = within(sources).getByText('agent-one').closest('tr')!;
    expect(within(agentRow).getByText('summarization')).toBeInTheDocument();
    const userRow = within(sources).getByText('user').closest('tr')!;
    expect(within(userRow).getByText('anonymization')).toBeInTheDocument();
    expect(within(userRow).getByText('summarization')).toBeInTheDocument();
  });

  it('shows the entities traversed as an unordered set, with no arrow chain', () => {
    render(
      <DataLineageView state={derived({ entities: ['agent-one', 'llm-x', 'user'] })} />,
    );
    const group = screen.getByLabelText('Entities traversed');
    // Membership is the whole claim: the spec defines this element as unordered
    // and defers ordering to a future trace-derived API, so the UI asserts WHICH
    // entities, never in what sequence.
    for (const entity of ['user', 'agent-one', 'llm-x']) {
      expect(within(group).getByText(entity)).toBeInTheDocument();
    }
    // No arrow chain: rendering `a -> b -> c` would assert a sequence the data
    // does not carry. The old LongArrowAltRightIcon left an `svg` between hops.
    expect(group.querySelector('svg')).toBeNull();
  });

  it('renders the same entity set regardless of the order the array arrives in', () => {
    // The wire array is sorted for byte-stable re-derivation only. A permuted
    // array is the SAME set, so it must render the same members — nothing in the
    // UI may turn array position into meaning.
    const members = (arr: string[]) => {
      const { unmount } = render(<DataLineageView state={derived({ entities: arr })} />);
      const labels = Array.from(
        screen.getByLabelText('Entities traversed').querySelectorAll('.pf-v5-c-label'),
      ).map((el) => el.textContent);
      unmount();
      return new Set(labels);
    };

    expect(members(['user', 'agent-one', 'llm-x'])).toEqual(
      members(['llm-x', 'user', 'agent-one']),
    );
  });

  it('renders a pending lineage distinctly as "not yet computed", with no sources block', () => {
    render(<DataLineageView state={{ kind: 'pending' }} />);
    // The eventual-consistency window (P-data-lineage has not derived this leg
    // yet) — an explicit state, never an empty block.
    expect(screen.getByText(/not yet computed/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Data sources')).toBeNull();
    expect(screen.queryByLabelText('Entities traversed')).toBeNull();
  });

  it('renders a failed read as an error, NOT as "not yet computed"', () => {
    // "We could not ask" and "the answer is not ready" prompt opposite actions:
    // retry vs wait. Collapsing the former into the latter leaves a reader
    // waiting forever on a request that already failed.
    render(<DataLineageView state={{ kind: 'error' }} />);
    expect(screen.getByText(/failed to load lineage/i)).toBeInTheDocument();
    expect(screen.queryByText(/not yet computed/i)).toBeNull();
    // And it makes no claim about the data itself.
    expect(screen.queryByLabelText('Data sources')).toBeNull();
    expect(screen.queryByLabelText('Entities traversed')).toBeNull();
    expect(screen.queryByText(/originates here/i)).toBeNull();
  });

  it('announces the lineage read failure to assistive tech', () => {
    // The failure is what stops a governance reader trusting the (absent)
    // lineage, so it must not be a colour-only signal.
    render(<DataLineageView state={{ kind: 'error' }} />);
    expect(screen.getByRole('alert').textContent).toMatch(/failed to load lineage/i);
  });

  it('states an origin’s genuinely empty triple as a real derived result', () => {
    // A derived origin legitimately has no sources and an empty entity set
    // (ADR-0027): that is a REAL value and must stay distinguishable from the
    // null state.
    render(
      <DataLineageView
        state={derived({ data_sources: [], source_transformations: {}, entities: [] })}
      />,
    );
    expect(screen.queryByText(/not yet computed/i)).toBeNull();
    expect(screen.getByText(/originates here/i)).toBeInTheDocument();
  });

  it('shows a no-transformations note for a source that had none applied', () => {
    render(
      <DataLineageView
        state={derived({
          data_sources: ['agent-one'],
          source_transformations: { 'agent-one': [] },
          entities: ['agent-one'],
        })}
      />,
    );
    const agentRow = screen
      .getByLabelText('Data sources')
      .querySelector('tbody tr')!;
    expect(within(agentRow as HTMLElement).getByText(/none/i)).toBeInTheDocument();
  });

  it('marks the long Entity-natural-key cells for the wrap that stops panel clipping', () => {
    // Real sources/entities are long unbreakable tokens
    // (`tool:agent:(proj,svc):name`) rendered in a ~30%-wide floating panel.
    // jsdom applies no CSS file, so assert the CONTRACT: the cells opt into
    // `dg-lineage-key`, and that class grants the wrap through PF's inner
    // `.pf-v5-c-label__text` (which forces nowrap + ellipsis and cannot be
    // overridden from an inline style on the outer Label). Verified visually in
    // the running app; this guards the regression.
    render(
      <DataLineageView
        state={derived({
          data_sources: ['tool:agent:(travel_advisor,travel-advisor):search_destinations'],
          entities: ['llm:ete-litellm.ai-models.example.com/claude-haiku-4-5'],
        })}
      />,
    );
    const sourceCell = screen
      .getByLabelText('Data sources')
      .querySelector('tbody td')!;
    expect(sourceCell).toHaveClass('dg-lineage-key');
    const entityLabel = screen
      .getByLabelText('Entities traversed')
      .querySelector('.pf-v5-c-label')!;
    expect(entityLabel).toHaveClass('dg-lineage-key');

    // Vitest runs from the ui/ package root, so resolve from cwd.
    const css = readFileSync(resolve('src/styles/global.css'), 'utf8');
    expect(css).toMatch(/\.dg-lineage-key \.pf-v5-c-label__text/);
    expect(css).toMatch(/overflow-wrap:\s*anywhere/);
  });

  it('lists a source that carries no entry in the transformations map', () => {
    // The map is keyed by source, but a source need not appear in it; the
    // source list is the authority on which origins exist.
    render(
      <DataLineageView
        state={derived({ data_sources: ['orphan-src'], source_transformations: {} })}
      />,
    );
    expect(
      within(screen.getByLabelText('Data sources')).getByText('orphan-src'),
    ).toBeInTheDocument();
  });

  // ── Friendly names over qualified natural keys ─────────────────────────────
  //
  // Lineage stores the natural key on purpose (ADR-0027) — it is the entity's
  // identity, qualified so two same-named tools on different agents cannot
  // collapse into one source. But `tool:agent:(travel_advisor,travel-advisor):search_destinations`
  // is not what a reader recognises, and every other flow surface already shows
  // `display_name`. These fix the presentation without touching the stored value.

  const TOOL_KEY = 'tool:agent:(travel_advisor,travel-advisor):search_destinations';
  const LLM_KEY = 'llm:ete-litellm.ai-models.example.com/claude-haiku-4-5';

  it('renders a source’s friendly display name instead of its qualified key', async () => {
    mockEntities([
      entity({ id: 't1', kind: 'tool', natural_key: TOOL_KEY, display_name: 'search_destinations' }),
    ]);
    render(
      <DataLineageView
        state={derived({
          data_sources: [TOOL_KEY],
          source_transformations: { [TOOL_KEY]: ['summarization'] },
          entities: [],
        })}
      />,
    );
    const sources = screen.getByLabelText('Data sources');
    await waitFor(() =>
      expect(within(sources).getByText('search_destinations')).toBeInTheDocument(),
    );
    // The qualified key is no longer the visible text of the cell.
    expect(within(sources).queryByText(TOOL_KEY)).toBeNull();
  });

  it('renders a traversed entity’s friendly display name instead of its qualified key', async () => {
    mockEntities([
      entity({ id: 'l1', kind: 'llm', natural_key: LLM_KEY, display_name: 'claude-haiku-4-5' }),
    ]);
    render(<DataLineageView state={derived({ data_sources: [], entities: [LLM_KEY] })} />);
    const group = screen.getByLabelText('Entities traversed');
    await waitFor(() => expect(within(group).getByText('claude-haiku-4-5')).toBeInTheDocument());
    expect(within(group).queryByText(LLM_KEY)).toBeNull();
  });

  it('keeps the full natural key discoverable in a tooltip on the shortened label', async () => {
    // The friendly name alone is ambiguous, so the identity must stay reachable.
    // Asserted on the `title` attribute — the convention EntityPill and SpanTree
    // already use for a qualified value behind a short one.
    mockEntities([
      entity({ id: 't1', kind: 'tool', natural_key: TOOL_KEY, display_name: 'search_destinations' }),
      entity({ id: 'l1', kind: 'llm', natural_key: LLM_KEY, display_name: 'claude-haiku-4-5' }),
    ]);
    render(
      <DataLineageView
        state={derived({ data_sources: [TOOL_KEY], entities: [LLM_KEY] })}
      />,
    );
    await waitFor(() =>
      expect(
        within(screen.getByLabelText('Data sources')).getByText('search_destinations'),
      ).toBeInTheDocument(),
    );
    const sourceCell = screen.getByLabelText('Data sources').querySelector('tbody td')!;
    expect(sourceCell).toHaveAttribute('title', TOOL_KEY);

    const entityLabel = screen
      .getByLabelText('Entities traversed')
      .querySelector('.pf-v5-c-label')!;
    expect(entityLabel).toHaveAttribute('title', LLM_KEY);
  });

  it('falls back to the raw natural key when the entity set does not name it', async () => {
    // Entities load asynchronously and lineage may cite an entity the current set
    // lacks. An unresolved name must never render blank or as "undefined" — the
    // key is the identity the row actually asserts.
    mockEntities([]);
    render(
      <DataLineageView state={derived({ data_sources: [TOOL_KEY], entities: [LLM_KEY] })} />,
    );
    const sources = screen.getByLabelText('Data sources');
    expect(within(sources).getByText(TOOL_KEY)).toBeInTheDocument();
    expect(
      within(screen.getByLabelText('Entities traversed')).getByText(LLM_KEY),
    ).toBeInTheDocument();
    // No redundant tooltip when the label already IS the key.
    const sourceCell = sources.querySelector('tbody td')!;
    expect(sourceCell).not.toHaveAttribute('title');
    // And it stays that way after the (empty) read settles — no late blanking.
    await waitFor(() => expect(within(sources).getByText(TOOL_KEY)).toBeInTheDocument());
  });

  it('falls back to the key when a failed entities read means no names at all', async () => {
    (fetch as ReturnType<typeof vi.fn>).mockImplementation(async () => ({
      ok: false,
      status: 500,
      json: async () => ({}),
    }));
    render(<DataLineageView state={derived({ data_sources: [TOOL_KEY], entities: [] })} />);
    // A name lookup that cannot be made degrades the LABEL only; the lineage
    // itself was read successfully and is still fully stated.
    await waitFor(() =>
      expect(
        within(screen.getByLabelText('Data sources')).getByText(TOOL_KEY),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByText(/failed to load lineage/i)).toBeNull();
  });

  it('keeps two sources sharing a display name distinguishable, with their own transformations', async () => {
    // `create_booking` exists on more than one agent, which is exactly why the
    // stored key is qualified. Relabelling both rows `create_booking` must not
    // make them look like one source, nor swap their transformation sets — the
    // map is keyed by natural key, not by label.
    const bookingKey = 'tool:agent:(x,booking-agent):create_booking';
    const otherKey = 'tool:agent:(y,other-agent):create_booking';
    mockEntities([
      entity({ id: 'b1', kind: 'tool', natural_key: bookingKey, display_name: 'create_booking' }),
      entity({ id: 'b2', kind: 'tool', natural_key: otherKey, display_name: 'create_booking' }),
    ]);
    render(
      <DataLineageView
        state={derived({
          data_sources: [bookingKey, otherKey],
          source_transformations: { [bookingKey]: ['anonymization'], [otherKey]: ['summarization'] },
          entities: [bookingKey, otherKey],
        })}
      />,
    );
    const sources = screen.getByLabelText('Data sources');
    await waitFor(() =>
      expect(within(sources).getAllByText('create_booking')).toHaveLength(2),
    );

    // Both rows are present and told apart by their tooltipped full key.
    const cells = Array.from(sources.querySelectorAll('tbody td[data-label="Source"]'));
    expect(cells.map((c) => c.getAttribute('title'))).toEqual([bookingKey, otherKey]);

    // Each row still carries ITS OWN transformations — the relabel did not slide
    // the map lookup onto the neighbouring source.
    const rows = Array.from(sources.querySelectorAll('tbody tr'));
    expect(within(rows[0] as HTMLElement).getByText('anonymization')).toBeInTheDocument();
    expect(within(rows[0] as HTMLElement).queryByText('summarization')).toBeNull();
    expect(within(rows[1] as HTMLElement).getByText('summarization')).toBeInTheDocument();

    // Same for the entity set: two members, not one collapsed label.
    const labels = Array.from(
      screen.getByLabelText('Entities traversed').querySelectorAll('.pf-v5-c-label'),
    );
    expect(labels.map((l) => l.getAttribute('title'))).toEqual([bookingKey, otherKey]);
  });

  it('still distinguishes the three states once names are resolved', async () => {
    // Guard on the resolution work not disturbing ADR-0027 D10's distinctions:
    // the name lookup is a label concern and must not leak into error / pending /
    // derived, nor into the two derived empty-states.
    mockEntities([entity({ id: 't1', kind: 'tool', natural_key: TOOL_KEY, display_name: 'search_destinations' })]);

    const { unmount } = render(<DataLineageView state={{ kind: 'error' }} />);
    expect(screen.getByRole('alert').textContent).toMatch(/failed to load lineage/i);
    unmount();

    const p = render(<DataLineageView state={{ kind: 'pending' }} />);
    expect(screen.getByText(/not yet computed/i)).toBeInTheDocument();
    p.unmount();

    render(
      <DataLineageView
        state={derived({ data_sources: [], source_transformations: {}, entities: [] })}
      />,
    );
    expect(screen.getByText(/originates here/i)).toBeInTheDocument();
    expect(screen.getByText(/no entities traversed/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/not yet computed/i)).toBeNull());
  });
});
