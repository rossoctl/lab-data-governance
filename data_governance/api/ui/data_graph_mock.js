/*
 * MOCKED data-graph for the patent-app demo — the "lineage" view. (ADR-0012)
 *
 * ════════════════════════════════════════════════════════════════════════
 *  READ THIS BEFORE EDITING — what this file is and is NOT
 * ════════════════════════════════════════════════════════════════════════
 *
 * The execution forest (ADR-0009 forest_logic.js) is the *tracing* view:
 * who-called-whom, derived purely from captured spans. It structurally cannot
 * express two things the patent demo hinges on (scenario.md §7):
 *   - T1: that keywords (clean) and summary (confidential) are DIFFERENT,
 *     even though they appear as two flat, identical write_file siblings;
 *   - T2: that web_search(summary) traces back to the confidential DB `D`
 *     across a file + session gap (a fresh trace has no edge to T1).
 *
 * This file is the *lineage* view that DOES express them: a DATA graph
 * (data-identity edges — "these bytes came from those bytes") that crosses
 * calls, stores, and sessions. It is the payoff of the demo (plan/07, step 8).
 *
 * IT IS HARDCODED / MOCKED THIS ROUND. Every node and edge below is typed in
 * by hand from scenario.md §8 — NOTHING here is computed from spans. We are
 * drawing the *desired target*, not deriving it. The UI labels it "MOCKED"
 * prominently so a viewer never mistakes it for a real derivation.
 *
 * The NON-mocked version is real future work, not a dead end: DG already has
 * the substrate — the `edge_annotations` table (`derived` / `derived_from`,
 * migration #0004 / ADR-0007) is designed for exactly this cross-edge taint
 * propagation. A graph-builder would populate it from the captured spans +
 * a content classifier at each transform node, and this same view would then
 * render a COMPUTED data graph with the mock flag off.
 *
 * ── Shape (left-to-right "river" spine + a y-axis LLM branch) ──
 *   D ─d→ patent-agent ─┬─ d2 → F1 (clean)         (agent ⇅ LLM:
 *                       └─ d3 → F2 (confidential)    d up, d1 back down)
 *        ……… fresh trace / session gap …………
 *   F1 ─d2→ agent ─d2→ web_search          (clean, approved)
 *   F2 ─d3→ agent ─d3→ web_search ‼        (CONFIDENTIAL — the leak)
 *
 * The LLM is OFF the storage spine (a delegated sub-call): the agent sends d,
 * gets d1 back, and the AGENT writes the two parts d2/d3. Running-index naming —
 * d (raw) → d1 (LLM output) → d2,d3 (its parts) — so the SAME token rides
 * write→read→egress: data identity across the store + session gap is explicit
 * (d3 traces home d3←d1←d←D) with no session-scoped trace. F1/F2 are SHARED
 * nodes (written in T1, read in T2). Classification is by content, not ancestry.
 *
 * Honesty guardrails carried verbatim from scenario.md §6 / plan/07:
 *   - Keep precision (T1) and recall (T2) as DISTINCT wins; don't blur them.
 *   - Don't claim perfect precision.
 *   - Not sold as classifier-evasion: a content gate *does* approve keywords
 *     and may partly catch the summary. The airtight win is cross-session
 *     RECALL — no stateless gate or session-scoped trace recovers the
 *     summary's origin across the file/session gap.
 *
 * API:  dataGraph() -> a pure literal { mocked, title, columns, boundaryAfter,
 *       nodes, edges, identity, legend, notes }. Pure, no DOM; same
 *       dual-export pattern as forest_logic.js / forest_scenario_overlay.js.
 *
 * Node `type`  → renderer icon: datastore | agent | transform | file | external
 * Node `verdict` ∈ {clean, confidential, mixed} (the classifier's output).
 */

'use strict';

