import { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Spinner } from '@patternfly/react-core';
import { useLegLabels } from '../../api/hooks';
import { SensitivityBadge } from '../SensitivityBadge';
import { trackableValues, type TrackedValue } from '../../lib/labelTrack';
import { useLabelTrack } from './labelTrack';
import type { LegLabels } from '../../types';

/** Where the picker opens: a point (the right-click) in viewport coordinates. */
export interface PickerAnchor {
  x: number;
  y: number;
}

/** What the picker is about: one interaction, with the leg the reader pointed at open. */
export interface PickerTarget {
  interactionId: string;
  legType: 'request' | 'response';
  at: PickerAnchor;
}

/**
 * The check menu for tracking values on the graph: both legs of one interaction,
 * one tick box per carried-in value, "all" / "none" per leg, the leg the reader
 * right-clicked open and the other folded (the echo repeats the request's
 * values, so both are offered but one is in the way). Ticks take effect on the
 * graph at once; the banner shows what is ticked. Closes on Escape, a click
 * outside, or Done. Reads the same labels resource the Data lineage tab reads.
 */
export function TrackPicker({ traceId, target, onClose }: { traceId: string; target: PickerTarget; onClose: () => void }) {
  const labelsQ = useLegLabels(traceId, target.interactionId);
  const track = useLabelTrack();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [onClose]);

  // Kept on screen: the point is the pointer, so the menu opens down and right
  // of it but never past the viewport's edge.
  const style: React.CSSProperties = {
    position: 'fixed',
    left: Math.min(target.at.x, Math.max(0, window.innerWidth - 380)),
    top: Math.min(target.at.y, Math.max(0, window.innerHeight - 320)),
  };

  const legs: LegLabels[] = labelsQ.data
    ? (['request', 'response'] as const).flatMap((t) => {
        const leg = labelsQ.data.byLeg.get(t);
        return leg ? [leg] : [];
      })
    : [];
  const trackable = legs.map((leg) => ({ leg, values: trackableValues(target.interactionId, [leg]) }));
  const any = trackable.some((g) => g.values.length > 0);

  return (
    <div ref={ref} className="dg-track-picker" style={style} role="dialog" aria-label="Track values on the graph" data-testid="track-picker">
      <div className="dg-track-picker__title">Track on the graph</div>
      {labelsQ.isLoading ? (
        <Spinner size="md" aria-label="Loading the values" />
      ) : labelsQ.isError ? (
        <div style={{ color: 'var(--dg-color-muted)' }}>The labels read failed.</div>
      ) : !any ? (
        <div style={{ color: 'var(--dg-color-muted)' }}>No value on this interaction was carried in from an earlier step.</div>
      ) : (
        trackable.map(({ leg, values }) => (
          <LegGroup key={leg.leg_type} leg={leg} values={values} open={leg.leg_type === target.legType} track={track} />
        ))
      )}
      <div className="dg-track-picker__foot">
        <Button variant="link" isInline onClick={onClose}>
          Done
        </Button>
      </div>
    </div>
  );
}

function LegGroup({
  leg,
  values,
  open: initiallyOpen,
  track,
}: {
  leg: LegLabels;
  values: TrackedValue[];
  open: boolean;
  track: ReturnType<typeof useLabelTrack>;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  const ticked = values.filter((v) => track.tracked.has(v.key)).length;
  const name = leg.leg_type === 'request' ? 'Request' : 'Response';
  const others = () => [...track.tracked.values()].filter((t) => !values.some((v) => v.key === t.key));
  return (
    <div className="dg-track-picker__group" data-testid={`track-group-${leg.leg_type}`}>
      <div className="dg-track-picker__head">
        <Button variant="link" isInline onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? '▾' : '▸'} {name} #{leg.seq}
        </Button>{' '}
        <span style={{ color: 'var(--dg-color-muted)' }}>
          {values.length === 0 ? 'nothing carried in' : `${ticked} of ${values.length}`}
        </span>
        {values.length > 0 && open && (
          <>
            {' · '}
            <Button variant="link" isInline onClick={() => track.replace([...others(), ...values])}>
              all
            </Button>
            {' · '}
            <Button variant="link" isInline onClick={() => track.replace(others())}>
              none
            </Button>
          </>
        )}
      </div>
      {open &&
        values.map((v) => {
          const label = leg.labels.find((l) => v.key.endsWith(`/${l.entity_type}/${l.start ?? 'x'}-${l.end ?? 'x'}/${l.text}`));
          return (
            <Checkbox
              key={v.key}
              id={`pick-${v.key}`}
              className="dg-track-picker__row"
              isChecked={track.tracked.has(v.key)}
              onChange={(_e, checked) => track.set(v.key, checked ? v : null)}
              aria-label={`Track ${v.text} (${v.entityType}) on the graph`}
              label={
                <span>
                  <span className="dg-mono">{v.text}</span> {v.entityType}{' '}
                  {label?.sensitivity_level && <SensitivityBadge level={label.sensitivity_level} />}{' '}
                  <span style={{ color: 'var(--dg-color-muted)' }}>{label?.regulatory_tags.join(', ')}</span>
                </span>
              }
            />
          );
        })}
    </div>
  );
}
