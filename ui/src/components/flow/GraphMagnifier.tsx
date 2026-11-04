import { useEffect, useRef, useState } from 'react';

/** Lens diameter (px) and how much it enlarges what is under it. */
const LENS_SIZE = 240;
const LENS_ZOOM = 2.5;

/**
 * A magnifying glass over the graph (issue #220): a round lens that follows the
 * pointer and shows the drawing under it enlarged, without changing the graph's
 * own zoom — so a reader can read a dense corner's labels and keep the whole
 * picture in view.
 *
 * The lens shows a CLONE of the surface's `<svg>`, scaled. The clone is refreshed
 * whenever the graph's DOM changes (a pan, a zoom, a drag, a live update), at
 * most once a frame. It is in the same document, so the graph's stylesheet
 * colours it exactly as the original. `pointer-events: none` on the lens, so
 * clicks, drags and hovers still reach the graph beneath.
 */
export function GraphMagnifier({ surface }: { surface: HTMLElement | null }) {
  const lensRef = useRef<HTMLDivElement>(null);
  const refreshRef = useRef<(() => void) | null>(null);
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  // Whether the lens is showing — read by the observer, which clones only then.
  const visibleRef = useRef(false);
  visibleRef.current = at != null;

  // Keep the clone in step with the graph. The observer watches the whole
  // surface, because PF draws its `<svg>` only after measuring the surface; it
  // ignores the lens's own changes (the clone it inserts), which would otherwise
  // re-trigger it forever.
  useEffect(() => {
    const lens = lensRef.current;
    if (!lens || !surface) return;
    let frame = 0;
    const refresh = () => {
      frame = 0;
      const svg = surface.querySelector<SVGSVGElement>('[data-test-id="topology"] > svg');
      if (!svg) return;
      const clone = svg.cloneNode(true) as SVGSVGElement;
      const { width, height } = svg.getBoundingClientRect();
      // Inline, not attributes: PF's stylesheet sizes the surface svg to 100%,
      // which would shrink the clone to the lens.
      clone.style.width = `${width}px`;
      clone.style.height = `${height}px`;
      clone.setAttribute('aria-hidden', 'true');
      // The clone keeps the graph's tab stops but none of its handlers; `inert`
      // keeps keyboard focus out of it.
      clone.setAttribute('inert', '');
      clone.classList.add('dg-graph-magnifier__clone');
      lens.replaceChildren(clone);
    };
    // A hidden lens does no work: the clone is rebuilt only while it shows,
    // and once when it appears (the pointer effect below).
    const observer = new MutationObserver((records) => {
      if (!visibleRef.current) return;
      if (records.every((r) => lens.contains(r.target))) return;
      if (frame === 0) frame = requestAnimationFrame(refresh);
    });
    refreshRef.current = refresh;
    observer.observe(surface, { subtree: true, childList: true, attributes: true, characterData: true });
    return () => {
      observer.disconnect();
      if (frame !== 0) cancelAnimationFrame(frame);
      lens.replaceChildren();
      refreshRef.current = null;
    };
  }, [surface]);

  // The graph may have changed while the lens was hidden: re-clone on showing.
  const showing = at != null;
  useEffect(() => {
    if (showing) refreshRef.current?.();
  }, [showing]);

  // Follow the pointer over the surface.
  useEffect(() => {
    if (!surface) return;
    const move = (e: PointerEvent) => {
      const r = surface.getBoundingClientRect();
      setAt({ x: e.clientX - r.left, y: e.clientY - r.top });
    };
    const leave = () => setAt(null);
    surface.addEventListener('pointermove', move);
    surface.addEventListener('pointerleave', leave);
    return () => {
      surface.removeEventListener('pointermove', move);
      surface.removeEventListener('pointerleave', leave);
    };
  }, [surface]);

  const half = LENS_SIZE / 2;
  return (
    <div
      ref={lensRef}
      className="dg-graph-magnifier"
      data-testid="graph-magnifier"
      aria-hidden="true"
      style={{
        width: LENS_SIZE,
        height: LENS_SIZE,
        left: (at?.x ?? 0) - half,
        top: (at?.y ?? 0) - half,
        visibility: at ? 'visible' : 'hidden',
        // The clone sits at the surface's origin; move it so the point under
        // the pointer lands in the lens centre, then enlarge around that.
        ['--dg-lens-x' as string]: `${half - (at?.x ?? 0) * LENS_ZOOM}px`,
        ['--dg-lens-y' as string]: `${half - (at?.y ?? 0) * LENS_ZOOM}px`,
        ['--dg-lens-zoom' as string]: String(LENS_ZOOM),
      }}
    />
  );
}
