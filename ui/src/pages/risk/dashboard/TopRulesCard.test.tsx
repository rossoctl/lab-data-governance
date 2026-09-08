import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { TopRulesCard } from './TopRulesCard';
import type { TopRuleItem } from '../../../risk-api/types';

function renderCard(items: TopRuleItem[]) {
  return render(
    <MemoryRouter>
      <TopRulesCard items={items} />
    </MemoryRouter>,
  );
}

describe('TopRulesCard', () => {
  it('links each row to its rule detail page', () => {
    const items: TopRuleItem[] = [
      {
        rule_id: 'rule-1',
        rule_name: 'Excessive tool use',
        count: 9,
        trace_count: 4,
        risk_level: 'high',
        risk_level_distribution: { high: 9 },
      },
    ];
    renderCard(items);

    const table = screen.getByLabelText('Top rules');
    const link = within(table).getByRole('link', { name: /excessive tool use/i });
    expect(link).toHaveAttribute('href', '/risk/rules/rule-1');
    expect(within(table).getByText('4')).toBeInTheDocument();
    expect(within(table).getByText('high')).toBeInTheDocument();
  });

  it('falls back to the rule id when rule_name is null, and still renders a row when risk_level is null', () => {
    const items: TopRuleItem[] = [
      {
        rule_id: 'rule-2',
        rule_name: null,
        count: 3,
        trace_count: 1,
        risk_level: null,
        risk_level_distribution: {},
      },
    ];
    renderCard(items);

    const table = screen.getByLabelText('Top rules');
    const link = within(table).getByRole('link', { name: 'rule-2' });
    expect(link).toHaveAttribute('href', '/risk/rules/rule-2');
    expect(within(table).getAllByRole('row')).toHaveLength(2); // header + 1 data row
  });

  // Issue #222: "triggered", never "fired". This empty state was previously
  // uncovered.
  it('says "No rules triggered in this window" when empty, not "fired"', () => {
    renderCard([]);

    expect(screen.getByText('No rules triggered in this window.')).toBeInTheDocument();
    expect(screen.queryByText(/fired/i)).not.toBeInTheDocument();
  });

  it('renders an "All Rules" link to the rules catalog, independent of item count', () => {
    renderCard([]);

    const link = screen.getByRole('link', { name: /all rules/i });
    expect(link).toHaveAttribute('href', '/risk/rules');
  });
});
