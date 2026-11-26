// Set a clean DAM install up for the Sentry on DAM story, through DAM's own UI.
//
//   LITELLM_KEY=... PW_FROM=~/development/dam/packages/e2e/playwright node setup.mjs <step>...
//
// Steps (each safe to run again):
//   alice-base   Alice accepts the terms, turns on the experimental features the story uses
//                (MCP policy enforcement, Advanced connections) and connects the model provider.
//   bob-base     Bob accepts the terms.
//   dev-base     dev (the operator) accepts the terms.
//   mcp-server   Alice's MCP server connection github-mcp (https://api.githubcopilot.com/mcp/).
//   pat-draft    Alice's custom-header connection github-pat (Authorization: Bearer <PAT> for
//                api.githubcopilot.com), filled in up to the PAT, which its owner types by hand: a
//                visible browser waits on that form until the connection exists.
//   agents       Alice's two agents, with their descriptions, github-mcp, and Bob in People.
//   policy       dev writes the company security policy (POLICY, or the story's) on the Operator page.
//   sentry-on / sentry-off   Alice turns Sentry on or off for both agents.
// The model provider's key comes from LITELLM_KEY and is never printed.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";

const require = createRequire(process.env.PW_FROM + "/package.json");
const { chromium } = require("@playwright/test");

const BASE = "http://localhost:4444";
export const AGENTS = {
  "issue-workbench":
    "Helps the team work on issues in the s-and-p-team demo repositories. It reads and summarizes issues, posts comments, and hands an issue back and forth between the developer and the tester with its labels, ready for testing or needs a fix. It never closes issues.",
  "qa-signoff":
    "Final QA review. It reads an issue and its history, posts the sign-off comment, and closes the issue as completed, or as not planned. It never relabels issues and never changes what an issue says.",
};
export const POLICY =
  process.env.POLICY ??
  "Developers and testers may read issues, comment on them and label them. Only testers close an issue, and the QA sign-off agent is used by testers only. Personal data and restricted data must not be sent to an untrusted external destination.";

const steps = process.argv.slice(2);
const headed = steps.includes("pat-draft") || steps.includes("mcp-with-pat");
const browser = await chromium.launch({
  executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: !headed,
});

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

async function signIn(user) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  await page.goto(BASE + "/");
  await page.locator("#username").or(page.getByTestId("app-sidebar")).first().waitFor({ timeout: 60_000 });
  if (await page.locator("#username").isVisible()) {
    await page.locator("#username").fill(user);
    await page.locator("#password").fill(user);
    await page.locator("input[type=submit], button[type=submit], #kc-login").first().click();
  }
  await page.waitForURL((u) => !u.href.includes("/realms/"), { timeout: 60_000 });
  const accept = page.getByRole("button", { name: "I accept the Terms of Use" });
  await accept.or(page.getByTestId("app-sidebar")).first().waitFor({ timeout: 60_000 });
  if (await accept.isVisible().catch(() => false)) {
    await accept.click();
    log(user, "accepted the terms");
  }
  await page.getByTestId("app-sidebar").waitFor({ timeout: 60_000 });
  return { ctx, page };
}

async function setSwitch(page, name, on) {
  const sw = page.getByRole("switch", { name, exact: true });
  await sw.waitFor();
  if ((await sw.getAttribute("aria-checked")) !== String(on)) {
    await sw.click();
    await page.waitForFunction(
      ([n, v]) => [...document.querySelectorAll("[role=switch]")].some((e) => e.getAttribute("aria-label") === n && e.getAttribute("aria-checked") === v),
      [name, String(on)],
    );
    log(`${name}: ${on ? "on" : "off"}`);
  }
}

async function features(page, names) {
  await page.goto(BASE + "/settings/features");
  for (const name of names) await setSwitch(page, name, true);
}

async function connectLiteLLM(page) {
  await page.goto(BASE + "/settings/providers");
  await page.getByRole("heading").first().waitFor();
  await page.waitForTimeout(2500);
  const connect = page.getByRole("button", { name: "Connect IBM ETE LiteLLM Proxy" });
  if (!(await connect.isVisible().catch(() => false))) {
    log("model provider: already connected");
    return;
  }
  if (!process.env.LITELLM_KEY) throw new Error("LITELLM_KEY is not set");
  await connect.click();
  await page.getByRole("textbox", { name: "IBM ETE LiteLLM Proxy API key" }).fill(process.env.LITELLM_KEY);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await connect.waitFor({ state: "detached", timeout: 30_000 }).catch(() => {});
  log("model provider: IBM ETE LiteLLM Proxy connected");
}

