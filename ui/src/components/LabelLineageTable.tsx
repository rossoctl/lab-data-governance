import { useState } from 'react';
import { Button, Label } from '@patternfly/react-core';
import { Link } from 'react-router-dom';
import type { LabelOrigin, LegLabel, LegLabels, LineageHop, LineageStatus } from '../types';
import { SensitivityBadge } from './SensitivityBadge';
import { LabelLineage } from './LabelLineage';
import { NO_HOP_LINKS, hopText, otherTraceHref, type HopLinks } from '../lib/hopLinks';

/**
 * The per-leg **Label lineage** view: one block per value label lineage carried
 * into the payload, with where it was born and the hop it came from last (the
 * previous hop, distance 1 — several when the value arrived by several routes),
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
  links = NO_HOP_LINKS,
}: {
  /** The labels read's row for this leg; `null` when the read has none. */
  leg: LegLabels | null;
  lineageStatus: LineageStatus;
  traceId: string;
  links?: HopLinks;
}) {
  const rows = (leg?.labels ?? []).filter((l) => l.carried_in.length > 0);
  const foundOnly = (leg?.labels ?? []).length - rows.length;
  return (
    <div data-testid="label-lineage-table">
      {lineageStatus === null && (
        <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.8rem', marginBottom: '0.375rem' }}>
          Label lineage: not derived for this trace
        </div>
      )}
      {rows.length === 0 ? (
        <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.85rem' }}>
          No value on this payload was carried in from an earlier step.
        </div>
      ) : (
        <ul className="dg-lineage-values" aria-label="Label lineage">
          {rows.map((l, i) => (
            <Row key={`${l.entity_type}-${l.start}-${l.end}-${i}`} label={l} traceId={traceId} links={links} />
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

function Row({ label, traceId, links }: { label: LegLabel; traceId: string; links: HopLinks }) {
  const [open, setOpen] = useState(false);
  const chains = label.carried_in.reduce((n, o) => n + (o.chains?.length ?? 0), 0);
  const prev = previousHops(label);
  return (
    <li className="dg-lineage-value" data-testid="lineage-value">
      <div className="dg-lineage-value__head">
        <span className="dg-mono">{label.text}</span>{' '}
        <Label isCompact color="orange">{label.entity_type}</Label>{' '}
        {label.sensitivity_level && <SensitivityBadge level={label.sensitivity_level} />}{' '}
        <span>{label.regulatory_tags.join(', ')}</span>
      </div>
      <div className="dg-lineage-value__line">
        <span className="dg-lineage-value__key">born at</span>
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
        <span className="dg-lineage-value__key">from</span>
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
