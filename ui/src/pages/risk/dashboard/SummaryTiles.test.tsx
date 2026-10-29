import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { SummaryTiles } from './SummaryTiles';
import type { RiskSummary } from '../../../risk-api/types';

function summary(overrides: Partial<RiskSummary> = {}): RiskSummary {
  return {
    window: '24h',
    from: '2026-08-01T00:00:00Z',
    to: '2026-08-02T00:00:00Z',
    agents: { total: 4, risky: 1, risky_pct: 25 },
    users: { total: 2, risky: 0, risky_pct: 0 },
    workflows: { total: 10, risky: 3, risky_pct: 30 },
    evaluated_interactions: { total: 100, risky: 5, risky_pct: 5 },
    rules_fired: { total: 6, critical: 2 },
    computed_at: '2026-08-02T00:00:00Z',
    ...overrides,
  };
}

describe('SummaryTiles', () => {
  it('renders four tiles with correct numbers, and no Users tile', () => {
    render(<SummaryTiles summary={summary()} />);
    expect(screen.getByText('Agents')).toBeInTheDocument();
    expect(screen.queryByText('Users')).not.toBeInTheDocument();
    expect(screen.getByText('Traces')).toBeInTheDocument();
    expect(screen.getByText('Monitored interactions')).toBeInTheDocument();
    expect(screen.getByText('Rules triggered')).toBeInTheDocument();

    expect(screen.getByText(/4 total/)).toBeInTheDocument();
    expect(screen.getByText(/1 risky \(25%\)/)).toBeInTheDocument();
    expect(screen.getByText(/10 total/)).toBeInTheDocument();
    expect(screen.getByText(/100 total/)).toBeInTheDocument();
  });

  it('renders the rules-triggered tile with total + critical, not a risky_pct', () => {
    render(<SummaryTiles summary={summary()} />);
    expect(screen.getByText(/6 total/)).toBeInTheDocument();
    expect(screen.getByText(/2 critical/)).toBeInTheDocument();
  });

  it('renders 0% rather than NaN% when a tile has 0 total', () => {
    render(
      <SummaryTiles
        summary={summary({ agents: { total: 0, risky: 0, risky_pct: 0 } })}
      />,
    );
    expect(screen.getByText(/^0 total$/)).toBeInTheDocument();
    expect(screen.getByText(/^0 risky \(0%\)$/)).toBeInTheDocument();
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });

  // Issue #222: the decided vocabulary. These tile labels are the user-facing
  // contract, so the retired wording is asserted absent rather than merely
  // "the new wording is present" — a stray duplicate tile would pass the
  // latter. The API field names (`workflows`, `evaluated_interactions`,
  // `rules_fired`) deliberately keep their server-side spelling; only the
  // rendered labels change.
  it('uses the decided terminology and none of the retired labels', () => {
    render(<SummaryTiles summary={summary()} />);
    expect(screen.queryByText('Evaluated actions')).not.toBeInTheDocument();
    expect(screen.queryByText('Rules fired')).not.toBeInTheDocument();
    expect(screen.queryByText('Workflows')).not.toBeInTheDocument();
  });

  it('renders each tile\'s risky/critical line in the danger colour', () => {
    render(<SummaryTiles summary={summary()} />);
    expect(screen.getByText(/1 risky \(25%\)/)).toHaveStyle({
      color: 'var(--pf-v5-global--danger-color--100)',
    });
    expect(screen.getByText(/2 critical/)).toHaveStyle({
      color: 'var(--pf-v5-global--danger-color--100)',
    });
  });
});
