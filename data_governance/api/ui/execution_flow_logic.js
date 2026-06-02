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
  let currentHighlightSpanIds = [];

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

  // "Highlight on tree" button — created lazily in JS so we don't touch markup.
  // Shared by the interaction & entity panels. It is parented into the SPANS
  // section header (#detail-attributes-h) by renderSpansTable on every call,
  // because the sibling code paths set that header's text via `textContent`,
  // which destroys any child nodes. See renderSpansTable / setView / showPayload.
  let highlightBtn = document.getElementById('highlight-tree-btn');
  if (!highlightBtn && detailAttrsHeader) {
    highlightBtn = document.createElement('button');
    highlightBtn.id = 'highlight-tree-btn';
    highlightBtn.className = 'refresh-btn';
    highlightBtn.textContent = 'Highlight on tree';
    highlightBtn.style.display = 'none';
    highlightBtn.addEventListener('click', async () => {
      const ids = currentHighlightSpanIds;
      if (!ids || !ids.length) return;
      setView('tree');
      if (window.TraceTreeNav && window.TraceTreeNav.highlightSpansInTree) {
        try { await window.TraceTreeNav.highlightSpansInTree(ids); } catch (_e) {}
      }
    });
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
      const nameTd = document.createElement('td');
      nameTd.textContent = e.display_name;
      const detTd = document.createElement('td');
      detTd.style.color = '#888';
      detTd.textContent = e.detected_from;
      tr.appendChild(kindTd);
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

    currentHighlightSpanIds = evidence.map(e => e.span_id).filter(Boolean);
    if (highlightBtn) highlightBtn.style.display = '';
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

    currentHighlightSpanIds = evidence.map(e => e.span_id).filter(Boolean);
    if (highlightBtn) highlightBtn.style.display = '';
  }

  treeBtn.addEventListener('click', () => setView('tree'));
  flowBtn.addEventListener('click', () => setView('flow'));
})();
