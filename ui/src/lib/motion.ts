/**
 * Smooth motion for a picture that changes under its reader (ADR-0034).
 *
 * The live trace page re-derives the graph on every derived write, and a write can
 * move an entity's cell (its call depth or its chronology changed). Each thing that
 * has to move — a node, an edge's bendpoint — CHASES its target with a critically
 * damped spring rather than playing a fixed-length tween. The difference is what
 * the reader sees when a second push lands while the first move is still in
 * flight, which on a live page is the normal case, not the rare one:
 *
 * - a tween has to be either FINISHED (the node jumps to the old destination, then
 *   sets off again) or RESTARTED from rest (the node stops dead mid-air, then sets
 *   off again) — a visible hitch either way;
 * - a spring is just given a new target. It keeps its position AND its velocity,
 *   so the path bends towards the new target without a stop or a jump.
 *
 * Starting from rest, the spring also eases IN as well as out, so a node never
 * leaves its cell at full speed.
 *
 * THE READER WINS. Every frame checks that a thing is still where the previous
 * frame put it. If it is not, someone else moved it — the reader is dragging it —
 * and it is dropped rather than fought over.
 *
 * Every frame's writes run inside ONE `batch` (the caller passes a MobX action),
 * so the observers redraw once per frame, not once per moving thing.
 *
 * Render-free and clock-injectable, so it is unit-tested without a browser.
 */

export interface XY {
  readonly x: number;
  readonly y: number;
}

/** One thing that can move: where it is drawn now (`undefined` once it is gone), and how to draw it elsewhere. */
export interface Movable {
  read(): XY | undefined;
  write(p: XY): void;
}

export interface MotionOptions {
  /**
   * The spring's time constant. Roughly a third of the way there after one
   * `smoothTimeMs`, nine-tenths after two, and settled (under half a pixel) after
   * about four, whatever the distance.
   */
  smoothTimeMs?: number;
  /** Wraps one frame's writes; the graph passes a MobX action. */
  batch?: (fn: () => void) => void;
  /** No frames at all: every move lands at once. For prefers-reduced-motion and for environments without frames. */
  instant?: boolean;
  now?: () => number;
  requestFrame?: (cb: () => void) => number;
  cancelFrame?: (handle: number) => void;
}

/** 150 ms: most of a move is visible in the first third of a second, and it is over in about two-thirds. */
export const SMOOTH_TIME_MS = 150;

/**
 * The most time one frame may advance the motion by. A frame that comes late (the
 * page was busy re-rendering a push) would otherwise move everything its whole
 * lateness at once — a skip. Capped, a late frame moves at most two frames' worth
 * and the motion runs a little long instead.
 */
const MAX_STEP_S = 1 / 30;

/** Under this distance (px) and speed (px/s) a thing is put exactly on its target and released. */
const SETTLE_PX = 0.5;
const SETTLE_SPEED = 5;

interface Track {
  movable: Movable;
  target: XY;
  /** Where this engine last drew it — the "was it moved by someone else?" reference. */
  last: XY;
  vx: number;
  vy: number;
}

/**
 * One step of a critically damped spring on one axis (the closed-form
 * approximation games use as `SmoothDamp`): stable for any frame gap, never
 * overshoots past the target.
 */
function dampAxis(
  current: number,
  target: number,
  velocity: number,
  smoothTime: number,
  dt: number,
): [number, number] {
  const omega = 2 / smoothTime;
  const x = omega * dt;
  const decay = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
  const change = current - target;
  const temp = (velocity + omega * change) * dt;
  let next = target + (change + temp) * decay;
  let nextVelocity = (velocity - omega * temp) * decay;
  // Crossed the target this step: stop on it instead of swinging past.
  if (target - current > 0 === next > target) {
    next = target;
    nextVelocity = 0;
  }
  return [next, nextVelocity];
}

const same = (a: XY, b: XY) => a.x === b.x && a.y === b.y;

export class Motion {
  private readonly tracks = new Map<string, Track>();
  private readonly smoothTime: number;
  private readonly batch: (fn: () => void) => void;
  private readonly instant: boolean;
  private readonly now: () => number;
  private readonly requestFrame: ((cb: () => void) => number) | null;
  private readonly cancelFrame: (handle: number) => void;
  private handle: number | null = null;
  private lastTick = 0;

  constructor(options: MotionOptions = {}) {
    this.smoothTime = (options.smoothTimeMs ?? SMOOTH_TIME_MS) / 1000;
    this.batch = options.batch ?? ((fn) => fn());
    this.now = options.now ?? (() => performance.now());
    this.requestFrame =
      options.requestFrame ??
      (typeof requestAnimationFrame === 'function' ? (cb) => requestAnimationFrame(cb) : null);
    this.cancelFrame =
      options.cancelFrame ?? ((h) => (typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame(h) : undefined));
    this.instant = options.instant === true || this.requestFrame === null;
  }

  /**
   * Send `key` towards `target` from wherever it is drawn now. Already moving: the
   * target changes and the velocity is kept. Already there and still: nothing.
   */
  moveTo(key: string, movable: Movable, target: XY): void {
    const at = movable.read();
    if (at === undefined) {
      this.tracks.delete(key);
      return;
    }
    const track = this.tracks.get(key);
    if (track !== undefined && !same(at, track.last)) {
      // Moved by someone else since our last frame: it is the reader's now.
      this.tracks.delete(key);
      return;
    }
    if (track === undefined && same(at, target)) return;
    if (this.instant) {
      this.batch(() => movable.write(target));
      this.tracks.delete(key);
      return;
    }
    if (track === undefined) {
      this.tracks.set(key, { movable, target, last: at, vx: 0, vy: 0 });
    } else {
      track.movable = movable;
      track.target = target;
    }
    this.schedule();
  }

  /**
   * Whether `key` is in flight under this engine — false once it has settled, or
   * once someone else has moved it (which also releases it here).
   */
  isMoving(key: string): boolean {
    const track = this.tracks.get(key);
    if (track === undefined) return false;
    const at = track.movable.read();
    if (at === undefined || !same(at, track.last)) {
      this.tracks.delete(key);
      return false;
    }
    return true;
  }

  /** Stop everything where it is drawn. */
  stop(): void {
    this.tracks.clear();
    if (this.handle !== null) this.cancelFrame(this.handle);
    this.handle = null;
  }

  private schedule(): void {
    if (this.handle !== null || this.requestFrame === null) return;
    this.lastTick = this.now();
    this.handle = this.requestFrame(this.tick);
  }

  private readonly tick = (): void => {
    this.handle = null;
    const now = this.now();
    const dt = Math.min(MAX_STEP_S, Math.max(0, (now - this.lastTick) / 1000));
    this.lastTick = now;
    this.batch(() => {
      for (const [key, t] of this.tracks) {
        const at = t.movable.read();
        if (at === undefined || !same(at, t.last)) {
          this.tracks.delete(key);
          continue;
        }
        const [x, vx] = dampAxis(at.x, t.target.x, t.vx, this.smoothTime, dt);
        const [y, vy] = dampAxis(at.y, t.target.y, t.vy, this.smoothTime, dt);
        const settled =
          Math.abs(x - t.target.x) < SETTLE_PX &&
          Math.abs(y - t.target.y) < SETTLE_PX &&
          Math.hypot(vx, vy) < SETTLE_SPEED;
        const next = settled ? t.target : { x, y };
        t.movable.write(next);
        t.last = next;
        t.vx = vx;
        t.vy = vy;
        if (settled) this.tracks.delete(key);
      }
    });
    if (this.tracks.size > 0) {
      this.handle = this.requestFrame!(this.tick);
    }
  };
}
