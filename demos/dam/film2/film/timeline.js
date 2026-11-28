// Timeline for the second Sentry on DAM film: intent and company policy. Every cue is anchored to
// a spoken word (W) or a narration line (L), from Kokoro's own timestamps (cues.js).
// The take has one video per stream (Keycloak's admin console, Alice's and Bob's DAM, the
// operator's DAM), all on the take's clock. Alice and Bob are shown side by side, the person
// acting in the larger pane; Keycloak and the operator fill the frame. Segments map film time to
// take time, speed-ramped as in the first film: typing at its real speed, the agent's work
// fast-forwarded, the answer held. The page exposes window.seek(t), which returns a promise once
// every video sits on its frame.
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
const NX = (key) => NEXT(key) - 0.2;
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
  arch: L("This is how DAM").start - 0.4,
  keycloak: L("Our demo has").start - 0.45,
  cast: L("They share two").start - 0.45,
  login: L("Alice and Bob each").start - 0.45,
  act1: L("Act one").start - 0.4,
  act1ui: L("Alice asks the workbench").start - 0.45,
  unchecked: L("Every request went").start - 0.4,
  plug: L("This is where").start - 0.45,
  plugUi: L("Alice turns Sentry").start - 0.4,
  onboard: L("Sentry reads each").start - 0.4,
  act2: L("Act two").start - 0.4,
  act2ui: L("Alice asks for the").start - 0.45,
  policy: L("Now let's address").start - 0.3,
  split: L("Sentry takes in").start - 0.4,
  act3: L("Act three").start - 0.4,
  act3ui: L("Alice hands the issue").start - 0.45,
  summary: L("Here is every request").start - 0.45,
  close: L("A call goes through").start - 0.5,
};
const O = 0.3;
const ACTS_AT = [[B.act1, B.act1ui + O, 1], [B.act2, B.act2ui + O, 2], [B.act3, B.act3ui + O, 3]];
const SCENES = [
  ["s-title",     B.title,     B.arch + O,     { s0: 1.03, s1: 1.10, x0: 0, y0: 0, x1: -1, y1: 0 }],
  ["s-arch",      B.arch,      B.keycloak + O, { s0: 1.06, s1: 1.12, x0: 0, y0: 0, x1: 0.5, y1: 0 }],
  ["s-ui",        B.keycloak,  B.summary + O,  null],
  ["s-cast",      B.cast,      B.login + O,    { s0: 1.03, s1: 1.09, x0: 0, y0: 0, x1: 0.8, y1: 0 }],
  ["s-unchecked", B.unchecked, B.plug + O,     { s0: 1.04, s1: 1.10, x0: 0, y0: 0, x1: 1, y1: 0 }],
  ["s-plug",      B.plug,      B.plugUi + O,   { s0: 1.05, s1: 1.12, x0: 0, y0: 0, x1: -0.5, y1: 0 }],
  ["s-onboard",   B.onboard,   B.act2 + O,     { s0: 1.03, s1: 1.10, x0: 0, y0: 0, x1: 1, y1: 0 }],
  ["s-split",     B.split,     B.act3 + O,     { s0: 1.04, s1: 1.10, x0: 0, y0: 0, x1: -1, y1: 0 }],
  ["s-summary",   B.summary,   B.close + O,    { s0: 1.02, s1: 1.07, x0: 0, y0: 0, x1: -0.5, y1: 0 }],
  ["s-close",     B.close,     DURATION + 1,   { s0: 1.05, s1: 1.10, x0: 0, y0: 0, x1: 0.5, y1: 0 }],
];
const ACT_STILL = { 1: "d03_unchecked", 2: "d04_knight_arrives", 3: "d06_knight_stops" };

// ---- the take: speed-ramped segments ---------------------------------------------------------
// Camera points in the recording's pixels (1920x1080 frames of a 1440x810 page at 4/3).
const CAM = {
  full:     { z: 1.0,  x: 960,  y: 540 },
  composer: { z: 1.55, x: 1240, y: 900 },
  answer:   { z: 1.2,  x: 1240, y: 520 },
  desc:     { z: 1.35, x: 1250, y: 380 },
  people:   { z: 1.3,  x: 1250, y: 420 },
  card:     { z: 1.35, x: 1330, y: 860 },
  policy:   { z: 1.45, x: 1220, y: 470 },
  login:    { z: 1.25, x: 960,  y: 520 },
  kcUsers:  { z: 1.2,  x: 1100, y: 480 },
  kcRoles:  { z: 1.35, x: 1100, y: 470 },
};
const SEGS = [];
function seg(keys, extra = {}) { SEGS.push({ keys, ...extra }); }
const WARN = (window.TIMING_WARNINGS = []);

