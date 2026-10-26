// shots.js <out-dir> — the stills of Sentry's policy on the take's cluster: the Rules page and rule DG-001, 1920x1080 at device
// scale 2, plus the boxes the rings need (the rules table, DG-001's conditions, its enforcement suggestion).
//   NODE_PATH=<playwright>/node_modules node p3/shots.js p3/shots
const { chromium } = require('playwright'); const fs = require('fs'), path = require('path');
const out = path.resolve(process.argv[2]); fs.mkdirSync(out, { recursive: true });
const PORT = process.env.HTTP_PORT || '8080', DG = `http://dg.localtest.me:${PORT}`;

(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 })).newPage();
  await p.route(u => /localtest\.me:\d+/.test(u.href) && !u.href.includes(':' + PORT + '/'), r => r.abort());   // only this take's cluster
  const box = async l => l.first().boundingBox().catch(() => null), boxes = {};
  await p.goto(DG + '/ui/risk/rules', { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(5000); await p.mouse.move(5, 1075);
  boxes.rules = await box(p.locator('table')); boxes.rows = await p.locator('table tbody tr').count();
  await p.screenshot({ path: path.join(out, 'rules.png') });
  await p.goto(DG + '/ui/risk/rules/DG-001', { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(5000); await p.mouse.move(5, 1075);
  boxes.conditions = await box(p.locator('table')); boxes.enforcement = await box(p.getByText('Enforcement suggestion').locator('..'));
  await p.screenshot({ path: path.join(out, 'dg001.png') });
  fs.writeFileSync(path.join(out, 'boxes.json'), JSON.stringify(boxes, null, 1)); console.log(JSON.stringify(boxes));
  await b.close();
})();
