// Record the film's Keycloak scene on its own: the realm admin's console with the realm's users,
// then alice's role (developer) and bob's (tester).
//
//   KC_ADMIN_PASSWORD=... PW_FROM=~/development/dam/packages/e2e/playwright node record_kc.mjs <take>
//
// Writes build/ui/<take>/session-kc.mp4 and build/ui/<take>/kc-events.json (users, alice, bob, end,
// in seconds from the start of that video); film_data.py uses them for the scene in place of the
// take's own Keycloak step. The password is read from the cluster by the caller and never printed.
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(process.env.PW_FROM + "/package.json");
const { chromium } = require("@playwright/test");

const TAKE = process.argv[2] || "rehearsal";
const OUT = path.resolve(`../build/ui/${TAKE}`);
mkdirSync(`${OUT}/frames-kc`, { recursive: true });
const browser = await chromium.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: true });
const page = await (await browser.newContext({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 4 / 3 })).newPage();

await page.goto("http://keycloak.localhost:4444/admin/master/console/#/platform/users");
await page.locator("#username").waitFor({ timeout: 60_000 });
await page.locator("#username").fill("admin");
await page.locator("#password").fill(process.env.KC_ADMIN_PASSWORD);
await page.locator("input[type=submit], button[type=submit], #kc-login").first().click();
await page.getByRole("link", { name: "alice", exact: true }).waitFor({ timeout: 60_000 });
await page.waitForTimeout(1500);

const frames = [];
let capturing = true;
const t0 = Date.now();
const now = () => +((Date.now() - t0) / 1000).toFixed(2);
const loop = (async () => {
  while (capturing) {
    const started = Date.now();
    const file = `${OUT}/frames-kc/${String(frames.length).padStart(5, "0")}.jpg`;
    await page.screenshot({ path: file, type: "jpeg", quality: 90 }).then(() => frames.push({ file, t: started }), () => {});
    await new Promise((r) => setTimeout(r, Math.max(0, 100 - (Date.now() - started))));
  }
})();

const ev = { step: "keycloak", user: "kc", start: 0 };
ev.users = now();
await page.waitForTimeout(4000);
for (const user of ["alice", "bob"]) {
  await page.getByRole("link", { name: "Users", exact: true }).first().click();
  await page.getByRole("link", { name: user, exact: true }).click();
  await page.getByRole("tab", { name: /Role mapping/ }).click();
  await page.getByRole("main").getByText(user === "alice" ? "developer" : "tester", { exact: true }).first().waitFor({ timeout: 30_000 });
  ev[user] = now();
  await page.waitForTimeout(4500);
}
ev.end = now();
capturing = false;
await loop;
const end = Date.now();
await browser.close();
writeFileSync(`${OUT}/kc-events.json`, JSON.stringify(ev, null, 1));

const lines = ["ffconcat version 1.0"];
frames.forEach((f, i) => lines.push(`file '${f.file}'`, `duration ${(((i + 1 < frames.length ? frames[i + 1].t : end) - f.t) / 1000).toFixed(3)}`));
lines.push(`file '${frames[frames.length - 1].file}'`);
writeFileSync(`${OUT}/frames-kc.ffconcat`, lines.join("\n") + "\n");
const r = spawnSync("ffmpeg", ["-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", `${OUT}/frames-kc.ffconcat`,
  "-vf", "scale=1920:1080:flags=lanczos,setsar=1,fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "20", `${OUT}/session-kc.mp4`], { stdio: "inherit" });
console.log(r.status === 0 ? `session-kc.mp4: ${frames.length} frames over ${ev.end}s` : "ffmpeg failed", JSON.stringify(ev));
