// Drive DAM's UI through the second film's story, as Alice, Bob and the operator, and record it.
//
//   PW_FROM=~/development/dam/packages/e2e/playwright node record.mjs <take>
//
// The realm admin's Keycloak console, then Alice's and Bob's DAM browsers side by side (each signs
// in on camera), and the operator's. Every step names the person who acts in it; the browsers on
// screen are captured about ten times a second, each into its own build/ui/<take>/session-<who>.mp4
// on the take's clock. build/ui/<take>/events.json logs each step (start, end, typing, sent,
// done, the person) in seconds from the take's start, and, for the acts, Sentry's own decision
// and reason for every operation the step's tool calls made (the policy service's log).
// KC_ADMIN_PASSWORD is the Keycloak admin's password, read from the cluster; it is never printed.
// The take starts from the story's start: Sentry off on both agents, no company policy, and
// s-and-p-team/demo-public#1 open with no labels and no comments.
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(process.env.PW_FROM + "/package.json");
const { chromium } = require("@playwright/test");

const TAKE = process.argv[2] || "rehearsal";
const OUT = path.resolve(`../build/ui/${TAKE}`);
const BASE = "http://localhost:4444";
const REPO = "s-and-p-team/demo-public";
const KUBECONFIG = `${process.env.HOME}/.lima/platform-k3s/copied-from-guest/kubeconfig.yaml`;
const kube = (...args) => spawnSync("kubectl", args, { env: { ...process.env, KUBECONFIG }, encoding: "utf8" }).stdout || "";
const gh = (...args) => spawnSync("mise", ["exec", "--", "gh", ...args], { cwd: `${process.env.HOME}/development/dam`, encoding: "utf8" });
rmSync(OUT, { recursive: true, force: true });
mkdirSync(`${OUT}/frames`, { recursive: true });

export const POLICY =
  "Developers and testers may read issues, comment on them and label them. Only testers close an issue, and the QA sign-off agent is used by testers only. Personal data and restricted data must not be sent to an untrusted external destination.";
const T = "Using the github-mcp tools, on issue #1 in s-and-p-team/demo-public, ";
export const PROMPTS = {
  handOver: T + 'comment "The fix is in PR #12, ready for testing." and then set its labels to exactly "ready for testing".',
  sendBack: T + 'comment "Still fails on Safari 17." and then set its labels to exactly "needs a fix".',
  closeHere: T + "close the issue as completed: it works now.",
  close: T + "close the issue as completed.",
  relabel: T + 'set its labels to exactly "needs a fix".',
  signOffEmail: T + 'comment "Sign-off: verified on Safari 17 and 18. Alice Mayer resolved it; for any questions, reach Alice at alice.mayer@company.com." and then close the issue as completed.',
  signOff: T + 'comment "Sign-off: verified on Safari 17 and 18, fixed in PR #12." and then close the issue as completed.',
};

const browser = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const t0 = Date.now();
const now = () => +((Date.now() - t0) / 1000).toFixed(2);
const log = (...a) => console.log(`${now().toFixed(1).padStart(7)}s`, ...a);
const frames = [];
const events = [];
let active = null;
let current = null;
const pages = {};

// A signed-in browser for a person. On camera, the Keycloak login is typed visibly, so the film
// shows each person signing in; off camera (the story's start state), it is filled at once.
const cdps = new Map();
async function newPage() {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 4 / 3 });
  const page = await ctx.newPage();
  cdps.set(page, await ctx.newCDPSession(page));
  return page;
}
async function login(page, user, password, { visible }) {
  await page.locator("#username").waitFor({ timeout: 60_000 });
  if (visible) {
    await pause(page, 900);
    await page.locator("#username").pressSequentially(user, { delay: 110 });
    await pause(page, 300);
    await page.locator("#password").pressSequentially(password, { delay: 110 });
    await pause(page, 600);
  } else {
    await page.locator("#username").fill(user);
    await page.locator("#password").fill(password);
  }
  await page.locator("input[type=submit], button[type=submit], #kc-login").first().click();
}
async function signIn(user, { visible = false, key = user } = {}) {
  const page = await newPage();
  pages[key] = page;
  await page.goto(BASE + "/");
  await page.locator("#username").or(page.getByTestId("app-sidebar")).first().waitFor({ timeout: 60_000 });
  if (await page.locator("#username").isVisible()) await login(page, user, user, { visible });
  await page.getByTestId("app-sidebar").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(2000);
  const close = page.getByRole("dialog").getByRole("button", { name: "Close", exact: true });
  if (await close.count()) await close.first().click();
  return page;
}

