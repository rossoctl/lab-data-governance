// map_shots.js <views.json> <out-dir> — the fixed state map's stills: one PNG per view (1920x1080 at device scale 2) and
// boxes.json with the rects the stages ring (in CSS px): every node, the card, the card's patient rows.
//   NODE_PATH=<playwright>/node_modules node v3/map_shots.js v3/map-views.json v3/map
// views.json: {"<name>": {"v": "intro|A|B|C|N", "card": {title, tag, sub, rows: [[key, value, patient?]], foot}}}
const { chromium } = require('playwright'); const fs = require('fs'), path = require('path');
const [viewsArg, outArg] = process.argv.slice(2);
const views = JSON.parse(fs.readFileSync(viewsArg, 'utf8')), out = path.resolve(outArg); fs.mkdirSync(out, { recursive: true });
const MAP = 'file://' + path.resolve(__dirname, 'map.html');

(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 })).newPage();
  const boxes = {};
  for (const [name, view] of Object.entries(views)) {
    await p.goto(`${MAP}?v=${view.v}&card=${encodeURIComponent(JSON.stringify(view.card || {}))}`);
    await p.waitForTimeout(900);
    await p.screenshot({ path: path.join(out, name + '.png') });
    boxes[name] = await p.evaluate(() => {
      const r = el => { if (!el) return null; const b = el.getBoundingClientRect(); return [b.x, b.y, b.width, b.height].map(v => Math.round(v)); };
      const nodes = Object.fromEntries(['patient', 'sched', 'report', 'export', 'records', 'fcst', 'review', 'lookup', 'courier', 'assistant', 'clinical', 'fulfil', 'delivery', 'llm', 'pg']
        .map(id => [id, window.boxOf(id).map(v => Math.round(v))]));
      return { nodes, card: r(document.getElementById('card')), box: r(document.querySelector('#card .box')),
               tag: r(document.querySelector('#card .tag')), patient: (() => { const ps = [...document.querySelectorAll('#card .box .p')];
                 if (!ps.length) return null; const rs = ps.map(e => e.parentElement.getBoundingClientRect());
                 const x = Math.min(...rs.map(q => q.x)), y = Math.min(...rs.map(q => q.y));
                 return [x, y, Math.max(...rs.map(q => q.x + q.width)) - x, Math.max(...rs.map(q => q.y + q.height)) - y].map(Math.round); })() };
    });
  }
  fs.writeFileSync(path.join(out, 'boxes.json'), JSON.stringify(boxes, null, 1));
  console.log(Object.keys(boxes).join(' '));
  await b.close();
})();
