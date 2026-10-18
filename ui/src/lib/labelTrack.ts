import type { Interaction, LegLabel, LegLabels, LineageHop } from '../types';
import type { RuleListItem } from '../risk-api/types';
import type { GraphHighlight } from '../components/ExecutionFlowGraph';

/**
 * Tracking labelled values on the graphs (the label lineage overlay).
 *
 * A risk decision is on a payload; label lineage is per value; an edge on the
 * execution-flow graph and the sequence diagram is a leg, one payload. So the
 * chain of one value cannot be "the path of an edge": the picture is the UNION of
 * the routes of every value the reader chose to track, drawn as a highlight over
 * the graph that is already there — the legs those values sat on lit, every other
 * leg dimmed. Pure functions, so the derivation is testable without an SVG; the
 * components only paint the sets.
 *
 * What it says: where these values were. What it never says: that a value caused a
 * decision (the engine records no such thing — see `PolicyDecisionPanel`).
 */

/** One value under tracking: which leg it is on, what it is, and its chains. */
export interface TrackedValue {
  key: string;
  interactionId: string;
  legType: 'request' | 'response';
  text: string;
  entityType: string;
  chains: LineageHop[][];
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
 * decision-derived default selection on the risk page: the reader
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

/** The sets the graphs paint for a set of tracked values, on the page's trace. */
export interface LabelTrackHighlight {
  /** `<interaction>:<leg_type>` of every leg a tracked value sat on, in this trace. */
  edgeIds: string[];
  /** The interactions those legs belong to. */
  interactionIds: string[];
  /** The entities at the ends of those interactions (node ids). */
  nodeIds: string[];
  /** Hops in other traces, which this graph cannot show. */
  foreignHops: number;
}

export function deriveLabelTrackHighlight(
  tracked: readonly TrackedValue[],
  interactions: readonly Interaction[],
  traceId: string,
): LabelTrackHighlight {
  const edgeIds = new Set<string>();
  const interactionIds = new Set<string>();
  let foreignHops = 0;
  for (const v of tracked) {
    for (const chain of v.chains) {
      for (const hop of chain) {
        if (hop.trace_id !== traceId) {
          foreignHops += 1;
          continue;
        }
        edgeIds.add(`${hop.interaction_id}:${hop.leg_type}`);
        interactionIds.add(hop.interaction_id);
      }
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
    foreignHops,
  };
}

/** The execution-flow graph's highlight for the sets: a question with no selected node, so the rest dims. */
export function toGraphHighlight(h: LabelTrackHighlight): GraphHighlight {
  return { selectedNodeId: null, nodeIds: h.nodeIds, edgeIds: h.edgeIds, isQuestion: true };
}
