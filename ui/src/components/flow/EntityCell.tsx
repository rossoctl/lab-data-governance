import type { Entity } from '../../types';
import { EntityPill } from '../EntityPill';

/**
 * A Caller/Callee cell: the entity's kind pill plus its display name, or `?`
 * when the interaction names an entity id the trace's entity set doesn't carry
 * (or names none at all). Shared by both interaction tables so their
 * unknown-participant rendering can't diverge.
 */
export function EntityCell({ entity }: { entity: Entity | undefined }) {
  if (!entity) return <>?</>;
  return (
    <>
      <EntityPill entity={entity} /> {entity.display_name}
    </>
  );
}