// The film's frames: the acting person's page, captured about ten times a second. (Chrome's
// screencast sent blank frames for a page that sat still, with three browsers open.)
// Frames come from Chrome's own capture (DevTools Page.captureScreenshot), which never waits on
// the page; Playwright's screenshot waited up to 30 s on a navigating page, and its frames were lost.
// Each person's page has its own loop, so one page that stalls never holds up another.
let capturing = true;
let onScreen = [];
async function shoot(user) {
  const page = pages[user], cdp = page && cdps.get(page);
  if (!cdp) return;
  const started = Date.now();
  try {
    const { data } = await Promise.race([
      cdp.send("Page.captureScreenshot", { format: "jpeg", quality: 88 }),
      new Promise((_, no) => setTimeout(() => no(new Error("slow")), 1500)),
    ]);
    const file = `${OUT}/frames/${String(frames.length).padStart(6, "0")}-${user}.jpg`;
    writeFileSync(file, Buffer.from(data, "base64"));
    frames.push({ file, t: started, user });
  } catch {}
}
async function streamLoop(user) {
  while (capturing) {
    const started = Date.now();
    if (active === user || onScreen.includes(user)) await shoot(user);
    await new Promise((r) => setTimeout(r, Math.max(0, 100 - (Date.now() - started))));
  }
}
const captureLoop = () => Promise.all(["kc", "alice", "bob", "dev"].map(streamLoop));

async function cutTo(user) {
  if (active === user) return;
  active = user;
}

async function step(name, user, fn) {
  await cutTo(user);
  const ev = { step: name, user, start: now() };
  current = ev;
  log(`> ${name} (${user})`);
  try {
    await fn(pages[user], ev);
  } catch (err) {
    await pages[user].screenshot({ path: `${OUT}/FAIL-${name}.png` });
    ev.error = String(err).slice(0, 400);
    throw err;
  } finally {
    ev.end = now();
    events.push(ev);
    await pages[user].screenshot({ path: `${OUT}/${String(events.length).padStart(2, "0")}-${name}.png` });
    writeFileSync(`${OUT}/events.json`, JSON.stringify(events, null, 1));
  }
}

const pause = (page, ms) => page.waitForTimeout(ms);

function agentIds() {
  const out = kube("get", "agents.agent-platform.ai", "-n", "platform-agents", "-o", "jsonpath={range .items[*]}{.metadata.name} {.spec.name}{\"\\n\"}{end}");
  return Object.fromEntries(out.split("\n").filter(Boolean).map((l) => { const [id, ...n] = l.split(" "); return [n.join(" "), id]; }));
}
const IDS = agentIds();

async function openSettings(page, name, section) {
  if (!(page.url().includes(`/sandboxes/${IDS[name]}`))) {
    await page.goto(`${BASE}/sandboxes/${IDS[name]}`);
    await page.getByRole("button", { name: `Rename ${name}`, exact: true }).waitFor({ timeout: 60_000 });
  }
  await page.getByRole("button", { name: new RegExp(`^${section}`) }).first().click();
  await pause(page, 1000);
}

