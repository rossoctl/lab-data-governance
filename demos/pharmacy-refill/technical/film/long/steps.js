// steps.js — the drill-in stage shared by P4–P7: a frame of the short → the camera pushes through its `door` → a sequence
// of Sentry stills (1920x1080 at 2x), each with a camera framing and one ring + label → the camera pulls back out through
// the door of the `back` frame. A part's stage.html defines SPEC (frames, stills, boxes, labels) and loads this file;
// the times come from film4.py (render.js) as ?cues=<json>: {zoomEnd, steps: [start, …], pull, pullEnd, end}.
// Opt-in (v4): a step's `seq: {dir, n}` plays a recorded frame sequence (dir/0001.png …, 30 fps, 1920x1080) from its
// start; a step's `ff` shows a fast-forward badge; SPEC.tag replaces the top tag's text; `veil` (step or SPEC) darkens
// what the ring leaves out (default .45).
// Every frame is a pure function of time: window.render(t).
const C = Object.assign({ zoomEnd: 1.2, steps: SPEC.steps.map((_, i) => 1.4 + 3 * i) },
  JSON.parse(decodeURIComponent((location.search.match(/cues=([^&]*)/) || [])[1] || '{}')));
if (C.pull === undefined) { C.pull = C.steps[C.steps.length - 1] + 3; C.pullEnd = C.pull + 1.2; C.end = C.pullEnd + .05; }

const IMGS = [...new Set(SPEC.steps.map(s => s.img))];   // one layer per distinct still
document.body.insertAdjacentHTML('beforeend', `
<style>
:root{--gold:#e5c268;--red:#ef5b47;--green:#5fd38a}
html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#0d0f11}
.layer{position:absolute;left:0;top:0;width:1920px;height:1080px;transform-origin:0 0;background-size:1920px 1080px;background-repeat:no-repeat;will-change:transform,opacity}
#tag{position:absolute;left:50%;top:18px;transform:translateX(-50%);padding:8px 18px;border-radius:18px;background:rgba(12,14,16,.88);z-index:9;
  font:700 17px 'JetBrains Mono',monospace;letter-spacing:.3em;color:var(--gold);opacity:0;white-space:nowrap}#tag i{font-style:normal;color:#d8d4c6;letter-spacing:.2em}
#ring{position:absolute;border:4px solid var(--gold);border-radius:10px;box-shadow:0 0 0 9999px rgba(4,10,12,.45);opacity:0;z-index:7}
#ring.red{border-color:var(--red)}#ring.green{border-color:var(--green)}
#lab{position:absolute;background:var(--gold);color:#1b1a16;padding:10px 18px 11px;border-radius:8px;font:800 30px/1.1 Archivo,sans-serif;z-index:8;
  box-shadow:0 10px 36px rgba(0,0,0,.45);opacity:0;max-width:640px}#lab.red{background:var(--red);color:#fff}#lab.green{background:var(--green);color:#0f1d14}
#lab small{display:block;font:600 18px/1.3 'Source Sans 3',sans-serif;margin-top:3px}
img.layer{width:1920px;height:1080px}
#ff{position:absolute;right:28px;top:18px;background:rgba(10,20,24,.9);border:2px solid var(--gold);color:var(--gold);border-radius:20px;padding:7px 15px;
  font:700 19px/1 'JetBrains Mono',monospace;opacity:0;z-index:9}
</style>
<div id="front" class="layer" style="background-image:url(${SPEC.front})"></div>
<div id="back" class="layer" style="background-image:url(${SPEC.back || SPEC.front})"></div>
${IMGS.map((im, i) => SPEC.steps.some(s => s.img === im && s.seq) ? `<img id="i${i}" class="layer" src="${im}" alt="">`
  : `<div id="i${i}" class="layer" style="background-image:url(${im})"></div>`).join('')}
<div id="ring"></div><div id="lab"></div><div id="tag">${SPEC.tag || 'UNDER THE HOOD · <i>live on rossoctl</i>'}</div><div id="ff"></div>`);
document.head.insertAdjacentHTML('beforeend', '<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@800&family=Source+Sans+3:wght@600&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet">');

const cl = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v)), ease = u => u < .5 ? 4 * u * u * u : 1 - Math.pow(-2 * u + 2, 3) / 2;
const seg = (t, a, b) => ease(cl((t - a) / (b - a))), lerp = (a, b, u) => a + (b - a) * u, lerpR = (A, B, u) => A.map((v, i) => lerp(v, B[i], u));
const $ = id => document.getElementById(id), FULL = [0, 0, 1920, 1080];
const put = (el, R, op) => { el.style.transform = `translate(${R[0]}px,${R[1]}px) scale(${R[2] / 1920},${R[3] / 1080})`; el.style.opacity = op; };
const onto = (g, S = FULL) => { const s = S[2] / g[2]; return [S[0] - g[0] * s, S[1] - g[1] * s, 1920 * s, 1080 * s]; };   // a layer whose region g fills S
const fit169 = r => { let w = r[2], h = r[3]; if (w / h > 16 / 9) h = w * 9 / 16; else w = h * 16 / 9; return [r[0] + r[2] / 2 - w / 2, r[1] + r[3] / 2 - h / 2, w, h]; };
const contain = r => { const w = Math.min(r[2], r[3] * 16 / 9), h = w * 9 / 16; return [r[0] + (r[2] - w) / 2, r[1] + (r[3] - h) / 2, w, h]; };
const inR = (R, g) => [R[0] + g[0] * R[2] / 1920, R[1] + g[1] * R[3] / 1080, g[2] * R[2] / 1920, g[3] * R[3] / 1080];
// a step's camera: the whole page, or a 16:9 window kept inside the page
const camOf = s => { if (!s.cam) return FULL; const r = fit169(s.cam), w = Math.min(r[2], 1920), h = w * 9 / 16;
  return [cl(r[0], 0, 1920 - w), cl(r[1], 0, 1080 - h), w, h]; };

