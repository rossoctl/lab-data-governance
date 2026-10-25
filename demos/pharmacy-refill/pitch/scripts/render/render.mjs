// Render film/film.html to video by seeking its timeline frame by frame in headless Chrome.
//
//   node render.mjs                     full film -> build/film_silent.mp4
//   node render.mjs --stills 5,47.9,69  preview frames -> build/preview/t_<sec>.png
//   node render.mjs --from 45 --to 55   render only a section (for quick checks)
import { spawn } from "node:child_process";
import { createReadStream, mkdirSync, statSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const FPS = 30;

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".png": "image/png", ".ttf": "font/ttf" };

// Serve the project root over http so fonts and images load without file:// CORS limits.
function serve() {
  const server = http.createServer((req, res) => {
    const p = path.join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname));
    if (!p.startsWith(ROOT)) return res.writeHead(403).end();
    try { statSync(p); } catch { return res.writeHead(404).end(); }
    res.writeHead(200, { "content-type": TYPES[path.extname(p)] || "application/octet-stream" });
    createReadStream(p).pipe(res);
  });
  return new Promise((ok) => server.listen(0, "127.0.0.1", () => ok(server)));
}

const server = await serve();
const url = `http://127.0.0.1:${server.address().port}/film/film.html`;
const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
await page.goto(url);
await page.waitForFunction(() => window.ready === true);
const duration = await page.evaluate(() => window.DURATION);

// Seek, then wait two animation frames so Chrome has rastered every tile before capture.
async function seekSettled(t) {
  await page.evaluate((t) => new Promise((done) => {
    window.seek(t);
    requestAnimationFrame(() => requestAnimationFrame(done));
  }), t);
}

async function frame(t) {
  await seekSettled(t);
  return page.screenshot({ type: "png" });
}

if (opt("--stills")) {
  const dir = path.join(ROOT, "build/preview");
  mkdirSync(dir, { recursive: true });
  for (const s of opt("--stills").split(",").map(Number)) {
    await seekSettled(s);
    await page.screenshot({ path: path.join(dir, `t_${s.toFixed(2)}.png`) });
    console.log(`still ${s}`);
  }
} else {
  const from = Number(opt("--from") ?? 0);
  const to = Number(opt("--to") ?? duration);
  const out = path.join(ROOT, opt("--out") ?? (opt("--from") ? "build/section.mp4" : "build/film_silent.mp4"));
  const ff = spawn("ffmpeg", ["-v", "error", "-y", "-f", "image2pipe", "-framerate", String(FPS), "-c:v", "png", "-i", "-",
    "-vf", "scale=out_color_matrix=bt709:out_range=tv", "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-r", String(FPS), out],
    { stdio: ["pipe", "inherit", "inherit"] });
  const n = Math.round((to - from) * FPS);
  const t0 = Date.now();
  for (let i = 0; i < n; i++) {
    const buf = await frame(from + i / FPS);
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once("drain", r));
    if (i % 150 === 0) console.log(`frame ${i}/${n}  t=${(from + i / FPS).toFixed(1)}s  ${((Date.now() - t0) / 1000).toFixed(0)}s elapsed`);
  }
  ff.stdin.end();
  await new Promise((r) => ff.on("close", r));
  console.log(`wrote ${out}`);
}

await browser.close();
server.close();
