import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EntityPill } from './EntityPill';
import type { Entity } from '../types';

const ent = (over: Partial<Entity> = {}): Entity => ({
  id: 'e',
  kind: 'agent',
  natural_key: 'agent:(p,a)',
  display_name: 'agent-a',
  detected_from: '',
  ...over,
});

describe('EntityPill', () => {
  it('labels the pill with the entity kind', () => {
    render(<EntityPill entity={ent({ kind: 'agent' })} />);
    expect(screen.getByText('agent')).toBeInTheDocument();
  });

  it('marks a deployed tool with a solid-border title and an in-framework tool as dashed', () => {
    const { rerender } = render(
      <EntityPill entity={ent({ kind: 'tool', natural_key: 'tool:(p,svc)' })} />,
    );
    expect(screen.getByTitle(/Deployed as its own MCP service/i)).toBeInTheDocument();

    rerender(
      <EntityPill entity={ent({ kind: 'tool', natural_key: 'tool:agent:(p,a):search' })} />,
    );
    expect(screen.getByTitle(/In-framework tool hosted by an agent/i)).toBeInTheDocument();
  });
});
