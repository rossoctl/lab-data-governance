import { describe, it, expect, vi } from 'vitest';
import { ENTER_MS, Motion, SMOOTH_TIME_MS, fadeIn, type Movable, type XY } from './motion';

/** A hand-cranked clock and frame queue: `frame()` advances time and runs one frame. */
function rig(options: { instant?: boolean } = {}) {
  let t = 0;
  let queued: (() => void) | null = null;
  let batches = 0;
  const motion = new Motion({
    now: () => t,
    requestFrame: (cb) => {
      queued = cb;
      return 1;
    },
    cancelFrame: () => {
      queued = null;
    },
    batch: (fn) => {
      batches += 1;
      fn();
    },
    instant: options.instant,
  });
  return {
    motion,
    /** Advance `ms` and run the queued frame, if any. */
    frame(ms = 16) {
      t += ms;
      const cb = queued;
      queued = null;
      cb?.();
    },
    get pending() {
      return queued !== null;
    },
    get batches() {
      return batches;
    },
  };
}

/** A point that records every write. */
function dot(start: XY): Movable & { at: XY; writes: XY[] } {
  const d = {
    at: start,
    writes: [] as XY[],
    read: () => d.at,
    write: (p: XY) => {
      d.at = p;
      d.writes.push(p);
    },
  };
  return d;
}

const run = (r: ReturnType<typeof rig>, frames: number) => {
  for (let i = 0; i < frames && r.pending; i += 1) r.frame();
};

