// Explore DAM's UI: sign in as dev, screenshot each step (build/explore/NN-name.png).
import { createRequire } from "node:module";
import path from "node:path";
const require = createRequire(process.env.PW_FROM + "/package.json");
const { chromium } = require("@playwright/test");
const OUT = path.resolve("../build/explore");
const browser = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
const page = await ctx.newPage();
let n = 0;
const shot = async (name) => { await page.waitForTimeout(800); await page.screenshot({ path: `${OUT}/${String(++n).padStart(2, "0")}-${name}.png` }); console.log("shot", n, name, page.url()); };
await page.goto("http://localhost:4444/");
await page.waitForLoadState("networkidle");
if (await page.locator("#username").count()) {
  await page.locator("#username").fill("dev");
  await page.locator("#password").fill("dev");
  await page.screenshot({ path: `${OUT}/00-login.png` });
  const submit = page.locator('input[type=submit], button[type=submit], #kc-login').first();
  await submit.click();
}
await page.getByTestId("app-sidebar").waitFor({ timeout: 30000 });
await shot("home");
const step = process.argv[2] || "";
if (step === "create") {
  await page.getByRole("button", { name: "Create agent" }).last().click();
  await shot("create-form");
  const fields = await page.evaluate(() => [...document.querySelectorAll("input, textarea, select, button, [role=switch], [role=combobox]")]
    .filter((e) => e.offsetParent).map((e) => `${e.tagName.toLowerCase()}#${e.id || ""}[name=${e.getAttribute("name") || ""}][aria=${e.getAttribute("aria-label") || ""}][ph=${e.getAttribute("placeholder") || ""}] ${(e.innerText || e.value || "").trim().slice(0, 40)}`));
  console.log(fields.join("\n"));
}
if (step === "settings") {
  const name = process.argv[3];
  await page.getByTestId("app-sidebar").getByText(name, { exact: true }).first().click();
  await page.getByRole("button", { name: `Configure ${name}` }).first().click();
  await shot("settings-general");
  for (const section of ["Connections", "Network access"]) {
    await page.getByRole("button", { name: section }).or(page.getByRole("link", { name: section })).or(page.getByRole("tab", { name: section })).first().click();
    await shot("settings-" + section.toLowerCase().replace(/ /g, "-"));
  }
  await page.mouse.wheel(0, 1500);
  await shot("network-scrolled");
}
await browser.close();
