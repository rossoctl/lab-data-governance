import { useState } from 'react';
import { Button, Label } from '@patternfly/react-core';
import { Link } from 'react-router-dom';
import type { LabelOrigin, LegLabel, LineageHop } from '../types';
import { useHopHover } from './flow/hopHover';
import { NO_HOP_LINKS, hopText, otherTraceHref, type HopLinks } from '../lib/hopLinks';

/**
 * The lineage of one labelled value on a leg: for each origin label lineage
 * reached it from, every chain the value travelled — the payload where the
 * classifier found it (the excerpt, unit highlighted), each carrier, and this
 * leg — in seq order. The first chain of each origin is open; the rest fold
 * under a count. Rendered on both pages that show a leg's labels.
 *
 * Facts only: a chain says where the value was, never that it caused a decision.
 * A value with no carried-in origin, or an origin whose read carried no chains
 * (an older server), renders nothing — there is never an empty chain row.
 */
export function LabelLineage({
  label,
  traceId,
  links = NO_HOP_LINKS,
}: {
  label: LegLabel;
  /** The page's trace: a hop in any other trace is marked as a boundary. */
  traceId: string;
  links?: HopLinks;
}) {
  const origins = label.carried_in.filter((o) => (o.chains?.length ?? 0) > 0);
  if (origins.length === 0) return null;
  return (
    <div className="dg-label-lineage" data-testid="label-lineage">
      {origins.map((o) => (
        <OriginChains key={`${o.origin_interaction_id}-${o.origin_leg_type}`} origin={o} traceId={traceId} links={links} />
      ))}
    </div>
  );
}

function OriginChains({ origin, traceId, links }: { origin: LabelOrigin; traceId: string; links: HopLinks }) {
  const [first, ...rest] = origin.chains ?? [];
  const [open, setOpen] = useState(false);
  return (
    <div className="dg-label-lineage__origin">
      <Chain hops={first} traceId={traceId} links={links} />
      {rest.length > 0 && (
        <>
          <Button variant="link" isInline onClick={() => setOpen((v) => !v)} className="dg-label-lineage__more">
            {open ? 'Fewer chains' : `${rest.length} more ${rest.length === 1 ? 'chain' : 'chains'}`}
          </Button>
          {open && rest.map((hops, i) => <Chain key={i} hops={hops} traceId={traceId} links={links} />)}
        </>
      )}
    </div>
  );
}

const ROLE_WORD: Record<LineageHop['role'], string> = { origin: 'origin', hop: 'then', here: 'current' };

function Chain({ hops, traceId, links }: { hops: LineageHop[]; traceId: string; links: HopLinks }) {
  const truncated = hops[0]?.role !== 'origin';
  return (
    <ol className="dg-label-lineage__chain" aria-label="Lineage chain">
      {truncated && <li className="dg-label-lineage__gap">earlier hops not recorded</li>}
      {hops.map((h, i) => (
        <Hop key={`${h.interaction_id}-${h.leg_type}-${i}`} hop={h} traceId={traceId} links={links} />
      ))}
    </ol>
  );
}

function Hop({ hop, traceId, links }: { hop: LineageHop; traceId: string; links: HopLinks }) {
  const setHover = useHopHover();
  const foreign = hop.trace_id !== traceId;
  const to = foreign ? otherTraceHref(hop) : links.sameTrace(hop);
  const text = hopText(hop);
  return (
    <li
      className={`dg-label-lineage__hop dg-label-lineage__hop--${hop.role}`}
      onMouseEnter={foreign ? undefined : () => setHover(hop.interaction_id)}
      onMouseLeave={foreign ? undefined : () => setHover(null)}
    >
      <span className="dg-label-lineage__role">{ROLE_WORD[hop.role]}</span>{' '}
      {to != null ? <Link to={to}>{text}</Link> : <span>{text}</span>}
      {foreign && (
        <>
          {' '}
          <Label isCompact color="gold" data-testid="trace-boundary">
            in trace {hop.trace_id.slice(0, 8)}…
          </Label>
        </>
      )}
      {hop.role === 'origin' && hop.excerpt != null ? (
        <Excerpt text={hop.excerpt.text} start={hop.excerpt.start} end={hop.excerpt.end} />
      ) : hop.text != null ? (
        <>
          {' '}
          <span className="dg-mono dg-label-lineage__text">&ldquo;{hop.text}&rdquo;</span>
        </>
      ) : null}
    </li>
  );
}

/** The origin payload's text around the unit, the unit marked. */
function Excerpt({ text, start, end }: { text: string; start: number; end: number }) {
  return (
    <div className="dg-mono dg-label-lineage__excerpt" data-testid="origin-excerpt">
      {text.slice(0, start)}
      <mark>{text.slice(start, end)}</mark>
      {text.slice(end)}
    </div>
  );
}