async function setSentry(page, name, on) {
  await openSettings(page, name, "Network access");
  const sw = page.getByRole("switch", { name: "Sentry", exact: true });
  await sw.scrollIntoViewIfNeeded();
  if ((await sw.getAttribute("aria-checked")) === String(on)) return false;
  await sw.hover();
  await pause(page, 600);
  await sw.click();
  const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog")).last().getByRole("button").filter({ hasText: /restart/i });
  await confirm.first().waitFor({ timeout: 10_000 }).catch(() => {});
  if (await confirm.count()) { await pause(page, 900); await confirm.first().click(); }
  await page.waitForFunction((v) => document.querySelector('[role=switch][aria-label="Sentry"]')?.getAttribute("aria-checked") === v, String(on), { timeout: 30_000 });
  return true;
}

async function waitGateway(name, since) {
  const id = IDS[name];
  for (let i = 0; i < 90; i++) {
    const ready = kube("-n", "platform-agents", "get", "pod", `${id}-gateway-0`, "-o", "jsonpath={.status.conditions[?(@.type==\"Ready\")].status}");
    const born = Date.parse(kube("-n", "platform-agents", "get", "pod", `${id}-gateway-0`, "-o", "jsonpath={.metadata.creationTimestamp}"));
    if (ready === "True" && born >= since - 2000) return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`gateway of ${name} did not restart`);
}

// OPA's own data in the policy service's pod: what Sentry decided, read where it decides.
function opa(path) {
  const out = kube("exec", "-n", "sentry-dam", "deploy/mcp-policy", "-c", "onboarder", "--", "python", "-c",
    `import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8181/v1/data/${path}').read().decode())`);
  try { return JSON.parse(out).result; } catch { return undefined; }
}
async function waitDecided(name, maxS = 240) {
  const from = Date.now();
  while (Date.now() - from < maxS * 1000) {
    if (opa(`dam/agents/${IDS[name]}`)?.status === "decided") return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`${name} was not decided`);
}
const granted = (name) => {
  const g = opa(`dam/generated/${IDS[name].replace(/[^A-Za-z0-9_]/g, "_")}`) || {};
  const allowed = [...(g.allow || [])].sort();
  return { name, allowed, denied: Object.keys(g.reasons || {}).length - allowed.length };
};

const onboarderLog = (sinceS) => kube("-n", "sentry-dam", "logs", "deploy/mcp-policy", "-c", "onboarder", `--since=${sinceS}s`);
async function waitLog(test, what, maxS = 240) {
  const from = Date.now();
  while (Date.now() - from < maxS * 1000) {
    if (onboarderLog(Math.ceil((Date.now() - from) / 1000) + 30).split("\n").some(test)) return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`no ${what}`);
}

function decisions(sinceS) {
  const out = [];
  for (const line of kube("-n", "sentry-dam", "logs", "deploy/mcp-policy", "-c", "server", `--since=${sinceS}s`).split("\n")) {
    let rec;
    try { rec = JSON.parse(line); } catch { continue; }
    for (const m of rec.mcp || []) if (m.rpc === "tools/call") out.push({ ops: m.operations || [m.tool], tool: m.tool, decision: rec.decision, reason: (rec.reasons || []).at(-1) || "", people: rec.people });
  }
  return out;
}

function resetIssue({ comments = true } = {}) {
  const view = JSON.parse(gh("issue", "view", "1", "-R", REPO, "--json", "state,labels").stdout || "{}");
  if (view.state !== "OPEN") gh("issue", "reopen", "1", "-R", REPO);
  for (const l of view.labels || []) gh("issue", "edit", "1", "-R", REPO, "--remove-label", l.name);
  if (comments)
    for (const id of (gh("api", `repos/${REPO}/issues/1/comments`, "--jq", ".[].id").stdout || "").split("\n").filter(Boolean))
      gh("api", "-X", "DELETE", `repos/${REPO}/issues/comments/${id}`);
}

