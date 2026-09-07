/**
 * Adapts `GET /risk/traces/{trace_id}`'s forest (`ForestInteraction[]`, the
 * risk API's own shape — `risk-api/types.ts`) into the non-generic
 * `flow.Entity[]`/`flow.Interaction[]` that `lib/graph.ts`'s `deriveGraph`
 * and `lib/sequenceDiagram.ts`'s `deriveSequenceDiagram` already consume.
 * The reuse seam issue #170 is built around (user instruction: "reuse as
 * much as possible... extend as needed"): the two derivations and their
 * renderers (`ExecutionFlowGraph`, `InteractionDiagram`) are untouched by
 * the shape mismatch below; only this module absorbs it.
 *
 * TWO GAPS the forest payload has relative to `flow.Interaction`:
 *
 * 1. NO LEG `seq`. `flow.InteractionLeg.seq` is load-bearing for both
 *    derivations (message order, arrow labels, column assignment), but the
 *    risk API's `ForestLeg` carries none. `get_trace_risk_detail`
 *    (`retrieval/risk.py`) already returns `interactions` sorted by each
 *    interaction's request-leg `occurred_at` (no-request-leg last), so
 *    ARRAY POSITION *is* the temporal order the server committed to. This
 *    module synthesizes `seq` from that position rather than re-deriving an
 *    order of its own: interaction at index `i` (0-based) gets its request
 *    leg `seq = i + 1`. Derived, not re-sorted on `occurred_at` —
 *    re-sorting here could silently disagree with the server on ties or on
 *    null-timestamp placement, which the server has already resolved once.
 *
 *    STATED LIMITATION: this is coarser than the `/api/` tree's real
 *    trace-wide leg `seq` (`flow.ts`'s own domain) and cannot interleave a
 *    long call's response after a nested child's later request — it only
 *    orders at the interaction grain, not the leg grain, because that is
 *    the finest order the risk payload's own sort gives us. If the risk API
 *    ever returns a genuine leg `seq`, this is the one place to change.
 *
 *    REQUEST LEGS ONLY (issue #170 follow-up): the execution-flow graph on
 *    this page draws one arrow per call, not one per direction of the wire
 *    — a response arrow doubling every edge back added visual noise without
 *    adding information the graph needs to convey. `toFlowInteractions`
 *    therefore keeps only each interaction's request leg; `deriveGraph`
 *    already draws exactly one edge for a request-only interaction (the
 *    same code path it uses for a call whose response hasn't arrived yet),
 *    so no change to `graph.ts` was needed. A consequence, not a separate
 *    decision: `duration_seconds` is always `null` here (there is no
 *    response leg to measure a delta against) and `any_error` reflects only
 *    the request leg's own outcome — neither is rendered by this page.
 *
 * 2. NO ENTITY METADATA. `ForestInteraction.caller_entity_id`/
 *    `callee_entity_id` are opaque ids with no `kind`/`display_name` — that
 *    comes from the pre-existing, non-risk `GET /api/traces/{traceId}/entities`
 *    (`useEntities`, `ui/src/api/hooks.ts`). `toFlowEntities` joins the two:
 *    a real row wins; an id the forest references but `useEntities` didn't
 *    return (or a wholly absent/failed entities read) degrades to a
 *    synthesized `kind: 'unknown'` stand-in labelled with the bare id,
 *    rather than pushing the interaction into `deriveGraph`'s `dropped`
 *    list — a label going missing is a presentation problem, not a reason
 *    to hide a real interaction.
 */
import type { Entity, Interaction, InteractionLeg } from './flow';
import type { ForestInteraction } from '../risk-api/types';
import { regulatoryTagsOf, classificationLevelsOf } from './classificationSummary';
import type { InteractionClassification } from './classificationSummary';

/**
 * OR-aggregate a forest interaction's leg errors, tri-state preserved: an
 * explicit `true` on either leg wins, `false` only when every leg present
 * explicitly reports `false`, and `null` when no leg reports at all (not
 * yet aggregated) — the same never-render-unknown-as-a-verdict rule
 * `graph.GraphEdgeSpec.isError`'s docstring follows for a single leg,
 * applied here across an interaction's legs.
 */
