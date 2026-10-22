import { useState } from 'react';
import { Button, Checkbox, Label } from '@patternfly/react-core';
import { Link } from 'react-router-dom';
import type { LabelOrigin, LegLabel, LegLabels, LineageHop, LineageStatus } from '../types';
import { SensitivityBadge } from './SensitivityBadge';
import { LabelLineage } from './LabelLineage';
import { NO_HOP_LINKS, hopText, otherTraceHref, type HopLinks } from '../lib/hopLinks';
import { trackableValues, trackedValueOf } from '../lib/labelTrack';
import { useLabelTrack } from './flow/labelTrack';

/**
 * The per-leg label lineage view (the side panel's **Data lineage** tab): one
 * block per value label lineage carried into the payload, with its origin (where
 * the classifier found it) and the previous hop (the hop it came from last,
 * distance 1 — several when the value arrived by several routes),
 * each a link to that interaction. "Show chains" opens the full routes for the
 * value (`LabelLineage`). A stacked list, not a table: it has to read in the
 * ~500px detail panel, where five columns fold into slivers. Values the
 * classifier found here with nothing carried in are not listed: they have no
 * lineage, and the Classification tab lists them.
 *
 * Facts about the payload only; nothing here says a decision used a label.
 */
export function LabelLineageTable({
  leg,
  lineageStatus,
  traceId,
  interactionId,
  links = NO_HOP_LINKS,
}: {
  /** The labels read's row for this leg; `null` when the read has none. */
  leg: LegLabels | null;
  lineageStatus: LineageStatus;
  traceId: string;
  /** The leg's interaction: the identity of its values under tracking. Without it the tick boxes are not offered. */
  interactionId?: string;
  links?: HopLinks;
}) {
  const rows = (leg?.labels ?? []).filter((l) => l.carried_in.length > 0);
  const foundOnly = (leg?.labels ?? []).length - rows.length;
  const track = useLabelTrack();
  const trackable = track.available && interactionId != null && leg != null ? trackableValues(interactionId, [leg]) : [];
  const trackedHere = trackable.filter((v) => track.tracked.has(v.key)).length;
  return (
    <div data-testid="label-lineage-table">
      {/* Tracking on the graphs (lib/labelTrack): the legs the ticked values sat
          on are lit on every view, the rest dimmed. Offered only where a page
          provides the tracking (the trace page, the risk trace page). */}
      {trackable.length > 0 && (
        <div className="dg-lineage-track-bar">
          <span style={{ color: 'var(--dg-color-muted)' }}>Track on the graph:</span>{' '}
          <Button variant="link" isInline onClick={() => track.replace([...track.tracked.values(), ...trackable])}>
            all {trackable.length} on this leg
          </Button>
          {trackedHere > 0 && (
            <>
              {' · '}
              <Button
                variant="link"
                isInline
                onClick={() => track.replace([...track.tracked.values()].filter((v) => !trackable.some((t) => t.key === v.key)))}
              >
                none on this leg
              </Button>
            </>
          )}
        </div>
      )}
      {lineageStatus === null && (
        <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.8rem', marginBottom: '0.375rem' }}>
          Data lineage: not derived for this trace
        </div>
      )}
      {rows.length === 0 ? (
        <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.85rem' }}>
          No value on this payload was carried in from an earlier step.
        </div>
      ) : (
        <ul className="dg-lineage-values" aria-label="Data lineage">
          {rows.map((l, i) => (
            <Row
              key={`${l.entity_type}-${l.start}-${l.end}-${i}`}
              label={l}
              traceId={traceId}
              links={links}
              tracking={
                track.available && interactionId != null && leg != null
                  ? { value: trackedValueOf(interactionId, leg, l), track }
                  : null
              }
            />
          ))}
        </ul>
      )}
      {foundOnly > 0 && (
        <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.8rem', marginTop: '0.375rem' }}>
          {foundOnly} more {foundOnly === 1 ? 'value' : 'values'} the classifier found here only (Classification tab).
        </div>
      )}
    </div>
  );
}

/** The origin hop of an origin's chains, or a hop built from the origin's own fields when the read carried no chains. */
function bornHop(o: LabelOrigin): LineageHop {
  const first = o.chains?.[0]?.[0];
  if (first && first.role === 'origin') return first;
  return {
    interaction_id: o.origin_interaction_id, leg_type: o.origin_leg_type, seq: o.origin_seq,
    trace_id: o.origin_trace_id ?? '', caller: o.caller, callee: o.callee,
    text: o.unit_text, start: null, end: null, role: 'origin', excerpt: null,
  };
}

