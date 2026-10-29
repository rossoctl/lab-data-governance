import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { RiskByCategoryCard } from './RiskByCategoryCard';
import type { CategoryCount } from '../../../risk-api/types';

describe('RiskByCategoryCard', () => {
  it('renders rows sorted as served, with a "Policy events" column', () => {
    const items: CategoryCount[] = [
      { category: 'privacy', count: 12 },
      { category: 'security', count: 4 },
    ];
    render(<RiskByCategoryCard items={items} />);

    const table = screen.getByLabelText('Risk by category');
    expect(within(table).getByText('Policy events')).toBeInTheDocument();

    const rows = within(table).getAllByRole('row').slice(1); // drop header row
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText('privacy')).toBeInTheDocument();
    expect(within(rows[0]).getByText('12')).toBeInTheDocument();
    expect(within(rows[1]).getByText('security')).toBeInTheDocument();
    expect(within(rows[1]).getByText('4')).toBeInTheDocument();
  });

  it('renders an empty state when items is empty', () => {
    render(<RiskByCategoryCard items={[]} />);
    expect(screen.getByText(/no policy events/i)).toBeInTheDocument();
  });
});