function anyError(legs: readonly { error: boolean | null }[]): boolean | null {
  if (legs.some((l) => l.error === true)) return true;
  if (legs.length > 0 && legs.every((l) => l.error === false)) return false;
  return null;
}

/**
 * Widen the risk forest's interactions to `flow.Interaction[]`, synthesizing
 * `seq` from array position (see module header) and keeping only each
 * interaction's REQUEST leg — the execution-flow graph this feeds draws one
 * arrow per call, not one per direction of the wire. Field-for-field
 * pass-through otherwise, including a null `caller_entity_id`/
 * `callee_entity_id` — both derivations already route that into `dropped`.
 */
export function toFlowInteractions(forest: readonly ForestInteraction[]): Interaction[] {
  return forest.map((ix, i) => {
    const requestLeg = ix.legs.find((l) => l.leg_type === 'request');

    const legs: InteractionLeg[] = requestLeg
      ? [
          {
            leg_type: requestLeg.leg_type,
            occurred_at: requestLeg.occurred_at,
            payload_hash: requestLeg.payload_hash,
            error: requestLeg.error,
            seq: i + 1,
          },
        ]
      : [];

    return {
      id: ix.interaction_id,
      caller_entity_id: ix.caller_entity_id,
      callee_entity_id: ix.callee_entity_id,
      summary: ix.summary,
      parent_interaction_id: ix.parent_interaction_id,
      legs,
      duration_seconds: null,
      any_error: anyError(legs),
      span_count: ix.span_count,
      anchor_count: ix.anchor_count,
    };
  });
}

/** Every entity id the forest references as a caller or callee, deduplicated, null ids excluded. */
export function unresolvedEntityIds(
  forest: readonly ForestInteraction[],
  entities: readonly Entity[] | undefined,
): string[] {
  const known = new Set((entities ?? []).map((e) => e.id));
  const missing = new Set<string>();
  for (const ix of forest) {
    if (ix.caller_entity_id != null && !known.has(ix.caller_entity_id)) missing.add(ix.caller_entity_id);
    if (ix.callee_entity_id != null && !known.has(ix.callee_entity_id)) missing.add(ix.callee_entity_id);
  }
  return [...missing];
}

/**
 * The full entity set the flow derivations need: `entities`' real rows, plus
 * a synthesized `kind: 'unknown'` stand-in (labelled with the bare id) for
 * every id the forest references that `entities` didn't resolve — including
 * every referenced id when `entities` is `undefined` entirely (the entities
 * read failed or hasn't returned yet). Degrades labels, not correctness:
 * the interaction is still drawn, just with a placeholder participant.
 */
export function toFlowEntities(
  forest: readonly ForestInteraction[],
  entities: readonly Entity[] | undefined,
): Entity[] {
  const result: Entity[] = [...(entities ?? [])];
  for (const id of unresolvedEntityIds(forest, entities)) {
    result.push({
      id,
      kind: 'unknown',
      natural_key: id,
      display_name: id,
      detected_from: 'unknown',
    });
  }
  return result;
}

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
 * follow-up: regulatory tags as edge labels on the risk trace graph), read
 * via `lib/classificationSummary.ts`'s defensive narrowing of
 * `risk.classification_summary`. Mirrors `riskLevelByInteraction` immediately
 * above — a side-channel `Map` the graph consumes ALONGSIDE the flow types,
 * not a field threaded through them (see `ExecutionFlowGraph.tsx`'s
 * `classificationByInteraction` prop doc for why: `toFlowInteractions` above
 * deliberately drops `ix.risk`, and the non-risk `/api/` path this same
 * adapter's sibling derivations feed has no source for classification at
 * all, so a `flow.Interaction` field would be permanently-`undefined` on two
 * of three tabs).
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
 */
export function classificationByInteraction(
  forest: readonly ForestInteraction[],
): Map<string, InteractionClassification> {
  const map = new Map<string, InteractionClassification>();
  for (const ix of forest) {
    if (ix.risk == null) continue;
    const tags = regulatoryTagsOf(ix.risk.classification_summary);
    const levels = classificationLevelsOf(ix.risk.classification_summary);
    if (tags.length > 0 || levels.length > 0) map.set(ix.interaction_id, { tags, levels });
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