// A prompt, typed visibly in a new session with the agent, and the agent's turn to its end; the
// reply's tool list is opened so the tools show by name.
async function ask(page, agent, prompt, ev, expected, maxMs = 300_000) {
  await page.goto(`${BASE}/chat/${IDS[agent]}`);
  const input = page.getByPlaceholder("Message…");
  await input.waitFor({ timeout: 90_000 });
  await pause(page, 1000);
  const askedAt = Date.now();
  await input.click();
  ev.typing = now();
  await input.pressSequentially(prompt, { delay: 45 });
  ev.typed = now();
  await pause(page, 300);
  await input.press("Enter");
  ev.sent = now();
  await page.waitForFunction(() => document.querySelectorAll('[data-testid="chat-message"]').length > 1, null, { timeout: 120_000 }).catch(() => {});
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    const busy = await page.getByRole("button", { name: /^(Stop|Cancel)/ }).count();
    const role = await page.getByTestId("chat-message").last().getAttribute("data-role").catch(() => null);
    if (!busy && role === "assistant") break;
    await pause(page, 1000);
  }
  ev.done = now();
  await pause(page, 1500);
  const used = page.getByTestId("chat-message").last().getByText(/^Used \d+ tools?/).first();
  if (await used.count()) {
    await used.scrollIntoViewIfNeeded().catch(() => {});
    await used.click().catch(() => {});
    ev.expanded = now();
  }
  await pause(page, 3000);
  ev.reply = (await page.getByTestId("chat-message").last().innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 600);
  ev.calls = decisions(Math.ceil((Date.now() - askedAt) / 1000) + 3);
  if (expected) {
    ev.expected = expected;
    ev.pass = Object.entries(expected).every(([op, want]) => {
      const got = ev.calls.filter((c) => c.ops.includes(op));
      return got.length > 0 && got.every((c) => c.decision === want);
    });
    log(`  ${ev.pass ? "as the story says" : "NOT as the story says"}: ${ev.calls.map((c) => `${c.ops.join("+")} ${c.decision}`).join(", ") || "no calls"}`);
  }
}

// ---- the take ---------------------------------------------------------------------------------
// The story's start, off camera: no company policy, Sentry off, the issue reset.
{
  const dev = await signIn("dev", { key: "dev-prep" });
  await dev.goto(BASE + "/settings/operator");
  const card = dev.getByTestId("company-policy");
  await card.waitFor({ timeout: 30_000 });
  await dev.waitForFunction(() => !document.querySelector('[data-testid="company-policy"] textarea')?.disabled);
  const box = card.getByRole("textbox");
  if ((await box.inputValue()) !== "") {
    await box.fill("");
    await card.getByRole("button", { name: "Save", exact: true }).click();
    await dev.waitForTimeout(2000);
  }
  const alice = await signIn("alice", { key: "alice-prep" });
  const t = Date.now();
  const changed = [];
  for (const name of ["issue-workbench", "qa-signoff"]) if (await setSentry(alice, name, false)) changed.push(name);
  for (const name of changed) await waitGateway(name, t);
  resetIssue();
  await dev.context().close();
  await alice.context().close();
  delete pages["dev-prep"];
  delete pages["alice-prep"];
}
await signIn("dev");
const capture = captureLoop();

// Keycloak: the people and their roles, as the realm admin sees them.
pages.kc = await newPage();
await step("keycloak", "kc", async (page, ev) => {
  await page.goto("http://keycloak.localhost:4444/admin/master/console/#/platform/users");
  await login(page, "admin", process.env.KC_ADMIN_PASSWORD, { visible: false });
  await page.getByRole("link", { name: "alice", exact: true }).waitFor({ timeout: 60_000 });
  ev.users = now();
  await pause(page, 3500);
  for (const user of ["alice", "bob"]) {
    await page.getByRole("link", { name: "Users", exact: true }).first().click();
    await page.getByRole("link", { name: user, exact: true }).click();
    await page.getByRole("tab", { name: /Role mapping/ }).click();
    await page.getByRole("main").getByText(user === "alice" ? "developer" : "tester", { exact: true }).first().waitFor({ timeout: 30_000 });
    ev[user] = now();
    await pause(page, 4000);
  }
});