function op(askKey, resKey, step, { result, label, what, until } = {}) {
  const st = S[step];
  const fa = L(askKey).start - 0.3;
  const fT = fa + 0.5 + (st.typed - st.typing);
  const fS = fT + 0.4;
  const fR = L(resKey).start - 0.15;
  if (fR < fS + 1.0) WARN.push(`${step}: answer at ${fR.toFixed(1)} but typing ends ${fS.toFixed(1)}`);
  const f3 = until ?? NX(resKey);
  const shown = st.expanded ?? st.done;
  // The answer is held up to 1.5 s before the step's end: the next step by the same person opens
  // a new chat right after, and the step's last frames already show that page loading.
  const last = Math.max(shown, st.end - 1.5);
  const keys = [[fa, st.typing - 0.5, CAM.composer], [fT, st.typed, CAM.composer], [fS, st.sent, CAM.full],
    [Math.max(fR, fS + 0.5), shown, CAM.answer]];
  const fE = Math.max(fR, fS + 0.5) + (last - shown);
  if (fE < f3) keys.push([fE, last, CAM.answer], [f3, last, CAM.answer]);
  else keys.push([f3, last, CAM.answer]);
  const denied = (st.calls || []).filter((c) => c.decision === "deny").at(-1);
  seg(keys, { ff: [fS, Math.max(fR, fS + 0.5), (shown - st.sent) / Math.max(0.5, fR - fS)], result, label, what, user: st.user,
    reason: result === "block" && denied ? denied.reason : null, resultAt: L(resKey).start - 0.05, until: f3 });
}
function act(keys, what, user) {
  const k0 = keys[0], k1 = keys[keys.length - 1];
  seg(keys, { ff: [k0[0], k1[0], (k1[1] - k0[1]) / (k1[0] - k0[0])], what, user });
}

