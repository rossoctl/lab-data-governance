/* P-interactions graph prototype — graph view. THROWAWAY.
 *
 * Adds a "Graphs (proto)" tab to the trace-tree page showing the three
 * intermediate stages produced by the new graph-based extractor:
 *   Step 1     — base graph (white nodes + traceparent edges)
 *   Step 2.a/b — colored graph (Gray/Black, additive edge colors,
 *                combined-span duplicates, between-boundary flags)
 *   Step 2.c   — entity graph (one node per connected component)
 *
 * Loads from /proto/graphs/<trace_id>.
 */

(function () {
  const treeBtn  = document.getElementById('view-tree-btn');
  const flowBtn  = document.getElementById('view-flow-btn');
  const graphBtn = document.getElementById('view-graph-btn');
  if (!graphBtn) return;

  const treeEl        = document.getElementById('tree');
  const flowSection   = document.getElementById('flow-section');
  const graphSection  = document.getElementById('graph-section');
  const graphEmpty    = document.getElementById('graph-empty');
  const graphContent  = document.getElementById('graph-content');

  let graphLoaded = false;

  function getTraceId() {
    // Match the trace id after a `/trace/` segment anywhere in the path —
    // tolerant of a gateway path prefix and a trailing slash/query — then
    // fall back to the last path segment. The previous anchored
    // `^/trace/` regex returned null whenever the page was served under a
    // prefix, and loadGraph() then bailed before fetching (issue: graph
    // tab stuck on the "run the CLI" hint despite populated scratch tables).
    const m = window.location.pathname.match(/\/trace\/([0-9a-fA-F]+)/);
    if (m) return m[1];
    const segs = window.location.pathname.split('/').filter(Boolean);
    return segs.length ? segs[segs.length - 1] : null;
  }

  function setAllViews(view) {
    treeBtn.classList.toggle('active', view === 'tree');
    flowBtn.classList.toggle('active', view === 'flow');
    graphBtn.classList.toggle('active', view === 'graph');

    treeEl.style.display       = view === 'tree'  ? 'block' : 'none';
    flowSection.style.display  = view === 'flow'  ? 'block' : 'none';
    graphSection.style.display = view === 'graph' ? 'block' : 'none';

    if (view === 'graph' && !graphLoaded) loadGraph();
  }

  treeBtn.onclick  = () => setAllViews('tree');
  flowBtn.onclick  = () => setAllViews('flow');
  graphBtn.onclick = () => setAllViews('graph');

  // -------------------------------------------------------------------------
  // Sub-tab switching (base / colored / entity)
  // -------------------------------------------------------------------------

  const SUB_TABS = ['base', 'colored', 'entity'];

  const subtabBtns = document.querySelectorAll('.graph-subtab-btn');
  subtabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.subtab;
      subtabBtns.forEach(b => b.classList.toggle('active', b.dataset.subtab === target));
      SUB_TABS.forEach(name => {
        const el = document.getElementById('graph-subtab-' + name);
        if (el) el.style.display = name === target ? '' : 'none';
      });
    });
  });

  // -------------------------------------------------------------------------
  // Data loading
  // -------------------------------------------------------------------------

  async function loadGraph() {
    const traceId = getTraceId();
    if (!traceId) {
      // Don't latch graphLoaded — leave the tab retryable rather than
      // permanently stuck if the trace id can't be resolved.
      graphEmpty.style.display = '';
      graphEmpty.textContent = 'Could not determine trace id from the URL.';
      return;
    }
    try {
      const resp = await fetch('/proto/graphs/' + encodeURIComponent(traceId));
      if (!resp.ok) {
        graphEmpty.style.display = '';
        graphEmpty.textContent = 'Failed to load: HTTP ' + resp.status;
        return;
      }
      const data = await resp.json();
      renderGraphs(data);
      // Only latch after a successful render, so a transient failure or an
      // unresolved trace id leaves the tab retryable on the next click.
      graphLoaded = true;
    } catch (e) {
      graphEmpty.style.display = '';
      graphEmpty.textContent = 'Failed to load: ' + e;
    }
  }

  // -------------------------------------------------------------------------
  // Topological sort (Kahn's algorithm). Falls back to original order on cycle.
  // -------------------------------------------------------------------------

  function topoSort(nodes, edges) {
    const nodeIds = new Set(nodes.map(n => n.id));
    const inDegree = new Map(nodes.map(n => [n.id, 0]));
    const adj = new Map(nodes.map(n => [n.id, []]));

    for (const e of edges) {
      if (!nodeIds.has(e.from) || !nodeIds.has(e.to)) continue;
      adj.get(e.from).push(e.to);
      inDegree.set(e.to, (inDegree.get(e.to) || 0) + 1);
    }

    const queue = nodes.filter(n => inDegree.get(n.id) === 0).map(n => n.id);
    const sorted = [];
    while (queue.length) {
      const id = queue.shift();
      sorted.push(id);
      for (const nid of (adj.get(id) || [])) {
        const d = inDegree.get(nid) - 1;
        inDegree.set(nid, d);
        if (d === 0) queue.push(nid);
      }
    }
    if (sorted.length === nodes.length) {
      const pos = new Map(sorted.map((id, i) => [id, i]));
      return [...nodes].sort((a, b) => pos.get(a.id) - pos.get(b.id));
    }
    return nodes;
  }

  // -------------------------------------------------------------------------
  // Rendering helpers
  // -------------------------------------------------------------------------

  function shortId(id) {
    return id ? id.slice(0, 8) + '…' : '—';
  }

  function shortScope(scope) {
    if (!scope) return '—';
    const s = Array.isArray(scope) ? scope.join(', ') : scope;
    return s
      .replace(/opentelemetry\.instrumentation\./g, 'otel.')
      .replace(/openinference\.instrumentation\./g, 'oi.');
  }

  function colorPill(color) {
    const el = document.createElement('span');
    el.className = 'color-pill col-' + (color || 'white');
    el.textContent = color || '?';
    return el;
  }

  function markerPill(text, cls) {
    const el = document.createElement('span');
    el.className = 'marker-pill ' + cls;
    el.textContent = text;
    return el;
  }

  function nodeLabel(node) {
    return node.label || '(' + (node.node_type || 'node') + ' ' + shortId(node.id) + ')';
  }

  // -------------------------------------------------------------------------
  // Span tooltip
  // -------------------------------------------------------------------------

  const tooltip = document.createElement('div');
  tooltip.id = 'graph-span-tooltip';
  tooltip.style.cssText = [
    'position:fixed', 'z-index:9999', 'background:#1a1a1a',
    'border:1px solid #444', 'border-radius:4px',
    'padding:0.6rem 0.85rem', 'font-size:0.76rem', 'color:#e0e0e0',
    'pointer-events:none', 'display:none',
    'max-width:480px', 'max-height:70vh', 'overflow-y:auto',
    'box-shadow:0 4px 20px rgba(0,0,0,0.7)', 'line-height:1.5',
    'font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif',
  ].join(';');
  document.body.appendChild(tooltip);

  const spanCache = new Map();

  async function fetchSpanForTooltip(sid, traceId) {
    if (spanCache.has(sid)) return spanCache.get(sid);
    try {
      const params = new URLSearchParams({ trace_id: traceId, span_id: sid });
      const resp = await fetch('/spans?' + params.toString());
      if (!resp.ok) { spanCache.set(sid, null); return null; }
      const data = await resp.json();
      const span = (data.spans && data.spans[0]) || null;
      spanCache.set(sid, span);
      return span;
    } catch (_) {
      spanCache.set(sid, null);
      return null;
    }
  }

  function formatTooltip(span) {
    if (!span) return '<em style="color:#888">span not found</em>';

    function row(label, value) {
      if (value == null || value === '') return '';
      const v = (typeof value === 'object')
        ? `<pre style="margin:0;white-space:pre-wrap;font-size:0.73rem;color:#c9c9c9;background:#111;padding:0.25rem 0.4rem;border-radius:2px;max-height:120px;overflow-y:auto">${JSON.stringify(value, null, 2)}</pre>`
        : `<span style="font-family:ui-monospace,monospace;color:#e0e0e0">${String(value)}</span>`;
      return `<tr><td style="color:#888;padding:0.1rem 0.6rem 0.1rem 0;white-space:nowrap;vertical-align:top">${label}</td><td style="vertical-align:top">${v}</td></tr>`;
    }

    const status = span.error === true
      ? '<span style="color:#f85149">⚠ error</span>'
      : span.error === false
        ? '<span style="color:#6acf6a">✓ ok</span>'
        : '<span style="color:#888">unset</span>';

    let duration = '';
    if (span.started_at && span.ended_at) {
      duration = ' <span style="color:#888">(' + (new Date(span.ended_at) - new Date(span.started_at)).toFixed(1) + ' ms)</span>';
    } else if (span.started_at) {
      duration = ' <span style="color:#888">(open)</span>';
    }

    const title = `<div style="font-weight:600;font-size:0.82rem;margin-bottom:0.4rem;color:#92c5f9">${span.name || '(unnamed)'}</div>`;

    const table = [
      row('service', span.service_name),
      row('kind', span.kind),
      row('status', status + (span.status_message ? ' ' + span.status_message : '')),
      row('span_id', span.span_id),
      row('parent_id', span.parent_id),
      row('trace_id', span.trace_id),
      row('started_at', span.started_at ? span.started_at.replace('T', ' ').replace(/\.\d+Z$/, 'Z') + duration : null),
      row('ended_at', span.ended_at ? span.ended_at.replace('T', ' ').replace(/\.\d+Z$/, 'Z') : null),
      row('observed_at', span.observed_at ? span.observed_at.replace('T', ' ').replace(/\.\d+Z$/, 'Z') : null),
      row('seq', span.seq),
      row('arrival_seq', span.arrival_seq),
      row('scope', span.scope),
      row('attributes', span.attributes && Object.keys(span.attributes).length ? span.attributes : null),
      row('resource_attributes', span.resource_attributes && Object.keys(span.resource_attributes).length ? span.resource_attributes : null),
      row('events', span.events),
      row('links', span.links),
      row('otlp', span.otlp && Object.keys(span.otlp).length ? span.otlp : null),
    ].filter(Boolean).join('');

    return title + `<table style="border-collapse:collapse;width:100%">${table}</table>`;
  }

  let tooltipTarget = null;

  document.addEventListener('mousemove', (ev) => {
    if (tooltip.style.display === 'none') return;
    const gap = 14;
    let x = ev.clientX + gap;
    let y = ev.clientY + gap;
    const tw = tooltip.offsetWidth;
    const th = tooltip.offsetHeight;
    if (x + tw > window.innerWidth - 8) x = ev.clientX - tw - gap;
    if (y + th > window.innerHeight - 8) y = ev.clientY - th - gap;
    tooltip.style.left = x + 'px';
    tooltip.style.top  = y + 'px';
  });

  // -------------------------------------------------------------------------
  // Span highlight (pulse on the tree row after navigation)
  // -------------------------------------------------------------------------

  if (!document.getElementById('graph-highlight-style')) {
    const s = document.createElement('style');
    s.id = 'graph-highlight-style';
    s.textContent = `
      @keyframes span-highlight-pulse {
        0%   { background: #4a3800; }
        60%  { background: #2c2c00; }
        100% { background: #1c1c1c; }
      }
      .row.graph-highlight {
        animation: span-highlight-pulse 1.6s ease-out forwards;
      }
    `;
    document.head.appendChild(s);
  }

  function highlightTreeRow(sid) {
    const row = document.querySelector('.row[data-span-id="' + sid + '"]');
    if (!row) return;
    row.classList.remove('graph-highlight');
    void row.offsetWidth;
    row.classList.add('graph-highlight');
    row.addEventListener('animationend', () => row.classList.remove('graph-highlight'), { once: true });
  }

  // -------------------------------------------------------------------------
  // Span link (clickable + hover-tooltip)
  // -------------------------------------------------------------------------

  function spanLink(sid) {
    const traceId = getTraceId();
    const link = document.createElement('span');
    link.className = 'span-link';
    link.textContent = sid.slice(0, 10);

    link.addEventListener('mouseenter', async (ev) => {
      tooltipTarget = sid;
      tooltip.innerHTML = '<span style="color:#888">Loading…</span>';
      tooltip.style.display = 'block';
      tooltip.style.left = (ev.clientX + 14) + 'px';
      tooltip.style.top  = (ev.clientY + 14) + 'px';
      const span = await fetchSpanForTooltip(sid, traceId);
      if (tooltipTarget !== sid) return;
      tooltip.innerHTML = formatTooltip(span);
    });

    link.addEventListener('mouseleave', () => {
      tooltipTarget = null;
      tooltip.style.display = 'none';
    });

    link.addEventListener('click', () => {
      tooltip.style.display = 'none';
      setAllViews('tree');
      if (window.TraceTreeNav && window.TraceTreeNav.selectSpanByIdInTree) {
        window.TraceTreeNav.selectSpanByIdInTree(sid)
          .then(() => highlightTreeRow(sid))
          .catch(() => {});
      }
    });

    return link;
  }

  function spanIdsCell(node) {
    const spanIds = node.span_ids || [];
    const el = document.createElement('td');
    el.style.fontFamily = 'ui-monospace,monospace';
    el.style.fontSize = '0.78rem';

    if (!spanIds.length) {
      el.style.color = '#888';
      el.textContent = '—';
      return el;
    }

    spanIds.forEach((sid, i) => {
      if (i > 0) el.appendChild(document.createTextNode(' '));
      el.appendChild(spanLink(sid));
    });

    if (node.is_target_duplicate) {
      el.appendChild(document.createTextNode(' '));
      const tag = document.createElement('span');
      tag.style.color = '#888';
      tag.style.fontSize = '0.7rem';
      tag.textContent = '(dup)';
      tag.title = 'duplicate node — Step 2.b combined source-and-target span';
      el.appendChild(tag);
    }

    return el;
  }

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  function renderGraphs(data) {
    const base    = data.base    || { nodes: [], edges: [] };
    const colored = data.colored || { nodes: [], edges: [] };
    const entity  = data.entity  || { nodes: [], edges: [] };

    const noData = (
      (!base.nodes    || base.nodes.length    === 0) &&
      (!colored.nodes || colored.nodes.length === 0) &&
      (!entity.nodes  || entity.nodes.length  === 0)
    );
    if (noData) {
      graphEmpty.style.display = '';
      return;
    }
    graphEmpty.style.display = 'none';
    graphContent.style.display = '';

    const baseById    = Object.fromEntries((base.nodes    || []).map(n => [n.id, n]));
    const coloredById = Object.fromEntries((colored.nodes || []).map(n => [n.id, n]));
    const entityById  = Object.fromEntries((entity.nodes  || []).map(n => [n.id, n]));

    const baseSorted    = topoSort(base.nodes    || [], base.edges    || []);
    const coloredSorted = topoSort(colored.nodes || [], colored.edges || []);
    const entitySorted  = topoSort(entity.nodes  || [], entity.edges  || []);

    document.getElementById('graph-base-summary').textContent =
      `${(base.nodes||[]).length} nodes · ${(base.edges||[]).length} edges`;
    renderInterleaved('graph-base-nodes-table', baseSorted, base.edges || [], baseById, 'base');

    const flagCount     = (colored.nodes || []).filter(n => n.flagged).length;
    const dupCount      = (colored.nodes || []).filter(n => n.is_target_duplicate).length;
    const inferredCount = (colored.nodes || []).filter(n => n.is_inferred).length;
    document.getElementById('graph-colored-summary').textContent =
      `${(colored.nodes||[]).length} nodes · ${(colored.edges||[]).length} edges` +
      (flagCount     ? ` · ${flagCount} flagged`      : '') +
      (dupCount      ? ` · ${dupCount} duplicates`    : '') +
      (inferredCount ? ` · ${inferredCount} inferred` : '');
    renderInterleaved('graph-colored-nodes-table', coloredSorted, colored.edges || [], coloredById, 'colored');

    const entInferredCount = (entity.nodes || []).filter(n => n.is_inferred).length;
    document.getElementById('graph-entity-summary').textContent =
      `${(entity.nodes||[]).length} entities · ${(entity.edges||[]).length} edges` +
      (entInferredCount ? ` · ${entInferredCount} inferred` : '');
    renderInterleaved('graph-entity-nodes-table', entitySorted, entity.edges || [], entityById, 'entity');
  }

  // Interleaved node + outgoing-edge rendering. `mode` ∈ {base, colored, entity}.
  function renderInterleaved(tableId, nodes, edges, nodeById, mode) {
    const tbody = document.querySelector('#' + tableId + ' tbody');
    tbody.innerHTML = '';

    if (!nodes.length) {
      const tr = document.createElement('tr');
      const t = document.createElement('td');
      t.colSpan = 4; t.style.color = '#666'; t.style.fontStyle = 'italic';
      t.textContent = '(none)';
      tr.appendChild(t); tbody.appendChild(tr);
      return;
    }

    const outEdges = new Map(nodes.map(n => [n.id, []]));
    const unanchored = [];
    edges.forEach(e => {
      if (outEdges.has(e.from)) outEdges.get(e.from).push(e);
      else unanchored.push(e);
    });

    function nodeRow(node) {
      const tr = document.createElement('tr');
      tr.style.background = node.flagged ? '#2a2200' : '#1a1a1a';

      // Scope cell
      const tScope = document.createElement('td');
      const pill = document.createElement('span');
      pill.className = 'scope-pill';
      pill.textContent = shortScope(node.scope);
      pill.title = Array.isArray(node.scope) ? node.scope.join(', ') : (node.scope || '');
      tScope.appendChild(pill);
      tr.appendChild(tScope);

      // Color / Markers cell
      const tColor = document.createElement('td');
      tColor.appendChild(colorPill(node.color));
      if (mode !== 'base') {
        if (node.is_boundary)         tColor.appendChild(markerPill('boundary', 'marker-boundary'));
        if (node.is_target_duplicate) tColor.appendChild(markerPill('target',   'marker-target'));
        if (node.is_inferred)         tColor.appendChild(markerPill('inferred', 'marker-inferred'));
        if (node.flagged)             tColor.appendChild(markerPill('flagged',  'marker-flagged'));
      }
      tr.appendChild(tColor);

      // Label
      const tLabel = document.createElement('td');
      tLabel.textContent = node.label || '(stub)';
      tLabel.style.color = node.label ? '#e0e0e0' : '#666';
      tLabel.style.fontStyle = node.label ? '' : 'italic';
      tr.appendChild(tLabel);

      // Span IDs
      tr.appendChild(spanIdsCell(node));
      return tr;
    }

    function edgeRow(edge) {
      const tr = document.createElement('tr');
      tr.style.background = '#111';

      const tArrow = document.createElement('td');
      tArrow.style.textAlign = 'center';
      tArrow.style.color = '#555';
      tArrow.style.fontSize = '0.85rem';
      tArrow.textContent = '↓';
      tr.appendChild(tArrow);

      const tKind = document.createElement('td');
      const kpill = document.createElement('span');
      kpill.className = 'edge-kind-pill ek-' + (edge.kind || 'white');
      kpill.textContent = edge.colors || edge.kind || '?';
      kpill.title = 'colors: ' + (edge.colors || edge.kind);
      tKind.appendChild(kpill);
      tr.appendChild(tKind);

      const toNode = nodeById[edge.to];
      const tTo = document.createElement('td');
      tTo.style.color = '#888';
      tTo.style.fontSize = '0.78rem';
      tTo.textContent = '→ ' + (toNode ? nodeLabel(toNode) : shortId(edge.to));
      tr.appendChild(tTo);

      tr.appendChild(document.createElement('td'));
      return tr;
    }

    nodes.forEach(node => {
      tbody.appendChild(nodeRow(node));
      (outEdges.get(node.id) || []).forEach(e => tbody.appendChild(edgeRow(e)));
    });

    unanchored.forEach(e => tbody.appendChild(edgeRow(e)));
  }

  graphBtn.addEventListener('click', () => setAllViews('graph'));

  // "Process this trace" button inside the empty state: runs the extractor
  // for the current trace on demand, then re-loads the graph view. Same
  // semantics as the CLI (single-trace; replaces any existing proto data).
  const processBtn = graphEmpty.querySelector('[data-proto-process]');
  if (processBtn) {
    processBtn.addEventListener('click', async () => {
      const traceId = getTraceId();
      if (!traceId) return;
      const original = processBtn.textContent;
      processBtn.disabled = true;
      processBtn.textContent = 'Processing…';
      try {
        const resp = await fetch('/proto/process/' + encodeURIComponent(traceId), { method: 'POST' });
        if (!resp.ok) {
          processBtn.disabled = false;
          processBtn.textContent = original;
          graphEmpty.appendChild(document.createElement('br'));
          graphEmpty.appendChild(document.createTextNode('Failed to process: HTTP ' + resp.status));
          return;
        }
        // Re-run the loader: clear the latch so it re-fetches and renders.
        graphLoaded = false;
        await loadGraph();
      } catch (e) {
        processBtn.disabled = false;
        processBtn.textContent = original;
        graphEmpty.appendChild(document.createElement('br'));
        graphEmpty.appendChild(document.createTextNode('Failed to process: ' + e));
      }
    });
  }
})();