async function openCatalog(page, category) {
  await page.goto(BASE + "/settings/connections");
  await page.getByRole("button", { name: /^Add connection/ }).click();
  const dialog = page.getByRole("dialog").last();
  await dialog.getByText("Connection catalog").waitFor();
  await dialog.getByText(category, { exact: true }).click();
  await page.waitForTimeout(800);
  return dialog;
}

// The catalog's MCP servers pane lists presets (Figma) beside the custom server: open the custom one.
async function newMcpServerForm(page, dialog) {
  const buttons = dialog.getByRole("button", { name: /^New/ });
  for (let i = 0; i < (await buttons.count()); i++) {
    await buttons.nth(i).click();
    if (await dialog.getByPlaceholder("my-mcp-server").waitFor({ timeout: 2500 }).then(() => true, () => false)) return;
    await dialog.getByRole("button", { name: "Back", exact: true }).click();
    await page.waitForTimeout(500);
  }
  throw new Error("no custom MCP server form in the catalog");
}

async function hasConnection(page, name) {
  await page.goto(BASE + "/settings/connections");
  await page.getByRole("heading", { name: "Connections" }).waitFor();
  await page.waitForTimeout(1500);
  return (await page.getByRole("button", { name: `Actions for ${name}`, exact: true }).count()) > 0;
}

const sidebar = (page) => page.getByTestId("app-sidebar");
const KUBECONFIG = `${process.env.HOME}/.lima/platform-k3s/copied-from-guest/kubeconfig.yaml`;
// The agents by name, from DAM's Agent resources: the sidebar shows only some of them.
function agentIds() {
  const out = spawnSync("kubectl", ["get", "agents.agent-platform.ai", "-n", "platform-agents", "-o", "jsonpath={range .items[*]}{.metadata.name} {.spec.name}{\"\\n\"}{end}"],
    { env: { ...process.env, KUBECONFIG }, encoding: "utf8" }).stdout || "";
  const ids = {};
  for (const line of out.split("\n").filter(Boolean)) {
    const [id, ...name] = line.split(" ");
    (ids[name.join(" ")] ??= []).push(id);
  }
  return ids;
}

async function createAgent(page, name) {
  if (agentIds()[name]) {
    log(`${name}: exists`);
    return;
  }
  await sidebar(page).getByRole("button", { name: "Create agent" }).click();
  await page.getByText("Blank agent", { exact: true }).click();
  await page.getByRole("button", { name: /^Rename / }).first().waitFor({ timeout: 90_000 });
  await page.getByRole("button", { name: /^Rename / }).first().click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type(name);
  await page.keyboard.press("Enter");
  await sidebar(page).getByText(name, { exact: true }).first().waitFor({ timeout: 30_000 });
  log(`${name}: created`);
}

async function openSettings(page, name, section) {
  const here = page.url().includes("/sandboxes/") && (await page.getByRole("button", { name: `Rename ${name}`, exact: true }).count());
  if (!here) {
    const [id] = agentIds()[name] ?? [];
    if (!id) throw new Error(`no agent ${name}`);
    await page.goto(`${BASE}/sandboxes/${id}`);
    await page.getByRole("button", { name: `Rename ${name}`, exact: true }).waitFor({ timeout: 60_000 });
  }
  if (section)
    await page.getByRole("button", { name: new RegExp(`^${section}`) }).or(page.getByRole("link", { name: new RegExp(`^${section}`) })).first().click();
  await page.waitForTimeout(1200);
}

async function dump(page, tag) {
  await page.screenshot({ path: `/tmp/dam-explore/${tag}.png` });
  const items = await page.evaluate(() => [...document.querySelectorAll("main button, main input, main textarea, main [role=switch], main h1, main h2, main h3, main label, nav a")]
    .filter((e) => e.offsetParent).map((e) => `${e.tagName.toLowerCase()} ${(e.getAttribute("aria-label") || e.innerText || e.getAttribute("placeholder") || "").trim().replace(/\s+/g, " ").slice(0, 70)}`));
  console.log(`== ${tag}\n` + [...new Set(items)].join("\n"));
}

// Sentry on or off for an agent: its Network access switch, which restarts the gateway.
async function setSentry(page, name, on) {
  await openSettings(page, name, "Network access");
  const sw = page.getByRole("switch", { name: "Sentry", exact: true });
  await sw.scrollIntoViewIfNeeded();
  if ((await sw.getAttribute("aria-checked")) === String(on)) {
    log(`${name}: Sentry already ${on ? "on" : "off"}`);
    return;
  }
  await sw.click();
  const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog")).last().getByRole("button").filter({ hasText: /restart/i });
  if (await confirm.count()) await confirm.first().click();
  await page.waitForFunction((v) => document.querySelector('[role=switch][aria-label="Sentry"]')?.getAttribute("aria-checked") === v, String(on), { timeout: 30_000 });
  log(`${name}: Sentry ${on ? "on" : "off"}`);
}

