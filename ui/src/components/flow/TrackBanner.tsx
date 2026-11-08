import { Button, Label } from '@patternfly/react-core';
import type { TrackedValue } from '../../lib/labelTrack';

/**
 * The line above the views while values are tracked on the graph: which values,
 * each with its own remove, and a clear. Says what is lit; never why anything
 * was decided. `foreignHops` names the hops of those values in other traces,
 * which this trace's views cannot show.
 */
export function TrackBanner({
  tracked,
  foreignHops,
  onRemove,
  onClear,
  onChoose,
}: {
  tracked: readonly TrackedValue[];
  foreignHops: number;
  onRemove: (key: string) => void;
  onClear: () => void;
  /** Opens the track picker for the selected interaction, anchored to the button; absent when nothing is selected. */
  onChoose?: (at: { x: number; y: number }) => void;
}) {
  if (tracked.length === 0) return null;
  return (
    <div className="dg-track-banner" data-testid="track-banner" role="status">
      <span>
        Tracking {tracked.length} {tracked.length === 1 ? 'value' : 'values'} on the graph — the legs they sat on are lit, the rest dimmed:
      </span>
      {tracked.map((v) => (
        <Label key={v.key} isCompact color="blue" onClose={() => onRemove(v.key)} closeBtnAriaLabel={`Stop tracking ${v.text} (${v.entityType})`}>
          <span className="dg-mono">{v.text}</span> {v.entityType}
        </Label>
      ))}
      {foreignHops > 0 && (
        <span style={{ color: 'var(--dg-color-muted)' }}>
          ({foreignHops} {foreignHops === 1 ? 'hop' : 'hops'} in other traces not shown)
        </span>
      )}
      {onChoose && (
        <Button
          variant="link"
          isInline
          onClick={(e) => {
            const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
            onChoose({ x: r.left, y: r.bottom + 4 });
          }}
        >
          Choose values…
        </Button>
      )}
      <Button variant="link" isInline onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}
