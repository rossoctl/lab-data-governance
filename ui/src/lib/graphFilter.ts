/**
 * Graph filters for the Execution Flow view (issue #220): narrow a large trace
 * to the part a reader is asking about, by REMOVING the interactions (and then
 * the entities) that do not take part — not by dimming them.
 *
 * Two filters; with both on, the risk filter applies within the entity's paths:
 *
 * - **Entity** — the call paths through one entity. The interactions that name
 *   it as caller or callee, plus every interaction ABOVE them in the call tree
 *   (`parent_interaction_id`, the calls that led there) and every interaction
 *   BELOW them (what those calls set off). Walking the call tree rather than the
 *   entity graph matters on a hub: an agent that called five tools would
 *   otherwise pull all five into a filter on just one of them.
 * - **Risk level** — the interactions at or above a level, plus the calls that
 *   led to them, so a risky call is shown with its path rather than as an
 *   orphan arrow. Needs the risk layer's levels; an interaction with no level
 *   (not computed yet) never passes.
 *
 * Pure and render-free, like every `lib/` module here.
 */
import type { Entity, Interaction } from './flow';
import { isAtLeastRiskLevel } from './riskLevel';

export interface GraphFilter {
  /** Keep the call paths through this entity id; `null` = no entity filter. */
  entityId: string | null;
  /** Keep interactions at or above this risk level; `null` = no risk filter. */
  minRiskLevel: string | null;
}

export const NO_GRAPH_FILTER: GraphFilter = { entityId: null, minRiskLevel: null };

/** The supported minimum-risk thresholds, in the picker's display order. */
export const MIN_RISK_LEVELS: readonly string[] = ['critical', 'high', 'medium', 'low'];

/** An absent or unsupported URL threshold means no minimum-risk filter. */
export function parseMinRiskLevel(value: string | null): string | null {
  return value != null && MIN_RISK_LEVELS.includes(value) ? value : null;
}

export function isFilterActive(f: GraphFilter): boolean {
  return f.entityId != null || f.minRiskLevel != null;
}

export interface FilteredTrace {
  entities: Entity[];
  interactions: Interaction[];
}

/** The ids of `seeds` and of every interaction above them in the call tree. */
function withAncestors(seeds: Iterable<string>, byId: ReadonlyMap<string, Interaction>): Set<string> {
  const out = new Set<string>();
  for (const seed of seeds) {
    let id: string | null = seed;
    // `out.has` also stops a malformed parent cycle.
    while (id != null && !out.has(id)) {
      out.add(id);
      id = byId.get(id)?.parent_interaction_id ?? null;
    }
  }
  return out;
}

/** The ids of `seeds` and of every interaction below them in the call tree. */
function withDescendants(seeds: Iterable<string>, children: ReadonlyMap<string, readonly string[]>): Set<string> {
  const out = new Set<string>();
  const stack = [...seeds];
  while (stack.length > 0) {
    const id = stack.pop()!;
    if (out.has(id)) continue;
    out.add(id);
    stack.push(...(children.get(id) ?? []));
  }
  return out;
}

export function filterTrace(
  entities: readonly Entity[],
  interactions: readonly Interaction[],
  filter: GraphFilter,
  riskLevelByInteraction?: ReadonlyMap<string, string>,
): FilteredTrace {
  if (!isFilterActive(filter)) return { entities: [...entities], interactions: [...interactions] };

  const byId = new Map(interactions.map((ix) => [ix.id, ix]));
  const children = new Map<string, string[]>();
  for (const ix of interactions) {
    if (ix.parent_interaction_id == null) continue;
    const list = children.get(ix.parent_interaction_id) ?? [];
    list.push(ix.id);
    children.set(ix.parent_interaction_id, list);
  }

  // The entity's call paths (or every interaction) …
  let keep: Set<string> = new Set(interactions.map((ix) => ix.id));
  if (filter.entityId != null) {
    const id = filter.entityId;
    const seeds = interactions.filter((ix) => ix.caller_entity_id === id || ix.callee_entity_id === id).map((ix) => ix.id);
    keep = new Set([...withAncestors(seeds, byId), ...withDescendants(seeds, children)]);
  }
  // … then, of those, the risky ones and the calls that led to them. The level
  // is tested INSIDE the entity's paths, so a risky call on a sibling branch
  // cannot keep a shared ancestor. Every ancestor of an interaction on the
  // paths is itself on the paths, so this stays inside them.
  if (filter.minRiskLevel != null) {
    const min = filter.minRiskLevel;
    const seeds = [...keep].filter((id) => {
      const level = riskLevelByInteraction?.get(id);
      return level != null && isAtLeastRiskLevel(level, min);
    });
    keep = withAncestors(seeds, byId);
  }

  const keptInteractions = interactions.filter((ix) => keep.has(ix.id));
  const touched = new Set<string>();
  for (const ix of keptInteractions) {
    if (ix.caller_entity_id) touched.add(ix.caller_entity_id);
    if (ix.callee_entity_id) touched.add(ix.callee_entity_id);
  }
  // The focused entity stays on the picture even when nothing passes, so the
  // reader sees what the filter is about rather than an empty canvas.
  if (filter.entityId != null) touched.add(filter.entityId);
  return { entities: entities.filter((e) => touched.has(e.id)), interactions: keptInteractions };
}
