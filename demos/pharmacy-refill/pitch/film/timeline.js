// Timeline for film.html. Every cue is anchored to a spoken word: W(line, "word") is the moment that
// word starts in voice/af_heart/narration.wav, from Kokoro's own token timestamps (words.json, written
// by scripts/tts.py). Re-voicing or rewording a line moves its cues with it; nothing here is a raw time.
// The page exposes window.seek(t); scripts/render/render.mjs calls it once per frame.
const WORDS = window.WORDS;     // [{line, beat, word, start, end}]
const LINES = window.LINES;     // [{n, beat, text, start, end}]

const norm = (s) => s.toLowerCase().replace(/[^a-z0-9-]/g, "");
const opening = (text, n) => (text.match(/[A-Za-z0-9'-]+/g) || []).slice(0, n).map(norm).join(" ");
// Lines are named by their opening words, so adding or removing a line never shifts a cue.
function lineNo(key) {
  const n = key.split(" ").length;
  const hits = LINES.filter((l) => opening(l.text, n) === opening(key, n));
  if (hits.length !== 1) throw new Error(`line key "${key}" matches ${hits.length} narration lines`);
  return hits[0].n;
}
// Start of the k-th occurrence (default first) of `word` in the narration line that opens with `key`.
function W(key, word, k = 1) {
  const line = lineNo(key);
  const hits = WORDS.filter((w) => w.line === line && norm(w.word) === norm(word));
  if (hits.length < k) throw new Error(`cue not found: "${key}…" "${word}" #${k}`);
  return hits[k - 1].start;
}
const L = (key) => LINES[lineNo(key) - 1];   // a narration line: .start / .end
const END = LINES[LINES.length - 1].end;
const DURATION = END + 1.6;

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const ease = (x) => x * x * (3 - 2 * x);                       // smoothstep
const ramp = (t, t0, d) => ease(clamp((t - t0) / d));            // 0→1 over [t0, t0+d]
const win = (t, t0, t1, d = 0.45) => Math.min(ramp(t, t0, d), 1 - ramp(t, t1 - d, d));
const $ = (id) => document.getElementById(id);

// Scene boundaries: each scene starts a little before its first line and overlaps the next by 0.3s.
const B = {
  app: 0,
  flow: L("That one").start - 0.3,
  intro: L("Sentry is data").start - 0.45,
  rule: L("Let's take").start - 0.45,
  l1: L("Level one").start - 0.3,
  gate: W("Both are", "blocks") - 0.15,
  l2a: L("Level two").start - 0.3,
  l2b: L("But Sentry's").start - 0.65,
  l3a: L("With full context Sentry").start - 0.25,
  l3b: L("A nightly").start - 0.25,
  fix: L("Sentry doesn't").start - 0.5,
  refill: L("This way").start - 0.25,
  pay: L("This way").end + 0.6,
  close: L("Sentry with").start - 0.3,
};
const O = 0.3;   // cross-fade overlap

// Scenes: [id, start, end, kenBurns {from scale, to scale, from x/y %, to x/y %}]
const SCENES = [
  ["s-app",    B.app,    B.flow + O,   { s0: 1.03, s1: 1.10, x0: 0, y0: 0, x1: -1, y1: 0 }],
  ["s-flow",   B.flow,   B.intro + O,  { s0: 1.04, s1: 1.12, x0: 0, y0: 0, x1: 1, y1: -0.5 }],
  ["s-intro",  B.intro,  B.rule + O,   { s0: 1.10, s1: 1.02, x0: 1, y0: 0, x1: 0, y1: 0 }],
  ["s-rule",   B.rule,   B.l1 + O,     { s0: 1.00, s1: 1.07, x0: 0, y0: 0, x1: 0, y1: 0.6 }],
  ["s-l1",     B.l1,     B.gate + O,   { s0: 1.03, s1: 1.10, x0: 0, y0: 0, x1: 1, y1: 0 }],
  ["s-gate",   B.gate,   B.l2a + O,    { s0: 1.02, s1: 1.12, x0: 0, y0: 0, x1: 0, y1: 1 }],
  ["s-l2a",    B.l2a,    B.l2b + O,    { s0: 1.08, s1: 1.18, x0: 0, y0: 0, x1: -1, y1: 1 }],
  ["s-l2b",    B.l2b,    B.l3a + O,    { s0: 1.02, s1: 1.08, x0: 0, y0: 0, x1: 0.5, y1: 0 }],
  ["s-l3a",    B.l3a,    B.l3b + O,    { s0: 1.04, s1: 1.12, x0: 0, y0: 0, x1: -1, y1: 0 }],
  ["s-l3b",    B.l3b,    B.fix + O,    { s0: 1.10, s1: 1.02, x0: -1, y0: 0, x1: 0, y1: 0 }],
  ["s-fix",    B.fix,    B.refill + O, { s0: 1.02, s1: 1.09, x0: 0, y0: 0, x1: 0, y1: -1 }],
  ["s-refill", B.refill, B.pay + O,    { s0: 1.04, s1: 1.10, x0: 0, y0: 0, x1: -1, y1: 0 }],
  ["s-pay",    B.pay,    B.close + O,  null],
  ["s-close",  B.close,  DURATION + 1, null],
];

// Elements that fade in at t0 and out at t1. [id, t0, t1, {dx, dy, scale, stamp}]
const ELEMENTS = [
  ["phone", 0.3, B.flow + O, { dy: 40 }],
  ["tap", W("She taps", "Refill") - 0.1, W("She taps", "mobile"), { scale: 1.3 }],
  ["app-cap", W("She taps", "triggers"), B.flow + O, { dy: 16 }],
  ["flow-count", W("That one", "sets"), B.intro + O, { dy: 20 }],
  ["flow-graph", B.flow + 0.2, W("One it", "One") + 0.2, { dy: 30, scale: 0.96 }],
  ["flow-cap", W("Sentry tracks", "checks"), W("One it", "One") + 0.2, { dy: 14 }],
  ["flow-risk", W("One it", "One") - 0.1, B.intro + O, {}],
  ["flow-ring", W("One it", "risky") - 0.2, B.intro + O, { scale: 1.5 }],
  ["intro-card", B.intro + 0.3, B.rule + O, {}],
  ["intro-cortex", W("Sentry is data", "monitors"), B.rule + O, { dy: 10 }],
  ["p1", W("It's built", "lineage"), B.rule + O, { dy: 26 }],
  ["p2", W("It's built", "classification"), B.rule + O, { dy: 26 }],
  ["p3", W("It's built", "risk"), B.rule + O, { dy: 26 }],
  ["rule-text", W("Let's take", "personal") - 0.2, B.l1 + O, { dy: 12 }],
  ["policy", L("Let's take").end - 0.4, B.fix + O, { dy: -14 }],
  ["pills", B.l1 + 0.3, B.refill + O, { dy: 14 }],
  ["l1-chip", B.l1 + 0.3, B.gate + O, { dx: 30 }],
  ["l1-record", W("In Rosa's", "record") - 0.3, B.gate + O, { dx: -30 }],
  ["l1-tag-name", W("In Rosa's", "name"), B.gate + O, { dx: -20 }],
  ["l1-tag-mrn", W("In Rosa's", "medical"), B.gate + O, { dx: -20 }],
  ["l1-dest", W("Both are", "external"), B.gate + O, { dy: 26 }],
  ["l1-alert", W("Both are", "Sending"), B.gate + O, { dy: 26 }],
  ["l1-stamp", W("Both are", "blocks"), W("With full context at", "local") - 0.4, { stamp: true }],
  ["gate-cap", W("With full context at", "local"), L("But what happens").start - 0.1, { dy: 16 }],
  ["gate-q", L("But what happens").start, B.l2a + O, { dy: 16 }],
  ["l2-chip", B.l2a + 0.3, B.l2b + O, { dx: 30 }],
  // The OPID table goes up as the out-of-context number is described ("At release, it's out of context").
  ["l2-opid", L("At release").start, B.l2b + O, { dy: 26 }],
  ["l2-cap", L("Local enforcement would").start, B.l2b + O, { dy: 16 }],
  ["l2b-chip", B.l2b, B.l3a + O, {}],
  ["thread", W("But Sentry's", "lineage"), B.l3a + O, {}],
  ["l2-tag-grey", B.l2b + 0.5, W("But Sentry's", "medical") + 0.6, {}],
  ["l2-tag-red", W("But Sentry's", "medical") + 0.3, B.l3a + O, { scale: 1.12 }],
  ["l2-origin", W("But Sentry's", "number", 1) - 0.4, B.l3a + O, { dy: 12 }],
  ["l2-mrn", L("It's still").start, B.l3a + O, { dy: 26 }],
  ["l2-stamp", W("It's still", "blocked"), B.l3a + O, { stamp: true }],
  ["l3-recap", B.l3a + 0.3, L("A nightly").start - 0.6, { dy: 20 }],
  ["l3-q", L("But what if").start, L("A nightly").start - 0.6, { dy: 12 }],
  ["l3-chip", W("But what if", "session") - 0.2, B.l3b + O, { dx: 30 }],
  ["l3-s1", W("But what if", "session"), B.l3b + O, { dy: 20 }],
  ["l3b-chip", B.l3b, B.fix + O, {}],
  ["l3-s2", W("A nightly", "nightly"), B.fix + O, { dy: -16 }],
  ["l3-link", L("Sentry's cross-session").start, B.fix + O, { dy: 26 }],
  ["l3-stamp", W("Sentry's cross-session", "blocked"), B.fix + O, { stamp: true }],
  ["fix-role", L("Sentry doesn't").start, W("Once the", "fixed"), { dy: 16 }],
  ["fix-payload", W("Once the", "fixed"), B.refill + O, { dx: -30 }],
  ["fix-stamp", W("Once the", "through"), B.refill + O, { stamp: true }],
  ["refill-cap", W("This way", "refill"), B.pay + O, { dy: 16 }],
  ["pay-foot", W("Local enforcement sees", "Local"), B.close + O, { dy: 12 }],
  ["pay-foot2", W("Local enforcement sees", "protecting"), B.close + O, { dy: 10 }],
  ["close-card", B.close + 0.2, DURATION + 1, { scale: 0.96 }],
  ["close-live", W("Sentry with", "live") - 0.2, DURATION + 1, { dy: 14 }],
  ["close-ibm", W("Sentry with", "release"), DURATION + 1, {}],
];

const PILLS = [["l1", B.l1, B.l2a], ["l2", B.l2a, B.l3a], ["l3", B.l3a, B.fix], ["fix", B.fix, B.pay]];

// Payoff rows reveal in the pause before the closing line, one every 0.4s.
const ROWS = ["r1", "r2", "r3", "r4"].map((id, k) => [id, B.pay + 0.3 + 0.4 * k]);

function applyElement(id, t0, t1, fx, t) {
  const el = $(id);
  if (!el) return;
  const o = win(t, t0, t1, fx.stamp ? 0.12 : 0.45);
  const p = ramp(t, t0, fx.stamp ? 0.18 : 0.55);
  const base = el.dataset.base ?? (el.dataset.base = el.style.transform || "");
  let extra;
  if (fx.stamp) {
    extra = ` scale(${1.9 - 0.9 * clamp((t - t0) / 0.18)})`;   // slam from 1.9x to 1x
  } else {
    const k = 1 - p;
    extra = ` translate(${(fx.dx || 0) * k}px, ${(fx.dy || 0) * k}px) scale(${1 + ((fx.scale || 1) - 1) * k})`;
  }
  el.style.opacity = o;
  el.style.transform = `${base}${extra}`;
}

function seek(t) {
  for (const [id, t0, t1, kb] of SCENES) {
    const sc = $(id);
    const o = win(t, t0, t1, 0.35);
    sc.style.opacity = o;
    sc.style.visibility = o > 0 ? "visible" : "hidden";
    if (kb && o > 0) {
      const u = clamp((t - t0) / (t1 - t0));
      const s = kb.s0 + (kb.s1 - kb.s0) * u;
      const x = kb.x0 + (kb.x1 - kb.x0) * u;
      const y = kb.y0 + (kb.y1 - kb.y0) * u;
      sc.querySelector(".bg").style.transform = `translate(${x}%, ${y}%) scale(${s})`;
    }
  }

  for (const [id, t0, t1, fx] of ELEMENTS) applyElement(id, t0, t1, fx, t);

  // The counter lands on 26 as "twenty-six" is spoken.
  const t26 = W("That one", "twenty-six");
  $("count-n").textContent = String(Math.round(1 + 25 * ramp(t, W("That one", "sets"), t26 - W("That one", "sets") + 0.2)));
  // The risk layer replaces the plain graph as "One, it marks as risky" is spoken.
  $("flow-graph").style.opacity = Math.min(win(t, B.flow + 0.2, B.intro + O), 1 - ramp(t, W("One it", "One") - 0.1, 0.5));
  const tRing = W("One it", "risky") - 0.2;
  if (t > tRing) $("flow-ring").style.transform = `scale(${1 + 0.08 * Math.sin((t - tRing) * 6)})`;

  const dims = {
    "s-flow": 0.55 * win(t, B.flow + 0.2, B.intro + O),
    "s-intro": 0.45 * win(t, B.intro + 0.3, B.rule + O),
    "s-l1": 0.4 * win(t, W("In Rosa's", "record") - 0.3, B.gate + O),
    "s-gate": 0.25 * win(t, W("With full context at", "local"), B.l2a + O),
    "s-l2a": 0.3 * win(t, L("At release").start, B.l2b + O),
    "s-l2b": 0.2 * win(t, L("It's still").start, B.l3a + O),
    "s-l3a": 0.55 * win(t, B.l3a + 0.3, L("A nightly").start - 0.6),
    "s-l3b": 0.3 * win(t, L("Sentry's cross-session").start, B.fix + O),
    "s-fix": 0.35 * win(t, L("Sentry doesn't").start, B.refill + O),
  };
  for (const [id, d] of Object.entries(dims)) $(id).querySelector(".dim").style.opacity = d;

  // Level 2: the thread draws back from the knight's lantern, hop by hop, as "four hops back" is spoken.
  const tFour = W("But Sentry's", "four");
  const path = $("thread-path");
  const len = path.getTotalLength();
  path.style.strokeDasharray = len;
  path.style.strokeDashoffset = len * (1 - ramp(t, tFour - 0.6, 2.6));
  document.querySelectorAll("#hops .hop").forEach((c, k) => (c.style.opacity = ramp(t, tFour - 0.1 + k * 0.45, 0.2)));
  const origin = $("thread-origin");
  if (origin) origin.style.opacity = ramp(t, W("But Sentry's", "number", 1) - 0.1, 0.25);
  // The grey "operational ID" tag gives way to the red MRN tag as "medical record number" is spoken.
  $("l2-tag-grey").style.opacity = Math.min(win(t, B.l2b + 0.5, B.l3a + O), 1 - ramp(t, W("But Sentry's", "medical") + 0.3, 0.35));

  for (const [id, t0] of ROWS) $(id).style.opacity = ramp(t, t0, 0.3);

  for (const [p, t0, t1] of PILLS) {
    document.querySelector(`.pill[data-p="${p}"]`).classList.toggle("on", t >= t0 && t < t1);
  }

  $("fade").style.opacity = ramp(t, DURATION - 0.8, 0.8);
}

window.DURATION = DURATION;
window.seek = seek;
window.CUES = Object.fromEntries(ELEMENTS.map(([id, t0]) => [id, +t0.toFixed(2)]));
document.fonts.ready.then(() => { window.layout?.(); window.ready = true; seek(0); });
