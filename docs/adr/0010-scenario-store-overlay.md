# Scenario store overlay for the execution forest (demo-only)

The execution forest (ADR-0009) is scenario-agnostic and synthesizes nothing:
every node maps to a real captured `(trace_id, span_id)`, and it has **no
datastore nodes** because the tool→datastore hop is uncaptured — the sidecar's
`lineage.target.id` names the *tool*, not the store behind it. The patent-app
demo, though, needs to *show* the stores — the confidential patent DB **D** and
the filesystem **F** the keywords/summary land in — because the data-lineage
story is about *where data goes*, and "stops at the tool boundary" hides exactly
that. The store identity is in no generic span attribute; it lives only in the
app-specific tool-call **args** (`read_patent {patent_id}`, `read_file {name}`,
`write_file {content}`).

## Decision

Add an **opt-in, demo-only** store overlay as a SEPARATE module —
`api/ui/forest_scenario_overlay.js` — that maps the patent-app's tool calls to
`D`/`F` nodes from their args, rendered as a **dashed 4th column** behind a UI
toggle ("Data stores · demo overlay"). The scenario-agnostic core
(`forest_logic.js`, ADR-0009) is **not touched**; this overlay is the explicit,
contained exception to ADR-0009's *agnostic + nothing-synthesized* rule.

Mapping (patent-app specific):

- `read_patent` → **D** (database, read).
- `read_file` / `write_file` → **F** (filesystem); sub-identified by a filename
  arg when one is present (`read_file {name}` → `F · keywords` / `F · summary`).
  `write_file` carries only `content` (no filename), so the two writes
  **converge on one `F` node** — honest given the captured args.
- `web_search` → **no store node**: it is external egress, not a datastore. That
  leak is the data graph's concern (demo plan step 07), not this view's.

Stores are de-duplicated by id within a trace, so repeated calls converge.

## Why isolated (not folded into `forest_logic.js`)

- ADR-0009's value is a forest reusable across any Kagenti agent that never lies
  about provenance. Putting tool-name/arg knowledge into it forfeits both. The
  wall keeps the core honest.
- The overlay **synthesizes** nodes (no store span exists) — precisely what
  ADR-0009 forbids. So it is visibly demarcated (dashed nodes, "demo overlay"
  tag, a note reading *"derived from tool args, not captured spans"*) and
  trivially removable: delete the file, the `<script>`/toggle in `forest.html`,
  and the `_UI_ASSETS` whitelist entry — nothing else changes.
- Opt-in: toggle off ⇒ the pure ADR-0009 forest. For a non-patent-app trace the
  overlay matches no tools and adds nothing.

## Considered alternatives

1. **Capture the tool→store hop for real.** The proper fix: instrument the MCP
   tools' Postgres/MinIO access so D/F become genuine spans and the agnostic
   core renders them with no overlay. Rejected *for now* — larger, and blocked
   on the uncaptured non-HTTP Postgres wire / uninstrumented S3 that ADR-0009
   already calls out. Remains the de-mock target (below).
2. **Generic "store (uncaptured)" ghost node.** Agnostic + honest, but cannot
   name `D` vs `F` (no generic signal carries store identity) — too weak for the
   demo narrative.
3. **This overlay.** Chosen: gives the demo its named D/F nodes without
   corrupting the agnostic core, at the cost of explicit, contained
   scenario-coupling.

## Consequences

- Scenario-coupled: keyed to patent-app tool identities + arg shapes. An
  unrelated app reusing a name like `read_file` would get spurious store nodes —
  toggle the overlay off for it (documented in the module header).
- `web_search` egress is intentionally not shown here; the precision/recall
  story (demo plan 01) belongs to the data graph (07), not the forest.
- **De-mock path:** when alternative 1 lands, delete the overlay and the
  captured store spans flow through the agnostic forest unchanged — the same
  additive, no-rewrite posture ADR-0009 / ADR-0007 use elsewhere.

## Files

- `api/ui/forest_scenario_overlay.js` — the overlay (pure, node-tested).
- `api/ui/forest.html` — `<script>` + toggle + dashed store column + dynamic note.
- `api/__init__.py` — `_UI_ASSETS` whitelist entry.
- `tests/api/test_forest_scenario_overlay_js.py` — overlay unit tests.
- Related: **ADR-0009** (the agnostic forest this overlays); `demo-patent-app/plan`
  05–07 (the demo it serves).