/** The hops at distance 1 from this leg, each once, over every chain of every origin. */
function previousHops(label: LegLabel): LineageHop[] {
  const seen = new Map<string, LineageHop>();
  for (const o of label.carried_in) {
    for (const chain of o.chains ?? []) {
      const prev = chain.length >= 2 ? chain[chain.length - 2] : undefined;
      if (prev) seen.set(`${prev.trace_id}/${prev.interaction_id}/${prev.leg_type}`, prev);
    }
  }
  return [...seen.values()].sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
}

function Row({
  label,
  traceId,
  links,
  tracking,
}: {
  label: LegLabel;
  traceId: string;
  links: HopLinks;
  /** The value's tracked shape and the page's tracking, when offered; `value` is null for a value with no chains. */
  tracking: { value: ReturnType<typeof trackedValueOf>; track: ReturnType<typeof useLabelTrack> } | null;
}) {
  const [open, setOpen] = useState(false);
  const chains = label.carried_in.reduce((n, o) => n + (o.chains?.length ?? 0), 0);
  const prev = previousHops(label);
  const tracked = tracking?.value != null && tracking.track.tracked.has(tracking.value.key);
  return (
    <li className="dg-lineage-value" data-testid="lineage-value" data-tracked={tracked ? 'true' : undefined}>
      <div className="dg-lineage-value__head">
        {tracking?.value != null && (
          <Checkbox
            id={`track-${tracking.value.key}`}
            className="dg-lineage-value__track"
            aria-label={`Track ${label.text} (${label.entity_type}) on the graph`}
            isChecked={tracked}
            onChange={(_e, checked) => tracking.track.set(tracking.value!.key, checked ? tracking.value : null)}
          />
        )}
        <span className="dg-mono">{label.text}</span>{' '}
        <Label isCompact color="orange">{label.entity_type}</Label>{' '}
        {label.sensitivity_level && <SensitivityBadge level={label.sensitivity_level} />}{' '}
        <span>{label.regulatory_tags.join(', ')}</span>
      </div>
      <div className="dg-lineage-value__line">
        <span className="dg-lineage-value__key">origin</span>
        <span>
          {label.carried_in.map((o, j) => {
            const h = bornHop(o);
            return (
              <div key={`${o.origin_interaction_id}-${o.origin_leg_type}-${j}`}>
                <HopLink hop={h} traceId={traceId} links={links} />
                {o.unit_text !== o.unit_value && (
                  <span style={{ color: 'var(--dg-color-muted)' }}> (marked &ldquo;{o.unit_text}&rdquo; there)</span>
                )}
              </div>
            );
          })}
        </span>
      </div>
      <div className="dg-lineage-value__line">
        <span className="dg-lineage-value__key">previous</span>
        <span>
          {prev.length === 0 ? (
            <span style={{ color: 'var(--dg-color-muted)' }}>—</span>
          ) : (
            prev.map((h) => (
              <div key={`${h.trace_id}/${h.interaction_id}/${h.leg_type}`}>
                <HopLink hop={h} traceId={traceId} links={links} />
              </div>
            ))
          )}
        </span>
      </div>
      {chains > 0 && (
        <div className="dg-lineage-value__line">
          <span className="dg-lineage-value__key" />
          <Button variant="link" isInline onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? 'Hide chains' : chains === 1 ? 'Show chain' : `Show ${chains} chains`}
          </Button>
        </div>
      )}
      {open && <LabelLineage label={label} traceId={traceId} links={links} />}
    </li>
  );
}

/** A hop as a link (the page's convention in this trace, the flow view in another), marked when in another trace. */
function HopLink({ hop, traceId, links }: { hop: LineageHop; traceId: string; links: HopLinks }) {
  const foreign = hop.trace_id !== '' && hop.trace_id !== traceId;
  const to = foreign ? otherTraceHref(hop) : links.sameTrace(hop);
  const text = hopText(hop);
  return (
    <>
      {to != null ? <Link to={to}>{text}</Link> : <span>{text}</span>}
      {foreign && (
        <>
          {' '}
          <Label isCompact color="gold" data-testid="trace-boundary">in trace {hop.trace_id.slice(0, 8)}…</Label>
        </>
      )}
    </>
  );
}
