// rec_live.js <out-dir> <label> — the film's live recording: starts one refill on the take's cluster through take/take.sh
// (take.sh run <label> refill rosa, which records the trace id in the take's traces.txt), finds Sentry's new trace and
// records its graph page while the sidecars' exchanges arrive (1680x855). Writes <out>/<label>.webm + <label>.marks.json.
// Requests to any other cluster's port are blocked. take/sequence.sh runs it for run A (TAKE= the take dir).
const { chromium } = require('playwright'); const { spawn } = require('child_process');
const fs = require('fs'), path = require('path');
const [outArg, label] = process.argv.slice(2); const out = path.resolve(outArg); fs.mkdirSync(out, { recursive: true });
const PORT = process.env.HTTP_PORT || '8080', W = 1680, H = 855, DG = `http://dg.localtest.me:${PORT}`;
const TAKE = path.resolve(__dirname, '../../../take/take.sh');

(async () => {
  const b = await chromium.launch({ args: ['--force-device-scale-factor=1'] });
  const ctx = await b.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1, recordVideo: { dir: out, size: { width: W, height: H } } });
  await ctx.route(u => /localtest\.me:\d+/.test(u.href) && !u.href.includes(':' + PORT + '/'), r => { console.log('BLOCKED (another cluster):', r.request().url()); r.abort(); });
  const p = await ctx.newPage(); const T0 = Date.now(); const marks = [];
  const mark = (ev, o = {}) => { marks.push({ ev, at: +((Date.now() - T0) / 1000).toFixed(2), ...o }); console.log(((Date.now() - T0) / 1000).toFixed(1), ev, o.text || ''); };
  const hold = ms => p.waitForTimeout(ms);
  const ids = async () => (await (await fetch(DG + '/api/traces?limit=50')).json()).traces.map(t => t.trace_id);
  const seen = new Set(await ids());
  await p.goto(DG + '/ui/traces', { waitUntil: 'networkidle' }); mark('seg', { seg: 'traces' }); await hold(1500);
  const conv = spawn('bash', [TAKE, 'run', label, 'refill', 'rosa'], { stdio: ['ignore', 'pipe', 'pipe'] });
  let convOut = '', done = false; conv.stdout.on('data', d => { convOut += d; process.stdout.write(d); }); conv.stderr.on('data', d => { convOut += d; });
  conv.on('exit', c => { done = true; mark('conv-exit', { text: String(c) }); });
  mark('conv-start', { text: label });
  // the conversation's trace: the first new trace rooted at the assistant's A2A message (not a tool's start-up trace)
  let tid = null;
  for (let i = 0; i < 240 && !tid; i++) { await hold(1000);
    const ts = (await (await fetch(DG + '/api/traces?limit=10')).json()).traces;
    const n = ts.find(t => !seen.has(t.trace_id) && ((t.listing_root || {}).name || '').startsWith('pharmacy-assistant a2a')); if (n) tid = n.trace_id; }
  if (!tid) throw new Error('no new trace');
  mark('trace', { text: tid });
  await hold(2500);
  await p.goto(DG + `/ui/traces/${tid}/graph`, { waitUntil: 'domcontentloaded' }); await hold(1500); mark('seg', { seg: 'graph-live' });
  while (!done) await hold(1000);
  for (let i = 0; i < 60; i++) await hold(1000);   // derivation catches up after the last exchange
  await p.goto(DG + `/ui/traces/${tid}/graph`, { waitUntil: 'domcontentloaded' }); await hold(3500); mark('seg', { seg: 'graph-final' });
  await hold(4000);
  fs.writeFileSync(path.join(out, `${label}.conv.log`), convOut);
  fs.writeFileSync(path.join(out, `${label}.marks.json`), JSON.stringify(marks, null, 1));
  const v = p.video(); await ctx.close(); fs.renameSync(await v.path(), path.join(out, `${label}.webm`));
  await b.close();
})();
