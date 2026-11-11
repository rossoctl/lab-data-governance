import type { CarrierLeg, Interaction, LegLabel, LegLabels, LineageHop } from '../types';
import type { RuleListItem } from '../risk-api/types';
import type { GraphHighlight } from '../components/ExecutionFlowGraph';

/**
 * Tracking labelled values on the graphs (the label lineage overlay).
 *
 * A risk decision is on a payload; label lineage is per value; an edge on the
 * execution-flow graph and the sequence diagram is a leg, one payload. So the
 * chain of one value cannot be "the path of an edge": the picture is the UNION of
 * the carriers (every leg on any route) of every value the reader chose to track, drawn as a highlight over
 * the graph that is already there — the legs those values sat on lit, every other
 * leg dimmed. Pure functions, so the derivation is testable without an SVG; the
 * components only paint the sets.
 *
 * What it says: where these values were. What it never says: that a value caused a
 * decision (the engine records no such thing — see `PolicyDecisionPanel`).
 */

/** One value under tracking: which leg it is on, what it is, its chains and the legs it sat on. */
export interface TrackedValue {
  key: string;
  interactionId: string;
  legType: 'request' | 'response';
  text: string;
  entityType: string;
  chains: LineageHop[][];
  /** Every leg on any route, from the read's `carriers`; the chains' hops from an older server. */
  carriers: CarrierLeg[];
}

/** The identity of a labelled value on a leg: the leg, the type and the span. */
export function trackKey(interactionId: string, legType: string, label: Pick<LegLabel, 'entity_type' | 'start' | 'end' | 'text'>): string {
  return `${interactionId}/${legType}/${label.entity_type}/${label.start ?? 'x'}-${label.end ?? 'x'}/${label.text}`;
}

/** The tracked shape of one carried-in value of a leg; `null` for a value with nothing carried in (nothing to track). */
export function trackedValueOf(interactionId: string, leg: LegLabels, label: LegLabel): TrackedValue | null {
  const chains = label.carried_in.flatMap((o) => o.chains ?? []);
  if (chains.length === 0) return null;
  return {
    key: trackKey(interactionId, leg.leg_type, label),
    interactionId,
    legType: leg.leg_type,
    text: label.text,
    entityType: label.entity_type,
    chains,
    carriers: label.carried_in.flatMap((o) => o.carriers ?? (o.chains ?? []).flat()),
  };
}

/** Every trackable value of an interaction's legs. */
export function trackableValues(interactionId: string, legs: Iterable<LegLabels>): TrackedValue[] {
  const out: TrackedValue[] = [];
  for (const leg of legs) {
    for (const label of leg.labels) {
      const v = trackedValueOf(interactionId, leg, label);
      if (v) out.push(v);
    }
  }
  return out;
}

/**
 * Whether a labelled value carries a label the fired rules name: a regulatory tag a
 * rule's `data_items` asks for, or the classification level one asks for. The
 * decision-derived default selection with the risk layer on: the reader
 * starts from the values whose labels the rules name and unticks or adds. A rule
 * with no `data_items` criteria names nothing (it would select everything).
 */
export function namedByRules(label: Pick<LegLabel, 'regulatory_tags' | 'sensitivity_level'>, rules: readonly RuleListItem[]): boolean {
  for (const rule of rules) {
    for (const item of rule.data_items) {
      if (item.regulatory_tags?.some((t) => label.regulatory_tags.includes(t))) return true;
      if (item.classification_level && item.classification_level === label.sensitivity_level) return true;
    }
  }
  return false;
}

/**
 * The right-click's default on a leg: which of its carried-in values start out
 * tracked. With fired rules, the values whose labels those rules name (the
 * decision-derived default); with none, every carried-in value of
 * the leg.
 */
export function defaultTrackValues(
  interactionId: string,
  legs: Iterable<LegLabels>,
  legType: 'request' | 'response',
  rules: readonly RuleListItem[],
): TrackedValue[] {
  const out: TrackedValue[] = [];
  for (const leg of legs) {
    if (leg.leg_type !== legType) continue;
    for (const label of leg.labels) {
      if (rules.length > 0 && !namedByRules(label, rules)) continue;
      const v = trackedValueOf(interactionId, leg, label);
      if (v) out.push(v);
    }
  }
  return out;
}

/** The sets the graphs paint for a set of tracked values, on the page's trace. */
export interface LabelTrackHighlight {
  /** `<interaction>:<leg_type>` of every leg a tracked value sat on, in this trace. */
  edgeIds: string[];
  /** The interactions those legs belong to. */
  interactionIds: string[];
  /** The entities at the ends of those interactions (node ids). */
  nodeIds: string[];
  /** Legs in other traces, each once, which this graph cannot show. */
  foreignHops: number;
}

export function deriveLabelTrackHighlight(
  tracked: readonly TrackedValue[],
  interactions: readonly Interaction[],
  traceId: string,
): LabelTrackHighlight {
  const edgeIds = new Set<string>();
  const interactionIds = new Set<string>();
  const foreign = new Set<string>();
  for (const v of tracked) {
    for (const leg of v.carriers) {
      if (leg.trace_id !== traceId) {
        foreign.add(`${leg.trace_id}/${leg.interaction_id}:${leg.leg_type}`);
        continue;
      }
      edgeIds.add(`${leg.interaction_id}:${leg.leg_type}`);
      interactionIds.add(leg.interaction_id);
    }
  }
  const nodeIds = new Set<string>();
  for (const ix of interactions) {
    if (!interactionIds.has(ix.id)) continue;
    if (ix.caller_entity_id) nodeIds.add(ix.caller_entity_id);
    if (ix.callee_entity_id) nodeIds.add(ix.callee_entity_id);
  }
  return {
    edgeIds: [...edgeIds].sort(),
    interactionIds: [...interactionIds].sort(),
    nodeIds: [...nodeIds].sort(),
    foreignHops: foreign.size,
  };
}

/** The execution-flow graph's highlight for the sets: a question with no selected node, so the rest dims. */
export function toGraphHighlight(h: LabelTrackHighlight): GraphHighlight {
  return { selectedNodeId: null, nodeIds: h.nodeIds, edgeIds: h.edgeIds, isQuestion: true };
}
