// shots.js <out-dir> mode=<mode> t=<trace> [rep=<interaction>] [look=…] [courier=…] [rec=…] — the film's stills of Sentry on
// the take's cluster (film.env), 1920x1080 at device scale 2, and boxes.json with what the rings frame (CSS px). Modes:
//   tap      run A, risk off: the whole graph; one exchange opened (who called whom, what was sent); the risk layer on
//   level1   run A: the report exchange red, its payload, its classification, the verdict and the call that went through;
//            courier=: the courier exchange, its payload and its allow
//   level2   run B: the report's payload, the classifier's reading beside the carried-in label, the number tracked on
//            the whole graph (hop pairs boxed), the lineage card, the verdict
//   night    run N1 (t) after run B (b=): the trace list with both; B's records write (rec=); the nightly's records
//            answer (ans=) read alone and carried in across sessions; the export's verdict and its link back
//   fixed    run C (t) and N2 (n2=): each report's payload and its allow
//   take/film.sh stills runs every mode with the take's ids (<take>/ids.json)
const { chromium } = require('playwright'); const fs = require('fs'), path = require('path');
const [outArg, ...kv] = process.argv.slice(2); const A = Object.fromEntries(kv.map(s => s.split(/=(.*)/s).slice(0, 2)));
const out = path.resolve(outArg); fs.mkdirSync(out, { recursive: true });
const PORT = process.env.HTTP_PORT || '8080', DG = `http://dg.localtest.me:${PORT}`;   // film.env (take/film.sh exports it)

