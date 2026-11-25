// Drive DAM's UI through the film's story and record it.
//
//   PW_FROM=~/development/dam/packages/e2e/playwright node record.mjs <take> [step,step,...]
//
// Records the whole browser session with Chrome's screencast (full-quality JPEG frames with their
// own timestamps) as build/ui/<take>/session.mp4, 30 fps, and logs every step's start and end, in
// seconds from the start of that video, to build/ui/<take>/events.json, so the film can cut and
// time each clip. A screenshot is kept at the end of every step.
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(process.env.PW_FROM + "/package.json");
const { chromium } = require("@playwright/test");

const TAKE = process.argv[2] || "rehearsal";
const ONLY = process.argv[3] ? new Set(process.argv[3].split(",")) : null;
const AGENT = process.env.AGENT || "issue-triage";
const OUT = path.resolve(`../build/ui/${TAKE}`);
mkdirSync(OUT, { recursive: true });

const DESCRIPTION_V1 =
  "Issue triage assistant. Reads and summarizes open issues in the s-and-p-team demo repositories so the owner can prioritize them. It reports on issues and never changes repositories or issues.";
// The owner edits V1's last sentence: backspace over its end, then type the new clause.
const V1_CUT = "never changes repositories or issues.";
const V2_ADD = "may comment on an issue to ask the reporter for missing details, but it never closes issues and never changes repositories.";
const DESCRIPTION_V2 = DESCRIPTION_V1.slice(0, -V1_CUT.length) + V2_ADD;

export const PROMPTS = {
  list: "Using the github-mcp tools, list the open issues in s-and-p-team/demo-public and s-and-p-team/demo-private, with their numbers and titles.",
  close: "Using the github-mcp tools, close issue #1 in s-and-p-team/demo-public.",
  comment: 'Using the github-mcp tools, comment on issue #1 in s-and-p-team/demo-public: "Could you share which browser and version you use?"',
  search: 'Using the github-mcp tools, search s-and-p-team/demo-public issues for "John Smith 123-45-6789", using exactly that query text.',
};

const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});
// A smaller viewport at 4/3 scale: 1080p frames whose UI text reads about a third larger on screen.
const ctx = await browser.newContext({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 4 / 3 });
const page = await ctx.newPage();
const FRAMES = `${OUT}/frames`;
rmSync(FRAMES, { recursive: true, force: true });
mkdirSync(FRAMES, { recursive: true });
const frames = [];
const cdp = await ctx.newCDPSession(page);
cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
  const file = `${FRAMES}/${String(frames.length).padStart(6, "0")}.jpg`;
  writeFileSync(file, Buffer.from(data, "base64"));
  // The arrival time, on the same clock as the events (the screencast's own timestamp is not).
  frames.push({ file, t: Date.now() });
  cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
});
await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
const t0 = Date.now();
const now = () => +((Date.now() - t0) / 1000).toFixed(2);
const events = [];
let shots = 0;
let current = null;   // the step being recorded: ask() adds its moments to it

async function step(name, fn) {
  if (ONLY && !ONLY.has(name) && name !== "login") return;
  const ev = { step: name, start: now() };
  current = ev;
  console.log(`> ${name} @${ev.start}s`);
  try {
    await fn();
  } catch (err) {
    await page.screenshot({ path: `${OUT}/FAIL-${name}.png` });
    ev.error = String(err).slice(0, 400);
    events.push(ev);
    throw err;
  } finally {
    ev.end = now();
    if (!ev.error) events.push(ev);
    await page.screenshot({ path: `${OUT}/${String(++shots).padStart(2, "0")}-${name}.png` });
    writeFileSync(`${OUT}/events.json`, JSON.stringify(events, null, 1));
  }
}

const pause = (ms) => page.waitForTimeout(ms);
const sidebar = () => page.getByTestId("app-sidebar");