// Keycloak: the users, then each person's role.
{
  const k = S.keycloak;
  seg([[B.keycloak, k.users - 0.3, CAM.kcUsers], [L("Alice Mayer is").start - 0.3, k.users + 2.5, CAM.kcUsers]], { what: "Keycloak · the realm's users", user: "kc" });
  seg([[L("Alice Mayer is").start - 0.3, k.alice - 0.4, CAM.kcRoles], [L("Bob Segal is").start - 0.3, k.alice + 2.8, CAM.kcRoles]], { what: "Keycloak · alice's role: developer", user: "kc" });
  seg([[L("Bob Segal is").start - 0.3, k.bob - 0.4, CAM.kcRoles], [B.cast + O, k.bob + 3.0, CAM.kcRoles]], { what: "Keycloak · bob's role: tester", user: "kc" });
}
// Alice and Bob sign in, side by side.
{
  const a = S["login-alice"], b = S["login-bob"], l0 = B.login, mid = (l0 + NX("Alice and Bob each")) / 2 + 0.5;
  act([[l0, a.form - 0.3, CAM.login], [mid, a.home + 0.5, CAM.full]], "Alice signs in to DAM", "alice");
  act([[mid, b.form - 0.3, CAM.login], [NX("Alice and Bob each"), b.home + 0.5, CAM.full]], "Bob signs in to DAM", "bob");
}
// The agents, as Alice set them up: each one's description, then its people.
{
  const w = S["intro-w"], q = S["intro-q"], l = L("In DAM each agent"), mid = (l.start + NX("In DAM each agent")) / 2;
  seg([[l.start - 0.4, w.description - 0.3, CAM.desc], [l.start + 2.0, w.description + 2.5, CAM.desc], [l.start + 2.4, w.people + 0.3, CAM.people], [mid, w.end - 1.0, CAM.people]], { what: "issue-workbench · description and people", user: "alice" });
  seg([[mid, q.description - 0.3, CAM.desc], [mid + 2.0, q.description + 2.5, CAM.desc], [mid + 2.4, q.people + 0.3, CAM.people], [NX("In DAM each agent"), q.end - 1.0, CAM.people]], { what: "qa-signoff · description and people", user: "alice" });
}
// Act one: DAM as it is.
op("Alice asks the workbench", "Both calls go", "a1-handover", { result: "allow", what: "issue-workbench · hand over to Bob" });
op("Bob tests it", "That works too this", "a1-sendback", { result: "allow", what: "issue-workbench · send it back" });
op("Alice pushes another", "It closes but it", "a1-closehere", { result: "warn", label: "WRONG AGENT TO CLOSE", what: "issue-workbench · close #1" });
op("Next, Alice asks", "It closes and it", "a1-close", { result: "warn", label: "A DEVELOPER SIGNS OFF", what: "qa-signoff · close #1" });
op("Then Bob asks qa-signoff", "It is posted", "a1-signoff", { result: "warn", label: "PERSONAL DATA LEAVES", what: "qa-signoff · sign off", until: B.unchecked + O });
// Sentry on, for both agents.
{
  const st = S["sentry-on"], [w, q] = st.switched;
  act([[B.plugUi, w.at - 4, CAM.card], [B.plugUi + 2.5, w.at + 1, CAM.card], [B.plugUi + 3.2, q.at - 2.5, CAM.card],
    [B.onboard + O, q.at + 2, CAM.card]], "Network access · Sentry on, both agents", "alice");
}
// Act two: from each agent's description.
op("Alice asks for the", "Sentry checks both", "a2-handover", { result: "allow", what: "issue-workbench · hand over to Bob" });
op("Bob sends the issue back", "That is allowed too", "a2-sendback", { result: "allow", what: "issue-workbench · send it back" });
op("Again after another", "This time it is denied The reason", "a2-closehere", { result: "block", what: "issue-workbench · close #1" });
op("Bob then tries", "This is denied as", "a2-relabel", { result: "block", what: "qa-signoff · relabel #1" });
op("Then Alice asks", "This is allowed but", "a2-close", { result: "warn", label: "A DEVELOPER SIGNS OFF", what: "qa-signoff · close #1" });
op("With the issue reopened", "This is allowed too", "a2-signoff", { result: "warn", label: "PERSONAL DATA LEAVES", what: "qa-signoff · sign off", until: L("Now let's address").start - 0.3 });
// The company security policy, narrated as the operator types it.
{
  // The operator's page opens with the line that introduces the company policy, and holds until
  // the policy is typed as it is read.
  const st = S.policy, l = L("Developers and testers may"), fT = l.start;
  seg([[B.policy, st.start + 1.0, CAM.full], [fT - 0.6, st.typing - 0.3, CAM.policy], [fT, st.typing, CAM.policy],
    [fT + (st.typed - st.typing), st.typed, CAM.policy], [B.split + O, st.saved + 1.5, CAM.policy]], { what: "Operator · the company security policy", user: "dev" });
}
// Act three: plus the company security policy, in the story's order (the hand-over first, the
// two wrong requests, then the good sign-off), whatever order the take ran them in.
op("Alice hands the issue", "That still works", "a3-handover", { result: "allow", what: "issue-workbench · hand over to Bob" });
op("And Bob sends it", "That works too as", "a3-sendback", { result: "allow", what: "issue-workbench · send it back" });
op("After one more fix", "This time it is denied The user", "a3-close", { result: "block", what: "qa-signoff · close #1" });
op("Bob verifies the fix", "Denied the company security", "a3-signoff-email", { result: "block", what: "qa-signoff · sign off" });
op("Bob writes the sign-off", "Now the comment is", "a3-signoff", { result: "allow", what: "qa-signoff · sign off, then close", until: B.summary + O });

function segIndexAt(t) {
  let last = -1;
  SEGS.forEach((s, i) => { if (t >= s.keys[0][0]) last = i; });
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

// ---- the panes -------------------------------------------------------------------------------
const BIG = { w: 1210, h: 681 }, SMALL = { w: 560, h: 315 }, TOP = 150;
const RECTS = {
  alice: { alice: { x: 60, y: TOP, ...BIG }, bob: { x: 1300, y: TOP, ...SMALL } },
  bob: { alice: { x: 60, y: TOP, ...SMALL }, bob: { x: 650, y: TOP, ...BIG } },
};
const FULL = { x: 96, y: 96, w: 1728, h: 972 };
const lerpRect = (a, b, u) => ({ x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u), w: lerp(a.w, b.w, u), h: lerp(a.h, b.h, u) });
function place(id, r, o = 1) {
  const e = $(id);
  e.style.left = `${r.x}px`; e.style.top = `${r.y}px`; e.style.width = `${r.w}px`; e.style.height = `${r.h}px`; e.style.opacity = o;
}
function setCam(id, r, c) {
  const s = (r.w / 1920) * c.z;
  const tx = r.w / 2 - c.x * s, ty = r.h / 2 - c.y * s;
  const minX = r.w - 1920 * s, minY = r.h - 1080 * s;
  $(id).style.transform = `translate(${clamp(tx, minX, 0)}px, ${clamp(ty, minY, 0)}px) scale(${s})`;
}
function label(id, r, o) {
  const e = $(id);
  e.style.left = `${r.x + 4}px`; e.style.top = `${r.y - 46}px`; e.style.opacity = o;
}

