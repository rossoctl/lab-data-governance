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
    entity_path: ['user', 'agent-one', 'llm-x'],
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

  it('shows the entity path in order', () => {
    render(
      <DataLineageView lineage={lineage({ entity_path: ['user', 'agent-one', 'llm-x'] })} />,
    );
    const path = screen.getByLabelText('Entity path');
    // Order is the whole point of entity_path (ADR-0027), so assert the
    // rendered sequence, not just membership.
    expect(path.textContent).toMatch(/user.*agent-one.*llm-x/);
  });

  it('renders a null lineage distinctly as "not yet computed", with no sources block', () => {
    render(<DataLineageView lineage={null} />);
    // The eventual-consistency window (P-data-lineage has not derived this leg
    // yet) — an explicit state, never an empty block.
    expect(screen.getByText(/not yet computed/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Data sources')).toBeNull();
    expect(screen.queryByLabelText('Entity path')).toBeNull();
  });

  it('states an origin’s genuinely empty triple as a real derived result', () => {
    // A derived origin legitimately has no sources and an empty path (ADR-0027):
    // that is a REAL value and must stay distinguishable from the null state.
    render(
      <DataLineageView
        lineage={lineage({ data_sources: [], source_transformations: {}, entity_path: [] })}
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
          entity_path: ['agent-one'],
        })}
      />,
    );
    const agentRow = screen
      .getByLabelText('Data sources')
      .querySelector('tbody tr')!;
    expect(within(agentRow as HTMLElement).getByText(/none/i)).toBeInTheDocument();
  });

  it('marks the long Entity-natural-key cells for the wrap that stops panel clipping', () => {
    // Real sources/hops are long unbreakable tokens
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
          entity_path: ['llm:ete-litellm.ai-models.example.com/claude-haiku-4-5'],
        })}
      />,
    );
    const sourceCell = screen
      .getByLabelText('Data sources')
      .querySelector('tbody td')!;
    expect(sourceCell).toHaveClass('dg-lineage-key');
    const hop = screen
      .getByLabelText('Entity path')
      .querySelector('.pf-v5-c-label')!;
    expect(hop).toHaveClass('dg-lineage-key');

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