for (const step of steps) {
  log("step", step);
  if (step === "policy") {
    const { ctx, page } = await signIn("dev");
    await page.goto(BASE + "/settings/operator");
    const card = page.getByTestId("company-policy");
    await card.waitFor({ timeout: 30_000 });
    const box = card.getByRole("textbox");
    await box.waitFor();
    await page.waitForFunction(() => !document.querySelector('[data-testid="company-policy"] textarea')?.disabled);
    if ((await box.inputValue()) === POLICY.trim()) log("company policy: unchanged");
    else {
      await box.fill(POLICY);
      await card.getByRole("button", { name: "Save", exact: true }).click();
      await card.getByText(/^Version \d+, saved/).waitFor({ timeout: 30_000 });
      log("company policy:", await card.getByText(/^Version \d+, saved/).innerText());
    }
    await page.screenshot({ path: "/tmp/dam-explore/policy.png" });
    await ctx.close();
  } else if (step === "sentry-on" || step === "sentry-off") {
    const { ctx, page } = await signIn("alice");
    for (const name of Object.keys(AGENTS)) await setSentry(page, name, step === "sentry-on");
    await ctx.close();
  } else if (step === "alice-base") {
    const { ctx, page } = await signIn("alice");
    await features(page, ["MCP policy enforcement", "Advanced connections"]);
    await connectLiteLLM(page);
    await page.screenshot({ path: "/tmp/dam-explore/alice-base.png" });
    await ctx.close();
  } else if (step === "mcp-with-pat") {
    // The agent's MCP client sends the MCP server connection's own header; a separate
    // custom-header connection does not reach it. So github-mcp carries the PAT itself.
    const { ctx, page } = await signIn("alice");
    for (const name of ["github-pat", "github-mcp"]) {
      if (!(await hasConnection(page, name))) continue;
      await page.getByRole("button", { name: `Actions for ${name}`, exact: true }).click();
      await page.getByRole("menuitem", { name: /Delete/ }).click();
      await page.getByRole("button", { name: "Delete connection", exact: true }).click();
      await page.waitForTimeout(2000);
      if (await hasConnection(page, name)) throw new Error(`${name} was not deleted`);
      log(`${name}: deleted`);
    }
    const dialog = await openCatalog(page, "MCP servers");
    await newMcpServerForm(page, dialog);
    await dialog.getByPlaceholder("my-mcp-server").fill("github-mcp");
    await dialog.getByPlaceholder("https://mcp.example.com/sse").fill("https://api.githubcopilot.com/mcp/");
    await dialog.getByRole("button", { name: "ADVANCED CONFIGURATION" }).click();
    await dialog.getByPlaceholder("Name (optional)").fill("Authorization");
    const value = dialog.getByPlaceholder("Value (optional)");
    await value.fill("Bearer ");
    await value.focus();
    await page.keyboard.press("End");
    log("github-mcp: waiting for the PAT to be typed after 'Bearer ' in the browser window, then Create");
    await dialog.waitFor({ state: "detached", timeout: 15 * 60_000 });
    if (!(await hasConnection(page, "github-mcp"))) throw new Error("github-mcp was not created");
    log("github-mcp: created with its Authorization header");
    await ctx.close();
  } else if (step === "mcp-server") {
    const { ctx, page } = await signIn("alice");
    if (await hasConnection(page, "github-mcp")) log("github-mcp: exists");
    else {
      const dialog = await openCatalog(page, "MCP servers");
      await dialog.getByRole("button", { name: /^New/ }).last().click();
      await dialog.getByPlaceholder("my-mcp-server").fill("github-mcp");
      await dialog.getByPlaceholder("https://mcp.example.com/sse").fill("https://api.githubcopilot.com/mcp/");
      await dialog.getByRole("button", { name: "Create", exact: true }).click();
      await page.waitForTimeout(2500);
      log("github-mcp: created");
    }
    await page.screenshot({ path: "/tmp/dam-explore/mcp-server.png" });
    await ctx.close();
  } else if (step === "pat-draft") {
    const { ctx, page } = await signIn("alice");
    if (await hasConnection(page, "github-pat")) log("github-pat: exists");
    else {
      const dialog = await openCatalog(page, "Custom headers");
      await dialog.getByRole("button", { name: /^New/ }).last().click();
      await dialog.getByPlaceholder("my-connection").fill("github-pat");
      await dialog.getByPlaceholder("api.example.com").fill("api.githubcopilot.com");
      await dialog.getByPlaceholder("X-API-Key").fill("Authorization");
      await dialog.getByPlaceholder("{value}").fill("Bearer {value}");
      await dialog.getByPlaceholder("•••••").focus();
      log("github-pat: waiting for the PAT to be typed in the browser window, then Create");
      await page.getByRole("button", { name: "Actions for github-pat", exact: true }).waitFor({ timeout: 15 * 60_000 });
      log("github-pat: created");
    }
    await ctx.close();
  } else if (step.startsWith("delete-agent=")) {
    const { ctx, page } = await signIn("alice");
    const name = step.split("=")[1];
    for (const id of agentIds()[name] ?? []) {
      await page.goto(`${BASE}/sandboxes/${id}`);
      await page.getByRole("button", { name: "Delete", exact: true }).first().click();
      await page.getByRole("button", { name: "Delete agent", exact: true }).click();
      await page.waitForTimeout(3000);
      log(`${name} (${id}): deleted`);
    }
    await ctx.close();
  } else if (step === "agents") {
    const { ctx, page } = await signIn("alice");
    for (const [name, description] of Object.entries(AGENTS)) {
      await createAgent(page, name);
      await openSettings(page, name, "General");
      const box = page.getByRole("textbox").first();
      if ((await box.inputValue()) !== description) {
        await box.fill(description);
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await page.getByRole("button", { name: "Save", exact: true }).waitFor({ state: "detached", timeout: 30_000 });
        log(`${name}: description saved`);
      }
      await openSettings(page, name, "People");
      if (!(await page.getByRole("button", { name: /^People/ }).filter({ hasText: /bob|editor/i }).count())) {
        await page.getByRole("textbox", { name: "Editor email address" }).fill("bob@platform.local");
        await page.getByRole("button", { name: "Add", exact: true }).click();
        await page.getByRole("button", { name: "Save", exact: true }).click();
        await page.getByRole("button", { name: /^People/ }).filter({ hasText: /bob|1 editor/i }).waitFor({ timeout: 30_000 });
        log(`${name}: Bob is an editor`);
      }
      for (const [category, conn] of [["MCP servers", "github-mcp"]]) {
        await openSettings(page, name, "Connections");
        if (await page.locator("main").getByText(conn, { exact: true }).count()) continue;
        await page.getByRole("button", { name: "Add connection" }).click();
        const dialog = page.getByRole("dialog").last();
        await dialog.getByText("Connection catalog").waitFor();
        await dialog.getByText(category, { exact: true }).click();
        await page.waitForTimeout(800);
        const row = dialog.locator("div").filter({ hasText: new RegExp(`^${conn}`) }).filter({ has: page.getByRole("button", { name: "Add to agent" }) }).last();
        await row.getByRole("button", { name: "Add to agent" }).click();
        await page.waitForTimeout(1500);
        await page.screenshot({ path: `/tmp/dam-explore/add-${name}-${conn}.png` });
        const restart = page.getByRole("alertdialog").or(page.getByRole("dialog")).last().getByRole("button").filter({ hasText: /restart|add|apply|confirm/i });
        if (await restart.count()) { log(`${name}: confirming "${await restart.first().innerText()}"`); await restart.first().click(); await page.waitForTimeout(1500); }
        await page.keyboard.press("Escape");
        await page.locator("main").getByText(conn, { exact: true }).first().waitFor({ timeout: 30_000 });
        log(`${name}: ${conn} added`);
      }
      await dump(page, `agent-${name}`);
    }
    await ctx.close();
  } else if (step === "try-people") {
    const { ctx, page } = await signIn("alice");
    await openSettings(page, "issue-workbench", "People");
    await page.getByRole("textbox", { name: "Editor email address" }).fill("bob@platform.local");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await page.waitForTimeout(2500);
    await dump(page, "people-after-add");
    await ctx.close();
  } else if (step === "explore-agent") {
    const { ctx, page } = await signIn("alice");
    await createAgent(page, "issue-workbench");
    for (const section of ["People"]) {
      await openSettings(page, "issue-workbench", section);
      await dump(page, "agent-" + section);
    }
    await ctx.close();
  } else if (step === "bob-base" || step === "dev-base") {
    const { ctx } = await signIn(step.split("-")[0]);
    await ctx.close();
  } else {
    throw new Error(`unknown step ${step}`);
  }
}
await browser.close();
