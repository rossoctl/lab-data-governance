/*
 * Pure data-layer for the execution-forest view (ADR-0009; multi-agent
 * delegation generalization ADR-0011).
 *
 * Turns the raw per-trace spans (GET /spans?trace_id=T) into the UNIFIED
 * per-invocation execution forest. For a single-agent trace that is
 * U -> A -> { L calls, tool calls } (flat siblings under the agent). For a
 * multi-agent delegation trace it is the NESTED delegation forest:
 * U -> A0 -> { A0's L/tool calls, sub-agent A1 -> {...}, A2 -> {...}, ... },
 * each sub-agent carrying its own L/tool/sub-agent children, recursively.
 *
 * SCENARIO-AGNOSTIC. The rules are expressed only in terms of the standard
 * Kagenti observability conventions — OpenInference span kinds (AGENT / LLM /
 * TOOL) and the lineage sidecar's hop kind (agent_to_tool) — never in terms of
 * any specific app's tool names, stores, routes, or agent names. Switching the
 * agent app (its tools, its LLM, its data stores, its agent topology) changes
 * the rendered content, not the algorithm.
 *
 * DG's trace-tree renders the raw parent_id tree, which for a Kagenti agent
 * (openai_agents + lineage sidecar) is unreadable: framework wrappers, httpx /
 * starlette plumbing, MCP-handshake noise, DUPLICATE tool spans (in-process
 * TOOL + sidecar TOOL for one call), and — across A2A delegation — agent-as-tool
 * wrapper spans. buildForest re-parents the real nodes onto the agent that made
 * them, nests sub-agents under their delegating agent, dedups the tool spans by
 * trace STRUCTURE, and drops the rest. Pure derivation over spans (ADR-0001):
 * every node carries its real (trace_id, span_id); nothing is synthesized.
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

/* The "real" agents of a trace: every in-process AGENT span that DIRECTLY owns
   at least one in-process LLM call — i.e. is that LLM's NEAREST in-process AGENT
   ancestor. This excludes the framework's outer Runner wrapper AGENT (its LLMs
   belong to the deeper inner agent), generalizing the single-agent "innermost
   AGENT" rule to every agent in a delegation. One agent app -> one real agent;
   an N-agent delegation -> N real agents (no name match, span-kinds only). */
function findRealAgents(spans, byId) {
  const agentIds = new Set(
    spans
      .filter((s) => _source(s) === 'in-process' && _oikind(s) === 'AGENT')
      .map((s) => s.span_id),
  );
  if (agentIds.size === 0) return [];
  const nearestAgent = (s) => {
    for (const a of _ancestors(s, byId)) if (agentIds.has(a.span_id)) return a;
    return null;
  };
  const owns = new Set();
  for (const s of spans) {
    if (_source(s) === 'in-process' && _oikind(s) === 'LLM') {
      const a = nearestAgent(s);
      if (a) owns.add(a.span_id);
    }
  }
  return spans.filter(
    (s) => _source(s) === 'in-process' && _oikind(s) === 'AGENT'
      && owns.has(s.span_id),
  );
}

/* Original single-anchor rule, kept only as the degenerate fallback for traces
   where no AGENT owns an LLM (LLM-less agents): the deepest in-process AGENT. */
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

/* One node per agent->tool call MADE BY a given agent. The in-process TOOL spans
   owned by the agent are the authoritative per-call list; agent-as-tool
   delegation wrappers (a TOOL span with a sub-agent in its subtree) are excluded
   here — they become a sub-agent node, not a tool node. Each sidecar
   agent_to_tool span owned by the agent is matched to its in-process TOOL span
   by trace STRUCTURE (the sidecar span nests under that TOOL's httpx client, so
   the TOOL span is one of its ancestors). The matched sidecar span is PREFERRED
   for display. Sidecar-only calls (no in-process TOOL twin) are kept; with no
   sidecar the in-process TOOL spans stand alone.

   `ownerOf(s)` returns s's nearest real-agent ancestor; the sidecar filter is
   thus ANCHOR-SCOPED to this agent (fixing the prior single-anchor asymmetry
   where every sidecar tool attached to one anchor regardless of the caller). */