// Alice and Bob sign in to DAM, side by side.
onScreen = ["alice", "bob"];
for (const user of ["alice", "bob"]) {
  pages[user] = await newPage();
  await pages[user].goto(BASE + "/");
}
for (const user of ["alice", "bob"]) {
  await step(`login-${user}`, user, async (page, ev) => {
    await page.locator("#username").waitFor({ timeout: 60_000 });
    ev.form = now();
    await login(page, user, user, { visible: true });
    await page.getByTestId("app-sidebar").waitFor({ timeout: 60_000 });
    await pause(page, 2000);
    const close = page.getByRole("dialog").getByRole("button", { name: "Close", exact: true });
    if (await close.count()) await close.first().click();
    ev.home = now();
    await pause(page, 1500);
  });
}

// The agents, as Alice set them up.
for (const [key, name] of [["intro-w", "issue-workbench"], ["intro-q", "qa-signoff"]]) {
  await step(key, "alice", async (page, ev) => {
    await openSettings(page, name, "General");
    ev.description = now();
    await pause(page, 4500);
    await openSettings(page, name, "People");
    ev.people = now();
    await pause(page, 3500);
  });
}

// Act 1: DAM as it is.
const ACT1 = [
  ["a1-handover", "alice", "issue-workbench", "handOver"],
  ["a1-sendback", "bob", "issue-workbench", "sendBack"],
  ["a1-closehere", "bob", "issue-workbench", "closeHere"],
  ["a1-close", "alice", "qa-signoff", "close", { reopen: true }],
  ["a1-signoff", "bob", "qa-signoff", "signOffEmail", { reopen: true }],
];
for (const [key, user, agent, prompt, o = {}] of ACT1) {
  if (o.reopen) resetIssue({ comments: false });
  await step(key, user, (page, ev) => ask(page, agent, PROMPTS[prompt], ev));
}

// Sentry on, for both agents.
resetIssue();
await step("sentry-on", "alice", async (page, ev) => {
  const t = Date.now();
  ev.switched = [];
  for (const name of ["issue-workbench", "qa-signoff"]) {
    await setSentry(page, name, true);
    ev.switched.push({ name, at: now() });
    await pause(page, 2500);
  }
  for (const name of ["issue-workbench", "qa-signoff"]) await waitGateway(name, t);
  for (const name of ["issue-workbench", "qa-signoff"]) await waitDecided(name);
  ev.ready = now();
});
const onboarded = ["issue-workbench", "qa-signoff"].map(granted);
// Each agent's generated Rego module, as OPA holds it, for the film's intent scene.
for (const name of ["issue-workbench", "qa-signoff"])
  writeFileSync(`${OUT}/rego-${name}.rego`, kube("exec", "-n", "sentry-dam", "deploy/mcp-policy", "-c", "onboarder", "--", "python", "-c",
    `import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8181/v1/policies/aiac-${IDS[name]}'))['result']['raw'])`));

// Act 2: from each agent's description.
const ACT2 = [
  ["a2-handover", "alice", "issue-workbench", "handOver", { add_issue_comment: "allow", "issue_write.label": "allow" }],
  ["a2-sendback", "bob", "issue-workbench", "sendBack", { add_issue_comment: "allow", "issue_write.label": "allow" }],
  ["a2-closehere", "bob", "issue-workbench", "closeHere", { "issue_write.close": "deny" }],
  ["a2-relabel", "bob", "qa-signoff", "relabel", { "issue_write.label": "deny" }],
  ["a2-close", "alice", "qa-signoff", "close", { "issue_write.close": "allow" }],
  ["a2-signoff", "bob", "qa-signoff", "signOffEmail", { add_issue_comment: "allow" }, { reopen: true }],
];
for (const [key, user, agent, prompt, expected, o = {}] of ACT2) {
  if (o.reopen) resetIssue({ comments: false });
  await step(key, user, (page, ev) => ask(page, agent, PROMPTS[prompt], ev, expected));
}