async function openSettings(section = "General") {
  if (!/\/sandboxes\//.test(page.url())) {
    await sidebar().getByText(AGENT, { exact: true }).first().click();
    await page.getByRole("button", { name: `Configure ${AGENT}` }).first().click();
    await page.waitForURL(/\/sandboxes\//);
  }
  await page.getByRole("button", { name: new RegExp(`^${section}`) }).or(page.getByRole("link", { name: new RegExp(`^${section}`) })).first().click();
  await pause(700);
}

// The description's Save button shows only when the text changed.
async function saveIfChanged() {
  const save = page.getByRole("button", { name: "Save", exact: true });
  if (await save.isVisible().catch(() => false)) {
    await save.click();
    await save.waitFor({ state: "hidden", timeout: 15_000 });
  }
  await pause(1200);
}

// A new chat session with the agent, from its row in the sidebar.
async function openChat() {
  const row = sidebar().locator("div").filter({ has: page.getByText(AGENT, { exact: true }) })
    .filter({ has: page.getByRole("button", { name: "New session" }) }).last();
  await row.hover();
  await row.getByRole("button", { name: "New session" }).click();
  await page.getByPlaceholder("Message…").waitFor({ timeout: 60_000 });
  await pause(800);
}

// Type a prompt visibly, send it, and wait for the agent's turn to end.
async function ask(prompt, { maxMs = 240_000 } = {}) {
  const input = page.getByPlaceholder("Message…");
  await input.click();
  if (current) current.typing = now();
  await input.pressSequentially(prompt, { delay: 40 });
  if (current) current.typed = now();
  await input.locator("xpath=..").getByRole("button", { name: /^(Send|Queue)$/ }).waitFor({ state: "visible", timeout: 60_000 });
  await pause(300);
  await input.press("Enter");
  if (current) current.sent = now();
  const before = await page.getByTestId("chat-message").count();
  // The turn has ended once an assistant message follows ours and the composer offers Send again.
  const deadline = Date.now() + maxMs;
  await page.waitForFunction((n) => document.querySelectorAll('[data-testid="chat-message"]').length > n, before, { timeout: 60_000 });
  while (Date.now() < deadline) {
    const busy = await page.getByRole("button", { name: /^(Stop|Cancel)/ }).count();
    const last = page.getByTestId("chat-message").last();
    const role = await last.getAttribute("data-role").catch(() => null);
    if (!busy && role === "assistant") break;
    await pause(1000);
  }
  if (current) current.done = now();
  await pause(1500);
  // Open the reply's tool list, so the tools it called show by name.
  const used = page.getByTestId("chat-message").last().getByText(/^Used \d+ tools?/).first();
  if (await used.count()) {
    await used.scrollIntoViewIfNeeded().catch(() => {});
    await used.click().catch(() => {});
    if (current) current.expanded = now();
    await pause(2500);
  }
}


// --- waiting on the platform (outside the page): the gateway's policy path and aiac's onboarder.
const KUBECONFIG = `${process.env.HOME}/.lima/platform-k3s/copied-from-guest/kubeconfig.yaml`;
const kube = (...args) => spawnSync("kubectl", args, { env: { ...process.env, KUBECONFIG }, encoding: "utf8" }).stdout || "";
let agentId = "";
function currentAgentId() {
  const m = page.url().match(/(agent-[0-9a-f]{16})/);
  if (m) agentId = m[1];
  return agentId;
}
async function waitGateway(checks, { since = 0, maxMs = 180_000 } = {}) {
  const id = agentId;
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    const cm = kube("-n", "platform-agents", "get", "cm", `${id}-envoy-bootstrap`, "-o", "jsonpath={.data.envoy\\.yaml}");
    const ready = kube("-n", "platform-agents", "get", "pod", `${id}-gateway-0`, "-o", "jsonpath={.status.conditions[?(@.type==\"Ready\")].status}");
    const born = Date.parse(kube("-n", "platform-agents", "get", "pod", `${id}-gateway-0`, "-o", "jsonpath={.metadata.creationTimestamp}"));
    // The gateway reads its bootstrap only at start: wait for one started after the switch.
    if (cm.includes(`path_prefix: /check/${id}/${checks}\n`) && ready === "True" && born >= since - 2000) return;
    await pause(2000);
  }
  throw new Error(`gateway of ${id} did not reach /check/${id}/${checks}`);
}
async function waitOnboarded(sinceSeconds, maxMs = 180_000) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    const log = kube("-n", "sentry-dam", "logs", "deploy/mcp-policy", "-c", "onboarder", `--since=${sinceSeconds}s`);
    if (log.split("\n").some((l) => l.includes('"onboarded"') && l.includes(agentId))) return;
    await pause(2000);
  }
  throw new Error(`aiac did not onboard ${agentId}`);
}
const sinceStep = (ev0) => Math.ceil((Date.now() - ev0) / 1000) + 5;