const vids = { alice: $("vid-alice"), bob: $("vid-bob"), kc: $("vid-kc"), dev: $("vid-dev") };
const lastSrc = {};
function setVideo(who, src) {
  const v = vids[who];
  if (Math.abs(src - (lastSrc[who] ?? -1)) < 1 / 120) return Promise.resolve();
  lastSrc[who] = src;
  return new Promise((done) => {
    const ok = () => { v.removeEventListener("seeked", ok); done(); };
    v.addEventListener("seeked", ok);
    v.currentTime = Math.max(0, src);
    setTimeout(ok, 3000);
  });
}

// ---- acts, bubbles ---------------------------------------------------------------------------
const ACTS = [[1, B.keycloak, B.plug], [2, B.plug, B.policy], [3, B.policy, DURATION + 1]];
const actAt = (t) => (ACTS.find(([, a, b]) => t >= a && t < b) || ACTS[0])[0];
const CHIP = { 1: ["1", "none", "DAM as it is"], 2: ["2", "aiac", "Sentry · descriptions"], 3: ["3", "dg", "Sentry · + company policy"] };
const ACTCARD = { 1: ["Act one", "DAM as it is", "no Sentry"], 2: ["Act two", "Sentry, from each agent's description", "the agent's intent"], 3: ["Act three", "Sentry, plus the company security policy", "intent, the user and role, and the data"] };