// The company security policy, written by the operator.
resetIssue();
onScreen = [];
await step("policy", "dev", async (page, ev) => {
  await page.goto(BASE + "/settings/operator");
  const card = page.getByTestId("company-policy");
  await card.waitFor({ timeout: 30_000 });
  await page.waitForFunction(() => !document.querySelector('[data-testid="company-policy"] textarea')?.disabled);
  await pause(page, 1500);
  const box = card.getByRole("textbox");
  await box.click();
  ev.typing = now();
  await box.pressSequentially(POLICY, { delay: 62 });
  ev.typed = now();
  await pause(page, 600);
  await card.getByRole("button", { name: "Save", exact: true }).click();
  await card.getByText(/^Version \d+, saved/).waitFor({ timeout: 30_000 });
  ev.saved = now();
  await waitLog((l) => l.includes('"company-policy-split"') && !l.includes('"version": 0'), "company policy split");
  ev.applied = now();
  await pause(page, 1500);
});
const split = onboarderLog(600).split("\n").filter((l) => l.includes('"company-policy-split"')).map((l) => JSON.parse(l)).at(-1);
const company = opa("dam/company");

onScreen = ["alice", "bob"];
// Act 3: plus the company security policy.
const ACT3 = [
  ["a3-close", "alice", "qa-signoff", "close", { "issue_write.close": "deny" }],
  ["a3-signoff-email", "bob", "qa-signoff", "signOffEmail", { add_issue_comment: "deny" }],
  ["a3-signoff", "bob", "qa-signoff", "signOff", { add_issue_comment: "allow", "issue_write.close": "allow" }],
  ["a3-handover", "alice", "issue-workbench", "handOver", { add_issue_comment: "allow", "issue_write.label": "allow" }, { reopen: true }],
  ["a3-sendback", "bob", "issue-workbench", "sendBack", { add_issue_comment: "allow", "issue_write.label": "allow" }],
];
for (const [key, user, agent, prompt, expected, o = {}] of ACT3) {
  if (o.reopen) resetIssue({ comments: false });
  await step(key, user, (page, ev) => ask(page, agent, PROMPTS[prompt], ev, expected));
}

await step("end", "alice", async (page) => { await page.goto(BASE + "/"); await pause(page, 3000); });
const end = Date.now();
capturing = false;
await capture;
await browser.close();
writeFileSync(`${OUT}/events.json`, JSON.stringify(events, null, 1));
writeFileSync(`${OUT}/sentry.json`, JSON.stringify({ ids: IDS, onboarded, split, company }, null, 1));

// One video per stream (kc, alice, bob, dev), each on the take's clock: a stream's frame lasts
// until its next one, and its first frame fills the time before it, so the film can show any
// stream at any moment of the take.
const results = {};
for (const user of ["kc", "alice", "bob", "dev"]) {
  const mine = frames.filter((f) => f.user === user).sort((x, y) => x.t - y.t);
  if (!mine.length) continue;
  const lines = ["ffconcat version 1.0"];
  mine.forEach((f, i) => {
    const next = i + 1 < mine.length ? mine[i + 1].t : end;
    lines.push(`file '${f.file}'`, `duration ${Math.max(0.001, (next - f.t) / 1000).toFixed(3)}`);
  });
  lines.push(`file '${mine[mine.length - 1].file}'`);
  writeFileSync(`${OUT}/frames-${user}.ffconcat`, lines.join("\n") + "\n");
  const lead = Math.max(0, (mine[0].t - t0) / 1000);
  const r = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", `${OUT}/frames-${user}.ffconcat`,
    "-vf", `scale=1920:1080:flags=lanczos,setsar=1,tpad=start_duration=${lead.toFixed(3)}:start_mode=clone,fps=30,format=yuv420p`,
    "-c:v", "libx264", "-preset", "medium", "-crf", "20", `${OUT}/session-${user}.mp4`], { stdio: "inherit" });
  results[user] = r.status === 0 ? mine.length : "ffmpeg failed";
}
log("streams:", JSON.stringify(results));
const failed = events.filter((e) => e.pass === false).map((e) => e.step);
log(failed.length ? `NOT as the story says: ${failed.join(", ")}` : "every act step as the story says");
