/*
 * Pure data-layer for the execution-forest view (ADR-0009).
 *
 * Turns the raw per-trace spans (GET /spans?trace_id=T) into the UNIFIED
 * per-invocation execution forest — U -> A -> { L calls, tool calls }, flat
 * siblings under the agent.
 *
 * SCENARIO-AGNOSTIC. The rules are expressed only in terms of the standard
 * Kagenti observability conventions — OpenInference span kinds (AGENT / LLM /
 * TOOL) and the lineage sidecar's hop kind (agent_to_tool) — never in terms of
 * any specific app's tool names, stores, or routes. Switching the agent app
 * (its tools, its LLM, its data stores) changes the rendered content, not the
 * algorithm: no tool table, no name munging, no hardcoded routes, nothing
 * synthesized.
 *
 * DG's trace-tree renders the raw parent_id tree, which for a Kagenti agent
 * (openai_agents + lineage sidecar) is unreadable: framework wrappers, httpx /
 * starlette plumbing, MCP-handshake noise, and DUPLICATE tool spans (in-process
 * TOOL + sidecar TOOL for one call). buildForest re-parents the real nodes flat
 * onto the agent, dedups the tool spans by trace STRUCTURE (the sidecar span
 * nests under the in-process TOOL span's httpx client), and drops the rest.
 * Pure derivation over spans (ADR-0001): every node carries its real
 * (trace_id, span_id); nothing is synthesized.
 *
 * Factored out of forest.html so it runs under node
 * (tests/api/test_forest_logic_js.py) without a browser — same pattern as
 * trace_tree_logic.js.
 */

'use strict';

function _attrs(s) { return (s && s.attributes) || {}; }
function _source(s) { return _attrs(s).source || null; }
function _oikind(s) { return _attrs(s)['openinference.span.kind'] || null; }
function _hop(s) { return _attrs(s)['lineage.hop.kind'] || null; }
function _target(s) { return _attrs(s)['lineage.target.id'] || null; }
function _key(s) { return s.trace_id + '|' + s.span_id; }

function _byStart(a, b) {
  return String(a.started_at).localeCompare(String(b.started_at));
}

function _byId(spans) {
  const m = new Map();
  for (const s of spans) m.set(s.span_id, s);
  return m;
}

/* Ancestor spans within the loaded set, nearest first. */
function _ancestors(s, byId) {
  const out = [];
  const seen = new Set();
  let cur = s.parent_id != null ? byId.get(s.parent_id) : null;
  while (cur && !seen.has(cur.span_id)) {
    seen.add(cur.span_id);
    out.push(cur);
    cur = cur.parent_id != null ? byId.get(cur.parent_id) : null;
  }
  return out;
}

function _descendsFrom(s, anchorId, byId) {
  for (const a of _ancestors(s, byId)) {
    if (a.span_id === anchorId) return true;
  }
  return false;
}

/* A = the innermost in-process AGENT span with in-process LLM descendants — the
   agent, not the outer Runner wrapper. "Innermost" = deepest ancestor chain, so
   the rule is framework-shape-agnostic (no name match). Single-agent scope:
   delegation forests (A -> sub-agent) are a documented future generalization
   (ADR-0009). */
function findAnchor(spans, byId) {
  const agents = spans.filter(
    (s) => _source(s) === 'in-process' && _oikind(s) === 'AGENT',
  );
  const llms = spans.filter(
    (s) => _source(s) === 'in-process' && _oikind(s) === 'LLM',
  );
  const qualifying = agents.filter((a) =>
    llms.some((l) => _descendsFrom(l, a.span_id, byId)),
  );
  const pool = qualifying.length > 0 ? qualifying : agents;
  if (pool.length === 0) return null;
  let best = pool[0];
  let bestDepth = _ancestors(best, byId).length;
  for (const a of pool.slice(1)) {
    const d = _ancestors(a, byId).length;
    if (d > bestDepth) { best = a; bestDepth = d; }
  }
  return best;
}

