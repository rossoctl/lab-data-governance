// Run the Sentry on DAM story's acts live through DAM's UI and check every step's outcome.
//
//   PW_FROM=~/development/dam/packages/e2e/playwright node story.mjs <act>...   (act1 act2 act3)
//
// Each act starts from issue #1 reset (reopened, no labels, no comments). A step is one prompt from
// Alice or Bob to one agent; its outcome is checked against the story: in acts 2 and 3 by Sentry's
// own decision for each operation (the policy service's log), in act 1 by GitHub's state. The
// switches and the company policy are set by setup.mjs (sentry-off / sentry-on / policy).
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(process.env.PW_FROM + "/package.json");
const { chromium } = require("@playwright/test");

const BASE = "http://localhost:4444";
const REPO = "s-and-p-team/demo-public";
const OUT = "/tmp/dam-story";
mkdirSync(OUT, { recursive: true });
const KUBECONFIG = `${process.env.HOME}/.lima/platform-k3s/copied-from-guest/kubeconfig.yaml`;
const kube = (...args) => spawnSync("kubectl", args, { env: { ...process.env, KUBECONFIG }, encoding: "utf8" }).stdout || "";
const gh = (...args) => spawnSync("mise", ["exec", "--", "gh", ...args], { cwd: `${process.env.HOME}/development/dam`, encoding: "utf8" });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

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

// [user, agent, prompt, expected]: expected maps an operation to "allow" or "deny" (acts 2, 3), or
// names what GitHub shows afterwards (act 1).
const ACTS = {
  act1: [
    ["alice", "issue-workbench", "handOver", { github: { labels: ["ready for testing"] } }],
    ["bob", "issue-workbench", "sendBack", { github: { labels: ["needs a fix"] } }],
    ["bob", "issue-workbench", "closeHere", { github: { state: "CLOSED" } }],
    ["alice", "qa-signoff", "close", { reopen: true, github: { state: "CLOSED" } }],
    ["bob", "qa-signoff", "signOffEmail", { reopen: true, github: { comment: "alice.mayer@company.com" } }],
  ],
  act2: [
    ["alice", "issue-workbench", "handOver", { add_issue_comment: "allow", "issue_write.label": "allow" }],
    ["bob", "issue-workbench", "sendBack", { add_issue_comment: "allow", "issue_write.label": "allow" }],
    ["bob", "issue-workbench", "closeHere", { "issue_write.close": "deny" }],
    ["bob", "qa-signoff", "relabel", { "issue_write.label": "deny" }],
    ["alice", "qa-signoff", "close", { "issue_write.close": "allow" }],
    ["bob", "qa-signoff", "signOffEmail", { reopen: true, add_issue_comment: "allow" }],
  ],
  act3: [
    ["alice", "qa-signoff", "close", { "issue_write.close": "deny" }],
    ["bob", "qa-signoff", "signOffEmail", { add_issue_comment: "deny" }],
    ["bob", "qa-signoff", "signOff", { add_issue_comment: "allow", "issue_write.close": "allow" }],
    ["alice", "issue-workbench", "handOver", { reopen: true, add_issue_comment: "allow", "issue_write.label": "allow" }],
    ["bob", "issue-workbench", "sendBack", { add_issue_comment: "allow", "issue_write.label": "allow" }],
  ],
};

function resetIssue({ comments = true } = {}) {
  const view = JSON.parse(gh("issue", "view", "1", "-R", REPO, "--json", "state,labels").stdout || "{}");
  if (view.state !== "OPEN") gh("issue", "reopen", "1", "-R", REPO);
  for (const l of view.labels || []) gh("issue", "edit", "1", "-R", REPO, "--remove-label", l.name);
  if (comments)
    for (const id of (gh("api", `repos/${REPO}/issues/1/comments`, "--jq", ".[].id").stdout || "").split("\n").filter(Boolean))
      gh("api", "-X", "DELETE", `repos/${REPO}/issues/comments/${id}`);
}

function githubState() {
  const v = JSON.parse(gh("issue", "view", "1", "-R", REPO, "--json", "state,labels,comments").stdout || "{}");
  return { state: v.state, labels: (v.labels || []).map((l) => l.name).sort(), comments: (v.comments || []).map((c) => c.body) };
}

function decisions(sinceSeconds) {
  const out = [];
  for (const line of kube("-n", "sentry-dam", "logs", "deploy/mcp-policy", "-c", "server", `--since=${sinceSeconds}s`).split("\n")) {
    let rec;
    try { rec = JSON.parse(line); } catch { continue; }
    for (const m of rec.mcp || []) if (m.rpc === "tools/call") out.push({ ops: m.operations || [m.tool], decision: rec.decision, reason: (rec.reasons || []).at(-1) || "", people: rec.people });
  }
  return out;
}

