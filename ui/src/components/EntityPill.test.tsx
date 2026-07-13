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

  it('gives every pill the bordered class, with the dashed modifier only for in-framework tools', () => {
    // The border is drawn by the `dg-ent-pill` class (global.css) on the outer
    // .pf-v5-c-label element, keyed off the kind color; in-framework tools add
    // the `--dashed` modifier. (jsdom doesn't apply stylesheet borders, so we
    // assert the class contract rather than a computed border style.)
    const labelOf = (text: string) =>
      screen.getByText(text).closest('.pf-v5-c-label') as HTMLElement;

    // Non-tool entity: bordered, not dashed.
    const { rerender } = render(<EntityPill entity={ent({ kind: 'agent' })} />);
    let pill = labelOf('agent');
    expect(pill).toHaveClass('dg-ent-pill');
    expect(pill).not.toHaveClass('dg-ent-pill--dashed');

    // Deployed tool: bordered, solid (no dashed modifier).
    rerender(<EntityPill entity={ent({ kind: 'tool', natural_key: 'tool:(p,svc)' })} />);
    pill = labelOf('tool');
    expect(pill).toHaveClass('dg-ent-pill');
    expect(pill).not.toHaveClass('dg-ent-pill--dashed');

    // In-framework tool: dashed (distinguishable from deployed).
    rerender(<EntityPill entity={ent({ kind: 'tool', natural_key: 'tool:agent:(p,a):search' })} />);
    pill = labelOf('tool');
    expect(pill).toHaveClass('dg-ent-pill', 'dg-ent-pill--dashed');
  });
});
