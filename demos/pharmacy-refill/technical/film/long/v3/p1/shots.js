// shots.js <out-dir> — P1's platform screens: rossoctl's Agent and Tool catalogs (namespace pharmacy) on the take's cluster,
// 1920x1080 at device scale 2, plus the boxes the zoom-through needs (the pharmacy-assistant row, the tables).
//   NODE_PATH=<playwright>/node_modules node p1/shots.js p1/shots
const { chromium } = require('playwright'); const fs = require('fs'), path = require('path');
const out = path.resolve(process.argv[2]); fs.mkdirSync(out, { recursive: true });
const PORT = process.env.HTTP_PORT || '8080', ROSSO = `http://rossoctl-ui.localtest.me:${PORT}`;

(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 })).newPage();
  await p.route(u => /localtest\.me:\d+/.test(u.href) && !u.href.includes(':' + PORT + '/'), r => r.abort());   // only this take's cluster
  const boxes = {};
  for (const page of ['agents', 'tools']) {
    await p.goto(ROSSO + '/' + page, { waitUntil: 'networkidle' });
    if (!(await p.getByText('pharmacy', { exact: true }).first().isVisible().catch(() => false)))
      await p.getByText('team1', { exact: true }).first().click();
    await p.getByText('pharmacy', { exact: true }).first().click(); await p.waitForLoadState('networkidle'); await p.waitForTimeout(1500);
    await p.mouse.move(5, 1075);
    const table = await p.locator('table').first().boundingBox();
    const row = page === 'agents' ? await p.locator('tr', { hasText: 'pharmacy-assistant' }).first().boundingBox() : null;
    const R = r => r && [r.x, r.y, r.width, r.height].map(Math.round);   // [x, y, w, h], as film4.py reads them
    boxes[page] = { table: R(table), row: R(row), rows: await p.locator('table tbody tr').count() };
    await p.screenshot({ path: path.join(out, page + '.png') });
  }
  fs.writeFileSync(path.join(out, 'boxes.json'), JSON.stringify(boxes, null, 1)); console.log(JSON.stringify(boxes));
  await b.close();
})();
