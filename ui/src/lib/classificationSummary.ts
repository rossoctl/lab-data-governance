/**
 * Reads `InteractionRisk.classification_summary` — the wire shape from
 * `GET /risk/traces/{trace_id}` (`data_governance/risk/engine/utils.py`'s
 * `_verdict_summary`) — and the presentation logic derived from it that is
 * shared by more than one consumer.
 *
 * ONE NARROWING, TWO CONSUMERS. This module used to be three module-private
 * functions inside `risk-components/PolicyDecisionPanel.tsx`. It was
 * extracted here (issue #170 follow-up: regulatory tags as edge labels on
 * the risk trace graph) once a second consumer — the execution-flow graph's
 * edge tags, via `riskForestAdapter.classificationByInteraction` — needed the
 * identical defensive narrowing of the same wire shape. Duplicating it would
 * have meant two places that could silently disagree about what counts as "a
 * classified leg", so it moved to the one layer with no React dependency that
 * both a `risk-components/` panel and a `components/` graph can import
 * without either depending on the other.
 */

/**
 * One classified LEG's shape inside `InteractionRisk.classification_summary`
 * when a payload was actually classified — `data_governance/risk/engine/
 * utils.py`'s `_verdict_summary`. The other two possible per-leg shapes are
 * `{classification_pending: true}` (still running) and `{payload: null}`
 * (nothing to classify); neither carries tags or a sensitivity level, so
 * both fall through this narrowing to the "none" default below.
 */
export interface ClassifiedLegSummary {
  sensitivity_level?: unknown;
  regulatory_tags?: unknown;
}

/**
 * `classification_summary` is typed `Record<string, unknown> | null` on the
 * wire (`risk-api/types.ts`'s `InteractionRisk`) because the API guarantees
 * only "a JSON object or null" — the per-leg value can be a classified
 * verdict, `{classification_pending: true}`, or `{payload: null}` (see
 * `utils.py`'s `classification_summary()`), and a leg absent from the object
 * entirely is a fourth, silent case. Rather than typing an optimistic
 * interface that a pending/no-payload/absent leg would violate at runtime,
 * this narrows defensively and falls back to "none"/`[]` for anything that
 * doesn't look like a classified verdict — never throws.
 *
 * This is the ONE place that narrowing happens; both `regulatoryTagsOf`/
 * `classificationLevelsOf` below and `riskForestAdapter.
 * classificationByInteraction` build on it rather than re-deriving it.
 */
export function classifiedLegs(summary: Record<string, unknown> | null): ClassifiedLegSummary[] {
  if (summary == null || typeof summary !== 'object') return [];
  return (['request', 'response'] as const)
    .map((leg) => summary[leg])
    .filter((v): v is ClassifiedLegSummary => typeof v === 'object' && v !== null);
}

/** Every `regulatory_tags` string across whichever legs were actually classified, deduplicated, first-seen order. */
export function regulatoryTagsOf(summary: Record<string, unknown> | null): string[] {
  const tags = new Set<string>();
  for (const leg of classifiedLegs(summary)) {
    if (Array.isArray(leg.regulatory_tags)) {
      for (const tag of leg.regulatory_tags) if (typeof tag === 'string') tags.add(tag);
    }
  }
  return [...tags];
}

/** Every `sensitivity_level` string across whichever legs were actually classified, deduplicated, first-seen order. */
export function classificationLevelsOf(summary: Record<string, unknown> | null): string[] {
  const levels = new Set<string>();
  for (const leg of classifiedLegs(summary)) {
    if (typeof leg.sensitivity_level === 'string') levels.add(leg.sensitivity_level);
  }
  return [...levels];
}

/**
 * An interaction's regulatory tags and sensitivity level(s), unioned and
 * deduplicated across whichever of its legs were actually classified. The
 * pair travels together (see `riskForestAdapter.classificationByInteraction`'s
 * docstring for why this is one record and not two separate maps) because a
 * consumer that shows the tags on their own (the graph's visible edge tag)
 * and a consumer that shows both (its hover text) are reading the same
 * underlying verdict, not two independent facts.
 */
export interface InteractionClassification {
  tags: readonly string[];
  levels: readonly string[];
}

/**
 * The edge tag renders through PatternFly's `DefaultConnectorTag`, which
 * draws exactly ONE non-wrapping `<text>` on the chord midpoint of what is
 * often a short arrow (see `ExecutionFlowGraph.tsx`'s `CurvedEdge` and the
 * comment at its `DefaultConnectorTag` composition on why that position
 * isn't overridable). Regulatory tags are short uppercase tokens (`PII`,
 * `GDPR`, `HIPAA`, `PCI`), not prose — `lib/graph.ts`'s `label` docstring
 * records that PROSE on an edge label was tried and rejected as illegible on
 * a short arrow, and this is deliberately not that: a bounded set of short
 * tokens, capped here, stays legible where a sentence would not.
 *
 * Two tags is the cap. At 2 tags + a `+N` overflow indicator the string is
 * ~14 characters; a third tag would push it past ~19-24, wider than the gap
 * between two adjacent graph nodes. `edgeTagLabel([])` returns `''` rather
 * than the word "none" — see `riskForestAdapter.classificationByInteraction`'s
 * docstring for why a graph mark must never assert a verdict the data
 * doesn't support (this deliberately diverges from `PolicyDecisionPanel`'s
 * `joinOrNone` → `'none'`, which is correct THERE — see the back-reference
 * comment beside that component's regulatory-tags row).
 *
 * Order is preserved from `tags` (wire order: first-seen across request then
 * response — see `classifiedLegs`), NOT sorted. The classifier's own order is
 * more likely to be meaningful than alphabetical (which would put `CCPA`
 * ahead of `PII`), but this does mean the two tags shown are the FIRST two,
 * not necessarily the most salient two. The full, untruncated list is always
 * available in the edge's hover text, which is what makes the truncation
 * lossless rather than a loss of information.
 */
export const EDGE_TAG_MAX_TAGS = 2;

export function edgeTagLabel(tags: readonly string[]): string {
  if (tags.length === 0) return '';
  const shown = tags.slice(0, EDGE_TAG_MAX_TAGS).join(', ');
  const overflow = tags.length - EDGE_TAG_MAX_TAGS;
  return overflow > 0 ? `${shown} +${overflow}` : shown;
}
