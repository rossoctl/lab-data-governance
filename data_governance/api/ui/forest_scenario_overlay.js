/*
 * Scenario STORE OVERLAY for the execution-forest view — DEMO-ONLY. (ADR-0010)
 *
 * ════════════════════════════════════════════════════════════════════════
 *  READ THIS BEFORE EDITING — what this file is and is NOT
 * ════════════════════════════════════════════════════════════════════════
 *
 * forest_logic.js (ADR-0009) is deliberately SCENARIO-AGNOSTIC and synthesizes
 * NOTHING: every node it emits maps to a real captured (trace_id, span_id), and
 * it has no datastore nodes because the tool->datastore hop is uncaptured (the
 * sidecar's lineage.target.id names the *tool*, not the store behind it).
 *
 * This file is the explicit, ISOLATED exception to that. For the patent-app
 * demo we want to *see* the data stores (the patent DB `D`, the filesystem `F`)
 * as nodes — the narrative needs them. Their identity is NOT in any generic
 * span attribute; it lives only in the app-specific tool-call ARGS. So this
 * overlay is, by design, coupled to the patent-app's tool identities + arg
 * shapes — and it is kept OUT of forest_logic.js so that core stays pure.
 *
 *   - It is OPT-IN (a UI toggle; off => pure ADR-0009 forest).
 *   - Its nodes are rendered visually distinct (dashed) and labeled
 *     "demo overlay" so a viewer never mistakes them for captured spans.
 *   - It SYNTHESIZES store nodes from tool args. That is the thing ADR-0009
 *     forbids — hence the hard wall: it is a separate, removable module.
 *
 * To remove the demo coupling: delete this file, drop the <script> + toggle in
 * forest.html, and de-whitelist it in api/__init__.py. forest_logic.js and the
 * forest itself are untouched.
 *
 * The PROPER (non-demo) fix is to capture the tool->store hop for real, so D/F
 * become genuine spans and the agnostic core renders them with no overlay — the
 * "de-mock" path noted in ADR-0010 (mirrors 07's edge_annotations story).
 *
 * ════════════════════════════════════════════════════════════════════════
 *
 * API:  storeOverlay(forest) -> { stores: [{id,label,kind,ops}], links:
 *       [{childKey,storeId,op}] }, where `forest` is buildForest()'s output and
 *       childKey is "<trace_id>|<span_id>" of the originating tool child so the
 *       renderer can draw a tool->store edge to the right card.
 *
 * Pure + node-testable (no DOM), same dual-export pattern as forest_logic.js.
 */

'use strict';

/* Patent-app store rules — keyed to THIS demo's tool identities + arg shapes
   (confirmed against captured spans / test fixtures):
     read_patent  args {"patent_id":N}        -> reads database  D
     read_file    args {"name":"keywords"|..} -> reads filesystem F (named)
     write_file   args {"content":"..."}      -> writes filesystem F (unnamed:
                                                  no filename arg, so writes
                                                  converge on a single F node)
     web_search   args {"query":"..."}        -> EXTERNAL egress, NOT a store
                                                  => intentionally no node here
                                                  (that leak is the data graph's
                                                  job, step 07).
   `nameKeys` is the ordered list of arg fields that, if present, sub-identify
   the store (so read_file splits into "F · keywords" / "F · summary"). */
const STORE_RULES = {
  read_patent: { base: 'D', kind: 'database',   op: 'read',  nameKeys: [] },
  read_file:   { base: 'F', kind: 'filesystem', op: 'read',  nameKeys: ['name', 'path', 'file', 'key', 'filename'] },
  write_file:  { base: 'F', kind: 'filesystem', op: 'write', nameKeys: ['name', 'path', 'file', 'key', 'filename'] },
  // web_search: external egress — deliberately absent (see ADR-0010).
};

/* Normalize a displayed tool name to a rule key. The forest names tools by the
   in-process TOOL span (e.g. "read_patent") or, for sidecar-only calls, the
   lineage target id (e.g. "read-patent-mcp"). Fold both to "read_patent". */
function _normTool(name) {
  return String(name || '')
    .toLowerCase()
    .replace(/[\s-]+/g, '_')
    .replace(/_mcp$/, '')
    .replace(/^mcp_/, '');
}

/* Tool-call args live on the (sidecar-preferred) span as input.value, usually a
   JSON string. Defensive: tolerate object or unparseable. */
function _args(span) {
  const v = span && span.attributes && span.attributes['input.value'];
  if (v && typeof v === 'object') return v;
  if (typeof v !== 'string') return {};
  try { return JSON.parse(v); } catch (_e) { return {}; }
}

function _spanKey(span) { return span.trace_id + '|' + span.span_id; }

/* Collect every tool node in the (possibly nested) forest. Tools can live under
   delegated sub-agents — a role:'agent' child carries its own children
   (ADR-0011) — so the walk recurses rather than scanning only the top level. */
function _collectTools(children, out) {
  for (const c of (children || [])) {
    if (c.role === 'tool') out.push(c);
    else if (c.role === 'agent') _collectTools(c.children, out);
  }
}

/* Derive the patent-app store nodes + tool->store links for one forest.
   Stores are de-duplicated by id within the trace, so multiple calls to the
   same store converge on one node (e.g. both write_file calls -> one `F`). */
function storeOverlay(forest) {
  const stores = new Map();  // id -> {id,label,kind,ops:[]}
  const links = [];          // {childKey, storeId, op}
  if (!forest || !forest.ok || !Array.isArray(forest.children)) {
    return { stores: [], links: [] };
  }
  const tools = [];
  _collectTools(forest.children, tools);
  for (const c of tools) {
    const rule = STORE_RULES[_normTool(c.name)];
    if (!rule) continue;  // unmapped tool (e.g. web_search) -> no store node

    const a = _args(c.span);
    let suffix = '';
    for (const k of rule.nameKeys) {
      if (a[k] != null && String(a[k]).length) { suffix = String(a[k]); break; }
    }
    const id = rule.base + (suffix ? '/' + suffix : '');
    const label = rule.base + (suffix ? ' · ' + suffix : '');
    if (!stores.has(id)) stores.set(id, { id, label, kind: rule.kind, ops: [] });
    const st = stores.get(id);
    if (!st.ops.includes(rule.op)) st.ops.push(rule.op);
    links.push({ childKey: _spanKey(c.span), storeId: id, op: rule.op });
  }
  return { stores: Array.from(stores.values()), links };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { storeOverlay };
}
if (typeof window !== 'undefined') {
  window.ForestOverlay = { storeOverlay };
}
