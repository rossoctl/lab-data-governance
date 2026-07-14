/* P-interactions prototype — execution-flow view. THROWAWAY.
 *
 * Adds a view switcher to the trace-tree page. Toggles between the
 * existing span tree and a flat table of (entities, interactions)
 * extracted from spans by the prototype CLI.
 *
 * Loads from /proto/interactions/<trace_id>; if the scratch tables
 * haven't been populated yet, shows a hint to run the CLI.
 */

(function () {
  // UI build version. Bump this on every change to the proto UI assets
  // (this file / graph_view_logic.js / trace_tree.html) so the header shows
  // whether the browser is running the latest bundle or a stale cached one.
  const UI_VERSION = 'proto-ui v16 (2026-07-14 · default view = Entities and interactions)';
  const versionEl = document.getElementById('ui-version');
  if (versionEl) versionEl.textContent = UI_VERSION;

  // Resizable quadrants. The 2×2 grid's column/row split fractions are the CSS
  // variables --col-split / --row-split on #flow-grid. Dragging the vertical
  // rail changes --col-split (both columns move together); the horizontal rail
  // changes --row-split (both rows together); the center handle changes both.
  // Fractions are clamped to [0.15, 0.85] so no quadrant collapses, and are
  // persisted to localStorage so the layout survives reloads.
  (function setupQuadrantResize() {
    const grid = document.getElementById('flow-grid');
    if (!grid) return;
    const LS_KEY = 'pi-flow-splits';
    const clamp = v => Math.max(0.15, Math.min(0.85, v));
    const MIN_H = 480;   // px — never shrink the grid below this

    // Restore any saved split + height.
    try {
      const saved = JSON.parse(localStorage.getItem(LS_KEY) || 'null');
      if (saved && typeof saved.col === 'number' && typeof saved.row === 'number') {
        grid.style.setProperty('--col-split', clamp(saved.col));
        grid.style.setProperty('--row-split', clamp(saved.row));
      }
      if (saved && typeof saved.height === 'number' && saved.height >= MIN_H) {
        grid.style.setProperty('--grid-height', saved.height + 'px');
      }
    } catch (_) { /* ignore malformed storage */ }

    function persist() {
      const cs = getComputedStyle(grid);
      const col = parseFloat(cs.getPropertyValue('--col-split')) || 0.5;
      const row = parseFloat(cs.getPropertyValue('--row-split')) || 0.5;
      // Height is stored only once the user has explicitly dragged it (an
      // inline px value on the element); otherwise leave it responsive.
      const inlineH = grid.style.getPropertyValue('--grid-height');
      const height = /px$/.test(inlineH) ? parseFloat(inlineH) : null;
      try { localStorage.setItem(LS_KEY, JSON.stringify({ col, row, height })); } catch (_) {}
    }

    // Bottom handle: drag down to extend the grid height (grows the bottom
    // quadrants and lets the page scroll). Height is measured from the grid's
    // top to the pointer.
    const bottomHandle = document.getElementById('flow-resize-bottom');
    if (bottomHandle) {
      const startBottom = (ev) => {
        ev.preventDefault();
        bottomHandle.classList.add('dragging');
        document.body.classList.add('flow-resizing');
        const move = (e) => {
          const pt = e.touches ? e.touches[0] : e;
          const top = grid.getBoundingClientRect().top;
          const h = Math.max(MIN_H, pt.clientY - top);
          grid.style.setProperty('--grid-height', h + 'px');
        };
        const up = () => {
          bottomHandle.classList.remove('dragging');
          document.body.classList.remove('flow-resizing');
          document.removeEventListener('mousemove', move);
          document.removeEventListener('mouseup', up);
          document.removeEventListener('touchmove', move);
          document.removeEventListener('touchend', up);
          persist();
        };
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
        document.addEventListener('touchmove', move, { passive: false });
        document.addEventListener('touchend', up);
      };
      bottomHandle.addEventListener('mousedown', startBottom);
      bottomHandle.addEventListener('touchstart', startBottom, { passive: false });
      // Double-click resets to the responsive viewport-fit height.
      bottomHandle.addEventListener('dblclick', () => {
        grid.style.removeProperty('--grid-height');
        persist();
      });
    }

    function startDrag(axes, railEl) {
      return (ev) => {
        ev.preventDefault();
        railEl.classList.add('dragging');
        document.body.classList.add('flow-resizing');
        const move = (e) => {
          const r = grid.getBoundingClientRect();
          const pt = e.touches ? e.touches[0] : e;
          if (axes.includes('col') && r.width > 0) {
            grid.style.setProperty('--col-split', clamp((pt.clientX - r.left) / r.width));
          }
          if (axes.includes('row') && r.height > 0) {
            grid.style.setProperty('--row-split', clamp((pt.clientY - r.top) / r.height));
          }
        };
        const up = () => {
          railEl.classList.remove('dragging');
          document.body.classList.remove('flow-resizing');
          document.removeEventListener('mousemove', move);
          document.removeEventListener('mouseup', up);
          document.removeEventListener('touchmove', move);
          document.removeEventListener('touchend', up);
          persist();
        };
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
        document.addEventListener('touchmove', move, { passive: false });
        document.addEventListener('touchend', up);
      };
    }

    const railV = document.getElementById('flow-split-v');
    const railH = document.getElementById('flow-split-h');
    const railC = document.getElementById('flow-split-c');
    if (railV) { railV.addEventListener('mousedown', startDrag(['col'], railV));
                 railV.addEventListener('touchstart', startDrag(['col'], railV), { passive: false }); }
    if (railH) { railH.addEventListener('mousedown', startDrag(['row'], railH));
                 railH.addEventListener('touchstart', startDrag(['row'], railH), { passive: false }); }
    if (railC) { railC.addEventListener('mousedown', startDrag(['col', 'row'], railC));
                 railC.addEventListener('touchstart', startDrag(['col', 'row'], railC), { passive: false }); }
    // Double-click any rail resets to an even 50/50 split.
    [railV, railH, railC].forEach(r => r && r.addEventListener('dblclick', () => {
      grid.style.setProperty('--col-split', 0.5);
      grid.style.setProperty('--row-split', 0.5);
      persist();
    }));
  })();

  const treeBtn = document.getElementById('view-tree-btn');
  const flowBtn = document.getElementById('view-flow-btn');
  const treeEl = document.getElementById('tree');
  const flowSection = document.getElementById('flow-section');
  const flowEmpty = document.getElementById('flow-empty');
  const detailAside = document.getElementById('detail');
  const detailTitle = document.getElementById('detail-title');
  const detailAttrsHeader = document.getElementById('detail-attributes-h');
  const detailAttrsPre = document.getElementById('detail-attributes');
  const detailSpansTable = document.getElementById('detail-spans-table');
  const entitiesTbody = document.querySelector('#entities-table tbody');
  const interactionsTbody = document.querySelector('#interactions-table tbody');
  const flowGraphEl = document.getElementById('flow-graph');
  const flowSeqEl = document.getElementById('flow-sequence');

  // Bridge between the interaction table rows, the graph edges, and the
  // sequence-diagram messages: hovering any one highlights the matching
  // interaction (by id) in all three (and dims the rest). Each renderer
  // registers a highlighter; `graphHighlight(id)` fans out to all of them.
  let highlighters = [];
  function graphHighlight(interactionId) {
    highlighters.forEach(fn => fn(interactionId));
  }

  // Entity-selection bridge: clicking an entity row highlights that entity's
  // node (graph) and lifeline (sequence). Renderers register an entity
  // highlighter; `entityHighlight(id|null)` fans out. `selectedEntityId` holds
  // the current click-selection (null = none) so it survives re-renders.
  let entityHighlighters = [];
  let selectedEntityId = null;
  function entityHighlight(entityId) {
    entityHighlighters.forEach(fn => fn(entityId));
    document.querySelectorAll('#entities-table tr[data-entity-id]').forEach(tr => {
      tr.classList.toggle('ent-selected', tr.dataset.entityId === entityId);
    });
  }

  // Click-through from a graph/sequence element to the relevant table + panel.
  //
  // selectEntity(id): make `id` the selected entity — highlight it in both
  //   diagrams (entityHighlight) and scroll+flash its Entities-table row.
  // selectInteractionInTable(id): find the interaction's row, scroll+flash it,
  //   and open the interaction detail panel (reuses the row's own click path so
  //   behaviour matches clicking the row directly).
  function selectEntity(entityId) {
    selectedEntityId = entityId;
    entityHighlight(entityId);
    const row = document.querySelector(
      `#entities-table tr[data-entity-id="${entityId}"]`);
    if (row) {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      flashRow(row);
    }
  }

  function selectInteractionInTable(interactionId) {
    graphHighlight(interactionId);   // persist highlight across both diagrams
    const row = document.querySelector(
      `#interactions-table tr[data-interaction-id="${interactionId}"]`);
    if (row) {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      flashRow(row);
      row.click();   // opens the interaction detail panel (existing handler)
    }
  }

  // Brief flash so a programmatically-selected row is easy to spot.
  function flashRow(row) {
    row.classList.remove('row-flash');
    void row.offsetWidth;            // restart the animation
    row.classList.add('row-flash');
    row.addEventListener('animationend',
      () => row.classList.remove('row-flash'), { once: true });
  }

  if (!treeBtn || !flowBtn || !flowSection || !entitiesTbody || !interactionsTbody) {
    return;
  }

  let flowLoaded = false;
  let flowData = null;

  function getTraceId() {
    // Tolerant of a gateway path prefix and trailing slash/query; falls
    // back to the last path segment. See graph_view_logic.js for the
    // rationale (anchored ^/trace/ regex returned null under a prefix).
    const m = window.location.pathname.match(/\/trace\/([0-9a-fA-F]+)/);
    if (m) return m[1];
    const segs = window.location.pathname.split('/').filter(Boolean);
    return segs.length ? segs[segs.length - 1] : null;
  }

  // The natural-key prefix IS the coarse kind (`llm:` / `tool:` /
  // `agent:`) per ADR-0007 "Natural-key prefixes are part of the public
  // algorithm vocabulary." Derived here so the API row stays minimal.
  function kindFromNaturalKey(naturalKey) {
    if (!naturalKey) return 'service';
    const i = naturalKey.indexOf(':');
    if (i <= 0) return 'service';
    return naturalKey.slice(0, i);
  }

  function setView(view) {
    if (view === 'tree') {
      treeBtn.classList.add('active');
      flowBtn.classList.remove('active');
      treeEl.style.display = 'block';
      flowSection.style.display = 'none';
      detailAside.classList.remove('flow-mode');
      detailTitle.textContent = 'Span detail';
      detailAttrsHeader.textContent = 'Attributes';
      detailSpansTable.style.display = 'none';
      detailAttrsPre.style.display = '';
    } else {
      treeBtn.classList.remove('active');
      flowBtn.classList.add('active');
      treeEl.style.display = 'none';
      flowSection.style.display = 'block';
      if (!flowLoaded) loadFlow();
    }
  }

  async function loadFlow() {
    const traceId = getTraceId();
    if (!traceId) {
      // Don't latch flowLoaded — leave the tab retryable.
      flowEmpty.style.display = '';
      flowEmpty.textContent = 'Could not determine trace id from the URL.';
      return;
    }
    try {
      const resp = await fetch('/proto/interactions/' + encodeURIComponent(traceId));
      if (!resp.ok) {
        flowEmpty.style.display = '';
        flowEmpty.textContent = 'Failed to load: HTTP ' + resp.status;
        return;
      }
      flowData = await resp.json();
      renderFlow();
      // Only latch after a successful render.
      flowLoaded = true;
    } catch (e) {
      flowEmpty.style.display = '';
      flowEmpty.textContent = 'Failed to load: ' + e;
    }
  }

  function renderFlow() {
    if (!flowData
        || (flowData.entities.length === 0 && flowData.interactions.length === 0)) {
      flowEmpty.style.display = '';
      return;
    }
    flowEmpty.style.display = 'none';

    const entById = new Map();
    flowData.entities.forEach(e => entById.set(e.id, e));

    entitiesTbody.innerHTML = '';
    selectedEntityId = null;
    flowData.entities.forEach(e => {
      const tr = document.createElement('tr');
      tr.dataset.entityId = e.id;
      // Click an entity row to highlight it in the graph + sequence diagram
      // (toggle: click again to clear). Hover gives a transient preview.
      tr.addEventListener('click', () => {
        selectedEntityId = (selectedEntityId === e.id) ? null : e.id;
        entityHighlight(selectedEntityId);
      });
      tr.addEventListener('mouseenter', () => {
        if (selectedEntityId == null) entityHighlight(e.id);
      });
      tr.addEventListener('mouseleave', () => {
        if (selectedEntityId == null) entityHighlight(null);
        else entityHighlight(selectedEntityId);
      });

      const phaseTd = document.createElement('td');
      const phasePill = document.createElement('span');
      phasePill.className = 'scope-pill';
      const scopeLabel = (e.scope_name || '—').replace(/^opentelemetry\.instrumentation\./, 'otel.').replace(/^openinference\.instrumentation\./, 'oi.');
      phasePill.textContent = scopeLabel;
      phasePill.title = e.scope_name || '';
      phaseTd.appendChild(phasePill);

      const kindTd = document.createElement('td');
      const kind = kindFromNaturalKey(e.natural_key);
      const pill = document.createElement('span');
      pill.className = 'ent-pill ' + kind;
      pill.textContent = kind;
      kindTd.appendChild(pill);
      // Inferred-peer marker — sourced from the typed boolean per
      // ADR-0007 ("Inferred identity is a boolean field, not a label
      // convention"); never derived from natural_key text.
      if (e.inferred) {
        const inferredPill = document.createElement('span');
        inferredPill.className = 'marker-pill marker-inferred';
        inferredPill.textContent = 'inferred';
        inferredPill.title = 'Inferred peer (Step 2.c) — unobserved side of a one-sided protocol call.';
        kindTd.appendChild(inferredPill);
      }

      const nameTd = document.createElement('td');
      // display_name is "unknown" until Step 3.b lands richer naming; the
      // natural_key (e.g. tool:get_weather) is the most informative thing
      // we currently have.
      const nameLabel = e.natural_key && e.natural_key !== 'unknown'
        ? e.natural_key
        : e.display_name;
      nameTd.textContent = nameLabel;
      if (e.inferred) {
        nameTd.style.fontStyle = 'italic';
        nameTd.style.color = '#9ec5e6';
      }

      const detTd = document.createElement('td');
      detTd.style.color = '#888';
      detTd.textContent = e.detected_from;

      // Column order: Display name, Kind, Detected from, Anchor span, Scope.
      tr.appendChild(nameTd);
      tr.appendChild(kindTd);
      tr.appendChild(detTd);
      tr.appendChild(spanLinkCell(e.anchor_span_id || null));
      tr.appendChild(phaseTd);
      entitiesTbody.appendChild(tr);
    });

    // Graph + sequence diagram of the interactions, drawn above the table. The
    // edge / message numbers match this table's `#` column (all three share the
    // 1-based render index of the list, which the API already returned sorted by
    // the global ordinal `order` — chronological across turns, LIFO within a
    // nested delegation. We do NOT re-sort by started_at here; `order` alone is
    // the display order).
    highlighters = [];
    entityHighlighters = [];
    const orderedEnts = orderEntitiesByFirstAppearance();
    renderFlowGraph(orderedEnts);
    renderSequenceDiagram(orderedEnts);
    // Table rows highlight in lockstep with the graph/sequence (one shared
    // highlighter; the row hover handlers below drive it the other way).
    highlighters.push((interactionId) => {
      document.querySelectorAll('#interactions-table tr[data-interaction-id]').forEach(tr => {
        tr.classList.toggle('graph-hover', tr.dataset.interactionId === interactionId);
      });
    });

    interactionsTbody.innerHTML = '';
    flowData.interactions.forEach((ix, idx) => {
      const tr = document.createElement('tr');
      tr.dataset.interactionId = ix.id;
      // Row ↔ edge highlight sync (see renderFlowGraph).
      tr.addEventListener('mouseenter', () => graphHighlight(ix.id));
      tr.addEventListener('mouseleave', () => graphHighlight(null));

      // Row number (1-based) reflecting the render order of the interactions
      // list. Purely a display aid for referring to a specific row.
      const tNum = document.createElement('td');
      tNum.style.color = '#666';
      tNum.style.fontFamily = 'ui-monospace, monospace';
      tNum.textContent = String(idx + 1);
      tr.appendChild(tNum);

      const tStarted = document.createElement('td');
      tStarted.style.color = '#888';
      tStarted.style.fontFamily = 'ui-monospace, monospace';
      tStarted.textContent = formatStartedAt(ix.started_at);
      tr.appendChild(tStarted);

      const caller = entById.get(ix.caller_entity_id);
      const callee = entById.get(ix.callee_entity_id);

      tr.appendChild(entityCell(caller));
      tr.appendChild(entityCell(callee));

      const tStatus = document.createElement('td');
      if (ix.error === true) { tStatus.textContent = 'ERROR'; tStatus.className = 'err-cell'; }
      else if (ix.error === false) { tStatus.textContent = 'ok'; tStatus.className = 'ok-cell'; }
      else { tStatus.textContent = '—'; tStatus.style.color = '#666'; }
      tr.appendChild(tStatus);

      // The anchor span is this interaction's timing/evidence span (ADR-0007
      // Step 3.c: one anchor per interaction) and is the payload jump target.
      // For ordinary interactions it is also where the payload came from. For an
      // A2A-delegation *response* leg the anchor is the responding agent's own
      // span while the payload originates from a different pooled span (the
      // call-site TOOL span); we intentionally jump to the anchor/evidence span
      // anyway, so the payload link and the interaction's evidence stay
      // consistent.
      const evidence = flowData.spans_by_interaction[ix.id] || [];
      const anchor = evidence.find(e => e.is_anchor);
      const anchorSpanId = anchor ? anchor.span_id : null;

      const tReq = document.createElement('td');
      if (ix.request_payload_hash) {
        const a = document.createElement('span');
        a.className = 'pl-link';
        a.textContent = ix.request_payload_hash.slice(0, 8);
        a.addEventListener('click', (ev) => {
          ev.stopPropagation();
          showPayload(ix.request_payload_hash, anchorSpanId);
        });
        tReq.appendChild(a);
      } else {
        tReq.style.color = '#666';
        tReq.textContent = '—';
      }
      tr.appendChild(tReq);

      const tResp = document.createElement('td');
      if (ix.response_payload_hash) {
        const a = document.createElement('span');
        a.className = 'pl-link';
        a.textContent = ix.response_payload_hash.slice(0, 8);
        a.addEventListener('click', (ev) => {
          ev.stopPropagation();
          showPayload(ix.response_payload_hash, anchorSpanId);
        });
        tResp.appendChild(a);
      } else {
        tResp.style.color = '#666';
        tResp.textContent = '—';
      }
      tr.appendChild(tResp);

      const tSpans = document.createElement('td');
      tSpans.style.color = '#888';
      const anchorCount = evidence.filter(e => e.is_anchor).length;
      tSpans.textContent = `${evidence.length} (${anchorCount} anchor)`;
      tr.appendChild(tSpans);

      tr.addEventListener('click', () => selectInteraction(ix, evidence));

      interactionsTbody.appendChild(tr);
    });
  }

  // ---------------------------------------------------------------------------
  // Interaction graph (proto). Entities are nodes laid out LEFT-TO-RIGHT in the
  // order they first appear in an interaction (by table row), so reading the
  // graph left→right follows the flow of execution. Each interaction is a
  // curved edge carrying a numbered badge = its 1-based row in the table. Call
  // legs are solid, response legs dashed (order band: call is the lower/even
  // band, response the higher/odd — ADR-0007 "Inferred interaction ordering").
  // Ported from build_interactions_ui.py (throwaway prototype).
  // ---------------------------------------------------------------------------

  const SVGNS = 'http://www.w3.org/2000/svg';

  function kindColor(entity) {
    if (!entity) return '#888';
    switch (kindFromNaturalKey(entity.natural_key)) {
      case 'agent': return '#79d4ff';                 // matches .ent-pill.agent
      case 'tool':  return '#79ffaa';                 // matches .ent-pill.tool
      case 'llm':   return '#b0b0ff';                 // matches .ent-pill.llm
      default:      return '#c9c9c9';
    }
  }

  // Order entities left-to-right by first appearance: an entity's rank is the
  // row of the FIRST interaction that touches it (caller or callee). Entities
  // never touched sort last, in original order. Shared by the graph and the
  // sequence diagram so their columns/lifelines line up.
  function orderEntitiesByFirstAppearance() {
    const ents = flowData.entities || [];
    const ixs = flowData.interactions || [];
    const firstRow = new Map();
    ixs.forEach((ix, idx) => {
      const row = idx + 1;
      for (const eid of [ix.caller_entity_id, ix.callee_entity_id]) {
        if (!firstRow.has(eid)) firstRow.set(eid, row);
      }
    });
    return ents
      .map((e, i) => ({ e, i, rank: firstRow.has(e.id) ? firstRow.get(e.id) : Infinity }))
      .sort((a, b) => (a.rank - b.rank) || (a.i - b.i))
      .map(o => o.e);
  }

  function renderFlowGraph(ordered) {
    // Idempotent: clear any prior render (e.g. after "Process this trace").
    flowGraphEl.innerHTML = '';
    const ixs = flowData.interactions || [];
    if (!ordered.length) return;

    // --- geometry -----------------------------------------------------------
    // Nodes on a horizontal band, evenly spaced. To give the many curved edges
    // (and the numbered badges) vertical room without overlapping neighbours,
    // stagger the nodes into two rows (even index high, odd index low).
    const N = ordered.length;
    const NW = 168, NH = 40;
    const COL_GAP = 40;                       // horizontal gap between columns
    const colW = NW + COL_GAP;
    const MARGIN_X = 30;
    const W = Math.max(900, MARGIN_X * 2 + N * colW);
    const ROW_STAGGER = 70;                   // vertical offset between the 2 rows
    const BAND_TOP = 150;                     // room above for upward edge curves
    const H = BAND_TOP + NH + ROW_STAGGER + 200;

    const pos = {};
    ordered.forEach((e, i) => {
      const cx = MARGIN_X + NW / 2 + i * colW;
      const cy = BAND_TOP + NH / 2 + (i % 2) * ROW_STAGGER;
      pos[e.id] = { x: cx, y: cy };
    });

    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('preserveAspectRatio', 'xMidYMin meet');
    // The quadrant's `.quad-body` handles scrolling. Keep the SVG at its
    // natural width so the graph stays legible (the cell scrolls when wider);
    // fit-to-width only when it already fits the cell.
    if (W > 900) svg.style.width = W + 'px';

    // Arrow markers (default + highlighted). markerWidth bumped so the
    // direction of each edge reads clearly at a glance.
    const defs = document.createElementNS(SVGNS, 'defs');
    defs.innerHTML =
      '<marker id="fg-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="10" markerHeight="10" orient="auto-start-reverse">' +
      '<path d="M0 0 L10 5 L0 10 z" fill="#8b95ad"/></marker>' +
      '<marker id="fg-arrow-hi" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="11" markerHeight="11" orient="auto-start-reverse">' +
      '<path d="M0 0 L10 5 L0 10 z" fill="#ffd24a"/></marker>';
    svg.appendChild(defs);

    const gEdges = document.createElementNS(SVGNS, 'g');
    const gLabels = document.createElementNS(SVGNS, 'g');
    const gNodes = document.createElementNS(SVGNS, 'g');
    svg.appendChild(gEdges);
    svg.appendChild(gLabels);
    svg.appendChild(gNodes);

    // --- nodes --------------------------------------------------------------
    // Iterate `ordered` so a node's order-of-appearance index is available for
    // a small "1st, 2nd, …" rank tag; position still comes from `pos[e.id]`.
    ordered.forEach((e, i) => {
      const p = pos[e.id];
      const g = document.createElementNS(SVGNS, 'g');
      g.setAttribute('class', 'fg-node');
      g.setAttribute('data-entity', e.id);
      g.setAttribute('transform', `translate(${p.x - NW / 2},${p.y - NH / 2})`);
      g.style.cursor = 'pointer';
      // Click a node → select that entity (highlight both diagrams + scroll the
      // Entities table to its row).
      g.addEventListener('click', () => selectEntity(e.id));
      // Primary (bold) line is the UNIQUE natural_key so no two nodes look
      // identical (many entities share a service display_name — e.g. the
      // payment-agent's agent/llm/tool entities all display as "payment-agent").
      // The service/display name goes on the sub-line for context.
      const key = e.natural_key && e.natural_key !== 'unknown'
        ? e.natural_key : (e.display_name || '(unknown)');
      const markers = (e.inferred ? '  •inf' : '');
      const sub = (e.display_name && e.display_name !== key) ? e.display_name : '';
      g.innerHTML =
        `<rect width="${NW}" height="${NH}" rx="7" stroke="${kindColor(e)}"></rect>` +
        `<text class="fg-ord" x="-6" y="26" text-anchor="end"></text>` +
        `<text x="8" y="17"></text>` +
        `<text class="fg-sub" x="8" y="31"></text>`;
      // textContent (not innerHTML) so entity names can't inject markup.
      // Order-of-appearance tag (1-based) to the left of each node.
      g.querySelector('.fg-ord').textContent = String(i + 1);
      const texts = g.querySelectorAll('text');
      texts[1].textContent = key + markers;
      texts[2].textContent = sub;
      gNodes.appendChild(g);
    });

    // --- edges --------------------------------------------------------------
    // Clip a point on the segment (cx,cy)->(px,py) to the node box centered at
    // (px,py) of half-size (hw,hh), plus a small gap, so an edge stops at the
    // node border (and its arrowhead sits just outside — visibly directed)
    // rather than running to the hidden center.
    function clipToBox(px, py, cx, cy, hw, hh, gap) {
      const dx = cx - px, dy = cy - py;
      if (dx === 0 && dy === 0) return { x: px, y: py };
      const sx = dx !== 0 ? (hw + gap) / Math.abs(dx) : Infinity;
      const sy = dy !== 0 ? (hh + gap) / Math.abs(dy) : Infinity;
      const s = Math.min(sx, sy, 1);
      return { x: px + dx * s, y: py + dy * s };
    }

    function edgePath(aId, bId, off) {
      const pa = pos[aId], pb = pos[bId];
      if (aId === bId) {                       // self-loop above the node
        const x = pa.x, y = pa.y - NH / 2;
        return { d: `M ${x - 16} ${y} C ${x - 46} ${y - 58}, ${x + 46} ${y - 58}, ${x + 16} ${y}`,
                 lx: x, ly: y - 46 };
      }
      const mx = (pa.x + pb.x) / 2, my = (pa.y + pb.y) / 2;
      const dx = pb.x - pa.x, dy = pb.y - pa.y, len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      const curve = off * 26;
      const cx = mx + nx * curve, cy = my + ny * curve;
      // Trim both ends to the node borders so the arrowhead is visible outside
      // the target box (the control point cx,cy is the direction the curve
      // leaves/enters each end). Small extra gap at the target for the arrow.
      const hw = NW / 2, hh = NH / 2;
      const start = clipToBox(pa.x, pa.y, cx, cy, hw, hh, 2);
      const end = clipToBox(pb.x, pb.y, cx, cy, hw, hh, 6);
      return { d: `M ${start.x} ${start.y} Q ${cx} ${cy} ${end.x} ${end.y}`,
               lx: (cx + mx) / 2, ly: (cy + my) / 2 };
    }

    const pairSeen = {};
    const byId = new Map();          // interaction id -> {path, label, self}
    let selfCount = 0;

    ixs.forEach((ix, idx) => {
      const row = idx + 1;
      const isSelf = ix.caller_entity_id === ix.callee_entity_id;
      if (isSelf) selfCount++;
      const key = ix.caller_entity_id + '|' + ix.callee_entity_id;
      const seen = pairSeen[key] || 0; pairSeen[key] = seen + 1;
      // Response legs carry the higher (odd) order band; fan out call vs resp.
      const isResp = (ix.order % 2) === 1;
      const off = (isResp ? 1 : -1) * (1 + Math.floor(seen / 2));
      const { d, lx, ly } = edgePath(ix.caller_entity_id, ix.callee_entity_id, off);

      const path = document.createElementNS(SVGNS, 'path');
      path.setAttribute('class', 'fg-edge ' + (isResp ? 'resp' : 'call'));
      path.setAttribute('d', d);
      path.setAttribute('marker-end', 'url(#fg-arrow)');
      path.dataset.self = isSelf ? '1' : '0';
      path.style.cursor = 'pointer';
      path.addEventListener('click', () => selectInteractionInTable(ix.id));
      gEdges.appendChild(path);

      const lab = document.createElementNS(SVGNS, 'g');
      lab.setAttribute('class', 'fg-label');
      lab.dataset.self = isSelf ? '1' : '0';
      lab.innerHTML =
        `<circle cx="${lx}" cy="${ly}" r="9"></circle>` +
        `<text x="${lx}" y="${ly + 3}">${row}</text>`;
      lab.addEventListener('mouseenter', () => graphHighlight(ix.id));
      lab.addEventListener('mouseleave', () => graphHighlight(null));
      lab.addEventListener('click', () => selectInteractionInTable(ix.id));
      gLabels.appendChild(lab);

      byId.set(ix.id, { path, lab, self: isSelf });
    });

    flowGraphEl.appendChild(svg);

    // --- legend + self-loop toggle -----------------------------------------
    const legend = document.createElement('div');
    legend.className = 'fg-legend';
    legend.innerHTML =
      '<span><span class="sw" style="background:#79d4ff"></span>agent</span>' +
      '<span><span class="sw" style="background:#79ffaa"></span>tool</span>' +
      '<span><span class="sw" style="background:#b0b0ff"></span>llm</span>';
    if (selfCount) {
      const lbl = document.createElement('label');
      const cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = true;
      cb.addEventListener('change', () => {
        byId.forEach(({ path, lab, self }) => {
          if (!self) return;
          const disp = cb.checked ? '' : 'none';
          path.style.display = disp; lab.style.display = disp;
        });
      });
      lbl.appendChild(cb);
      lbl.appendChild(document.createTextNode(' self-loops'));
      legend.appendChild(lbl);
    }
    flowGraphEl.appendChild(legend);

    // --- highlight sync -----------------------------------------------------
    highlighters.push((interactionId) => {
      byId.forEach(({ path, lab }, id) => {
        const on = id === interactionId;
        path.classList.toggle('hi', on);
        path.setAttribute('marker-end', on ? 'url(#fg-arrow-hi)' : 'url(#fg-arrow)');
        lab.classList.toggle('hi', on);
        if (interactionId != null) {
          path.classList.toggle('dim', !on);
          lab.classList.toggle('dim', !on);
        } else {
          path.classList.remove('dim');
          lab.classList.remove('dim');
        }
      });
      const cur = ixs.find(x => x.id === interactionId);
      gNodes.querySelectorAll('.fg-node').forEach(n => {
        if (interactionId == null) { n.classList.remove('dim'); return; }
        const eid = n.getAttribute('data-entity');
        n.classList.toggle('dim',
          !(cur && (eid === cur.caller_entity_id || eid === cur.callee_entity_id)));
      });
    });

    // --- entity-selection highlight (from the entities list) ----------------
    entityHighlighters.push((entityId) => {
      flowGraphEl.classList.toggle('ent-focus', entityId != null);
      gNodes.querySelectorAll('.fg-node').forEach(n => {
        n.classList.toggle('ent-hi', entityId != null && n.getAttribute('data-entity') === entityId);
      });
    });

    // --- zoom / pan ---------------------------------------------------------
    attachZoom(svg, flowGraphEl);

    // Re-apply any active entity selection after a re-render.
    if (selectedEntityId != null) entityHighlight(selectedEntityId);
  }

  // ---------------------------------------------------------------------------
  // Sequence diagram (proto). Lifelines are the entities in the same left-to-
  // right order as the graph; messages are the interactions top-to-bottom in
  // table-row order, each numbered to match the graph edge and the table `#`.
  // Call legs are solid arrows, response legs dashed. A self-message (caller ===
  // callee) is drawn as a small return loop on the lifeline.
  // ---------------------------------------------------------------------------
  function renderSequenceDiagram(ordered) {
    flowSeqEl.innerHTML = '';
    const ixs = flowData.interactions || [];
    if (!ordered.length || !ixs.length) return;

    const NW = 168, NH = 40;
    const COL_GAP = 40, colW = NW + COL_GAP;
    const MARGIN_X = 30, HEAD_TOP = 10, HEAD_H = NH;
    const ROW_H = 34;                              // vertical gap per message
    const FIRST_MSG_Y = HEAD_TOP + HEAD_H + 30;
    const N = ordered.length;
    const W = Math.max(900, MARGIN_X * 2 + N * colW);
    const H = FIRST_MSG_Y + ixs.length * ROW_H + 30;

    const colX = {};
    ordered.forEach((e, i) => { colX[e.id] = MARGIN_X + NW / 2 + i * colW; });

    const svg = document.createElementNS(SVGNS, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
    svg.setAttribute('preserveAspectRatio', 'xMinYMin meet');
    // Natural size; the quadrant's `.quad-body` scrolls (the message list grows
    // tall). Pin both dimensions so rows stay readable rather than squashed.
    if (W > 900) svg.style.width = W + 'px';
    svg.style.height = H + 'px';

    const defs = document.createElementNS(SVGNS, 'defs');
    defs.innerHTML =
      '<marker id="sq-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">' +
      '<path d="M0 0 L10 5 L0 10 z" fill="#5b6478"/></marker>' +
      '<marker id="sq-arrow-hi" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">' +
      '<path d="M0 0 L10 5 L0 10 z" fill="#ffd24a"/></marker>';
    svg.appendChild(defs);

    const gLifelines = document.createElementNS(SVGNS, 'g');
    const gMsgs = document.createElementNS(SVGNS, 'g');
    const gHeads = document.createElementNS(SVGNS, 'g');
    svg.appendChild(gLifelines);
    svg.appendChild(gMsgs);
    svg.appendChild(gHeads);

    // Lifelines + head boxes.
    ordered.forEach((e, i) => {
      const x = colX[e.id];
      const line = document.createElementNS(SVGNS, 'line');
      line.setAttribute('class', 'sq-lifeline');
      line.setAttribute('x1', x); line.setAttribute('x2', x);
      line.setAttribute('y1', HEAD_TOP + HEAD_H);
      line.setAttribute('y2', H - 20);
      line.setAttribute('data-entity', e.id);
      gLifelines.appendChild(line);

      const key = e.natural_key && e.natural_key !== 'unknown'
        ? e.natural_key : (e.display_name || '(unknown)');
      const markers = (e.inferred ? '  •inf' : '');
      const g = document.createElementNS(SVGNS, 'g');
      g.setAttribute('class', 'sq-head');
      g.setAttribute('data-entity', e.id);
      g.setAttribute('transform', `translate(${x - NW / 2},${HEAD_TOP})`);
      g.style.cursor = 'pointer';
      g.addEventListener('click', () => selectEntity(e.id));   // → Entities table
      g.innerHTML =
        `<rect width="${NW}" height="${HEAD_H}" rx="7" stroke="${kindColor(e)}"></rect>` +
        `<text x="8" y="17"></text><text class="fg-sub" x="8" y="31"></text>`;
      const t = g.querySelectorAll('text');
      t[0].textContent = key + markers;
      t[1].textContent = (e.display_name && e.display_name !== key) ? e.display_name : '';
      gHeads.appendChild(g);
    });

    // Messages, top-to-bottom in row order.
    const byId = new Map();
    ixs.forEach((ix, idx) => {
      const row = idx + 1;
      const y = FIRST_MSG_Y + idx * ROW_H;
      const isResp = (ix.order % 2) === 1;
      const x1 = colX[ix.caller_entity_id];
      const x2 = colX[ix.callee_entity_id];
      const isSelf = ix.caller_entity_id === ix.callee_entity_id;

      const g = document.createElementNS(SVGNS, 'g');
      g.setAttribute('class', 'sq-msg' + (isResp ? ' resp' : ''));
      g.dataset.self = isSelf ? '1' : '0';

      let labelX;
      if (isSelf) {
        // Self-message: a small loop to the right of the lifeline.
        const w = 26, h = 14;
        const p = document.createElementNS(SVGNS, 'path');
        p.setAttribute('class', 'sq-line');
        p.setAttribute('d', `M ${x1} ${y - h / 2} h ${w} v ${h} h ${-w}`);
        p.setAttribute('marker-end', 'url(#sq-arrow)');
        p.setAttribute('fill', 'none');
        g.appendChild(p);
        labelX = x1 + w + 8;
      } else {
        const line = document.createElementNS(SVGNS, 'line');
        line.setAttribute('class', 'sq-line');
        line.setAttribute('x1', x1); line.setAttribute('y1', y);
        line.setAttribute('x2', x2); line.setAttribute('y2', y);
        line.setAttribute('marker-end', 'url(#sq-arrow)');
        g.appendChild(line);
        labelX = (x1 + x2) / 2;
      }

      // Numbered badge on the message.
      const badge = document.createElementNS(SVGNS, 'g');
      badge.setAttribute('class', 'sq-badge');
      badge.innerHTML =
        `<circle cx="${labelX}" cy="${y - 10}" r="9"></circle>` +
        `<text x="${labelX}" y="${y - 7}">${row}</text>`;
      g.appendChild(badge);
      gMsgs.appendChild(g);

      const onEnter = () => graphHighlight(ix.id);
      const onLeave = () => graphHighlight(null);
      g.addEventListener('mouseenter', onEnter);
      g.addEventListener('mouseleave', onLeave);
      g.style.cursor = 'pointer';
      g.addEventListener('click', () => selectInteractionInTable(ix.id));  // → Interactions table
      byId.set(ix.id, g);
    });

    flowSeqEl.appendChild(svg);

    highlighters.push((interactionId) => {
      byId.forEach((g, id) => {
        const on = id === interactionId;
        g.classList.toggle('hi', on);
        const line = g.querySelector('.sq-line');
        if (line) line.setAttribute('marker-end', on ? 'url(#sq-arrow-hi)' : 'url(#sq-arrow)');
        if (interactionId != null) g.classList.toggle('dim', !on);
        else g.classList.remove('dim');
      });
      const cur = ixs.find(x => x.id === interactionId);
      gHeads.querySelectorAll('.sq-head').forEach(n => {
        if (interactionId == null) { n.classList.remove('dim'); return; }
        const eid = n.getAttribute('data-entity');
        n.classList.toggle('dim',
          !(cur && (eid === cur.caller_entity_id || eid === cur.callee_entity_id)));
      });
    });

    // --- entity-selection highlight (from the entities list) ----------------
    entityHighlighters.push((entityId) => {
      flowSeqEl.classList.toggle('ent-focus', entityId != null);
      const mark = (n) => n.classList.toggle(
        'ent-hi', entityId != null && n.getAttribute('data-entity') === entityId);
      gHeads.querySelectorAll('.sq-head').forEach(mark);
      gLifelines.querySelectorAll('.sq-lifeline').forEach(mark);
    });

    // --- zoom / pan ---------------------------------------------------------
    attachZoom(svg, flowSeqEl);

    if (selectedEntityId != null) entityHighlight(selectedEntityId);
  }

  // ---------------------------------------------------------------------------
  // Zoom + pan for an SVG diagram. Wraps all existing child <g> elements in a
  // single zoom layer and transforms it. Wheel = zoom at the cursor; drag on
  // empty canvas = pan; +/−/reset buttons in a corner. Independent per SVG.
  // ---------------------------------------------------------------------------
  function attachZoom(svg, containerEl) {
    // Move existing top-level <g> children into a zoom layer (keep <defs>).
    const layer = document.createElementNS(SVGNS, 'g');
    layer.setAttribute('class', 'zoom-layer');
    const groups = Array.from(svg.children).filter(c => c.tagName === 'g');
    groups.forEach(g => layer.appendChild(g));
    svg.appendChild(layer);

    let scale = 1, tx = 0, ty = 0;
    const MIN = 0.2, MAX = 8;
    const apply = () => layer.setAttribute('transform', `translate(${tx} ${ty}) scale(${scale})`);

    // viewBox → client scaling so wheel-zoom stays anchored under the cursor.
    function svgPoint(clientX, clientY) {
      const r = svg.getBoundingClientRect();
      const vb = svg.viewBox.baseVal;
      const sx = vb.width / r.width, sy = vb.height / r.height;
      return { x: (clientX - r.left) * sx, y: (clientY - r.top) * sy };
    }

    function zoomAt(clientX, clientY, factor) {
      const next = Math.max(MIN, Math.min(MAX, scale * factor));
      if (next === scale) return;
      const p = svgPoint(clientX, clientY);
      // Keep the point under the cursor fixed: p = (p - t)/scale must hold.
      tx = p.x - (p.x - tx) * (next / scale);
      ty = p.y - (p.y - ty) * (next / scale);
      scale = next;
      apply();
    }

    svg.addEventListener('wheel', (e) => {
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    }, { passive: false });

    // Drag-to-pan on empty canvas (ignore drags starting on interactive marks).
    let panning = false, sx0 = 0, sy0 = 0, tx0 = 0, ty0 = 0;
    svg.addEventListener('mousedown', (e) => {
      if (e.target.closest('.fg-label, .sq-msg, .fg-node, .sq-head')) return;
      panning = true; sx0 = e.clientX; sy0 = e.clientY; tx0 = tx; ty0 = ty;
      svg.classList.add('panning');
    });
    document.addEventListener('mousemove', (e) => {
      if (!panning) return;
      const r = svg.getBoundingClientRect();
      const vb = svg.viewBox.baseVal;
      tx = tx0 + (e.clientX - sx0) * (vb.width / r.width);
      ty = ty0 + (e.clientY - sy0) * (vb.height / r.height);
      apply();
    });
    document.addEventListener('mouseup', () => { panning = false; svg.classList.remove('panning'); });

    function reset() { scale = 1; tx = 0; ty = 0; apply(); }

    // Zoom controls (bottom-left of the container).
    const ctrl = document.createElement('div');
    ctrl.className = 'zoom-ctrl';
    const mk = (txt, title, fn) => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = txt; b.title = title;
      b.addEventListener('click', (ev) => { ev.stopPropagation(); fn(); });
      return b;
    };
    const r0 = svg.getBoundingClientRect();
    ctrl.appendChild(mk('−', 'Zoom out', () => zoomAt(r0.left + r0.width / 2, r0.top + r0.height / 2, 1 / 1.3)));
    ctrl.appendChild(mk('⤢', 'Reset zoom', reset));
    ctrl.appendChild(mk('+', 'Zoom in', () => zoomAt(r0.left + r0.width / 2, r0.top + r0.height / 2, 1.3)));
    containerEl.appendChild(ctrl);
  }

  async function showPayload(hash, anchorSpanId) {
    const resp = await fetch('/proto/payload/' + encodeURIComponent(hash));
    if (!resp.ok) {
      alert('Failed to fetch payload: ' + resp.status);
      return;
    }
    const data = await resp.json();
    detailAside.classList.add('flow-mode', 'open');   // reveal floating panel
    detailTitle.textContent = 'Payload';
    document.getElementById('detail-empty').style.display = 'none';
    document.getElementById('detail-body').style.display = '';
    detailAttrsHeader.textContent = 'Content';
    detailAttrsPre.style.display = '';
    detailAttrsPre.textContent = JSON.stringify(data.content, null, 2);
    detailSpansTable.style.display = 'none';

    const dl = document.getElementById('detail-identity');
    dl.innerHTML = '';
    [['kind', data.content_kind], ['hash', data.content_hash], ['bytes', data.byte_size]].forEach(([k, v]) => {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = String(v);
      dl.appendChild(dt); dl.appendChild(dd);
    });

    // Link to the span this payload was extracted from (the interaction's
    // anchor span). Clicking switches to the span tree, expands + selects the
    // span, and pulse-highlights its row.
    const dt = document.createElement('dt'); dt.textContent = 'span';
    const dd = document.createElement('dd');
    if (anchorSpanId) {
      const a = document.createElement('span');
      a.className = 'span-link';
      a.textContent = 'view in span tree →';
      a.title = anchorSpanId;
      a.addEventListener('click', () => navigateToSpan(anchorSpanId));
      dd.appendChild(a);
    } else {
      dd.style.color = '#666';
      dd.textContent = '—';
    }
    dl.appendChild(dt); dl.appendChild(dd);

    document.getElementById('detail-timing').innerHTML = '';
  }

  // Pulse-highlight a span row in the tree. Reuses the same `.graph-highlight`
  // animation the graph view injects (graph_view_logic.js); we re-inject the
  // style under the same id here so a highlight works even when the user
  // reaches the tree from the flow view without ever opening the graph view.
  function ensureHighlightStyle() {
    if (document.getElementById('graph-highlight-style')) return;
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

  function highlightTreeRow(spanId) {
    ensureHighlightStyle();
    const row = document.querySelector('.row[data-span-id="' + spanId + '"]');
    if (!row) return;
    row.classList.remove('graph-highlight');
    void row.offsetWidth;  // force reflow so re-adding the class restarts the animation
    row.classList.add('graph-highlight');
    row.addEventListener('animationend', () => row.classList.remove('graph-highlight'), { once: true });
  }

  async function navigateToSpan(spanId) {
    setView('tree');
    if (window.TraceTreeNav && window.TraceTreeNav.selectSpanByIdInTree) {
      try {
        await window.TraceTreeNav.selectSpanByIdInTree(spanId);
        highlightTreeRow(spanId);
      } catch (e) {
        // best-effort — leave the user in tree view
      }
    }
  }

  function entityCell(entity) {
    const td = document.createElement('td');
    if (!entity) {
      td.textContent = '?';
      return td;
    }
    const kind = kindFromNaturalKey(entity.natural_key);
    const kindPill = document.createElement('span');
    kindPill.className = 'ent-pill ' + kind;
    kindPill.textContent = kind;
    td.appendChild(kindPill);
    if (entity.inferred) {
      const inferredPill = document.createElement('span');
      inferredPill.className = 'marker-pill marker-inferred';
      inferredPill.textContent = 'inferred';
      inferredPill.title = 'Inferred peer (Step 2.c).';
      td.appendChild(inferredPill);
    }
    const label = entity.natural_key && entity.natural_key !== 'unknown'
      ? entity.natural_key
      : entity.display_name;
    const labelSpan = document.createElement('span');
    labelSpan.textContent = label;
    if (entity.inferred) {
      labelSpan.style.fontStyle = 'italic';
      labelSpan.style.color = '#9ec5e6';
    }
    td.appendChild(labelSpan);
    return td;
  }

  function formatStartedAt(iso) {
    if (!iso || typeof iso !== 'string') return '';
    const t = iso.indexOf('T');
    if (t < 0) return iso;
    return iso.slice(t + 1, t + 13);
  }

  function spanLinkCell(spanId) {
    const td = document.createElement('td');
    if (!spanId) {
      td.style.color = '#666';
      td.textContent = '—';
      return td;
    }
    const a = document.createElement('span');
    a.className = 'span-link';
    a.textContent = spanId.length > 16 ? spanId.slice(0, 16) + '…' : spanId;
    a.title = spanId;
    a.addEventListener('click', (ev) => {
      ev.stopPropagation();
      navigateToSpan(spanId);
    });
    td.appendChild(a);
    return td;
  }

  function selectInteraction(ix, evidence) {
    document.querySelectorAll('#interactions-table tr.selected').forEach(r => r.classList.remove('selected'));
    const row = document.querySelector(`#interactions-table tr[data-interaction-id="${ix.id}"]`);
    if (row) row.classList.add('selected');

    detailAside.classList.add('flow-mode', 'open');   // reveal floating panel
    detailTitle.textContent = 'Interaction details';

    document.getElementById('detail-empty').style.display = 'none';
    document.getElementById('detail-body').style.display = '';

    const dl = document.getElementById('detail-identity');
    dl.innerHTML = '';
    const fields = [
      ['summary', ix.summary],
      ['interaction_id', ix.id],
      ['anchor span(s)', evidence.filter(e => e.is_anchor).map(e => e.span_id).join(', ')],
      ['evidence spans', String(evidence.length)],
    ];
    fields.forEach(([k, v]) => {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v;
      dl.appendChild(dt); dl.appendChild(dd);
    });

    const tdl = document.getElementById('detail-timing');
    tdl.innerHTML = '';
    [['started_at', ix.started_at], ['ended_at', ix.ended_at]].forEach(([k, v]) => {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = String(v);
      tdl.appendChild(dt); tdl.appendChild(dd);
    });

    detailAttrsHeader.textContent = 'Spans';
    detailAttrsPre.style.display = 'none';
    detailSpansTable.style.display = '';
    const tbody = detailSpansTable.querySelector('tbody');
    tbody.innerHTML = '';
    evidence.forEach(ev => {
      const tr = document.createElement('tr');
      const tAnchor = document.createElement('td');
      tAnchor.className = 'anchor-cell';
      if (ev.is_anchor) {
        tAnchor.textContent = '⚓';
        tAnchor.title = 'anchor span';
      }
      tr.appendChild(tAnchor);
      tr.appendChild(spanLinkCell(ev.span_id));
      tr.appendChild(spanLinkCell(ev.parent_id));
      const tKind = document.createElement('td');
      tKind.textContent = ev.kind || '—';
      tKind.style.color = ev.kind ? '#c9c9c9' : '#666';
      tr.appendChild(tKind);
      const tSvc = document.createElement('td');
      tSvc.textContent = ev.service_name || '—';
      tSvc.style.color = ev.service_name ? '#c9c9c9' : '#666';
      tr.appendChild(tSvc);
      tbody.appendChild(tr);
    });
  }

  treeBtn.addEventListener('click', () => setView('tree'));
  flowBtn.addEventListener('click', () => setView('flow'));

  // "Process this trace" button inside the empty state: runs the extractor
  // for the current trace on demand, then re-loads the view. Same semantics
  // as running the CLI (single-trace; replaces any existing proto data).
  const processBtn = flowEmpty.querySelector('[data-proto-process]');
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
          flowEmpty.appendChild(document.createElement('br'));
          flowEmpty.appendChild(document.createTextNode('Failed to process: HTTP ' + resp.status));
          return;
        }
        // Re-run the loader: clear the latch so it re-fetches and renders.
        flowLoaded = false;
        await loadFlow();
      } catch (e) {
        processBtn.disabled = false;
        processBtn.textContent = original;
        flowEmpty.appendChild(document.createElement('br'));
        flowEmpty.appendChild(document.createTextNode('Failed to process: ' + e));
      }
    });
  }
})();