const browser = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const sessions = {};
async function as(user) {
  if (sessions[user]) return sessions[user];
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(BASE + "/");
  await page.locator("#username").or(page.getByTestId("app-sidebar")).first().waitFor({ timeout: 60_000 });
  if (await page.locator("#username").isVisible()) {
    await page.locator("#username").fill(user);
    await page.locator("#password").fill(user);
    await page.locator("input[type=submit], button[type=submit], #kc-login").first().click();
  }
  await page.getByTestId("app-sidebar").waitFor({ timeout: 60_000 });
  await page.waitForTimeout(2000);
  // An editor without a model provider of their own is offered one; their turns use the owner's.
  const close = page.getByRole("dialog").getByRole("button", { name: "Close", exact: true });
  if (await close.count()) await close.first().click();
  sessions[user] = page;
  return page;
}

function agentId(name) {
  const out = kube("get", "agents.agent-platform.ai", "-n", "platform-agents", "-o", "jsonpath={range .items[*]}{.metadata.name} {.spec.name}{\"\\n\"}{end}");
  const line = out.split("\n").find((l) => l.split(" ").slice(1).join(" ") === name);
  if (!line) throw new Error(`no agent ${name}`);
  return line.split(" ")[0];
}

async function ask(page, agent, prompt, maxMs = 300_000) {
  await page.goto(`${BASE}/chat/${agentId(agent)}`);
  const input = page.getByPlaceholder("Message…");
  await input.waitFor({ timeout: 90_000 });
  await input.fill(prompt);
  await input.press("Enter");
  const before = 0;
  await page.waitForFunction((n) => document.querySelectorAll('[data-testid="chat-message"]').length > n + 1, before, { timeout: 120_000 }).catch(() => {});
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    const busy = await page.getByRole("button", { name: /^(Stop|Cancel)/ }).count();
    const role = await page.getByTestId("chat-message").last().getAttribute("data-role").catch(() => null);
    if (!busy && role === "assistant") break;
    await page.waitForTimeout(1500);
  }
  await page.waitForTimeout(1500);
  return (await page.getByTestId("chat-message").last().innerText().catch(() => "")).replace(/\s+/g, " ").slice(0, 400);
}

const report = [];
let failed = 0;
for (const act of process.argv.slice(2)) {
  log(`=== ${act}`);
  resetIssue();
  for (const [user, agent, key, expected] of ACTS[act]) {
    if (expected.reopen) resetIssue({ comments: false });
    const started = Date.now();
    const page = await as(user);
    const reply = await ask(page, agent, PROMPTS[key]);
    await page.screenshot({ path: `${OUT}/${act}-${report.length + 1}-${user}-${agent}-${key}.png` });
    const seen = decisions(Math.ceil((Date.now() - started) / 1000) + 3);
    const checks = [];
    if (expected.github) {
      const gs = githubState();
      for (const [k, v] of Object.entries(expected.github)) {
        const ok = k === "comment" ? gs.comments.some((c) => c.includes(v)) : JSON.stringify(gs[k]) === JSON.stringify(v);
        checks.push({ check: `github ${k}`, want: v, got: k === "comment" ? gs.comments.length + " comments" : gs[k], ok });
      }
    }
    for (const [op, want] of Object.entries(expected)) {
      if (op === "github" || op === "reopen") continue;
      const got = seen.filter((d) => d.ops.includes(op));
      const ok = got.length > 0 && got.every((d) => d.decision === want);
      checks.push({ check: op, want, got: got.map((d) => `${d.decision} (${(d.people || []).join(",")})`).join("; ") || "no call", reason: got.at(-1)?.reason, ok });
    }
    const pass = checks.every((c) => c.ok);
    if (!pass) failed++;
    report.push({ act, user, agent, step: key, pass, checks, reply });
    log(`${pass ? "PASS" : "FAIL"} ${act} ${user}@${agent} ${key}`);
    for (const c of checks) log(`   ${c.ok ? "ok " : "BAD"} ${c.check}: want ${JSON.stringify(c.want)}, got ${JSON.stringify(c.got)}${c.reason ? " — " + c.reason.slice(0, 200) : ""}`);
    if (!pass) log(`   reply: ${reply}`);
    writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));
  }
}
await browser.close();
log(failed ? `${failed} step(s) failed` : "every step as the story says");
process.exit(failed ? 1 : 0);
