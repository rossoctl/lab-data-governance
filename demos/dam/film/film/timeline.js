// Timeline for the Sentry on DAM film. As in the pitch film, every cue is anchored to a spoken word
// (W(line, "word")) or a narration line (L(line)), from Kokoro's own timestamps (cues.js). The recorded
// DAM session (FILM.video, one continuous take) is cut into speed-ramped segments, each anchored to the
// narration line that describes it: typing at speed, the agent's work fast-forwarded, then the result
// held at its own pace while the camera moves in on the answer.
// The page exposes window.seek(t), which returns a promise once the video sits on its frame.
const WORDS = window.WORDS;
const LINES = window.LINES;
const FILM = window.FILM;
const S = FILM.steps;

const norm = (s) => s.toLowerCase().replace(/[^a-z0-9-]/g, "");
const opening = (text, n) => (text.match(/[A-Za-z0-9'-]+/g) || []).slice(0, n).map(norm).join(" ");
function lineNo(key) {
  const n = key.split(" ").length;
  const hits = LINES.filter((l) => opening(l.text, n) === opening(key, n));
  if (hits.length !== 1) throw new Error(`line key "${key}" matches ${hits.length} narration lines`);
  return hits[0].n;
}
function W(key, word, k = 1) {
  const line = lineNo(key);
  const hits = WORDS.filter((w) => w.line === line && norm(w.word) === norm(word));
  if (hits.length < k) throw new Error(`cue not found: "${key}…" "${word}" #${k}`);
  return hits[k - 1].start;
}
const L = (key) => LINES[lineNo(key) - 1];
const NEXT = (key) => { const l = L(key); return LINES[l.n] ? LINES[l.n].start : l.end + (l.hold || 0) + 0.6; };
const END = LINES[LINES.length - 1].end;
const DURATION = END + 2.0;

const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
const ease = (x) => x * x * (3 - 2 * x);
const ramp = (t, t0, d) => ease(clamp((t - t0) / d));
const win = (t, t0, t1, d = 0.45) => Math.min(ramp(t, t0, d), 1 - ramp(t, t1 - d, d));
const lerp = (a, b, u) => a + (b - a) * u;
const $ = (id) => document.getElementById(id);

// ---- scenes ----------------------------------------------------------------------------------
const B = {
  title: 0,
  arch: L("Each agent works").start - 0.4,
  home: L("We start with a clean").start - 0.45,
  nosec: L("Before we add").start - 0.35,
  nosecUi: L("We ask it to list").start - 0.45,
  plug: L("This is where").start - 0.45,
  s1ui: L("Now, let's turn on").start - 0.45,
  aiac: L("aiac's onboarding").start - 0.4,
  aiacTest: L("Now, let's see what happens").start - 0.45,
  dgArch: L("Now each call").start - 0.4,
  dgArchEnd: L("Same search").start - 0.45,
  dgpol: L("Behind that reason").start - 0.4,
  dgpolEnd: L("And listing the open").start - 0.45,
  summary: L("Here is every").start - 0.45,
  close: L("In this demo").start - 0.5,
};
const O = 0.3;
const SCENES = [
  ["s-title",  B.title,  B.arch + O,      { s0: 1.03, s1: 1.10, x0: 0, y0: 0, x1: -1, y1: 0 }],
  ["s-arch",   B.arch,   B.home + O,      { s0: 1.06, s1: 1.12, x0: 0, y0: 0, x1: 0.5, y1: 0 }],
  ["s-ui",     B.home,   B.summary + O,   null],
  ["s-nosec",  B.nosec,  B.nosecUi + O,   { s0: 1.04, s1: 1.10, x0: 0, y0: 0, x1: 1, y1: 0 }],
  ["s-plug",   B.plug,   B.s1ui + O,      { s0: 1.05, s1: 1.12, x0: 0, y0: 0, x1: -0.5, y1: 0 }],
  ["s-aiac",   B.aiac,   B.aiacTest + O,  { s0: 1.03, s1: 1.10, x0: 0, y0: 0, x1: 1, y1: 0 }],
  ["s-dgarch", B.dgArch, B.dgArchEnd + O, null],
  ["s-dgpol",  B.dgpol,  B.dgpolEnd + O,  { s0: 1.04, s1: 1.10, x0: 0, y0: 0, x1: -1, y1: 0 }],
  ["s-summary", B.summary, B.close + O,   null],
  ["s-close",  B.close,  DURATION + 1,    null],
];

// ---- the recorded session: speed-ramped segments ---------------------------------------------
// Camera presets, in the recording's pixels (1920x1080 frames of a 1440x810 page at 4/3).
const CAM = {
  full:     { z: 1.0,  x: 960,  y: 540 },
  home:     { z: 1.12, x: 900,  y: 560 },
  composer: { z: 1.5,  x: 1296, y: 905 },
  answer:   { z: 1.18, x: 1296, y: 520 },
  desc:     { z: 1.45, x: 1320, y: 340 },
  settings: { z: 1.15, x: 1250, y: 540 },
  card:     { z: 1.3,  x: 1320, y: 820 },
};
// A segment maps film time to session time with keyframes [filmT, srcT, cam].
const SEGS = [];
function seg(keys, extra = {}) { SEGS.push({ keys, ...extra }); }
const WARN = (window.TIMING_WARNINGS = []);

// A prompt and its answer. The prompt types at its real speed from the asking line, the agent's
// work fast-forwards, and the answer (its tool list opened) holds from the result line until the
// op's last line ends.
function op(askKey, resKey, step, { result, by, what, call, until } = {}) {
  const st = S[step];
  const fa = L(askKey).start - 0.3;
  const fT = fa + 0.5 + (st.typed - st.typing);
  const fS = fT + 0.4;
  const fR = L(resKey).start - 0.15;
  if (fR < fS + 1.0) WARN.push(`${step}: answer at ${fR.toFixed(1)} but typing ends ${fS.toFixed(1)}`);
  const f3 = until ?? NEXT(resKey) - 0.2;
  const shown = st.expanded ?? st.done;
  const keys = [[fa, st.typing - 0.5, CAM.composer], [fT, st.typed, CAM.composer], [fS, st.sent, CAM.full],
    [Math.max(fR, fS + 0.5), shown, CAM.answer]];
  const fE = Math.max(fR, fS + 0.5) + (st.end - shown);
  if (fE < f3) keys.push([fE, st.end, CAM.answer], [f3, st.end, CAM.answer]);
  else keys.push([f3, st.end, CAM.answer]);
  seg(keys, { ff: [fS, Math.max(fR, fS + 0.5), (shown - st.sent) / Math.max(0.5, fR - fS)], result, by, what, call, resultAt: L(resKey).start - 0.05, until: f3 });
}
// A settings action, played evenly between keyframes [filmT, srcT, cam].
function act(keys, what) {
  const k0 = keys[0], k1 = keys[keys.length - 1];
  seg(keys, { ff: [k0[0], k1[0], (k1[1] - k0[1]) / (k1[0] - k0[0])], what });
}
const NX = (key) => NEXT(key) - 0.2;

// A clean DAM, then create, describe, connect.
act([[B.home, S.home.start, CAM.full], [L("We create").start - 0.2, S.home.end, CAM.home]], "A clean DAM");
act([[L("We create").start - 0.2, S.create.start, CAM.full], [L("We give").start - 0.2, S.create.end, CAM.full]], "Create the agent");
{
  const st = S.describe, fD = L("Issue triage assistant").start - 0.1;
  seg([[L("We give").start - 0.2, st.start, CAM.settings], [fD, st.typing, CAM.desc],
    [fD + (st.typed - st.typing), st.typed, CAM.desc], [NX("Issue triage assistant"), st.end, CAM.desc]], { what: "Its description" });
}
act([[L("Then we connect").start - 0.2, S.connect.start, CAM.full], [B.nosec + O, S.connect.end, CAM.full]], "Connect it to GitHub");
// Stage 0: no Sentry.
op("We ask it to list", "It calls three", "s0-list", { result: "allow", what: "List the open issues", until: NX("demo-public is") });
op("Now, let's ask it to close", "The agent complies", "s0-close", { result: "allow", what: "Close issue #1" });
op("Next, we ask it to search", "The search goes out", "s0-search", { result: "warn", what: "Search with a name and SSN" });
// Stage 1: aiac.
{
  const st = S["s1-config"], l1 = L("Now, let's turn on"), l2 = L("The gateway restarts");
  act([[B.s1ui, st.start, CAM.card], [l1.end, st.start + 9, CAM.card], [B.aiac + O, st.end, CAM.card]], "Network access · MCP policy enforcement on, dg off");
}
op("Now, let's see what happens", "This time, the call is denied", "s1-close", { result: "block", by: "aiac", what: "Close issue #1", call: 0, until: NX("The reason quotes") });
op("What about adding", "Denied as well", "s1-comment", { result: "block", by: "aiac", what: "Comment on issue #1", call: 1 });
op("And the search with", "Searching is a", "s1-search", { result: "warn", what: "Search with a name and SSN", until: NX("Keeping data") });
// Stage 1b: the owner allows commenting.
{
  const st = S["s2-desc"], fD = L("They edit the last").start - 0.1;
  seg([[L("Now, the owner").start - 0.3, st.start, CAM.settings], [fD, st.typing, CAM.desc], [fD + (st.typed - st.typing), st.typed, CAM.desc],
    [NX("aiac sees the change"), st.end, CAM.desc]], { what: "The owner edits the description", ff: [fD + (st.typed - st.typing), NX("aiac sees the change"), (st.end - st.typed) / Math.max(0.5, NX("aiac sees the change") - fD - (st.typed - st.typing))] });
}
op("Now, when we ask for the same", "this time, the comment", "s2-comment", { result: "allow", what: "Comment on issue #1" });
op("And closing the issue", "Still denied", "s2-close", { result: "block", by: "aiac", what: "Close issue #1", call: 4 });
// Stage 2: aiac + dg.
act([[L("Finally").start - 0.3, S["s3-dg"].start, CAM.card], [B.dgArch + O, S["s3-dg"].end, CAM.card]], "Network access · dg on");
op("Same search", "This time, the call is blocked", "s3-search", { result: "block", by: "dg", what: "Search with a name and SSN", call: 5, until: B.dgpol + O });
op("And listing the open", "Still works", "s3-list", { result: "allow", what: "List the open issues" });

function segAt(t) {
  let last = null;
  for (const s of SEGS) { if (t >= s.keys[0][0]) last = s; }
  return last;
}
function sample(s, t) {
  const k = s.keys;
  if (t <= k[0][0]) return { src: k[0][1], cam: k[0][2] };
  for (let i = 1; i < k.length; i++) {
    if (t <= k[i][0]) {
      const u = (t - k[i - 1][0]) / (k[i][0] - k[i - 1][0]);
      const c0 = k[i - 1][2], c1 = k[i][2], e = ease(clamp(u * 1.6));
      return { src: lerp(k[i - 1][1], k[i][1], u), cam: { z: lerp(c0.z, c1.z, e), x: lerp(c0.x, c1.x, e), y: lerp(c0.y, c1.y, e) } };
    }
  }
  const lastK = k[k.length - 1];
  return { src: lastK[1], cam: lastK[2] };
}
function setCam(c) {
  // The UI frame (film.html #ui-frame) is 1728x972: the 1920x1080 recording at 0.9, zoomed by c.z.
  const FW = 1728, FH = 972, base = FW / 1920, s = base * c.z;
  const tx = FW / 2 - c.x * s, ty = FH / 2 - c.y * s;
  const minX = FW - 1920 * s, minY = FH - 1080 * s;
  $("cam").style.transform = `translate(${clamp(tx, minX, 0)}px, ${clamp(ty, minY, 0)}px) scale(${s})`;
}

// ---- stages, bubbles, architecture ----------------------------------------------------------
const STAGES = [[0, B.home, B.s1ui], [1, B.s1ui, L("Finally").start - 0.3], [2, L("Finally").start - 0.3, DURATION + 1]];
const stageAt = (t) => (STAGES.find(([, a, b]) => t >= a && t < b) || STAGES[0])[0];
const CHIP = { 0: ["0", "none", "No Sentry"], 1: ["1", "aiac", "aiac"], 2: ["2", "dg", "aiac + dg"] };

const ALLOWED_V1 = ["issue_read", "list_issues", "search_issues"];
const DENIED_SHOWN = ["issue_write", "add_issue_comment", "update_issue_comment", "create_pull_request", "delete_repository"];
function toolsHTML(allowed, denied, total) {
  const rows = [...allowed.map((t) => `<div class="t ok" data-k>${t}</div>`), ...denied.map((t) => `<div class="t no" data-k>${t}</div>`)];
  return rows.join("") + `<div class="more" data-k>+ ${total - allowed.length - denied.length} more denied</div>` +
    `<div class="sum" data-k>${allowed.length} allowed · ${total - allowed.length} denied</div>`;
}
function regoHTML(text, { hl } = {}) {
  const lines = text.split("\n");
  const pick = [];
  const pkg = lines.find((l) => l.startsWith("package "));
  pick.push(`<span class="c"># Generated by aiac for agent issue-triage</span>`, `<span class="k">package</span> ${pkg.slice(8)}`, "");
  pick.push(`<span class="c"># The tools the agent's description grants.</span>`);
  for (const l of lines.filter((l) => l.startsWith("allow contains"))) {
    const name = l.match(/"(.*)"/)[1];
    const row = `<span class="k">allow</span> contains <span class="s">"${name}"</span>`;
    pick.push(hl === name ? `<span class="hl">${row}</span>` : row);
  }
  pick.push("", `<span class="c"># The tools the policy prohibits for this agent.</span>`);
  const denies = lines.filter((l) => l.startsWith("deny contains"));
  const shownDeny = ["issue_write", "update_issue_comment", "add_issue_comment", "delete_repository"];
  for (const name of shownDeny) if (denies.some((l) => l.includes(`"${name}"`))) pick.push(`<span class="k">deny</span> contains <span class="d">"${name}"</span>`);
  pick.push(`<span class="c"># … ${denies.length - shownDeny.filter((n) => denies.some((l) => l.includes(`"${n}"`))).length} more prohibited</span>`);
  return pick.join("\n");
}
function reasonHTML(text) {
  return text.replace(/^(github-mcp\.\S+ for agent \S+ is denied): /, "<b>$1</b>: ");
}

let archBig, archPlug, archDg;
const ALLNODES = ["agent", "rules", "pep", "creds", "github", "ctrl", "sentry", "aiac", "dg", "owner", "api", "kc", "records", "model", "llm"];
const show = (o) => Object.fromEntries(ALLNODES.map((n) => [n, o[n] ?? 1]));

// Elements that fade in at t0 and out at t1. [id, t0, t1, {dx, dy, scale, stamp}]
const ELEMENTS = [
  ["title-card", 0.5, B.arch + O, { dy: 24 }],
  ["arch-big", B.arch + 0.2, B.home + O, { scale: 0.97 }],
  ["arch-cap", W("Sentry builds", "finer") - 0.3, B.home + O, { dy: 14 }],
  ["nosec-cap", B.nosec + 0.3, B.nosecUi + O, { dy: 14 }],
  ["ui-head", B.home + 0.2, B.summary + O, { dy: -10 }],
  ["pills", B.home + 0.2, B.summary + O, { dy: -10 }],
  ["arch-plug", B.plug + 0.2, B.s1ui + O, { scale: 0.97 }],
  ["plug-cap", W("Each tool", "checked") - 0.2, B.s1ui + O, { dy: 14 }],
  ["aiac-desc", B.aiac + 0.3, B.aiacTest + O, { dx: 30 }],
  ["aiac-tools", W("Three tools", "Three") - 0.2, B.aiacTest + O, { dy: 20 }],
  ["aiac-rego", L("Here is the policy").start - 0.2, B.aiacTest + O, { dy: 20 }],
  ["b-rego", W("aiac sees the change", "rewrites") - 0.3, NX("aiac sees the change"), { dx: -20 }],
  ["arch-dg", B.dgArch + 0.2, B.dgArchEnd + O, { scale: 0.97 }],
  ["dgarch-cap", B.dgArch + 0.6, B.dgArchEnd + O, { dy: 14 }],
  ["dg-policy", B.dgpol + 0.3, B.dgpolEnd + O, { dx: 30 }],
  ["dg-found", L("dg classified").start - 0.2, B.dgpolEnd + O, { dx: 30 }],
  ["dg-rule", L("The rule is compiled").start - 0.2, B.dgpolEnd + O, { dx: 30 }],
  ["dg-stamp", W("So Sentry blocks", "blocks"), B.dgpolEnd + O, { stamp: true }],
  ["sum-title", B.summary + 0.2, B.close + O, { dy: 20 }],
  ["sum-table", B.summary + 0.4, B.close + O, { dy: 20 }],
  ["sum-key", L("With no Sentry").start, B.close + O, { dy: 10 }],
  ["close-card", B.close + 0.2, DURATION + 1, { scale: 0.96 }],
];

function applyElement(id, t0, t1, fx, t) {
  const el = $(id);
  if (!el) return;
  const o = win(t, t0, t1, fx.stamp ? 0.12 : 0.45);
  const p = ramp(t, t0, fx.stamp ? 0.18 : 0.55);
  const base = el.dataset.base ?? (el.dataset.base = el.style.transform || "");
  let extra;
  if (fx.stamp) {
    extra = ` scale(${1.9 - 0.9 * clamp((t - t0) / 0.18)})`;
  } else {
    const k = 1 - p;
    extra = ` translate(${(fx.dx || 0) * k}px, ${(fx.dy || 0) * k}px) scale(${1 + ((fx.scale || 1) - 1) * k})`;
  }
  el.style.opacity = o;
  el.style.transform = `${base}${extra}`;
}
function stagger(container, t0, step = 0.18) {
  container.querySelectorAll("[data-k]").forEach((r, k) => (r.style.opacity = ramp(window.__t, t0 + k * step, 0.25)));
}

const vid = $("vid");
let lastSrc = -1;
function setVideo(src) {
  if (Math.abs(src - lastSrc) < 1 / 120) return Promise.resolve();
  lastSrc = src;
  return new Promise((done) => {
    const ok = () => { vid.removeEventListener("seeked", ok); done(); };
    vid.addEventListener("seeked", ok);
    vid.currentTime = Math.max(0, src);
    setTimeout(ok, 3000);
  });
}

function seek(t) {
  window.__t = t;
  for (const [id, t0, t1, kb] of SCENES) {
    const sc = $(id);
    const o = win(t, t0, t1, 0.35);
    sc.style.opacity = o;
    sc.style.visibility = o > 0 ? "visible" : "hidden";
    if (kb && o > 0) {
      const u = clamp((t - t0) / (t1 - t0));
      sc.querySelector(".bg").style.transform = `translate(${lerp(kb.x0, kb.x1, u)}%, ${lerp(kb.y0, kb.y1, u)}%) scale(${lerp(kb.s0, kb.s1, u)})`;
    }
  }
  for (const [id, t0, t1, fx] of ELEMENTS) applyElement(id, t0, t1, fx, t);

  // dims under overlays
  const dims = { "s-arch": 0.62 * win(t, B.arch + 0.2, B.home + O), "s-plug": 0.6 * win(t, B.plug + 0.2, B.s1ui + O),
    "s-aiac": 0.4 * win(t, B.aiac + 0.3, B.aiacTest + O), "s-dgpol": 0.45 * win(t, B.dgpol + 0.2, B.dgpolEnd + O), "s-dgarch": 0, "s-title": 0.35 * win(t, 0.5, B.arch + O), "s-nosec": 0.25 * win(t, B.nosec + 0.3, B.nosecUi + O) };
  for (const [id, d] of Object.entries(dims)) $(id).querySelector(".dim").style.opacity = d;

  // the architecture: the base path draws in; then what Sentry adds appears, piece by piece
  const a0 = B.arch + 0.5;
  archBig.set({ show: show({ pep: 0, sentry: 0.12, aiac: 0.12, dg: 0.12, llm: 0.3 }), adds: 0.08, wire: { w5: 0.1, w6: 0.1, w7: 0.1, wc: 0.1, wd: 0.08, wl: 0.08 },
    lit: { w1: ramp(t, a0, 0.6), w2: ramp(t, a0 + 0.5, 0.4), w3: ramp(t, a0 + 0.8, 0.4), w4: ramp(t, a0 + 1.1, 0.6) } });
  const pPep = ramp(t, W("Sentry plugs", "enforcement") - 0.3, 0.6);
  const pCheck = ramp(t, W("Each tool", "checked") - 0.2, 0.9);
  const pSentry = ramp(t, L("Sentry has").start, 0.6);
  const pAiac = ramp(t, W("Sentry has", "automated") - 0.5, 0.6);
  const pDg = ramp(t, W("Sentry has", "data") - 0.4, 0.6);
  const pStage1 = ramp(t, L("Each tool").end, 0.6);
  archPlug.set({ show: show({ pep: 0.15 + 0.85 * pPep, sentry: 0.12 + 0.88 * pSentry, aiac: 0.12 + 0.88 * pAiac, dg: 0.12 + 0.88 * pDg - 0.6 * pStage1, llm: 0.3 + 0.7 * pAiac }),
    adds: 0.08 + 0.92 * pPep,
    wire: { w5: 0.15 + 0.85 * pCheck, w6: 0.15 + 0.85 * pAiac, w7: 0.2, wc: 0.15 + 0.85 * pPep, wd: 0.08 + 0.92 * pAiac, wl: 0.08 + 0.92 * pAiac },
    lit: { w1: 1, w2: 1, w3: 1, w4: 1, wc: pPep, w5: pCheck, w6: pCheck } });
  const pDgLit = ramp(t, B.dgArch + 0.6, 0.9);
  archDg.set({ show: show({ dg: 0.35 + 0.65 * pDgLit }), lit: { w1: 1, w2: 1, w3: 1, w4: 1, w5: 1, w6: 1, w7: pDgLit } });

  // the UI: stage chip, pills
  const stage = stageAt(t);
  const [n, cls, label] = CHIP[stage];
  $("ui-chip-n").textContent = n; $("ui-chip-n").className = `n ${cls}`; $("ui-chip-t").textContent = label;
  document.querySelectorAll("#pills .pill").forEach((p) => p.classList.toggle("on", Number(p.dataset.p) === stage));

  // the video
  const s = segAt(t);
  let pending = Promise.resolve();
  if (s) {
    const { src, cam } = sample(s, t);
    setCam(cam);
    pending = setVideo(src);
    $("ui-what").textContent = s.what || "";
    const [ff0, ff1, x] = s.ff || [0, 0, 1];
    $("ff").style.opacity = x > 1.6 ? win(t, ff0, ff1, 0.2) : 0;
    $("ff-x").textContent = String(Math.max(2, Math.round(x)));
    // result stamp and reason
    const r = s.result, show1 = s.resultAt ? win(t, s.resultAt, s.until, 0.15) : 0;
    $("st-allow").style.opacity = r === "allow" ? show1 : 0;
    $("st-block").style.opacity = r === "block" ? show1 : 0;
    $("st-warn").style.opacity = r === "warn" ? show1 : 0;
    for (const id of ["st-allow", "st-block", "st-warn"]) {
      const e = $(id);
      if (Number(e.style.opacity) > 0) e.style.transform = `rotate(-7deg) scale(${1.7 - 0.7 * clamp((t - s.resultAt) / 0.18)})`;
    }
    $("st-block-by").textContent = `DENIED BY ${s.by || "aiac"}`;
    const call = s.call != null ? FILM.calls[s.call] : null;
    const showReason = r === "block" && call ? win(t, s.resultAt + 0.5, s.until, 0.3) : 0;
    $("b-reason").style.opacity = showReason;
    if (call) {
      $("reason").innerHTML = reasonHTML(call.reason.length > 260 ? call.reason.slice(0, call.reason.indexOf(": {") > 0 ? call.reason.indexOf(": {") : 260) + "." : call.reason);
      $("reason-tag").textContent = s.by === "dg" ? "dg · reason given" : "aiac · reason given";
      $("reason-tag").className = `tag ${s.by}`;
    }
  }

  // the summary table: each configuration's column comes in as the narration names it
  const colAt = [L("With no Sentry").start - 0.2, W("With aiac", "agent") - 0.4, W("With aiac", "until") - 0.2, L("With dg").start - 0.2];
  document.querySelectorAll("#sum-table [data-col]").forEach((e) => (e.style.opacity = 0.12 + 0.88 * ramp(t, colAt[Number(e.dataset.col)], 0.5)));

  // bubbles
  stagger($("tools-big"), W("Three tools", "Three") - 0.1, 0.16);
  $("b-tools").style.opacity = 0;

  $("fade").style.opacity = ramp(t, DURATION - 0.9, 0.9);
  return pending;
}

const SUM_COLS = [["No Sentry", "a clean DAM"], ["aiac", "first description"], ["aiac", "comments allowed"], ["aiac + dg", ""]];
// [label, class, shown in the demo, small]
const OK = (shown) => ["Allowed", "ok", shown, ""];
const NO = (shown, by = "aiac") => ["Denied", "no", shown, `by ${by}`];
const LEAK = (shown) => ["Allowed", "leak", shown, "personal data leaves"];
const SUM_ROWS = [
  ["List the open issues", [OK(true), OK(false), OK(false), OK(true)]],
  ["Close issue #1", [OK(true), NO(true), NO(true), NO(false)]],
  ["Comment on issue #1", [OK(false), NO(true), OK(true), OK(false)]],
  ["Search with a name and SSN", [LEAK(true), LEAK(true), LEAK(false), ["Blocked", "no", true, "by dg"]]],
];
function sumHTML() {
  const head = `<tr><th class="row"></th>${SUM_COLS.map(([a, b], i) => `<th data-col="${i}">${a}${b ? `<small>${b}</small>` : ""}</th>`).join("")}</tr>`;
  const rows = SUM_ROWS.map(([name, cells]) => `<tr><th class="row">${name}</th>${cells.map(([l, c, shown, sm], i) =>
    `<td data-col="${i}" class="${c}${shown ? " shown" : ""}">${l}${sm ? `<small>${sm}</small>` : ""}</td>`).join("")}</tr>`).join("");
  return `<table class="sum"><colgroup><col style="width:24%"><col style="width:19%"><col style="width:19%"><col style="width:19%"><col style="width:19%"></colgroup>${head}${rows}</table>`;
}

window.layout = () => {
  $("sum-table").innerHTML = sumHTML();
  archBig = Arch.mount($("arch-big"));
  archPlug = Arch.mount($("arch-plug"));
  archDg = Arch.mount($("arch-dg"));
  $("tools-big").innerHTML = toolsHTML(ALLOWED_V1, DENIED_SHOWN, 46);
  $("rego-big").innerHTML = regoHTML(FILM.regoV1);
  $("rego").innerHTML = regoHTML(FILM.regoV2, { hl: "add_issue_comment" });
};

for (const w of WARN) console.warn("timing:", w);
window.DURATION = DURATION;
window.seek = seek;
vid.src = FILM.video;
Promise.all([document.fonts.ready, new Promise((ok) => (vid.readyState >= 1 ? ok() : vid.addEventListener("loadedmetadata", ok, { once: true })))])
  .then(() => { window.layout(); return seek(0); })
  .then(() => { window.ready = true; });
