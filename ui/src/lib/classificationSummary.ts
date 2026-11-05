/**
 * Reads `InteractionRisk.classification_summary` — the wire shape from
 * `GET /risk/traces/{trace_id}` (`data_governance/risk/engine/utils.py`'s
 * `_verdict_summary`) — and the presentation logic derived from it that is
 * shared by more than one consumer.
 *
 * ONE NARROWING, TWO CONSUMERS. This module used to be three module-private
 * functions inside `risk-components/PolicyDecisionPanel.tsx`. It was
 * extracted here (issue #170 follow-up: regulatory tags as edge labels)
 * once a second consumer — the execution-flow graph's
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
 * both pass this narrowing contributing nothing to the unions below (the
 * risk layer's interaction panel tells them apart by key).
 */
export interface ClassifiedLegSummary {
  sensitivity_level?: unknown;
  regulatory_tags?: unknown;
  /** Label lineage's block (see `carriedOf`); on any of the three shapes. */
  inherited?: unknown;
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
  return classifiedLegEntries(summary).map(([, leg]) => leg);
}

export type LegName = 'request' | 'response';

/**
 * `classifiedLegs` with each leg's name kept (issue #295: the risk layer's
 * interaction panel shows the verdict PER LEG, where the unioned
 * `regulatoryTagsOf`/`classificationLevelsOf` below would lose which leg
 * carried what). Same narrowing, same order: request, then response.
 */
export function classifiedLegEntries(
  summary: Record<string, unknown> | null,
): [LegName, ClassifiedLegSummary][] {
  if (summary == null || typeof summary !== 'object') return [];
  return (['request', 'response'] as const)
    .map((leg): [LegName, unknown] => [leg, summary[leg]])
    .filter((e): e is [LegName, ClassifiedLegSummary] => typeof e[1] === 'object' && e[1] !== null);
}

/** One classified leg's `regulatory_tags`, strings only, in wire order. */
export function legTags(leg: ClassifiedLegSummary): string[] {
  return Array.isArray(leg.regulatory_tags) ? leg.regulatory_tags.filter((t): t is string => typeof t === 'string') : [];
}

/** One classified leg's `sensitivity_level`, or `null` when it isn't a string. */
export function legLevel(leg: ClassifiedLegSummary): string | null {
  return typeof leg.sensitivity_level === 'string' ? leg.sensitivity_level : null;
}

/** Every `regulatory_tags` string across whichever legs were actually classified, deduplicated, first-seen order. */
export function regulatoryTagsOf(summary: Record<string, unknown> | null): string[] {
  const tags = new Set<string>();
  for (const leg of classifiedLegs(summary)) for (const tag of legTags(leg)) tags.add(tag);
  return [...tags];
}

/** Every `sensitivity_level` string across whichever legs were actually classified, deduplicated, first-seen order. */
export function classificationLevelsOf(summary: Record<string, unknown> | null): string[] {
  const levels = new Set<string>();
  for (const leg of classifiedLegs(summary)) {
    const level = legLevel(leg);
    if (level != null) levels.add(level);
  }
  return [...levels];
}

/** Sensitivity levels, lowest first — the classifier's ladder (`label_lineage/summary.py`'s `LEVELS`). */
export const SENSITIVITY_LEVELS: readonly string[] = ['PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED'];

/**
 * The labels a leg's payload CARRIED IN — label lineage found the same text
 * carrying them earlier in the trace — as opposed to what the classifier
 * found reading this payload (`legTags`/`legLevel`, "Found here"). `level`
 * is the highest of them.
 */
export interface CarriedLabels {
  tags: readonly string[];
  level: string | null;
}

/**
 * The higher of two sensitivity levels on `SENSITIVITY_LEVELS`. A level off
 * the ladder ranks below `PUBLIC`, so it never hides a known one but still
 * shows when it is all there is.
 */
function higherLevel(a: string | null, b: string | null): string | null {
  if (a == null) return b;
  if (b == null) return a;
  return SENSITIVITY_LEVELS.indexOf(b) > SENSITIVITY_LEVELS.indexOf(a) ? b : a;
}

/**
 * One leg's `inherited` block — `{regulatory_tags, max_level, kinds,
 * recombined, count}`, written by `risk/engine/utils.py`'s
 * `classification_summary()` only when label lineage's edges into this leg
 * were forwarded into the decision's OPA input. It rides on all three leg
 * shapes, so a pending or payload-less leg can carry labels too. `count`
 * counts edges, not labels, and is deliberately not read.
 *
 * `null` when the block is absent or malformed, or names neither a tag nor
 * a level — never a claim the record does not make.
 */
export function carriedOf(leg: ClassifiedLegSummary): CarriedLabels | null {
  const block = leg.inherited;
  if (block == null || typeof block !== 'object') return null;
  const { regulatory_tags: tags, max_level: level } = block as Record<string, unknown>;
  const carried: CarriedLabels = {
    tags: Array.isArray(tags) ? [...new Set(tags.filter((t): t is string => typeof t === 'string'))] : [],
    level: typeof level === 'string' ? level : null,
  };
  return carried.tags.length > 0 || carried.level != null ? carried : null;
}

/** `carriedOf` across both legs: tags unioned first-seen, the highest level. `null` when no leg carried any. */
export function carriedOfSummary(summary: Record<string, unknown> | null): CarriedLabels | null {
  const tags = new Set<string>();
  let level: string | null = null;
  let any = false;
  for (const leg of classifiedLegs(summary)) {
    const carried = carriedOf(leg);
    if (carried == null) continue;
    any = true;
    for (const tag of carried.tags) tags.add(tag);
    level = higherLevel(level, carried.level);
  }
  return any ? { tags: [...tags], level } : null;
}

/**
 * An interaction's regulatory tags and sensitivity level(s), unioned and
 * deduplicated across whichever of its legs were actually classified. The
 * pair travels together (see `riskForestAdapter.classificationByInteraction`'s
 * docstring for why this is one record and not two separate maps) because a
 * consumer that shows the tags on their own (the graph's visible edge tag)
 * and a consumer that shows both (its hover text) are reading the same
 * underlying verdict, not two independent facts.
 *
 * `carried` (label lineage, `carriedOfSummary`) is present only when some leg
 * carried labels in. It feeds the hover text only, never the visible tag.
 */
export interface InteractionClassification {
  tags: readonly string[];
  levels: readonly string[];
  carried?: CarriedLabels;
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