function _toolChildren(spans, agentId, byId, ownerOf, delegationToolIds) {
  const owned = (s) => { const o = ownerOf(s); return !!o && o.span_id === agentId; };
  const inproc = spans.filter(
    (s) => _source(s) === 'in-process' && _oikind(s) === 'TOOL'
      && owned(s) && !delegationToolIds.has(s.span_id),
  );
  const sidecar = spans.filter(
    (s) => _source(s) === 'sidecar' && _hop(s) === 'agent_to_tool' && owned(s),
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

   Returns { user, agent, children, llmCount, toolCount, ok }. `children` are the
   flat siblings under the top agent in true started_at order, each a node of
   role 'L' | 'tool' | 'agent'. A role:'agent' node is a delegated sub-agent and
   carries its OWN { children, llmCount, toolCount } (recursively) — the nested
   delegation forest. llmCount/toolCount on the result are the TOP agent's own
   counts (a sub-agent's counts live on its node), so the single-agent shape is
   unchanged and additive: a flat renderer that only knows 'L'/'tool' degrades
   gracefully (it sees the sub-agent as one extra node); a nesting renderer opts
   into `children`. */
function buildForest(spans) {
  const byId = _byId(spans);

  let realAgents = findRealAgents(spans, byId);
  if (realAgents.length === 0) {
    // No AGENT owns an LLM: fall back to the original single-anchor rule
    // (deepest AGENT, even LLM-less) so degenerate traces behave as before.
    const anchor = findAnchor(spans, byId);
    if (!anchor) {
      return { user: null, agent: null, children: [], llmCount: 0, toolCount: 0, ok: false };
    }
    realAgents = [anchor];
  }

  const realIds = new Set(realAgents.map((a) => a.span_id));
  const ownerOf = (s) => {
    for (const a of _ancestors(s, byId)) if (realIds.has(a.span_id)) return a;
    return null;
  };

  // TOOL spans that are an ancestor of some real agent = agent-as-tool
  // delegation wrappers (openai_agents wraps each A2A peer as a FunctionTool).
  // Collapse them into the delegation edge (the sub-agent node), not tool calls.
  const delegationToolIds = new Set();
  for (const r of realAgents) {
    for (const anc of _ancestors(r, byId)) {
      if (_source(anc) === 'in-process' && _oikind(anc) === 'TOOL') {
        delegationToolIds.add(anc.span_id);
      }
    }
  }

  function buildAgentNode(agent) {
    const aid = agent.span_id;
    const lNodes = spans
      .filter((s) => {
        if (_source(s) !== 'in-process' || _oikind(s) !== 'LLM') return false;
        const o = ownerOf(s);
        return !!o && o.span_id === aid;
      })
      .map((s) => ({ role: 'L', span: s, provenance: [_key(s)] }));
    const toolNodes = _toolChildren(spans, aid, byId, ownerOf, delegationToolIds);
    const subAgents = realAgents
      .filter((a) => { const o = ownerOf(a); return !!o && o.span_id === aid; })
      .map((a) => buildAgentNode(a));
    const children = lNodes.concat(toolNodes).concat(subAgents)
      .sort((a, b) => _byStart(a.span, b.span));
    return {
      role: 'agent',
      span: agent,
      name: agent.name,
      source: _source(agent),
      children: children,
      llmCount: lNodes.length,
      toolCount: toolNodes.length,
      provenance: [_key(agent)],
    };
  }

  // The top agent = a real agent with no real-agent ancestor (the orchestrator).
  // If a trace somehow has several roots, prefer the one that delegates to the
  // most others, then the earliest — the true top.
  const tops = realAgents.filter((a) => ownerOf(a) === null);
  let top = tops[0];
  if (tops.length > 1) {
    const descCount = (a) =>
      realAgents.filter((r) => r !== a && _descendsFrom(r, a.span_id, byId)).length;
    top = tops.reduce((best, a) => {
      const d = descCount(a);
      const bd = descCount(best);
      if (d > bd) return a;
      if (d === bd) return _byStart(a, best) < 0 ? a : best;
      return best;
    }, tops[0]);
  }

  const node = buildAgentNode(top);
  return {
    user: rootOf(top, byId),
    agent: top,
    children: node.children,
    llmCount: node.llmCount,
    toolCount: node.toolCount,
    ok: true,
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { buildForest, findAnchor, findRealAgents, rootOf };
}
if (typeof window !== 'undefined') {
  window.Forest = { buildForest, findAnchor, findRealAgents, rootOf };
}
