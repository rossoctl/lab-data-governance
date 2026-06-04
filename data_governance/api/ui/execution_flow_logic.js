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

  if (!treeBtn || !flowBtn || !flowSection || !entitiesTbody || !interactionsTbody) {
    return;
  }

  let flowLoaded = false;
  let flowData = null;
  // The currently-selected flow item, as a pin descriptor — or null in pure
  // span-detail mode. Drives the Add/Unpin button state. See refreshHighlightBtn.
  let currentSelection = null;   // { key, label, spanIds }

  // Role glyph maps for the shared spans table.
  const IX_ROLE = (role) =>
    role === 'anchor'    ? { glyph: '⚓', title: 'anchor span' } :
    role === 'connector' ? { glyph: '↳', title: 'connector span', dim: true } :
    role === 'info'      ? { glyph: 'i', title: 'info span', dim: true } :
                           { glyph: '', title: '' };

  const ENTITY_ROLE = (role) =>
    role === 'discovered_via' ? { glyph: '✦', title: 'discovered via' } :
    role === 'identified_via' ? { glyph: '·', title: 'identified via', dim: true } :
                                { glyph: '', title: role || '' };

  // Add/Unpin highlight button — created lazily in JS so we don't touch markup.
  // Shared by the interaction & entity panels. It is parented into the SPANS
  // section header (#detail-attributes-h) by renderSpansTable on every call,
  // because the sibling code paths set that header's text via `textContent`,
  // which destroys any child nodes. See renderSpansTable / setView / showPayload.
  //
  // It acts on the CURRENT selection (single-select) and reflects that item's
  // pin state against the shared tree-side pin store (TraceTreeNav):
  //   - already pinned  → "Unpin" in the pin's color
  //   - not pinned      → "Add to highlights" previewing the next color
  // There is no cap — any number of sets can be pinned at once.
  let highlightBtn = document.getElementById('highlight-tree-btn');
  if (!highlightBtn && detailAttrsHeader) {
    highlightBtn = document.createElement('button');
    highlightBtn.id = 'highlight-tree-btn';
    highlightBtn.className = 'refresh-btn';
    highlightBtn.style.display = 'none';
    highlightBtn.addEventListener('click', async () => {
      const sel = currentSelection;
      const nav = window.TraceTreeNav;
      if (!sel || !nav) return;
      if (nav.isPinned(sel.key)) {
        nav.removePin(sel.key);          // unpin in place — stay in flow view
        return;                          // removePin fires onPinsChanged (refresh + repaint)
      }
      setView('tree');
      try { await nav.addPin(sel); } catch (_e) {}
      // addPin does NOT fire onPinsChanged, so refresh + repaint explicitly.
      refreshHighlightBtn();
      repaintFlowDots();
    });
  }

  // Set a swatch + text on the button in one shot (swatch optional).
  function setHighlightBtn(text, color, disabled) {
    if (!highlightBtn) return;
    highlightBtn.textContent = '';
    if (color) {
      const sw = document.createElement('span');
      sw.className = 'btn-swatch';
      sw.style.background = color;
      highlightBtn.appendChild(sw);
    }
    highlightBtn.appendChild(document.createTextNode(text));
    highlightBtn.disabled = !!disabled;
  }

  // Reconcile the button with currentSelection + the tree-side pin store.
  function refreshHighlightBtn() {
    if (!highlightBtn) return;
    const sel = currentSelection;
    const nav = window.TraceTreeNav;
    if (!sel || !nav) { highlightBtn.style.display = 'none'; return; }
    highlightBtn.style.display = '';
    if (nav.isPinned(sel.key)) {
      setHighlightBtn('Unpin', nav.slotColorFor(sel.key), false);
    } else {
      setHighlightBtn('Add to highlights', nav.nextFreeColor(), false);
    }
  }

  // Let legend-✕ / Esc (which the tree owns) refresh our button AND repaint the
  // flow dots afterwards — both are views of the same pin store.
  function onPinsChanged() {
    refreshHighlightBtn();
    repaintFlowDots();
  }
  if (window.TraceTreeNav && window.TraceTreeNav.setOnPinsChanged) {
    window.TraceTreeNav.setOnPinsChanged(onPinsChanged);
  }

  function getTraceId() {
    const m = window.location.pathname.match(/^\/trace\/([0-9a-f]+)/i);
    return m ? m[1] : null;
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
      detailAttrsHeader.classList.remove('detail-attrs-h--with-btn');
      detailSpansTable.style.display = 'none';
      detailAttrsPre.style.display = '';
      const hb = document.getElementById('highlight-tree-btn');
      if (hb) hb.style.display = 'none';
      // Left flow-detail mode; no flow item is "current" for the button now.
      currentSelection = null;
    } else {
      treeBtn.classList.remove('active');
      flowBtn.classList.add('active');
      treeEl.style.display = 'none';
      flowSection.style.display = 'block';
      if (!flowLoaded) loadFlow();
    }
  }

  async function loadFlow() {
    flowLoaded = true;
    const traceId = getTraceId();
    if (!traceId) return;
    try {
      const resp = await fetch('/proto/interactions/' + encodeURIComponent(traceId));
      if (!resp.ok) {
        flowEmpty.style.display = '';
        flowEmpty.textContent = 'Failed to load: HTTP ' + resp.status;
        return;
      }
      flowData = await resp.json();
      renderFlow();
    } catch (e) {
      flowEmpty.style.display = '';
      flowEmpty.textContent = 'Failed to load: ' + e;
    }
  }

  function renderFlow() {
    if (!flowData || flowData.interactions.length === 0) {
      flowEmpty.style.display = '';
      return;
    }
    flowEmpty.style.display = 'none';

    const entById = new Map();
    flowData.entities.forEach(e => entById.set(e.id, e));

    entitiesTbody.innerHTML = '';
    flowData.entities.forEach(e => {
      const tr = document.createElement('tr');
      const kindTd = document.createElement('td');
      const pill = document.createElement('span');
      pill.className = 'ent-pill ' + e.kind;
      pill.textContent = e.kind;
      kindTd.appendChild(pill);
      const markerTd = document.createElement('td');
      markerTd.className = 'flow-pin-cell';
      const nameTd = document.createElement('td');
      nameTd.textContent = e.display_name;
      const detTd = document.createElement('td');
      detTd.style.color = '#888';
      detTd.textContent = e.detected_from;
      tr.appendChild(kindTd);
      tr.appendChild(markerTd);
      tr.appendChild(nameTd);
      tr.appendChild(detTd);
      tr.dataset.entityId = e.id;
      const evidence = (flowData.spans_by_entity && flowData.spans_by_entity[e.id]) || [];
      tr.addEventListener('click', () => selectEntity(e, evidence));
      entitiesTbody.appendChild(tr);
    });

    interactionsTbody.innerHTML = '';

    // Compute depth for each interaction by walking parent links.
    // Interactions are ordered by started_at; parents always start before children,
    // so a single forward pass suffices.
    const depthById = new Map();
    flowData.interactions.forEach(ix => {
      const pid = ix.parent_interaction_id;
      const d = (pid && depthById.has(pid)) ? depthById.get(pid) + 1 : 0;
      depthById.set(ix.id, d);
    });

    flowData.interactions.forEach(ix => {
      const tr = document.createElement('tr');
      tr.dataset.interactionId = ix.id;
      const depth = depthById.get(ix.id) || 0;

      const tStarted = document.createElement('td');
      tStarted.style.color = '#888';
      tStarted.style.fontFamily = 'ui-monospace, monospace';
      tStarted.textContent = ix.started_at ? ix.started_at.split('T')[1].slice(0, 12) : '';
      tr.appendChild(tStarted);

      const markerTd = document.createElement('td');
      markerTd.className = 'flow-pin-cell';
      tr.appendChild(markerTd);

      const caller = entById.get(ix.caller_entity_id);
      const callee = entById.get(ix.callee_entity_id);

      const tCaller = document.createElement('td');
      if (depth > 0) {
        const indent = document.createElement('span');
        indent.style.color = '#555';
        indent.style.fontFamily = 'ui-monospace, monospace';
        indent.textContent = '│ '.repeat(depth - 1) + '└─ ';
        tCaller.appendChild(indent);
      }
      if (caller) {
        const p = document.createElement('span');
        p.className = 'ent-pill ' + caller.kind;
        p.textContent = caller.kind;
        tCaller.appendChild(p);
        tCaller.appendChild(document.createTextNode(caller.display_name));
      } else {
        tCaller.textContent = '?';
      }
      tr.appendChild(tCaller);

      const tCallee = document.createElement('td');
      if (callee) {
        const p = document.createElement('span');
        p.className = 'ent-pill ' + callee.kind;
        p.textContent = callee.kind;
        tCallee.appendChild(p);
        tCallee.appendChild(document.createTextNode(callee.display_name));
      } else {
        tCallee.textContent = '?';
      }
      tr.appendChild(tCallee);

      const tStatus = document.createElement('td');
      if (ix.error === true) { tStatus.textContent = 'ERROR'; tStatus.className = 'err-cell'; }
      else if (ix.error === false) { tStatus.textContent = 'ok'; tStatus.className = 'ok-cell'; }
      else { tStatus.textContent = '—'; tStatus.style.color = '#666'; }
      tr.appendChild(tStatus);

      const tReq = document.createElement('td');
      if (ix.request_payload_hash) {
        const a = document.createElement('span');
        a.className = 'pl-link';
        a.textContent = ix.request_payload_hash.slice(0, 8);
        a.addEventListener('click', (ev) => {
          ev.stopPropagation();
          showPayload(ix.request_payload_hash);
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
          showPayload(ix.response_payload_hash);
        });
        tResp.appendChild(a);
      } else {
        tResp.style.color = '#666';
        tResp.textContent = '—';
      }
      tr.appendChild(tResp);

      const tSpans = document.createElement('td');
      tSpans.style.color = '#888';
      const evidence = flowData.spans_by_interaction[ix.id] || [];
      const anchorCount = evidence.filter(e => e.role === 'anchor').length;
      tSpans.textContent = `${evidence.length} (${anchorCount} anchor)`;
      tr.appendChild(tSpans);

      tr.addEventListener('click', () => selectInteraction(ix, evidence));

      interactionsTbody.appendChild(tr);
    });

    repaintFlowDots();
  }

  // Paint a slot-color dot in each pinned row's marker cell (one per row),
  // reading the tree-side pin store. The marker column is always present, so
  // toggling a dot never shifts layout. Called after every flow render and on
  // every pin change (see onPinsChanged) to keep the views in sync.
  function repaintFlowDots() {
    const nav = window.TraceTreeNav;
    const colorByKey = new Map();
    if (nav && nav.getPins) {
      nav.getPins().forEach(p => colorByKey.set(p.key, p.color));
    }
    const paint = (tr, key) => {
      const cell = tr.querySelector('.flow-pin-cell');
      if (!cell) return;
      cell.textContent = '';
      const color = colorByKey.get(key);
      if (!color) return;
      const dot = document.createElement('span');
      dot.className = 'flow-pin-dot';
      dot.style.background = color;
      cell.appendChild(dot);
    };
    entitiesTbody.querySelectorAll('tr').forEach(tr => {
      paint(tr, 'entity:' + tr.dataset.entityId);
    });
    interactionsTbody.querySelectorAll('tr').forEach(tr => {
      paint(tr, 'interaction:' + tr.dataset.interactionId);
    });
  }

  async function showPayload(hash) {
    const resp = await fetch('/proto/payload/' + encodeURIComponent(hash));
    if (!resp.ok) {
      alert('Failed to fetch payload: ' + resp.status);
      return;
    }
    const data = await resp.json();
    detailAside.classList.add('flow-mode');
    detailTitle.textContent = 'Payload';
    document.getElementById('detail-empty').style.display = 'none';
    document.getElementById('detail-body').style.display = '';
    detailAttrsHeader.textContent = 'Content';
    detailAttrsHeader.classList.remove('detail-attrs-h--with-btn');
    detailAttrsPre.style.display = '';
    detailAttrsPre.textContent = JSON.stringify(data.content, null, 2);
    detailSpansTable.style.display = 'none';
    if (highlightBtn) highlightBtn.style.display = 'none';

    const dl = document.getElementById('detail-identity');
    dl.innerHTML = '';
    [['kind', data.content_kind], ['hash', data.content_hash], ['bytes', data.byte_size]].forEach(([k, v]) => {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = String(v);
      dl.appendChild(dt); dl.appendChild(dd);
    });
    document.getElementById('detail-timing').innerHTML = '';
  }

  async function navigateToSpan(spanId) {
    setView('tree');
    if (window.TraceTreeNav && window.TraceTreeNav.selectSpanByIdInTree) {
      try {
        await window.TraceTreeNav.selectSpanByIdInTree(spanId);
      } catch (e) {
        // best-effort — leave the user in tree view
      }
    }
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

  function renderSpansTable(evidence, roleFor) {
    detailAttrsHeader.textContent = 'Spans';
    // Re-parent the button into the SPANS header on every call: the line above
    // sets textContent, which clears the header's children. Right-alignment and
    // the flex layout come from the --with-btn class (see trace_tree.html).
    if (highlightBtn) {
      detailAttrsHeader.classList.add('detail-attrs-h--with-btn');
      detailAttrsHeader.appendChild(highlightBtn);
    }
    detailAttrsPre.style.display = 'none';
    detailSpansTable.style.display = '';
    const tbody = detailSpansTable.querySelector('tbody');
    tbody.innerHTML = '';
    evidence.forEach(ev => {
      const tr = document.createElement('tr');
      const tAnchor = document.createElement('td');
      tAnchor.className = 'anchor-cell';
      const r = roleFor(ev.role);          // { glyph, title, dim }
      tAnchor.textContent = r.glyph;
      tAnchor.title = r.title;
      if (r.dim) tAnchor.style.color = '#888';
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

  function selectInteraction(ix, evidence) {
    document.querySelectorAll('#interactions-table tr.selected').forEach(r => r.classList.remove('selected'));
    const row = document.querySelector(`#interactions-table tr[data-interaction-id="${ix.id}"]`);
    if (row) row.classList.add('selected');

    detailAside.classList.add('flow-mode');
    detailTitle.textContent = 'Interaction details';

    document.getElementById('detail-empty').style.display = 'none';
    document.getElementById('detail-body').style.display = '';

    const dl = document.getElementById('detail-identity');
    dl.innerHTML = '';
    const fields = [
      ['summary', ix.summary],
      ['interaction_id', ix.id],
      ['anchor span(s)', evidence.filter(e => e.role === 'anchor').map(e => e.span_id).join(', ')],
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

    renderSpansTable(evidence, IX_ROLE);

    currentSelection = {
      key: 'interaction:' + ix.id,
      label: ix.summary || ix.id,
      spanIds: evidence.map(e => e.span_id).filter(Boolean),
    };
    refreshHighlightBtn();
  }

  function selectEntity(entity, evidence) {
    document.querySelectorAll('#entities-table tr.selected').forEach(r => r.classList.remove('selected'));
    const row = document.querySelector(`#entities-table tr[data-entity-id="${entity.id}"]`);
    if (row) row.classList.add('selected');

    detailAside.classList.add('flow-mode');
    detailTitle.textContent = 'Entity details';
    document.getElementById('detail-empty').style.display = 'none';
    document.getElementById('detail-body').style.display = '';

    const dl = document.getElementById('detail-identity');
    dl.innerHTML = '';
    [
      ['display_name', entity.display_name],
      ['kind', entity.kind],
      ['natural_key', entity.natural_key],
      ['entity_id', entity.id],
      ['detected_from', entity.detected_from],
    ].forEach(([k, v]) => {
      const dt = document.createElement('dt'); dt.textContent = k;
      const dd = document.createElement('dd'); dd.textContent = v == null ? '—' : String(v);
      dl.appendChild(dt); dl.appendChild(dd);
    });

    // No timing for entities.
    document.getElementById('detail-timing').innerHTML = '';

    renderSpansTable(evidence, ENTITY_ROLE);

    currentSelection = {
      key: 'entity:' + entity.id,
      label: entity.display_name || entity.id,
      spanIds: evidence.map(e => e.span_id).filter(Boolean),
    };
    refreshHighlightBtn();
  }

  treeBtn.addEventListener('click', () => setView('tree'));
  flowBtn.addEventListener('click', () => setView('flow'));
})();
