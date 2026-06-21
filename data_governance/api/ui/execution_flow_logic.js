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
    flowData.entities.forEach(e => {
      const tr = document.createElement('tr');

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
      // display_name is "unknown" until Step 3.c lands richer naming; the
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

      tr.appendChild(phaseTd);
      tr.appendChild(kindTd);
      tr.appendChild(nameTd);
      tr.appendChild(detTd);
      tr.appendChild(spanLinkCell(e.anchor_span_id || null));
      entitiesTbody.appendChild(tr);
    });

    interactionsTbody.innerHTML = '';
    flowData.interactions.forEach(ix => {
      const tr = document.createElement('tr');
      tr.dataset.interactionId = ix.id;

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
      const anchorCount = evidence.filter(e => e.is_anchor).length;
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

    detailAside.classList.add('flow-mode');
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
})();
