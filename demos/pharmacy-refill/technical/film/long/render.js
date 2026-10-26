// render.js <stage.html> <cues.json> <out.mp4> [stills=t1,t2,…] — renders a part's stage frame by frame (30 fps,
// window.render(t)) and pipes the frames to ffmpeg (CRF 16). With stills=, writes only those frames as PNGs beside <out>.
//   film4.py stage calls it: node render.js <take>/stages/I4/stage.html <take>/out/I4.stage.cues.json <take>/out/I4.stage.mp4
const { chromium } = require('playwright'); const { spawn } = require('child_process'); const fs = require('fs'), path = require('path');
const [stage, cuesF, outF, ...kv] = process.argv.slice(2); const A = Object.fromEntries(kv.map(s => s.split('=')));
const cues = JSON.parse(fs.readFileSync(cuesF, 'utf8')), FPS = 30;

(async () => {
  const b = await chromium.launch(); const p = await (await b.newContext({ viewport: { width: 1920, height: 1080 } })).newPage();
  await p.goto('file://' + path.resolve(stage) + '?cues=' + encodeURIComponent(JSON.stringify(cues)));
  await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(800);
  const at = async t => { await p.evaluate(t => window.render(t), t);   // a recorded frame swapped into an <img> must be decoded first
    await p.evaluate(() => Promise.all([...document.images].map(i => i.decode().catch(() => {})))); };
  if (A.stills) {
    for (const t of A.stills.split(',')) { await at(+t); await p.screenshot({ path: outF.replace(/\.mp4$/, '') + `-t${t}.png` }); }
    return b.close();
  }
  const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(FPS), '-i', '-', '-c:v', 'libx264', '-crf', '16',
    '-preset', 'slow', '-pix_fmt', 'yuv420p', outF], { stdio: ['pipe', 'inherit', 'inherit'] });
  const n = Math.ceil(cues.end * FPS);
  for (let i = 0; i < n; i++) { await at(i / FPS);
    const png = await p.screenshot({ type: 'png' }); if (!ff.stdin.write(png)) await new Promise(r => ff.stdin.once('drain', r)); }
  ff.stdin.end(); const code = await new Promise(r => ff.on('close', r)); await b.close();
  if (code !== 0) { console.error(`ffmpeg exited ${code}: ${outF} is not a finished section`); process.exit(1); }
  console.log(`${outF}: ${n} frames`);
})();