const shortOp = (n) => n.replace(/^github-mcp\./, "").replace(/^issue_write\./, "issue_write · ");
function opsHTML(g) {
  const pick = ["add_issue_comment", "issue_write.label", "issue_write.close", "issue_write.assign", "issue_write.edit", "issue_read"];
  const allowed = new Set(g.allowed);
  return pick.map((n) => `<div class="t ${allowed.has(n) ? "ok" : "no"}" data-k>${shortOp(n)}</div>`).join("") +
    `<div class="sum" data-k>${g.allowed.length} allowed · ${g.denied} denied</div>`;
}
// One row per policy sentence: rules split from one sentence are shown once, with all they cover.
function mergedRules() {
  const by = new Map();
  for (const r of FILM.rules) {
    const m = by.get(r.text) ?? { text: r.text, roles: new Set(), holders: new Set(), on: [] };
    r.roles.forEach((x) => m.roles.add(x)); r.holders.forEach((x) => m.holders.add(x));
    m.on.push(r.agents.length ? `agent ${r.agents.join(", ")}` : r.operations.map(shortOp).join(" · "));
    by.set(r.text, m);
  }
  return [...by.values()];
}
function rulesHTML() {
  const rows = mergedRules().map((r) =>
    `<div class="rule" data-k><span class="q">“${r.text}”</span><span class="who2">access · ${[...r.roles].join(", ")}</span><span class="on">${r.on.join("  +  ")} · held by ${[...r.holders].join(", ")}</span></div>`);
  for (const d of FILM.dataRules)
    rows.push(`<div class="rule" data-k><span class="q">“${d.text}”</span><span class="who2">data governance</span><span class="on">classification · rules ${d.dg_rules.join(", ")}</span></div>`);
  return rows.join("");
}
function regoHTML() {
  const q = (x) => `<span class="s">"${x}"</span>`;
  const out = [`<span class="c"># data.dam.company: the rules, as Sentry wrote them into OPA</span>`];
  FILM.rules.forEach((r, i) => {
    out.push(`<span class="k">role_rules</span>[${i}] := {`);
    if (r.operations.length) out.push(`  "operations": [${r.operations.map((o) => q("github-mcp." + o)).join(", ")}],`);
    if (r.agents.length) out.push(`  "agents": [${r.agents.map(q).join(", ")}],`);
    out.push(`  "roles": [${r.roles.map(q).join(", ")}], "holders": [${r.holders.map(q).join(", ")}],`, `}`);
  });
  out.push("", `<span class="c"># every person the agent acts for must hold a role of each rule that covers the call</span>`,
    `<span class="k">role_denials contains</span> reason <span class="k">if</span> {`,
    `  <span class="k">some</span> [op, rule] <span class="k">in</span> covering`,
    `  <span class="k">some</span> person <span class="k">in</span> input.people`,
    `  <span class="k">not</span> person <span class="k">in</span> rule.holders`, `  reason := …`, `}`, "",
    `<span class="c"># data governance: DG-001, compiled to Rego (excerpt)</span>`,
    `<span class="k">triggered_rules contains</span> ${q("DG-001")} <span class="k">if</span> {`,
    `  <span class="k">some</span> item <span class="k">in</span> flow.data_items`,
    `  ${q("PII")} <span class="k">in</span> item.regulatory_tags`,
    `  dest.data_destination_trust_level == ${q("UNTRUSTED_EXTERNAL")}`, `}`);
  return out.join("\n");
}
// An agent's generated module, shortened: its package, the operations it grants, a few it
// prohibits, and how many reasons it carries.
function agentRegoHTML(name) {
  const text = (FILM.rego || {})[name] || "";
  const lines = text.split("\n");
  const q = (x) => `<span class="s">"${x}"</span>`;
  const pkg = (lines.find((l) => l.startsWith("package ")) || "package dam.generated.…").slice(8);
  const allow = lines.filter((l) => l.startsWith("allow contains")).map((l) => l.match(/"(.*)"/)[1]);
  const deny = lines.filter((l) => l.startsWith("deny contains")).map((l) => l.match(/"(.*)"/)[1]);
  const key = ["issue_write.close", "issue_write.label", "issue_write.assign", "issue_write.edit"].filter((n) => deny.includes(n));
  const reasons = lines.filter((l) => /^\t".*": /.test(l)).length;
  return [`<span class="c"># Generated from ${name}'s description</span>`, `<span class="k">package</span> ${pkg}`, "",
    ...allow.map((n) => `<span class="k">allow</span> contains ${q(n)}`), "",
    ...key.map((n) => `<span class="k">deny</span> contains <span class="d">"${n}"</span>`),
    `<span class="c"># … ${deny.length - key.length} more prohibited, ${reasons} reasons</span>`].join("\n");
}
function reasonHTML(text) {
  return text.replace(/^(github-mcp\.\S+ for agent [^:]+? is denied): /, "<b>$1</b>: ").replace(/: \{.*$/, ".");
}

let archBig, archPlug;
const ALLNODES = ["agent", "rules", "pep", "creds", "github", "ctrl", "sentry", "aiac", "dg", "owner", "api", "kc", "records", "model", "llm"];
const show = (o) => Object.fromEntries(ALLNODES.map((n) => [n, o[n] ?? 1]));

const ELEMENTS = [
  ["title-card", 0.5, B.arch + O, { dy: 24 }],
  ["arch-big", B.arch + 0.2, B.keycloak + O, { scale: 0.97 }],
  ["arch-cap", L("Each agent works").start, B.keycloak + O, { dy: 14 }],
  ["cast-head", B.cast + 0.2, B.login + O, { dy: 20 }],
  ["cast-agents", B.cast + 0.5, B.login + O, { dy: 20 }],
  ["ui-head", B.keycloak + 0.2, B.summary + O, { dy: -10 }],
  ["unchecked-cap", B.unchecked + 0.3, B.plug + O, { dy: 14 }],
  ["arch-plug", B.plug + 0.2, B.plugUi + O, { scale: 0.97 }],
  ["plug-cap", W("We've added an", "enforcement") - 0.2, B.plugUi + O, { dy: 14 }],
  ["onboard-ops", B.onboard + 0.3, B.act2 + O, { dy: 20 }],
  ["onboard-rego", L("Sentry writes each").start - 0.2, B.act2 + O, { dy: 20 }],
  ["split-rules", B.split + 0.3, B.act3 + O, { dx: -30 }],
  ["split-rego", L("On the right").start - 0.2, B.act3 + O, { dx: 30 }],
  ["sum-title", B.summary + 0.2, B.close + O, { dy: 20 }],
  ["sum-table", B.summary + 0.4, B.close + O, { dy: 20 }],
  ["sum-key", L("With DAM as it is").start, B.close + O, { dy: 10 }],
  ["close-card", B.close + 0.2, DURATION + 1, { scale: 0.96 }],
];

function applyElement(id, t0, t1, fx, t) {
  const el = $(id);
  if (!el) return;
  const o = win(t, t0, t1, 0.45);
  const p = ramp(t, t0, 0.55);
  const base = el.dataset.base ?? (el.dataset.base = el.style.transform || "");
  const k = 1 - p;
  el.style.opacity = o;
  el.style.transform = `${base} translate(${(fx.dx || 0) * k}px, ${(fx.dy || 0) * k}px) scale(${1 + ((fx.scale || 1) - 1) * k})`;
}
function stagger(container, t0, step = 0.18) {
  container.querySelectorAll("[data-k]").forEach((r, k) => (r.style.opacity = ramp(window.__t, t0 + k * step, 0.25)));
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
  // the act scenes: a still of their own, the act's card over it
  const actScene = ACTS_AT.find(([a, b]) => t >= a - 0.4 && t < b + 0.4);
  const sa = $("s-act");
  if (actScene) {
    const [a, b, n] = actScene, [tag, title, sub] = ACTCARD[n], o = win(t, a, b, 0.35);
    sa.style.opacity = o; sa.style.visibility = o > 0 ? "visible" : "hidden";
    $("act-bg").style.backgroundImage = `url(../stills/final/${ACT_STILL[n]}.png)`;
    $("act-bg").style.transform = `scale(${lerp(1.04, 1.1, clamp((t - a) / (b - a)))})`;
    $("act-tag").textContent = tag; $("act-t").textContent = title; $("act-s").textContent = sub;
    $("act-card").style.opacity = win(t, a + 0.3, b, 0.45);
  } else {
    sa.style.opacity = 0; sa.style.visibility = "hidden";
  }
  for (const [id, t0, t1, fx] of ELEMENTS) applyElement(id, t0, t1, fx, t);
  const dims = { "s-arch": 0.62 * win(t, B.arch + 0.2, B.keycloak + O), "s-plug": 0.6 * win(t, B.plug + 0.2, B.plugUi + O), "s-title": 0.35 * win(t, 0.5, B.arch + O),
    "s-unchecked": 0.25 * win(t, B.unchecked + 0.3, B.plug + O), "s-onboard": 0.45 * win(t, B.onboard + 0.3, B.act2 + O), "s-split": 0.62 * win(t, B.split + 0.2, B.act3 + O) };
  for (const [id, d] of Object.entries(dims)) $(id).querySelector(".dim").style.opacity = d;

  // the architecture: at first DAM alone, Sentry's pieces shaded; in the plug scene the policy
  // enforcement point, then Sentry and its two capabilities come in as the narration names them
  const a0 = B.arch + 0.5;
  archBig.set({ show: show({ pep: 0.12, sentry: 0.1, aiac: 0.1, dg: 0.1, llm: 0.25 }), adds: 0.06,
    wire: { w5: 0.08, w6: 0.08, w7: 0.08, wc: 0.08 },
    lit: { w1: ramp(t, a0, 0.6), w2: ramp(t, a0 + 0.5, 0.4), w3: ramp(t, a0 + 0.8, 0.4), w4: ramp(t, a0 + 1.1, 0.6) } });
  const pPep = ramp(t, W("We've added an", "enforcement") - 0.4, 0.6);
  const pCheck = ramp(t, W("We've added an", "checked") - 0.3, 0.9);
  const pSentry = ramp(t, L("Sentry's capabilities cover").start - 0.2, 0.6);
  const pAccess = ramp(t, W("Sentry's capabilities cover", "access") - 0.3, 0.5);
  const pGov = ramp(t, W("Sentry's capabilities cover", "governance") - 0.4, 0.5);
  archPlug.set({ show: show({ pep: 0.12 + 0.88 * pPep, sentry: 0.1 + 0.9 * pSentry, aiac: 0.1 + 0.9 * pAccess, dg: 0.1 + 0.9 * pGov, llm: 0.25 + 0.75 * pSentry }),
    adds: 0.06 + 0.94 * pPep,
    wire: { w5: 0.08 + 0.92 * pCheck, w6: 0.08 + 0.92 * pAccess, w7: 0.08 + 0.92 * pGov, wc: 0.08 + 0.92 * pPep },
    lit: { w1: 1, w2: 1, w3: 1, w4: 1, wc: pPep, w5: pCheck, w6: pCheck * pAccess, w7: pCheck * pGov } });

  // the cast: each agent's card stands out while it is described
  const tW = L("The first issue-workbench").start - 0.2, tQ = L("The second qa-signoff").start - 0.2, tEnd = NX("So once a fix");
  const onW = win(t, tW, tQ, 0.4), onQ = win(t, tQ, tEnd + 0.4, 0.4);
  const both = 1 - Math.max(onW, onQ);
  $("cast-w").style.opacity = 0.45 + 0.55 * Math.max(onW, both); $("cast-w").style.borderColor = `rgba(233, 196, 106, ${0.35 + 0.65 * onW})`;
  $("cast-q").style.opacity = 0.45 + 0.55 * Math.max(onQ, both); $("cast-q").style.borderColor = `rgba(233, 196, 106, ${0.35 + 0.65 * onQ})`;
  $("job-w").style.opacity = ramp(t, L("So Alice fixes").start - 0.2, 0.4);
  $("job-q").style.opacity = ramp(t, L("So once a fix").start - 0.2, 0.4);

  // the act chip
  const a = actAt(t);
  const [n, cls, labelText] = CHIP[a];
  $("ui-chip-n").textContent = n; $("ui-chip-n").className = `n ${cls}`; $("ui-chip-t").textContent = labelText;

  // the take
  const i = segIndexAt(t);
  const pending = [];
  if (i >= 0) {
    const s = SEGS[i];
    const { src, cam } = sample(s, t);
    $("ui-what").textContent = s.what || "";
    const full = s.user === "kc" || s.user === "dev";
    if (full) {
      place("pane-full", FULL, 1); place("pane-alice", RECTS.alice.alice, 0); place("pane-bob", RECTS.alice.bob, 0);
      vids.kc.style.opacity = s.user === "kc" ? 1 : 0; vids.dev.style.opacity = s.user === "dev" ? 1 : 0;
      setCam("cam-full", FULL, cam);
      pending.push(setVideo(s.user, src));
      label("lab-full", FULL, 0); label("lab-alice", RECTS.alice.alice, 0); label("lab-bob", RECTS.alice.bob, 0);
      $("lab-full-av").textContent = s.user === "kc" ? "K" : "O"; $("lab-full-av").className = `av ${s.user}`;
      $("lab-full-t").innerHTML = s.user === "kc" ? "Keycloak <small>the realm admin's console</small>" : "Operator <small>dev · DAM's Operator page</small>";
      $("pane-full").classList.add("active");
    } else {
      // the larger pane follows the person acting, moving over 0.6 s from the last person's
      const prev = SEGS.slice(0, i).reverse().find((x) => x.user === "alice" || x.user === "bob");
      const from = prev ? prev.user : s.user, u = from === s.user ? 1 : ease(clamp((t - s.keys[0][0]) / 0.6));
      const ra = lerpRect(RECTS[from].alice, RECTS[s.user].alice, u), rb = lerpRect(RECTS[from].bob, RECTS[s.user].bob, u);
      const before = SEGS[i - 1], fromFull = before && (before.user === "kc" || before.user === "dev") ? ramp(t, s.keys[0][0], 0.4) : 1;
      place("pane-alice", ra, fromFull); place("pane-bob", rb, fromFull); place("pane-full", FULL, 1 - fromFull);
      label("lab-alice", ra, fromFull); label("lab-bob", rb, fromFull); label("lab-full", FULL, 0);
      $("pane-alice").classList.toggle("active", s.user === "alice"); $("pane-bob").classList.toggle("active", s.user === "bob");
      setCam("cam-alice", ra, s.user === "alice" ? cam : CAM.full);
      setCam("cam-bob", rb, s.user === "bob" ? cam : CAM.full);
      pending.push(setVideo("alice", src), setVideo("bob", src));
    }
    const r = full ? FULL : RECTS[s.user][s.user];
    const [ff0, ff1, x] = s.ff || [0, 0, 1];
    $("ff").style.left = `${r.x + r.w - 150}px`; $("ff").style.top = `${r.y + 14}px`;
    $("ff").style.opacity = x > 1.6 ? win(t, ff0, ff1, 0.2) : 0;
    $("ff-x").textContent = String(Math.max(2, Math.round(x)));
    const res = s.result, show1 = s.resultAt ? win(t, s.resultAt, s.until, 0.15) : 0;
    for (const [id, kind] of [["st-allow", "allow"], ["st-block", "block"], ["st-warn", "warn"]]) {
      const e = $(id);
      e.style.left = `${r.x + 40}px`; e.style.top = `${r.y + r.h - 190}px`;
      e.style.opacity = res === kind ? show1 : 0;
      if (res === kind && show1 > 0) e.style.transform = `rotate(-7deg) scale(${1.5 - 0.6 * clamp((t - s.resultAt) / 0.18)})`;
    }
    $("st-warn-by").textContent = s.label || "PERSONAL DATA LEAVES";
    const showReason = res === "block" && s.reason ? win(t, s.resultAt + 0.5, s.until, 0.3) : 0;
    $("b-reason").style.left = `${r.x + 30}px`; $("b-reason").style.top = `${r.y + 30}px`;
    $("b-reason").style.opacity = showReason;
    if (s.reason) $("reason").innerHTML = reasonHTML(s.reason);
  } else {
    $("b-reason").style.opacity = 0;
  }

  // the bubbles
  stagger($("ops-w"), W("The workbench may", "workbench") - 0.2, 0.14);
  stagger($("ops-q"), W("The workbench may", "may", 2) - 0.2, 0.14);
  stagger($("rules"), W("Sentry takes in", "access") - 0.3, 0.9);

  // the summary table: each act's column comes in as the narration names it
  const colAt = [L("With DAM as it is").start - 0.2, L("With Sentry and each").start - 0.2, L("With the company security").start - 0.2];
  document.querySelectorAll("#sum-table [data-col]").forEach((e) => (e.style.opacity = 0.12 + 0.88 * ramp(t, colAt[Number(e.dataset.col)], 0.5)));

  $("fade").style.opacity = ramp(t, DURATION - 0.9, 0.9);
  return Promise.all(pending);
}

const SUM_COLS = [["Act one", "DAM as it is"], ["Act two", "Sentry · descriptions"], ["Act three", "Sentry · + company policy"]];
const OK = (shown, sm = "") => ["Allowed", "ok", shown, sm];
const NO = (shown, sm) => ["Denied", "no", shown, sm];
const WARNC = (shown, sm) => ["Allowed", "leak", shown, sm];
const SUM_ROWS = [
  ["Hand-over, Alice and Bob · issue-workbench", [OK(true), OK(true), OK(true)]],
  ["Bob closes · issue-workbench", [WARNC(true, "the wrong agent to close"), NO(true, "its description"), NO(false, "its description")]],
  ["Bob relabels · qa-signoff", [OK(false), NO(true, "its description"), NO(false, "its description")]],
  ["Alice closes · qa-signoff", [WARNC(true, "a developer signs off"), WARNC(true, "a developer signs off"), NO(true, "company policy: testers only")]],
  ["Sign-off with Alice's email · qa-signoff", [WARNC(true, "personal data leaves"), WARNC(true, "personal data leaves"), NO(true, "company policy: personal data")]],
  ["Clean sign-off by Bob · qa-signoff", [OK(false), OK(false), OK(true)]],
];
function sumHTML() {
  const head = `<tr><th class="row"></th>${SUM_COLS.map(([x, y], i) => `<th data-col="${i}">${x}<small>${y}</small></th>`).join("")}</tr>`;
  const rows = SUM_ROWS.map(([name, cells]) => `<tr><th class="row">${name}</th>${cells.map(([l, c, shown, sm], i) =>
    `<td data-col="${i}" class="${c}${shown ? " shown" : ""}">${l}${sm ? `<small>${sm}</small>` : ""}</td>`).join("")}</tr>`).join("");
  return `<table class="sum"><colgroup><col style="width:31%"><col style="width:23%"><col style="width:23%"><col style="width:23%"></colgroup>${head}${rows}</table>`;
}

window.layout = () => {
  $("sum-table").innerHTML = sumHTML();
  archBig = Arch.mount($("arch-big"));
  archPlug = Arch.mount($("arch-plug"));
  $("ops-w").innerHTML = opsHTML(FILM.granted["issue-workbench"]);
  $("ops-q").innerHTML = opsHTML(FILM.granted["qa-signoff"]);
  $("rego-w").innerHTML = agentRegoHTML("issue-workbench");
  $("rego-q").innerHTML = agentRegoHTML("qa-signoff");
  $("rules").innerHTML = rulesHTML();
  $("rego-rules").innerHTML = regoHTML();
};

for (const w of WARN) console.warn("timing:", w);
window.DURATION = DURATION;
window.seek = seek;
for (const [who, v] of Object.entries(vids)) v.src = FILM.videos[who];
Promise.all([document.fonts.ready, ...Object.values(vids).map((v) => new Promise((ok) => (v.readyState >= 1 ? ok() : v.addEventListener("loadedmetadata", ok, { once: true }))))])
  .then(() => { window.layout(); return seek(0); })
  .then(() => { window.ready = true; });