(async () => {
  const b = await chromium.launch();
  const p = await (await b.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2 })).newPage();
  await p.route(u => /localtest\.me:\d+/.test(u.href) && !u.href.includes(':' + PORT + '/'), r => r.abort());   // only this take's cluster
  const panel = p.locator('.dg-detail-panel'), boxes = {};
  const R = r => r && [r.x, r.y, r.width, r.height].map(Math.round);
  const bb = async l => R(await l.first().boundingBox().catch(() => null));
  const union = async (...ls) => { const bs = (await Promise.all(ls.map(l => l.first().boundingBox().catch(() => null)))).filter(Boolean);
    if (!bs.length) return null; const x = Math.min(...bs.map(b => b.x)), y = Math.min(...bs.map(b => b.y));
    return [x, y, Math.max(...bs.map(b => b.x + b.width)) - x, Math.max(...bs.map(b => b.y + b.height)) - y].map(Math.round); };
  const node = re => p.locator('svg text').filter({ hasText: re });
  const pair = (a, z) => union(node(a), node(z));
  const shot = async name => { await p.mouse.move(5, 1075); await p.waitForTimeout(600); await p.screenshot({ path: path.join(out, name + '.png') }); console.log('shot', name); };
  const center = async l => { await l.first().evaluate(e => e.scrollIntoView({ block: 'center' })); await p.waitForTimeout(700); };
  const open = async (t, iid, risk = 1) => {
    await p.goto(`${DG}/ui/traces/${t}/graph?${risk ? 'risk=1&' : ''}${iid ? 'iid=' + iid : ''}`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(6000);
    await p.getByRole('button', { name: 'Fit to Screen' }).click().catch(() => {}); await p.waitForTimeout(1500); };
  const tab = async name => { await panel.getByRole('tab', { name }).first().click(); await p.waitForTimeout(1500); };
  const pre = () => panel.locator('pre').first();
  const verdict = async name => { const rl = panel.getByText('Risk level', { exact: true }).first(); await center(rl);
    const last = panel.getByText(/restricted_external_sharing|pii_to_untrusted_external/).last();
    boxes[name] = await union(rl, (await last.count()) ? last : panel.getByText('Rules', { exact: true }).first()); await shot(name); };
  const allow = async name => { const a = panel.locator('div').filter({ has: p.getByText('Risk', { exact: true }) }).filter({ hasText: 'allow' }).last();
    await center(a); boxes[name] = await bb(a); await shot(name); };
  const graphBox = async () => bb(p.locator('.react-flow, svg').filter({ has: p.locator('text') }).first());
  // the graph's drawn content: the union of every label in its svg (nodes and edge chips), in CSS px
  const content = () => p.locator('svg text').evaluateAll(ts => { const rs = ts.map(t => t.getBoundingClientRect()).filter(r => r.width && r.x < 1340);
    const x = Math.min(...rs.map(r => r.x)), y = Math.min(...rs.map(r => r.y));
    return [x, y, Math.max(...rs.map(r => r.x + r.width)) - x, Math.max(...rs.map(r => r.y + r.height)) - y].map(Math.round); });

  if (A.mode === 'tap') {
    await open(A.t, null, 0); boxes.graph = await graphBox(); boxes.nodes = await content();
    boxes.lookPair = await pair(/^pharmacy-assistant$/, /^lookup-patient$/); await shot('graph');
    await open(A.t, A.look, 0); boxes.who = await bb(panel.getByText('summary', { exact: true }).locator('..'));
    boxes.whoPair = await pair(/^pharmacy-assistant$/, /^lookup-patient$/); await shot('exchange');
    await center(pre()); boxes.what = await bb(pre()); await shot('sent');
    await open(A.t, null, 1); boxes.red = await pair(/^report-dispense$/, /^demand-forec/); await shot('risk');
  }
  if (A.mode === 'level1') {
    await open(A.t, A.rep); boxes.red = await pair(/^report-dispense$/, /^demand-forec/); await shot('red');
    await center(pre()); boxes.payload = await bb(pre()); await shot('payload');
    await tab('Classification'); const found = panel.getByText('Found here (classifier)').first(); await center(found);
    const tbl = panel.locator('table').filter({ hasText: 'Origin' }).first();
    boxes.found = await union(found, panel.getByText('Carried in (data lineage)').first());
    boxes.labels = await bb(tbl); boxes.name = await bb(tbl.locator('tr', { hasText: 'Rosa Delgado' }));
    await shot('cls');
    await center(tbl.locator('tr', { hasText: 'Rosa Delgado' })); boxes.nameRow = await bb(tbl.locator('tr', { hasText: 'Rosa Delgado' }));
    boxes.numRows = await union(tbl.locator('tr', { hasText: 'OPID' }), tbl.locator('tr', { hasText: 'MRN' })); await shot('cls-name');
    await tab('Payload').catch(() => {});
    await verdict('verdict');
    const http = panel.getByText('POST → 200 (ok)').first(); await center(http); boxes.http = await bb(http); await shot('http');
    if (A.courier) { await open(A.t, A.courier); boxes.courier = await pair(/^schedule-delivery$/, /^courier/); await shot('courier');
      await center(pre()); boxes.courierPayload = await bb(pre()); await shot('courier-payload'); await allow('courier-allow'); }
  }
  if (A.mode === 'level2') {
    await open(A.t, A.rep); boxes.red = await pair(/^report-dispense$/, /^demand-forec/);
    await center(pre()); boxes.payload = await bb(pre()); await shot('payload');
    await tab('Classification'); const tbl = panel.locator('table').filter({ hasText: 'Origin' }).first(); await center(tbl.locator('tr', { hasText: 'OPID' }));
    boxes.alone = await bb(tbl.locator('tr', { hasText: 'OPID' })); boxes.carried = await bb(tbl.locator('tr', { hasText: 'MRN' }));
    boxes.readings = await union(tbl.locator('tr', { hasText: 'OPID' }), tbl.locator('tr', { hasText: 'MRN' })); await shot('readings');
    await tab('Data lineage'); const cb = panel.locator('input[type=checkbox]');
    const idx = await cb.evaluateAll(cs => cs.findIndex(c => { let e = c; for (let i = 0; i < 4 && e; i++) { e = e.parentElement;
      if (e && /4471820/.test(e.innerText) && /MRN/.test(e.innerText) && !/Metformin/.test(e.innerText)) return true; } return false; }));
    if (idx < 0) throw new Error('no MRN row to track');
    const blk = cb.nth(idx).locator('xpath=ancestor::*[contains(., "origin")][1]'); await center(blk); boxes.origin = await bb(blk); await shot('origin');
    await cb.nth(idx).check(); await p.waitForTimeout(2500);
    boxes.hop1 = await pair(/^pharmacy-assistant$/, /^lookup-patient$/); boxes.hop2 = await pair(/^pharmacy-assistant$/, /^fulfillment-agent$/);
    boxes.hop3 = await pair(/^fulfillment-agent$/, /^report-dispense$/); boxes.hop4 = await pair(/^report-dispense$/, /^demand-forec/);
    boxes.graph = await graphBox(); boxes.nodes = await content(); await shot('tracked');
    await tab('Payload').catch(() => {}); await verdict('verdict');
  }
  if (A.mode === 'night') {
    // N1's row by its root span's start to the second (a later nightly, N2, is on the same list), B's likewise; the
    // list holds the whole take (24 h) and is paged ("Load more") until N1's row is there, then scrolled to it
    if (!A.nAt || !A.bAt) throw new Error('night mode needs nAt= and bAt= (take/ids.py writes them)');
    await p.goto(`${DG}/ui/traces?window=24h`, { waitUntil: 'domcontentloaded' }); await p.waitForTimeout(5000);
    const rowN = p.locator('tr').filter({ hasText: 'pharmacy-ops a2a' }).filter({ hasText: A.nAt });
    for (let i = 0; i < 20 && !(await rowN.count()); i++) {
      const more = p.getByRole('button', { name: 'Load more' }); if (!(await more.count())) break;
      await more.click(); await p.waitForTimeout(2500); }
    if (!(await rowN.count())) throw new Error(`no nightly row started at ${A.nAt} UTC on the trace list (run N1)`);
    await center(rowN);
    boxes.rowN = await bb(rowN);
    boxes.rowB = await bb(p.locator('tr').filter({ hasText: A.bAt }).filter({ hasText: 'pharmacy-assistant' }));
    boxes.rows = await bb(p.locator('table').first()); await shot('list');
    await open(A.b, A.rec); boxes.write = await pair(/^review-refill$/, /^records/); await center(pre()); boxes.writePayload = await bb(pre()); await shot('write');
    await open(A.t, A.ans); boxes.claim = await pair(/^export-dispense-batch$/, /^records/);
    await panel.getByRole('tab', { name: 'Response' }).first().click().catch(() => {}); await p.waitForTimeout(1200);
    await tab('Classification'); const tbl = panel.locator('table').filter({ hasText: 'Origin' }).first(); await center(tbl.locator('tr', { hasText: 'OPID' }));
    boxes.alone = await bb(tbl.locator('tr', { hasText: 'OPID' })); boxes.carried = await bb(tbl.locator('tr', { hasText: 'MRN' }));
    boxes.readings = await union(tbl.locator('tr', { hasText: 'OPID' }), tbl.locator('tr', { hasText: 'MRN' })); await shot('answer');
    await panel.getByRole('tab', { name: 'Data lineage' }).locator('visible=true').first().click(); await p.waitForTimeout(1500);
    const cb = panel.locator('input[type=checkbox]');
    const idx = await cb.evaluateAll(cs => cs.findIndex(c => { let e = c; for (let i = 0; i < 4 && e; i++) { e = e.parentElement;
      if (e && e.offsetParent && /4471820/.test(e.innerText) && /MRN/.test(e.innerText) && /in trace/.test(e.innerText) && !/Metformin/.test(e.innerText)) return true; } return false; }));
    if (idx < 0) throw new Error('no cross-session MRN card');
    const card = cb.nth(idx).locator('xpath=ancestor::*[contains(., "origin")][1]'); await center(card); boxes.cross = await bb(card); await shot('cross');
    await open(A.t, A.rep); boxes.red = await pair(/^export-dispense-batch$/, /^demand-forec/); await shot('red');
    await verdict('verdict');
  }
  if (A.mode === 'fixed') {
    await open(A.t, A.rep); boxes.c = await pair(/^report-dispense$/, /^demand-forec/); await center(pre()); boxes.cPayload = await bb(pre()); await shot('c-payload');
    await allow('c-allow');
    await open(A.n2, A.n2rep); boxes.n2 = await pair(/^export-dispense-batch$/, /^demand-forec/); await center(pre()); boxes.n2Payload = await bb(pre()); await shot('n2-payload');
    await allow('n2-allow');
  }
  fs.writeFileSync(path.join(out, 'boxes.json'), JSON.stringify(boxes, null, 1)); console.log(JSON.stringify(boxes));
  await b.close();
})();