async function setSwitch(name, on, { confirm } = {}) {
  const sw = page.getByRole("switch", { name, exact: true });
  await sw.scrollIntoViewIfNeeded();
  if ((await sw.getAttribute("aria-checked")) === String(on)) return;
  await sw.hover();
  await pause(500);
  await sw.click();
  if (confirm) {
    const ok = page.getByRole("button", { name: confirm, exact: true });
    await ok.waitFor({ timeout: 10_000 });
    await pause(900);
    await ok.click();
  }
  await pause(1200);
}

await step("login", async () => {
  await page.goto("http://localhost:4444/");
  await page.waitForLoadState("networkidle");
  if (await page.locator("#username").count()) {
    await page.locator("#username").fill("dev");
    await page.locator("#password").fill("dev");
    await page.locator("input[type=submit], button[type=submit], #kc-login").first().click();
  }
  await sidebar().waitFor({ timeout: 60_000 });
  await pause(800);
});

// A clean slate: the film's agent from an earlier take is deleted, then the home page holds.
await step("reset", async () => {
  if (await sidebar().getByText(AGENT, { exact: true }).count()) {
    await sidebar().getByText(AGENT, { exact: true }).first().click();
    await page.getByRole("button", { name: `Configure ${AGENT}` }).first().click();
    await page.waitForURL(/\/sandboxes\//);
    await page.getByRole("button", { name: "Delete", exact: true }).first().click();
    await page.getByRole("button", { name: "Delete agent", exact: true }).click();
    await page.getByTestId("sidebar-agent-row").filter({ hasText: AGENT }).waitFor({ state: "detached", timeout: 60_000 });
  }
  await page.goto("http://localhost:4444/");
  await sidebar().waitFor();
  await pause(1500);
});

await step("home", async () => { await pause(5000); });

await step("create", async () => {
  await sidebar().getByRole("button", { name: "Create agent" }).click();
  await page.getByText("Blank agent", { exact: true }).waitFor();
  await pause(700);
  await page.getByText("Blank agent", { exact: true }).click();
  await page.getByRole("button", { name: /^Rename / }).first().waitFor({ timeout: 60_000 });
  await pause(800);
  await page.getByRole("button", { name: /^Rename / }).first().click();
  await pause(300);
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type(AGENT, { delay: 40 });
  await page.keyboard.press("Enter");
  await sidebar().getByText(AGENT, { exact: true }).first().waitFor({ timeout: 30_000 });
  await pause(800);
});

await step("describe", async () => {
  await openSettings();
  const box = page.getByRole("textbox").first();
  await box.click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.press("Backspace");
  current.typing = now();
  await box.pressSequentially(DESCRIPTION_V1, { delay: 45 });
  current.typed = now();
  await pause(400);
  await saveIfChanged();
});

await step("connect", async () => {
  await openSettings("Connections");
  await page.getByRole("button", { name: "Add connection" }).click();
  await page.getByText("Connection catalog").waitFor();
  await pause(700);
  await page.getByRole("button", { name: /^MCP servers/ }).or(page.getByText("MCP servers", { exact: true })).first().click();
  await pause(700);
  const row = page.locator("div").filter({ hasText: /^github-mcp/ }).filter({ has: page.getByRole("button", { name: "Add to agent" }) }).last();
  await row.getByRole("button", { name: "Add to agent" }).click();
  await pause(1200);
  await page.keyboard.press("Escape");
  await page.getByText("github-mcp", { exact: false }).first().waitFor({ timeout: 15_000 });
  await pause(1200);
});

// Stage 0: no Sentry yet. The agent lists, closes and searches with personal data, and all of it goes through.
await step("s0-list", async () => { await openChat(); await ask(PROMPTS.list); });
await step("s0-close", async () => { await ask(PROMPTS.close); });
await step("s0-search", async () => { await ask(PROMPTS.search); });

// Stage 1: aiac only. dg is set off first, which changes nothing while enforcement is off; then
// enforcement goes on with aiac alone, one gateway restart.
await step("s1-config", async () => {
  await openSettings("Network access");
  currentAgentId();
  const t = Date.now();
  await setSwitch("Data governance (dg)", false);
  await setSwitch("MCP policy enforcement", true, { confirm: "Turn on & restart" });
  await waitGateway("aiac", { since: t });
  await waitOnboarded(sinceStep(t));
  await pause(1500);
});
await step("s1-close", async () => { await openChat(); await ask(PROMPTS.close); });
await step("s1-comment", async () => { await ask(PROMPTS.comment); });
await step("s1-search", async () => { await ask(PROMPTS.search); });

// Stage 1b: the owner allows commenting in the description; aiac rewrites the policy.
await step("s2-desc", async () => {
  await openSettings("General");
  currentAgentId();
  const t = Date.now();
  const box = page.getByRole("textbox").first();
  await box.click();
  // The caret to the very end of the text (Cmd/Ctrl+End does not do this in a textarea on macOS).
  await box.evaluate((el) => { el.focus(); el.setSelectionRange(el.value.length, el.value.length); el.scrollTop = el.scrollHeight; });
  await pause(600);
  current.typing = now();
  for (let k = 0; k < V1_CUT.length; k++) { await page.keyboard.press("Backspace"); await pause(45); }
  await pause(400);
  await box.pressSequentially(V2_ADD, { delay: 45 });
  current.typed = now();
  if ((await box.inputValue()) !== DESCRIPTION_V2) throw new Error("description edit did not land: " + (await box.inputValue()));
  await pause(400);
  await saveIfChanged();
  await waitOnboarded(sinceStep(t));
  await pause(1500);
});
await step("s2-comment", async () => { await openChat(); await ask(PROMPTS.comment); });
await step("s2-close", async () => { await ask(PROMPTS.close); });

// Stage 2: aiac and dg.
await step("s3-dg", async () => {
  await openSettings("Network access");
  currentAgentId();
  const t = Date.now();
  await setSwitch("Data governance (dg)", true, { confirm: "Turn on & restart" });
  await waitGateway("aiac,dg", { since: t });
  await pause(1500);
});
await step("s3-search", async () => { await openChat(); await ask(PROMPTS.search); });
await step("s3-list", async () => { await ask(PROMPTS.list); });

await cdp.send("Page.stopScreencast").catch(() => {});
await browser.close();
writeFileSync(`${OUT}/events.json`, JSON.stringify(events, null, 1));

// Frames come only when the page changes: each one lasts until the next, so the video keeps the
// session's real timing. Frame times are on the same clock as the events (ms since t0).
if (frames.length) {
  const end = Date.now();
  const lines = ["ffconcat version 1.0"];
  frames.forEach((f, i) => {
    const next = i + 1 < frames.length ? frames[i + 1].t : end;
    lines.push(`file '${f.file}'`, `duration ${Math.max(0.001, (next - f.t) / 1000).toFixed(3)}`);
  });
  lines.push(`file '${frames[frames.length - 1].file}'`);
  writeFileSync(`${OUT}/frames.ffconcat`, lines.join("\n") + "\n");
  const lead = Math.max(0, (frames[0].t - t0) / 1000);
  const r = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", `${OUT}/frames.ffconcat`,
    "-vf", `scale=1920:1080:flags=lanczos,setsar=1,tpad=start_duration=${lead.toFixed(3)}:start_mode=clone,fps=30,format=yuv420p`, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    `${OUT}/session.mp4`], { stdio: "inherit" });
  console.log(r.status === 0 ? `wrote ${OUT}/session.mp4 (${frames.length} frames)` : "ffmpeg failed");
}
console.log(`events in ${OUT}/events.json`);