function ring(R, s, op) {
  const r = $('ring'), l = $('lab'); const x0 = Math.max(R[0] - 8, 12), y0 = Math.max(R[1] - 8, 12);
  Object.assign(r.style, { left: x0 + 'px', top: y0 + 'px', width: Math.min(R[0] + R[2] + 8, 1908) - x0 + 'px', height: Math.min(R[1] + R[3] + 8, 1068) - y0 + 'px' });
  r.className = s.tone || ''; l.className = s.tone || ''; l.innerHTML = `${s.label || ''}${s.sub ? `<small>${s.sub}</small>` : ''}`;
  r.style.boxShadow = `0 0 0 9999px rgba(4,10,12,${s.veil ?? SPEC.veil ?? .45})`;   // opt-in (v4): a darker veil on a dense drawing
  if (!s.label && !s.sub) op = [op, 0];   // a ring without a label (v3)
  const W = l.offsetWidth, H = l.offsetHeight, side = s.side || 'left';
  const pos = { left: [R[0] - W - 28, R[1]], right: [R[0] + R[2] + 28, R[1]], below: [R[0], R[1] + R[3] + 24], above: [R[0], R[1] - H - 24] }[side];
  const [rop, lop] = Array.isArray(op) ? op : [op, op]; r.style.opacity = rop;
  Object.assign(l.style, { left: cl(pos[0], 24, 1896 - W) + 'px', top: cl(pos[1], 70, 1056 - H) + 'px', opacity: lop });
}

function render(t) {
  const S = SPEC.steps, T = C.steps, n = S.length;
  // the camera through the door of the front frame; the first still grows out of it
  const z = seg(t, 0, C.zoomEnd), fR = lerpR(FULL, onto(fit169(SPEC.door)), z);
  put($('front'), fR, t < C.pull ? 1 - seg(t, C.zoomEnd - .4, C.zoomEnd) : 0);
  const out = seg(t, C.pull, C.pullEnd), bR = lerpR(onto(fit169(SPEC.backDoor || SPEC.door)), FULL, out);
  put($('back'), bR, t < C.pull ? 0 : seg(t, C.pull, C.pull + .5));
  // which still and which camera: each step crossfades in its still and moves the camera to its framing
  let k = 0; for (let i = 0; i < n; i++) if (t >= T[i] - .35) k = i;
  // SPEC.drift (v3): each held still drifts in slowly (a share of its frame over the step), so no still is frozen
  const D = SPEC.drift || 0, shrink = (r, f) => [r[0] + r[2] * f / 2, r[1] + r[3] * f / 2, r[2] * (1 - f), r[3] * (1 - f)];
  const camAt = i => D ? shrink(camOf(S[i]), D * cl((t - T[i]) / ((i + 1 < n ? T[i + 1] : C.pull) - T[i]))) : camOf(S[i]);
  let cam = camAt(0);
  for (let i = 1; i < n; i++) cam = lerpR(cam, camAt(i), seg(t, T[i] - .45, T[i] + .25));
  let pageR = onto(cam);                                            // where the stills sit on screen
  if (t < C.zoomEnd) pageR = lerpR(onto(cam, contain(inR(fR, SPEC.door))), onto(cam), seg(t, C.zoomEnd - .45, C.zoomEnd + .2));
  if (t >= C.pull) pageR = lerpR(onto(cam), onto(cam, contain(inR(bR, SPEC.backDoor || SPEC.door))), out);
  const pageOp = t < C.zoomEnd - .45 ? 0 : t < C.pull + .6 ? 1 : 1 - seg(t, C.pull + .6, C.pullEnd);
  // step k crossfades from the previous step's still to its own (the same still: the camera only moves)
  const cur = IMGS.indexOf(S[k].img), prev = k ? IMGS.indexOf(S[k - 1].img) : cur, f = k ? seg(t, T[k] - .35, T[k]) : 1;
  IMGS.forEach((_, i) => put($('i' + i), pageR, pageOp * (i === cur ? (cur === prev ? 1 : f) : i === prev ? 1 - f : 0)));
  $('tag').style.opacity = t > C.zoomEnd && t < C.pull && !S[k].notag ? 1 : 0;
  // a recorded sequence plays from its step's start and holds its last frame (v4)
  S.forEach((s, i) => { if (!s.seq) return; const f = Math.floor(cl((t - T[i]) * 30, 0, s.seq.n - 1)) + 1;
    const el = $('i' + IMGS.indexOf(s.img)), src = `${s.seq.dir}/${String(f).padStart(4, '0')}.png`; if (i === k || i === k - 1) el.src = src; });
  $('ff').textContent = S[k].ff || ''; $('ff').style.opacity = S[k].ff && t > T[k] && t < (k + 1 < n ? T[k + 1] : C.pull) ? 1 : 0;   // notag: a step on our own drawing, not a live page
  // the ring of the current step, faded in after its camera settles and out before the next one moves
  const s = S[k], a = T[k] + .3, b = k + 1 < n ? T[k + 1] - .45 : C.pull - .1;
  if (s.ring && t >= a && t < b) ring(inR(pageR, s.ring), s, Math.min(seg(t, a, a + .3), 1 - seg(t, b - .3, b)));
  else $('ring').style.opacity = $('lab').style.opacity = 0;
}
window.CUES = C; window.render = render; render(0);
