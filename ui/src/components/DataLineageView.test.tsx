import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { DataLineageView } from './DataLineageView';
import type { DataLineage } from '../types';

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

describe('DataLineageView', () => {
  it('lists the data sources the payload originated from', () => {
    render(<DataLineageView lineage={lineage()} />);
    const sources = screen.getByLabelText('Data sources');
    expect(within(sources).getByText('agent-one')).toBeInTheDocument();
    expect(within(sources).getByText('user')).toBeInTheDocument();
  });

  it('lists the transformations applied per data source', () => {
    render(<DataLineageView lineage={lineage()} />);
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
      <DataLineageView lineage={lineage({ entities: ['agent-one', 'llm-x', 'user'] })} />,
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
      const { unmount } = render(<DataLineageView lineage={lineage({ entities: arr })} />);
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

  it('renders a null lineage distinctly as "not yet computed", with no sources block', () => {
    render(<DataLineageView lineage={null} />);
    // The eventual-consistency window (P-data-lineage has not derived this leg
    // yet) — an explicit state, never an empty block.
    expect(screen.getByText(/not yet computed/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Data sources')).toBeNull();
    expect(screen.queryByLabelText('Entities traversed')).toBeNull();
  });

  it('states an origin’s genuinely empty triple as a real derived result', () => {
    // A derived origin legitimately has no sources and an empty entity set
    // (ADR-0027): that is a REAL value and must stay distinguishable from the
    // null state.
    render(
      <DataLineageView
        lineage={lineage({ data_sources: [], source_transformations: {}, entities: [] })}
      />,
    );
    expect(screen.queryByText(/not yet computed/i)).toBeNull();
    expect(screen.getByText(/originates here/i)).toBeInTheDocument();
  });

  it('shows a no-transformations note for a source that had none applied', () => {
    render(
      <DataLineageView
        lineage={lineage({
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
        lineage={lineage({
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
        lineage={lineage({ data_sources: ['orphan-src'], source_transformations: {} })}
      />,
    );
    expect(
      within(screen.getByLabelText('Data sources')).getByText('orphan-src'),
    ).toBeInTheDocument();
  });
});