function dataGraph() {
  // Columns 0..5 of the river. `boundaryAfter` draws the session/trace gap
  // line right after the files column (the F1/F2 written in T1, read in T2).
  const nodes = [
    { id: 'D',  col: 0, zone: 'T1', type: 'datastore', label: 'patent DB · Tier-1',
      sub: '', verdict: 'confidential',
      why: 'The confidential patent database (Tier-1). SOURCE FLOOR: anything read from D starts confidential regardless of its bytes — content can later declassify a derivative, but never raise D above its floor.' },
    { id: 'A1', col: 1, zone: 'T1', type: 'agent', label: 'patent-agent',
      sub: '', verdict: 'confidential',
      why: 'The agent reads d (the patent text) from D and hands it to its LLM. Holds confidential data.' },
    { id: 'L',  lane: 'top', over: 'A1', zone: 'T1', type: 'transform', label: 'LLM',
      sub: '', verdict: 'confidential',
      why: 'The TRANSFORMATION node (the LLM): it turns d into d1 (its full output). d1 is a derived metamorphosis of d, not a copy — lineage must carry contamination THROUGH it and then SPLIT it: the agent writes two parts of d1 (d2, d3) that diverge in classification. (The agent is the actor on the spine; the bytes derive from d1.)' },
    { id: 'F1', col: 2, zone: 'F', type: 'file', label: 'F1 · keywords.txt',
      sub: 'MinIO object', verdict: 'clean',
      why: 'CLEAN. By ancestry d2 ⟵ d1 ⟵ d ⟵ D, yet the content (keywords.txt) carries NO patent essence, so it is DECLASSIFIED below D’s floor. Tracing over-taints it purely by shared ancestry with d; lineage re-classifies by content and clears it. → the PRECISION win (axis 1).' },
    { id: 'F2', col: 2, zone: 'F', type: 'file', label: 'F2 · summary.txt',
      sub: 'MinIO object', verdict: 'confidential',
      why: 'CONFIDENTIAL. The content of d3 (summary.txt) carries the patent essence (and inherits D’s floor). Both tracing and lineage flag it here — but tracing is right for the wrong reason (it taints by ancestry, not by reading the content).' },
    { id: 'A2', col: 3, zone: 'T2', type: 'agent', label: 'patent-agent · fresh trace',
      sub: '', verdict: 'mixed',
      why: 'T2 is a NEW A2A session = a NEW trace. Nothing in T2’s trace points back to T1 — that gap is exactly what a session-scoped tracer cannot bridge. The agent reads the two files back (d2, d3) and calls web_search on each.' },
    { id: 'W1', col: 4, zone: 'T2', type: 'external', label: 'web_search',
      sub: 'egress', verdict: 'clean',
      why: 'CLEAN. Approved egress — sharing d2 (keywords.txt) with an outside tool is allowed. Everyone agrees: no leak.' },
    { id: 'W2', col: 4, zone: 'T2', type: 'external', label: 'web_search',
      sub: 'egress', verdict: 'confidential', leak: true,
      why: 'THE VIOLATION. Tracing is BLIND here: T2 is a fresh trace, the taint label was dropped at the file/session boundary. Lineage matches DATA IDENTITY across the gap — d3 ⟵ d1 ⟵ d ⟵ D — back to the confidential DB. → the RECALL win (axis 2), the one no stateless gate or session-scoped trace can reproduce.' },
  ];

  const edges = [
    // ── T1: ingest → agent → LLM round-trip → the AGENT writes the fork ──
    { from: 'D',  to: 'A1', data: 'd',  verdict: 'confidential' },
    { from: 'A1', to: 'L',  data: 'd',  verdict: 'confidential', vert: true },
    { from: 'L',  to: 'A1', data: 'd1', verdict: 'confidential', vert: true,
      why: 'The LLM returns d1 (its full output) to the agent. The transform happens here — but it is the AGENT, not the LLM, that then writes d1’s parts (d2, d3) to the stores.' },
    { from: 'A1', to: 'F1', data: 'd2', verdict: 'clean', fork: true,
      why: 'The fork, CLEAN branch. The agent writes d2 (a part of d1); content declassifies it below D’s floor.' },
    { from: 'A1', to: 'F2', data: 'd3',  verdict: 'confidential', fork: true,
      why: 'The fork, CONFIDENTIAL branch. The agent writes d3 (a part of d1); content keeps the patent essence. Same parent d1, opposite verdict: the split the execution forest cannot show.' },
    // ── T2: read back → egress (across the session gap) ──
    { from: 'F1', to: 'A2', data: 'd2', verdict: 'clean' },
    { from: 'F2', to: 'A2', data: 'd3',  verdict: 'confidential' },
    { from: 'A2', to: 'W1', data: 'd2', verdict: 'clean',
      why: 'Approved egress — d2 carries no essence.' },
    { from: 'A2', to: 'W2', data: 'd3',  verdict: 'confidential', leak: true,
      why: 'THE LEAK: the patent essence leaves to an external tool — a policy violation surfaced only by lineage.' },
  ];

  // Recall arc removed from the view (UI request). The leak's lineage back to D
  // is still fully traceable on the canvas via the shared token: d3 ← d1 ← d ← D.
  const identity = [];

  return {
    mocked: true,
    title: 'Desired data graph — where the patent data actually went',
    columns: 5,
    boundaryAfter: 2,            // dashed session/trace boundary after the F column
    boundaryLabel: 'fresh trace · session + store gap',
    nodes,
    edges,
    identity,
    // (metamorphosis story removed — the below-graph foot that displayed it is gone)
    legend: [
      { cls: 'clean', label: 'clean — declassified / approved (true-negative)' },
      { cls: 'conf',  label: 'confidential ‼ — carries the patent essence (source floor)' },
    ],
    notes: [
      'MOCKED this round — drawn by hand from scenario.md §8, not computed from spans.',
      'F1 / F2 are one node each: the same bytes written in T1 and read in T2. That persistent data identity across the store + session gap is what a session-scoped trace cannot represent.',
      'The real builder would populate DG’s edge_annotations (derived / derived_from, ADR-0007) from captured spans + a content classifier at each transform node.',
      'Precision (T1) and recall (T2) are DISTINCT wins. Not classifier-evasion — the airtight differentiation is cross-session recall.',
    ],
  };
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { dataGraph };
}
if (typeof window !== 'undefined') {
  window.DataGraphMock = { dataGraph };
}
