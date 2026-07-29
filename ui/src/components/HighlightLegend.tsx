import { Button } from '@patternfly/react-core';
import type { PinView } from '../lib/pins';

/**
 * Floating legend of the active highlight sets (ported from the vanilla
 * #highlight-legend). One row per pinned set — a slot-color swatch, its label,
 * and an unpin ✕. Hidden when nothing is pinned.
 */
export function HighlightLegend({
  pins,
  onRemove,
}: {
  pins: PinView[];
  onRemove: (key: string) => void;
}) {
  if (pins.length === 0) return null;
  return (
    <div
      style={{
        margin: '0 0 0.5rem',
        padding: '0.4rem 0.5rem',
        background: 'var(--dg-surface-panel)',
        border: '1px solid var(--dg-border-subtle)',
        borderRadius: 4,
        fontSize: '0.8rem',
      }}
    >
      <div style={{ color: 'var(--dg-color-muted)', textTransform: 'uppercase', fontSize: '0.7rem', marginBottom: '0.3rem' }}>
        Highlighted sets
      </div>
      {pins.map((p) => (
        <div key={p.key} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', padding: '0.1rem 0' }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: p.color }} />
          <span style={{ flex: 1, color: 'var(--dg-color-label)', overflow: 'hidden', textOverflow: 'ellipsis' }} title={p.label}>
            {p.label}
          </span>
          <Button variant="plain" aria-label={`Unpin ${p.label}`} onClick={() => onRemove(p.key)} style={{ padding: 0 }}>
            ✕
          </Button>
        </div>
      ))}
    </div>
  );
}
