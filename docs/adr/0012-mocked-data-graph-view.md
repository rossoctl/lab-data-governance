# Mocked data-graph view beside the execution forest (demo-only)

The execution forest (ADR-0009) is the *tracing* view: who-called-whom, derived
purely from captured spans, scenario-agnostic. By construction it cannot express
the two things the patent-app data-lineage demo exists to show
(`demo-patent-app/scenario.md` §7):

- **Precision (T1).** `write_file(keywords)` and `write_file(summary)` render as
  two flat, identical siblings under the agent. The tree cannot say one is
  **clean** and one **confidential**, nor that they fork from the same
  transformed payload `d′`.
- **Recall (T2).** `web_search(summary)` is a fresh trace with no edge back to
  T1; the tree cannot show that its bytes trace to the confidential DB **D**
  across a file + session gap.

Those are properties of a **data graph** (data-identity edges — "these bytes came
from those bytes"), not an execution tree. Step 07 of the demo plan asks for that
data graph *beside* the forest, and — per the brief — **mocked this round**: we
draw the desired target, we do not yet compute it.

## Decision

Add an **opt-in, demo-only** data-graph view as a SEPARATE module —
`api/ui/data_graph_mock.js` — that returns the **hardcoded** scenario §8 graph
(nodes, edges, classifications, the recall edge, the metamorphosis copy).
`api/ui/forest.html` gains a **view switch** ("Execution forest · Data graph")
that flips `body.mode-datagraph` and renders the data graph with the page's
existing card + SVG-edge engine. The scenario-agnostic core (`forest_logic.js`,
ADR-0009) and the store overlay (`forest_scenario_overlay.js`, ADR-0010) are
**not touched**.

What the mock renders (from scenario.md §8), as a single left-to-right *river*:

- T1: `D ─d→ A ─d→ L[transform d→d′]` then a **fork** from `d′` —
  `→ keywords → F1` (**clean**, TN) and `→ summary → F2` (**confidential** ‼, TP).
- A dashed **session/trace boundary** after the files; `F1`/`F2` are **single
  nodes** written in T1 and read in T2 — the node *is* the data identity.
- T2: `F1 → A → web_search(keywords)` (**clean**, approved) and
  `F2 → A → web_search(summary)` (**confidential** ‼ — the leak, lit red + pulsing).
- A sweeping dashed **recall edge** `web_search(summary) ⟵ d′ ⟵ d ⟵ D` — the
  cross-session data-identity edge a session-scoped trace structurally cannot
  have, and the axis-2 true-positive the trace gets wrong (false-negative).

Nodes/edges are colored by **classification** (clean = green, confidential =
red), each carrying the `<true, tracing, lineage>` rationale shown on click.

## Why mocked, and why isolated

- **Mocked, labeled as such.** A persistent `MOCKED` pill on the switch, a banner
  reading *"drawn by hand from scenario.md §8, not computed from spans,"* and a
  notes block stating the real builder would populate `edge_annotations`. A
  viewer never mistakes it for a derivation. This honors the brief's
  honesty guardrail (don't imply it was computed) and keeps precision (T1) and
  recall (T2) as **distinct** wins; it is explicitly **not** sold as
  classifier-evasion (the airtight win is cross-session recall).
- **Isolated + removable**, same wall as ADR-0010: the data + rationale live in
  one module; `forest_logic.js` stays pure. Delete the file, the `<script>` +
  view switch + `#datagraph` block in `forest.html`, and the `_UI_ASSETS`
  whitelist entry — the forest is untouched.
- **Opt-in.** Default view is the real forest; the data graph is one click away
  (or `/forest/<id>?view=datagraph` to deep-link / screenshot).

## Considered alternatives

1. **Compute the data graph for real** from spans + `edge_annotations`
   (`derived` / `derived_from`, migration #0004 / ADR-0007) and a content
   classifier at each transform node. The proper, non-mocked target — rejected
   *for now* (the brief scopes this round to the mock; the classifier and the
   cross-edge taint propagation are larger work). This view is built so the same
   shell renders a computed graph later with the mock flag off.
2. **A second standalone page/tab.** Rejected: the pitch is *same run, two
   graphs* — the metamorphosis (flat siblings → clean/confidential fork) reads
   only when the data graph sits in the same place as the forest, reusing one
   engine.
3. **This view.** Chosen: gives the demo its payoff data graph without corrupting
   the agnostic core, at the cost of explicit, contained, clearly-labeled mock
   data.

## Consequences

- Scenario-coupled and hardcoded: the graph is the patent-app's T1/T2, not a
  function of the loaded trace. The view is identical for any `trace_id` — honest
  given the "mocked" labeling, and the reason the pill/banner are non-dismissible.
- The forest path is unchanged; with the switch on *Execution forest* the page
  behaves exactly as before (ADR-0009/0010).
- **De-mock path:** when alternative 1 lands, swap `data_graph_mock.js`'s
  hardcoded literal for a query-time derivation over `edge_annotations`; the
  `forest.html` renderer + view switch stay — the same additive, no-rewrite
  posture as ADR-0009/0010.

## Files

- `api/ui/data_graph_mock.js` — the mocked §8 graph + rationale (pure, node-tested).
- `api/ui/forest.html` — view switch, `#datagraph` container, the data-graph
  renderer (cards + labeled SVG edges + recall arc), verdict CSS, deep-link hook.
- `api/__init__.py` — `_UI_ASSETS` whitelist entry.
- `tests/api/test_data_graph_mock_js.py` — referential-integrity + structure tests.
- Related: **ADR-0009** (the forest this sits beside), **ADR-0010** (the store
  overlay it layers on), **ADR-0007** (the `edge_annotations` substrate the real
  builder would use); `demo-patent-app/scenario.md` §8 + `plan/07`.
