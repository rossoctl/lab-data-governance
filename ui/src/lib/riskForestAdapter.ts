/**
 * Reads `GET /risk/traces/{trace_id}`'s forest (`ForestInteraction[]`, the
 * risk API's own shape — `risk-api/types.ts`) into what the trace page's risk
 * layer paints on the flow views: side-channel maps keyed by interaction id
 * (risk level, classification) that the renderers consume ALONGSIDE the
 * `/api/` flow types, and the trace's policy events. The flow types themselves
 * stay the `/api/` read's: the forest carries no leg `seq` and no entity
 * metadata (`caller_entity_id`/`callee_entity_id` are opaque ids), so the
 * graph, the diagram and the tables are drawn from that read, not from here.
 */
import type { ForestInteraction } from '../risk-api/types';
import { regulatoryTagsOf, classificationLevelsOf, carriedOfSummary } from './classificationSummary';
import type { InteractionClassification } from './classificationSummary';

/**
 * Interaction id -> risk level, omitting interactions whose `risk` is
 * `null` (not yet computed — eventual consistency per `ForestInteractionView`'s
 * docstring, not an error). The renderer seams (`ExecutionFlowGraph`,
 * `InteractionDiagram`) treat an omitted entry as "no risk colouring" rather
 * than as a green "safe" verdict.
 */
export function riskLevelByInteraction(forest: readonly ForestInteraction[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const ix of forest) {
    if (ix.risk != null) map.set(ix.interaction_id, ix.risk.risk_level);
  }
  return map;
}

/**
 * Interaction id -> regulatory tags + sensitivity level(s) (issue #170
 * follow-up: regulatory tags as edge labels), read
 * via `lib/classificationSummary.ts`'s defensive narrowing of
 * `risk.classification_summary`. Mirrors `riskLevelByInteraction` immediately
 * above — a side-channel `Map` the graph consumes ALONGSIDE the flow types,
 * not a field threaded through them (see `ExecutionFlowGraph.tsx`'s
 * `classificationByInteraction` prop doc for why: the `/api/` read the flow
 * types come from has no source for classification at all, so a
 * `flow.Interaction` field would be permanently `undefined` with the layer off).
 *
 * ONE RECORD PER ENTRY, not two separate maps: `InteractionClassification`
 * pairs `tags` with `levels` because the graph's visible edge tag reads only
 * `tags`, but its hover text reads both — a consumer of the pair must never
 * see one populated and the other missing because two independent lookups
 * happened to disagree.
 *
 * PRESENT WHEN EITHER IS NON-EMPTY, omitted only when BOTH are empty. A
 * genuine `{tags: [], levels: ['PUBLIC']}` verdict — classified, nothing
 * regulatory flagged — is present: it draws no visible tag (`edgeTagLabel`'s
 * truthiness gate on an empty list), but its hover text must still be able to
 * say "PUBLIC" rather than silently losing a real verdict. Omitted for the
 * same cases `riskLevelByInteraction` above has no entry for, plus a
 * classified-but-both-empty leg (which the wire shape doesn't currently
 * produce, but this function doesn't assume it never will).
 *
 * CARRIED-IN LABELS (label lineage, `carriedOfSummary`) add a `carried` key,
 * and only when some leg has them, so an entry without them is the same
 * `{tags, levels}` as before. They also make an entry for an interaction
 * whose legs are pending or payload-less: its visible tag stays `''`, its
 * hover text says what was carried in.
 */
export function classificationByInteraction(
  forest: readonly ForestInteraction[],
): Map<string, InteractionClassification> {
  const map = new Map<string, InteractionClassification>();
  for (const ix of forest) {
    if (ix.risk == null) continue;
    const tags = regulatoryTagsOf(ix.risk.classification_summary);
    const levels = classificationLevelsOf(ix.risk.classification_summary);
    const carried = carriedOfSummary(ix.risk.classification_summary);
    if (carried != null) map.set(ix.interaction_id, { tags, levels, carried });
    else if (tags.length > 0 || levels.length > 0) map.set(ix.interaction_id, { tags, levels });
  }
  return map;
}

/**
 * The forest interactions that are policy-decision violations: `risk` is
 * present AND its `triggered_rule_ids` is non-empty. Preserves forest order
 * (the server's own temporal sort), which is what makes violation 1 the
 * first one chronologically rather than an arbitrary pick.
 */
export function violationsOf(forest: readonly ForestInteraction[]): ForestInteraction[] {
  return forest.filter((ix) => (ix.risk?.triggered_rule_ids?.length ?? 0) > 0);
}
