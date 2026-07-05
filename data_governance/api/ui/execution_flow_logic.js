/* P-interactions execution-flow view.
 *
 * Adds a view switcher to the trace-tree page. Toggles between the
 * existing span tree and a flat table of (entities, interactions)
 * derived from spans by the in-cluster interactions processor
 * (`data-governance-interactions`).
 *
 * Data model — four lean REST resources under the trace:
 *   GET /traces/<id>/interactions            -> { interactions: [...] }
 *   GET /traces/<id>/entities                -> { entities: [...] }
 *   GET /traces/<id>/interactions/<iid>/spans -> { spans: [...] }
 *   GET /traces/<id>/entities/<eid>/spans     -> { spans: [...] }
 * The two lists are fetched up front (they populate the tables); each row's
 * span evidence is fetched lazily on click from the matching /spans
 * sub-resource. Interaction rows show an at-a-glance "N (M anchor)" count
 * from the span_count/anchor_count fields on the list response, so the count
 * column needs no extra fetch. If the derived graph hasn't been populated for
 * the trace yet, shows an empty-state hint.
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

  // Tool entities come in two flavours, distinguished purely by their
  // natural-key SHAPE (no schema field): a tool deployed as its own MCP service
  // is exactly `tool:(<project>,<service>)` — a single parenthesised tuple with
  // nothing after the closing paren. An in-framework tool hosted by an agent is
  // `tool:<owning_agent_natural_key>:<name>` (normally `tool:agent:(…):<name>`,
  // but also `tool:(unknown):<name>` when the owning agent is transiently
  // unresolved — note the trailing `:<name>` segment). We therefore key the
  // DEPLOYED case on the precise `tool:(…)` shape (closing paren ends the key)
  // and treat every other `tool:` key as in-framework, so a `tool:(unknown):…`
  // in-process tool is not misread as deployed. Returns
  // 'in-framework' | 'deployed' | null.
  function toolSubtype(entity) {
    if (!entity || entity.kind !== 'tool') return null;
    const nk = entity.natural_key || '';
    if (!nk.startsWith('tool:')) return null;
    if (/^tool:\([^)]*\)$/.test(nk)) return 'deployed';
    return 'in-framework';
  }

  // Build the kind pill for an entity. The label is always just the kind
  // (`tool`); the two tool flavours are distinguished by BORDER STYLE via a
  // subtype CSS class — in-framework is dashed, deployed is solid (see the
  // .ent-pill border rules). A tooltip names the flavour.
  function makeEntPill(entity) {
    const p = document.createElement('span');
    const sub = toolSubtype(entity);
    p.className = 'ent-pill ' + entity.kind + (sub ? ' ' + (sub === 'deployed' ? 'tool-deployed' : 'tool-inframework') : '');
    p.textContent = entity.kind;
    if (sub) p.title = sub === 'deployed' ? 'Deployed as its own MCP service' : 'In-framework tool hosted by an agent';
    return p;
  }

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
    const m = window.location.pathname.match(/^\/traces\/([0-9a-f]+)/i);
    return m ? m[1] : null;
  }

  function formatTime24Utc(iso) {
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return iso;
      return d.toISOString().slice(11, 19);
    } catch (_e) {
      return iso;
    }
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
    const base = '/traces/' + encodeURIComponent(traceId);
    try {
      // Interactions and entities are independent list resources; fetch them
      // in parallel and merge so renderFlow() sees the combined shape.
      const [ixResp, entResp] = await Promise.all([
        fetch(base + '/interactions'),
        fetch(base + '/entities'),
      ]);
      if (!ixResp.ok || !entResp.ok) {
        flowEmpty.style.display = '';
        flowEmpty.textContent =
          'Failed to load: HTTP ' + (ixResp.ok ? entResp.status : ixResp.status);
        return;
      }
      const [ix, ent] = await Promise.all([ixResp.json(), entResp.json()]);
      flowData = { ...ix, ...ent };
      renderFlow();
    } catch (e) {
      flowEmpty.style.display = '';
      flowEmpty.textContent = 'Failed to load: ' + e;
    }
  }

  // Lazy-fetch the span evidence for one interaction/entity row on click.
  // `resource` is 'interactions' or 'entities'. Returns the evidence list
  // (empty on any error, so the detail panel just shows an empty Spans table).
  async function fetchSpans(resource, id) {
    const traceId = getTraceId();
    if (!traceId) return [];
    const url = '/traces/' + encodeURIComponent(traceId) + '/' +
      resource + '/' + encodeURIComponent(id) + '/spans';
    try {
      const resp = await fetch(url);
      if (!resp.ok) return [];
      const data = await resp.json();
      return data.spans || [];
    } catch (_e) {
      return [];
    }
  }

  function renderFlow() {
    // Empty only when there is genuinely nothing derived for the trace. A trace
    // can have entities but no interactions yet (mid-drain, or spans that are
    // un-anchorable): still render the entities table in that case.
    if (!flowData ||
        (flowData.interactions.length === 0 && flowData.entities.length === 0)) {
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
      kindTd.appendChild(makeEntPill(e));
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
      tr.addEventListener('click', async () => {
        const evidence = await fetchSpans('entities', e.id);
        selectEntity(e, evidence);
      });
      entitiesTbody.appendChild(tr);
    });

    interactionsTbody.innerHTML = '';

    // Compute depth by walking parent links, independent of row order. The rows
    // arrive ordered by started_at, which is NOT a topological order — a parent
    // interaction can sort after its child (equal or NULL started_at, per the
    // /traces/<id>/interactions query's `ORDER BY started_at`), so a single forward
    // pass keyed on "parent already seen" would render such a child at depth 0.
    // Resolve each depth by following parent_interaction_id up through the full
    // set, memoising and guarding against cycles / missing parents.
    const ixById = new Map();
    flowData.interactions.forEach(ix => ixById.set(ix.id, ix));
    const depthById = new Map();
    function depthOf(id) {
      if (depthById.has(id)) return depthById.get(id);
      depthById.set(id, 0);  // cycle/self guard: break re-entry at this node
      const ix = ixById.get(id);
      const pid = ix && ix.parent_interaction_id;
      const d = (pid && ixById.has(pid)) ? depthOf(pid) + 1 : 0;
      depthById.set(id, d);
      return d;
    }
    flowData.interactions.forEach(ix => depthOf(ix.id));

    flowData.interactions.forEach(ix => {
      const tr = document.createElement('tr');
      tr.dataset.interactionId = ix.id;
      const depth = depthById.get(ix.id) || 0;

      const tStarted = document.createElement('td');
      tStarted.style.color = '#888';
      tStarted.style.fontFamily = 'ui-monospace, monospace';
      tStarted.textContent = ix.started_at ? formatTime24Utc(ix.started_at) : '';
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
        tCaller.appendChild(makeEntPill(caller));
        tCaller.appendChild(document.createTextNode(caller.display_name));
      } else {
        tCaller.textContent = '?';
      }
      tr.appendChild(tCaller);

      const tCallee = document.createElement('td');
      if (callee) {
        tCallee.appendChild(makeEntPill(callee));
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
      // Count comes from the list row (span_count/anchor_count); the span
      // list itself is fetched lazily on click.
      const spanCount = ix.span_count || 0;
      const anchorCount = ix.anchor_count || 0;
      tSpans.textContent = `${spanCount} (${anchorCount} anchor)`;
      tr.appendChild(tSpans);

      tr.addEventListener('click', async () => {
        const evidence = await fetchSpans('interactions', ix.id);
        selectInteraction(ix, evidence);
      });

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
    const resp = await fetch('/payloads/' + encodeURIComponent(hash));
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
    const timingRows = [
      ['started_at', ix.started_at],
      ['ended_at', ix.ended_at],
    ];
    // Duration only when both ends exist. Same formula/format as the span
    // detail panel (trace_tree.html): (ended - started) ms to 3 decimals.
    if (ix.started_at != null && ix.ended_at != null) {
      const ms = (new Date(ix.ended_at) - new Date(ix.started_at)).toFixed(3);
      timingRows.push(['duration', ms + ' ms']);
    }
    timingRows.forEach(([k, v]) => {
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
    const sub = toolSubtype(entity);
    [
      ['display_name', entity.display_name],
      ['kind', entity.kind],
      // Derived from the natural-key shape; shown only for tools.
      ...(sub ? [['tool_type', sub]] : []),
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
