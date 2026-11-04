import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Entity } from '../../lib/flow';
import { NO_GRAPH_FILTER } from '../../lib/graphFilter';
import { GraphFilterBar } from './GraphFilterBar';

const ent = (id: string, name: string): Entity => ({ id, kind: 'agent', natural_key: id, display_name: name, detected_from: 'test' });
const entities = [ent('e2', 'tool'), ent('e1', 'agent')];

function setup(over: Partial<React.ComponentProps<typeof GraphFilterBar>> = {}) {
  const props = {
    entities,
    filter: NO_GRAPH_FILTER,
    onFilterChange: vi.fn(),
    risk: true,
    shown: 3,
    total: 9,
    magnifier: false,
    onMagnifierChange: vi.fn(),
    ...over,
  };
  render(<GraphFilterBar {...props} />);
  return props;
}

describe('GraphFilterBar', () => {
  it('offers the entities sorted by name, and reports a pick', async () => {
    const p = setup();
    const select = screen.getByLabelText('Filter by entity');
    expect([...select.querySelectorAll('option')].map((o) => o.textContent)).toEqual([
      'All entities',
      'Paths through agent',
      'Paths through tool',
    ]);
    await userEvent.selectOptions(select, 'e2');
    expect(p.onFilterChange).toHaveBeenCalledWith({ entityId: 'e2', minRiskLevel: null });
  });

  it('offers the risk filter only with the risk layer on', () => {
    setup({ risk: false });
    expect(screen.queryByLabelText('Filter by risk level')).toBeNull();
  });

  it('shows the count and a clear button only while a filter is on', async () => {
    setup();
    expect(screen.queryByTestId('graph-filter-count')).toBeNull();
    const p = setup({ filter: { entityId: null, minRiskLevel: 'high' } });
    expect(screen.getByTestId('graph-filter-count').textContent).toBe('Showing 3 of 9 interactions');
    await userEvent.click(screen.getByRole('button', { name: 'Clear filter' }));
    expect(p.onFilterChange).toHaveBeenCalledWith(NO_GRAPH_FILTER);
  });

  it('switches the magnifier', async () => {
    const p = setup();
    await userEvent.click(screen.getByRole('checkbox', { name: 'Magnifier' }));
    expect(p.onMagnifierChange).toHaveBeenCalledWith(true);
  });
});