/* U->A inbound = the root ancestor of A (the topmost span above the agent — the
   inbound request span, whatever it is named). Generic: no route-name match.
   Returns null when A is itself the trace root. */
function rootOf(anchor, byId) {
  const anc = _ancestors(anchor, byId);
  return anc.length ? anc[anc.length - 1] : null;
}

function _toolNode(display, inproc, sidecar) {
  /* Display name = the in-process TOOL span's name (the tool's own name) when we
     have it; for a sidecar-only call, the sidecar's lineage target id. Both are
     real values off the span — no normalization, no lookup table. */
  return {
    role: 'tool',
    span: display, // the span whose detail/bodies the UI shows on click
    name: inproc ? inproc.name : (_target(sidecar) || display.name),
    target: _target(sidecar),
    source: _source(display),
    provenance: [inproc, sidecar].filter(Boolean).map(_key),
  };
}

/* One node per agent->tool call. The in-process TOOL spans are the authoritative
   per-call list. Each sidecar agent_to_tool span is matched to its in-process
   TOOL span by trace STRUCTURE — the sidecar span nests under that TOOL's httpx
   client span, so the TOOL span is one of its ancestors. The matched sidecar
   span is PREFERRED for display (wire bodies + lineage/trust). Sidecar-only
   calls (no in-process TOOL twin) are kept; with no sidecar at all the
   in-process TOOL spans stand alone. */
function _toolChildren(spans, anchorId, byId) {
  const inproc = spans.filter(
    (s) => _source(s) === 'in-process' && _oikind(s) === 'TOOL'
      && _descendsFrom(s, anchorId, byId),
  );
  const sidecar = spans.filter(
    (s) => _source(s) === 'sidecar' && _hop(s) === 'agent_to_tool',
  );
  const inprocIds = new Set(inproc.map((s) => s.span_id));

  const twin = new Map(); // in-process TOOL span_id -> its sidecar span
  const usedSidecar = new Set();
  for (const sc of sidecar.slice().sort(_byStart)) {
    const anc = _ancestors(sc, byId).find((a) => inprocIds.has(a.span_id));
    if (anc && !twin.has(anc.span_id)) {
      twin.set(anc.span_id, sc);
      usedSidecar.add(sc.span_id);
    }
  }

  const nodes = [];
  for (const ip of inproc.slice().sort(_byStart)) {
    const sc = twin.get(ip.span_id) || null;
    nodes.push(_toolNode(sc || ip, ip, sc));
  }
  for (const sc of sidecar.slice().sort(_byStart)) {
    if (usedSidecar.has(sc.span_id)) continue;
    nodes.push(_toolNode(sc, null, sc));
  }
  return nodes;
}

/* Build the unified execution forest for one trace's spans.
   Returns { user, agent, children: [{role:'L'|'tool', span, ...}], llmCount,
   toolCount, ok }. children are flat siblings under `agent`, in true
   started_at order (the tool-use loop interleaves L and tools). */
function buildForest(spans) {
  const byId = _byId(spans);
  const anchor = findAnchor(spans, byId);
  if (!anchor) {
    return { user: null, agent: null, children: [], llmCount: 0, toolCount: 0, ok: false };
  }
  const aid = anchor.span_id;
  const lNodes = spans
    .filter(
      (s) => _source(s) === 'in-process' && _oikind(s) === 'LLM'
        && _descendsFrom(s, aid, byId),
    )
    .map((s) => ({ role: 'L', span: s, provenance: [_key(s)] }));
  const toolNodes = _toolChildren(spans, aid, byId);
  const children = lNodes.concat(toolNodes).sort((a, b) => _byStart(a.span, b.span));
  return {
    user: rootOf(anchor, byId),
    agent: anchor,
    children: children,
    llmCount: lNodes.length,
    toolCount: toolNodes.length,
    ok: true,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildForest, findAnchor, rootOf };
}
if (typeof window !== 'undefined') {
  window.Forest = { buildForest, findAnchor, rootOf };
}