describe('Motion', () => {
  it('moves to the target, ends exactly on it, and never passes it', () => {
    const r = rig();
    const d = dot({ x: 0, y: 0 });
    r.motion.moveTo('a', d, { x: 220, y: 96 });
    run(r, 200);
    expect(r.pending).toBe(false);
    expect(d.at).toEqual({ x: 220, y: 96 });
    // Monotone on both axes: no overshoot, no wobble.
    for (let i = 1; i < d.writes.length; i += 1) {
      expect(d.writes[i].x).toBeGreaterThanOrEqual(d.writes[i - 1].x);
      expect(d.writes[i].x).toBeLessThanOrEqual(220);
      expect(d.writes[i].y).toBeLessThanOrEqual(96);
    }
  });

  it('is over in well under a second whatever the distance', () => {
    for (const distance of [40, 220, 1200]) {
      const r = rig();
      const d = dot({ x: 0, y: 0 });
      r.motion.moveTo('a', d, { x: distance, y: 0 });
      let elapsed = 0;
      while (r.pending) {
        r.frame();
        elapsed += 16;
      }
      expect(elapsed).toBeLessThan(SMOOTH_TIME_MS * 6);
    }
  });

  it('eases in: it does not leave at full speed', () => {
    const r = rig();
    const d = dot({ x: 0, y: 0 });
    r.motion.moveTo('a', d, { x: 220, y: 0 });
    run(r, 4);
    const steps = d.writes.map((p, i) => p.x - (i === 0 ? 0 : d.writes[i - 1].x));
    expect(steps[0]).toBeLessThan(steps[1]);
  });

  it('a new target mid-flight bends the path — no jump and no restart from rest', () => {
    const r = rig();
    const d = dot({ x: 0, y: 0 });
    r.motion.moveTo('a', d, { x: 220, y: 0 });
    run(r, 6);
    const before = d.at;
    const lastStep = d.writes[5].x - d.writes[4].x;
    // A second push lands: same direction, further on.
    r.motion.moveTo('a', d, { x: 440, y: 0 });
    expect(d.at).toEqual(before); // a new target moves nothing by itself
    r.frame();
    const nextStep = d.at.x - before.x;
    // Kept its speed: a restart from rest would crawl here, the way frame one did.
    expect(nextStep).toBeGreaterThan(d.writes[0].x);
    expect(nextStep).toBeGreaterThan(lastStep * 0.5);
    run(r, 200);
    expect(d.at).toEqual({ x: 440, y: 0 });
  });

  it('a late frame moves no further than two ordinary frames would', () => {
    const late = rig();
    const a = dot({ x: 0, y: 0 });
    late.motion.moveTo('a', a, { x: 1000, y: 0 });
    late.frame(16);
    late.frame(500); // the page was busy for half a second
    const ordinary = rig();
    const b = dot({ x: 0, y: 0 });
    ordinary.motion.moveTo('b', b, { x: 1000, y: 0 });
    ordinary.frame(16);
    ordinary.frame(17);
    ordinary.frame(16);
    // Uncapped, the late frame would have carried it nearly all the way (~1000).
    expect(a.at.x).toBeLessThan(b.at.x * 1.5);
  });

  it('lets go of a thing someone else moved, and never writes it again', () => {
    const r = rig();
    const d = dot({ x: 0, y: 0 });
    r.motion.moveTo('a', d, { x: 220, y: 0 });
    run(r, 3);
    d.at = { x: 900, y: 900 }; // the reader's drag
    const writes = d.writes.length;
    expect(r.motion.isMoving('a')).toBe(false);
    run(r, 50);
    expect(d.at).toEqual({ x: 900, y: 900 });
    expect(d.writes.length).toBe(writes);
  });

  it('lets go of a dragged thing even when a new target arrives before the next frame', () => {
    const r = rig();
    const d = dot({ x: 0, y: 0 });
    r.motion.moveTo('a', d, { x: 220, y: 0 });
    run(r, 3);
    d.at = { x: 900, y: 900 };
    r.motion.moveTo('a', d, { x: 440, y: 0 });
    run(r, 50);
    expect(d.at).toEqual({ x: 900, y: 900 });
  });

  it('writes every moving thing in ONE batch per frame', () => {
    const r = rig();
    const dots = [0, 1, 2, 3, 4].map((i) => dot({ x: i, y: 0 }));
    dots.forEach((d, i) => r.motion.moveTo(`d${i}`, d, { x: 300 + i, y: 50 }));
    const before = r.batches;
    r.frame();
    r.frame();
    expect(r.batches - before).toBe(2);
    expect(dots.every((d) => d.writes.length === 2)).toBe(true);
  });

  it('does nothing for a thing already where it should be', () => {
    const r = rig();
    const d = dot({ x: 5, y: 5 });
    r.motion.moveTo('a', d, { x: 5, y: 5 });
    expect(r.pending).toBe(false);
    expect(r.motion.isMoving('a')).toBe(false);
  });

  it('lands at once when instant (reduced motion, no frames)', () => {
    const r = rig({ instant: true });
    const d = dot({ x: 0, y: 0 });
    r.motion.moveTo('a', d, { x: 220, y: 96 });
    expect(d.at).toEqual({ x: 220, y: 96 });
    expect(r.pending).toBe(false);
  });

  it('stop leaves everything where it is drawn', () => {
    const r = rig();
    const d = dot({ x: 0, y: 0 });
    r.motion.moveTo('a', d, { x: 220, y: 0 });
    run(r, 3);
    const at = d.at;
    r.motion.stop();
    run(r, 50);
    expect(d.at).toEqual(at);
    expect(r.motion.isMoving('a')).toBe(false);
  });

  it('drops a thing that is gone', () => {
    const r = rig();
    let alive = true;
    const d = dot({ x: 0, y: 0 });
    const m: Movable = { read: () => (alive ? d.at : undefined), write: d.write };
    r.motion.moveTo('a', m, { x: 220, y: 0 });
    run(r, 2);
    alive = false;
    run(r, 50);
    expect(r.pending).toBe(false);
  });
});

describe('fadeIn', () => {
  const el = () => ({ animate: vi.fn() }) as unknown as Element & { animate: ReturnType<typeof vi.fn> };

  it('fades from transparent to opaque over the arrival’s length, easing out', () => {
    const e = el();
    fadeIn(e, { bornAt: 1000, durationMs: ENTER_MS }, 1000);
    expect(e.animate).toHaveBeenCalledWith([{ opacity: 0 }, { opacity: 1 }], {
      duration: ENTER_MS,
      delay: -0,
      easing: 'ease-out',
    });
  });

  it('picks a fade up where it stands: an element rebuilt mid-fade does not replay it', () => {
    const e = el();
    fadeIn(e, { bornAt: 1000, durationMs: ENTER_MS }, 1300);
    expect(e.animate.mock.calls[0][1].delay).toBe(-300);
  });

  it('draws at once an element rebuilt after its fade is over', () => {
    const e = el();
    fadeIn(e, { bornAt: 1000, durationMs: ENTER_MS }, 1000 + ENTER_MS);
    expect(e.animate).not.toHaveBeenCalled();
  });

  it('does nothing where there are no Web Animations', () => {
    expect(() => fadeIn({} as Element, { bornAt: 0, durationMs: ENTER_MS }, 0)).not.toThrow();
  });
});
