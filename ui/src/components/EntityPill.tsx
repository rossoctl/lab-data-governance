import { Label } from '@patternfly/react-core';
import { toolSubtype } from '../lib/flow';
import type { Entity } from '../types';

// Per-kind pill color, matching the vanilla .ent-pill palette.
const KIND_COLOR: Record<string, React.ComponentProps<typeof Label>['color']> = {
  user: 'gold',
  external_client: 'purple',
  agent: 'blue',
  tool: 'green',
  external_service: 'red',
  llm: 'blue',
};

/**
 * A pill labelled with an entity's kind. Every pill carries a visible border
 * (PF `Label isCompact` draws none by default) in its kind color. Tools carry
 * a border-STYLE cue for their subtype — in-framework = dashed, deployed
 * (standalone MCP service) = solid — plus a tooltip; the flavour is derived
 * from the natural-key shape (see toolSubtype), there is no schema field for
 * it. Non-tool kinds use a solid border.
 */
export function EntityPill({ entity }: { entity: Entity }) {
  const sub = toolSubtype(entity);
  const title = sub
    ? sub === 'deployed'
      ? 'Deployed as its own MCP service'
      : 'In-framework tool hosted by an agent'
    : undefined;
  return (
    <Label
      isCompact
      color={KIND_COLOR[entity.kind] ?? 'grey'}
      title={title}
      // `dg-ent-pill` (global.css) draws a 1px border in the label's own
      // kind color (PF puts the kind color on the __content child, so a plain
      // `currentColor` on the outer element would read the neutral foreground
      // instead). In-framework tools get the dashed modifier; everything else
      // is solid.
      className={`dg-ent-pill${sub === 'in-framework' ? ' dg-ent-pill--dashed' : ''}`}
    >
      {entity.kind}
    </Label>
  );
}
