import { connectorColor, type ConnectorRole } from './connectorColor';

/**
 * The flat view's request↔response connector cell. Given the drawing roles for
 * this row (one per interaction bracket covering it — see `flatConnectors`),
 * paints a small SVG: a vertical line down from center for a request `top`
 * edge (capped with a ▾ marker), up to center for a response `bottom` edge
 * (capped with ▴), and a full pass-through line for an in-between `through`
 * row. Overlapping brackets are laid out in adjacent lanes so their lines never
 * coincide. `pointer-events: none` on the SVG keeps the row click intact.
 *
 * The SVG is absolutely positioned to fill the cell's TRUE height (`inset: 0`
 * in a `position: relative` cell) and drawn with a fixed-height viewBox scaled
 * via `preserveAspectRatio="none"`. So each row's segment always spans the full
 * row — whatever a compact row actually measures, including cell padding — and
 * butts seamlessly against the adjacent rows' segments. That is what makes a
 * request→response bracket read as ONE unbroken line rather than the chopped
 * per-row pieces the old fixed 28px SVG produced. The end caps (▾/▴) and the
 * through pass-through keep their meaning; lanes keep overlapping brackets apart.
 */
export function ConnectorCell({ roles }: { roles: ConnectorRole[] }) {
  const laneW = 8; // horizontal spacing between overlapping brackets
  const width = Math.max(laneW, roles.length * laneW);
  // A nominal viewBox height the lines are drawn in; `preserveAspectRatio="none"`
  // stretches it to the cell's real pixel height, so the value is arbitrary —
  // only the ratios (mid = center) matter. Vertical lines don't distort under
  // that stretch, but glyphs would, so the ▾/▴ caps are drawn as separately-
  // positioned HTML markers (see below) rather than SVG <text>.
  const vbH = 100;
  const mid = vbH / 2;
  return (
    <>
      <svg
        width={width}
        height="100%"
        viewBox={`0 0 ${width} ${vbH}`}
        preserveAspectRatio="none"
        style={{
          position: 'absolute',
          inset: 0,
          display: 'block',
          pointerEvents: 'none',
          overflow: 'visible',
        }}
        aria-hidden="true"
        data-testid="flat-connector"
      >
        {roles.map(({ id, role }, lane) => {
          const x = lane * laneW + laneW / 2;
          const color = connectorColor(id);
          // top: line from center downward; bottom: from top edge to center;
          // through: full height. A non-scaling stroke keeps the line 2px wide
          // regardless of how tall the row (and thus the stretched viewBox) is.
          const y1 = role === 'bottom' ? 0 : mid;
          const y2 = role === 'top' ? vbH : mid;
          return (
            <g key={id} data-connector-id={id} data-connector-role={role} stroke={color} fill={color}>
              <line
                x1={x}
                y1={role === 'through' ? 0 : y1}
                x2={x}
                y2={role === 'through' ? vbH : y2}
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            </g>
          );
        })}
      </svg>
      {/* The ▾/▴ end caps as HTML markers centered on the cell, so they keep a
          fixed size and shape while the SVG lines stretch to the row height. */}
      {roles.map(({ id, role }, lane) =>
        role === 'through' ? null : (
          <span
            key={`${id}-cap`}
            aria-hidden="true"
            style={{
              position: 'absolute',
              top: '50%',
              left: lane * laneW + laneW / 2,
              transform: 'translate(-50%, -50%)',
              fontSize: 9,
              lineHeight: 1,
              color: connectorColor(id),
              pointerEvents: 'none',
            }}
          >
            {role === 'top' ? '▾' : '▴'}
          </span>
        ),
      )}
    </>
  );
}
