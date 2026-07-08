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
 * A pill labelled with an entity's kind. Tools carry a border-style cue for
 * their subtype (deployed = solid, in-framework = dashed) plus a tooltip —
 * the flavour is derived from the natural-key shape (see toolSubtype), there
 * is no schema field for it.
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
      style={sub === 'in-framework' ? { borderStyle: 'dashed' } : undefined}
    >
      {entity.kind}
    </Label>
  );
}
