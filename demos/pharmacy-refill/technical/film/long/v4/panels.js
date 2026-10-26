// panels.js <out-dir> <name>=<page.html?query> … — film v4's own drawings: each page screenshotted at 1920x1080, device
// scale 2, to <out-dir>/<name>.png, and the box of every element with an id to <out-dir>/boxes.json ({name: {id: [x, y,
// w, h]}}, CSS px), so a stage's rings come from the page, never from hand-copied numbers.
//   NODE_PATH=<playwright>/node_modules node v4/panels.js v4/panels/out pipeline=v4/panels/pipeline.html
const { chromium } = require('playwright'); const fs = require('fs'), path = require('path');
const [outArg, ...pages] = process.argv.slice(2); const out = path.resolve(outArg); fs.mkdirSync(out, { recursive: true });
const bf = path.join(out, 'boxes.json'), boxes = fs.existsSync(bf) ? JSON.parse(fs.readFileSync(bf, 'utf8')) : {};

(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 })).newPage();
  for (const spec of pages) {
    const [name, page] = spec.split(/=(.*)/s); const [file, q] = page.split(/\?(.*)/s);
    await p.goto('file://' + path.resolve(file) + (q ? '?' + q : ''));
    await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(700);
    await p.screenshot({ path: path.join(out, name + '.png') });
    boxes[name] = await p.evaluate(() => Object.fromEntries([...document.querySelectorAll('[id]')].map(e => {
      const r = e.getBoundingClientRect(); return [e.id, [r.x, r.y, r.width, r.height].map(Math.round)]; })));
    console.log(name, Object.keys(boxes[name]).join(' '));
  }
  fs.writeFileSync(bf, JSON.stringify(boxes, null, 1));
  await b.close();
})();
