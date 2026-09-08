/**
 * THE single source of the **Entity** kind → APPEARANCE decisions: its colour, and
 * (since issue #218) its graph node SHAPE.
 *
 * Lives in `lib/` (not in `EntityPill.tsx`, where it started) because two views
 * now paint by it: the entity pills in the flow tables, and the entity NODES in
 * the Execution Flow graph. A graph node and its table row must not disagree
 * about what colour an `agent` is, so neither restates the map — both read this.
 *
 * TWO DECISIONS, NOT ONE, and they are kept independent on purpose: `agent` and
 * `llm` share a colour but must not share a shape (see {@link KIND_SHAPE}). Deriving
 * either map from the other would make that pair indistinguishable again.
 *
 * `lib/` is also where this repo keeps its render-free logic (`flow.ts`,
 * `graph.ts`, `pins.ts`), which is what lets the mapping be unit-tested and
 * imported by a `.tsx` component without tripping the react-refresh
 * only-export-components rule.
 */
import type { LabelProps } from '@patternfly/react-core';

/** The PF `Label` colour a kind is painted in. */
export type EntityKindColor = NonNullable<LabelProps['color']>;

/** Per-kind colour, matching the vanilla `.ent-pill` palette exactly. */
export const KIND_COLOR: Record<string, EntityKindColor> = {
  user: 'gold',
  external_client: 'purple',
  agent: 'blue',
  tool: 'green',
  external_service: 'red',
  llm: 'blue',
};

/**
 * The fallback for a kind not in {@link KIND_COLOR} — one the backend has begun
 * emitting that the UI has no palette entry for yet. Named so the pill and the
 * graph node fall back identically instead of each picking its own neutral.
 */
export const KIND_COLOR_FALLBACK: EntityKindColor = 'grey';

/** The PF `Label` colour for a kind, fallback included. One decision, one place. */
export function colorForKind(kind: string): EntityKindColor {
  return KIND_COLOR[kind] ?? KIND_COLOR_FALLBACK;
}

/**
 * The node SHAPE a kind is drawn as (issue #218).
 *
 * THE SECOND PER-KIND DECISION THIS MODULE OWNS, and it is a genuinely separate
 * decision from {@link KIND_COLOR} rather than a re-expression of it. The proof is
 * the `agent`/`llm` pair: they deliberately share one colour (`blue`), so on any
 * view that paints by kind hue cannot tell them apart. Shape is what separates
 * them — which is exactly why #218 asks for shapes and not for more colours.
 *
 * WHY THESE ARE THIS MODULE'S OWN STRINGS, not PF's `NodeShape` enum. This file is
 * in the EAGER bundle: `EntityPill` and `InteractionDiagram` both import it
 * unlazily. Every `@patternfly/react-topology` import in the repo is confined to
 * `ExecutionFlowGraph.tsx` so that its ~130kB of JS and CSS rides the LAZY chunk (see
 * that file's import-order note, which is emphatic about it). `NodeShape` is a
 * runtime enum, not a type, so importing it here would drag the topology chunk into
 * the eager bundle for every reader of the trace list — a real regression to pay for
 * a naming convenience. The graph translates these names to `NodeShape` at its own
 * boundary, precisely as it already translates a colour NAME to a PF global variable
 * (see {@link PF_COLOR_TO_GLOBAL_VAR}): a NAME→NAME table at the edge, one decision
 * in the middle.
 *
 * The values coincide with PF's `NodeShape` member names, which is what makes that
 * translation one-to-one and total. That is a convenience, not a dependency — nothing
 * here reads the enum, and a PF rename would surface as a type error in the graph's
 * table rather than as a silently wrong shape.
 */
export type EntityKindShape = 'hexagon' | 'rect' | 'rhombus' | 'circle' | 'ellipse';

/**
 * Per-kind shape, straight from the issue: Agent - hexagon, Tool - rectangle,
 * LLM - diamond/rhombus, User - circle.
 *
 * DELIBERATELY NARROWER THAN {@link KIND_COLOR}, which knows six kinds. #218 names
 * four, so four are assigned and everything else takes
 * {@link KIND_SHAPE_FALLBACK}. Guessing a fifth shape for `external_client` or
 * `external_service` would put an unexplained outline on the graph — and a shape
 * the legend below does not name is exactly the puzzle a legend exists to prevent.
 */
export const KIND_SHAPE: Record<string, EntityKindShape> = {
  agent: 'hexagon',
  tool: 'rect',
  llm: 'rhombus',
  user: 'circle',
};

/**
 * The shape for a kind {@link KIND_SHAPE} does not name.
 *
 * `ellipse` — the shape EVERY node had before #218 — so an unnamed kind degrades to
 * the previous drawing rather than to one of the four shapes the legend claims a
 * specific meaning for. `Entity.kind` is `string | null` in `types.ts` (an open set
 * the backend can extend), and the DB's `entity_kind` enum already contains two
 * values this map does not name, so the fallback is a reachable path, not a
 * defensive flourish.
 *
 * A NAMED CONSTANT for the same reason {@link KIND_COLOR_FALLBACK} is one: the node
 * and the legend both read it, and each picking its own neutral is the drift the
 * constant prevents.
 */
export const KIND_SHAPE_FALLBACK: EntityKindShape = 'ellipse';

/** The shape a kind is drawn as, fallback included. One decision, one place. */
export function shapeForKind(kind: string): EntityKindShape {
  return KIND_SHAPE[kind] ?? KIND_SHAPE_FALLBACK;
}

/**
 * Each PF label colour → the PF GLOBAL palette variable that renders it.
 *
 * Why not the obvious `--pf-v5-c-label--m-<colour>__content--Color`? Because PF
 * declares those component variables on the `.pf-v5-c-label` selector itself, so
 * they resolve to nothing outside a label element. The graph's nodes are SVG
 * shapes, not labels, so referencing them there silently fell back and every node
 * came out the same grey. The `--pf-v5-global--*` variables below are declared at
 * `:root` (and re-declared by `pf-v5-theme-dark`), so they resolve anywhere in the
 * document AND still track the dark theme.
 *
 * The values chosen are the ones PF's own label rules point those colours at, so
 * a graph node reads as the same hue as its pill. This is a NAME→NAME table, not
 * a second kind→colour decision: {@link KIND_COLOR} remains the only place a kind
 * is assigned a colour.
 */
const PF_COLOR_TO_GLOBAL_VAR: Record<EntityKindColor, string> = {
  blue: '--pf-v5-global--primary-color--100',
  green: '--pf-v5-global--success-color--100',
  red: '--pf-v5-global--danger-color--100',
  gold: '--pf-v5-global--palette--gold-400',
  purple: '--pf-v5-global--palette--purple-300',
  orange: '--pf-v5-global--palette--orange-300',
  cyan: '--pf-v5-global--palette--cyan-300',
  grey: '--pf-v5-global--Color--200',
};

/**
 * The CSS variable reference a kind's colour should be painted with, usable
 * anywhere in the document (see {@link PF_COLOR_TO_GLOBAL_VAR} for why it is a
 * global rather than a label component variable).
 *
 * Returns a `var(…)` REFERENCE, never a resolved value, so the dark theme's own
 * overrides apply at paint time exactly as they do for the pills. The fallback is
 * the repo's `--dg-color-label` token, so a node stays visible if PF ever renames
 * the variable. No raw hex anywhere in the chain.
 */
export function kindColorVar(kind: string): string {
  return `var(${PF_COLOR_TO_GLOBAL_VAR[colorForKind(kind)]}, var(--dg-color-label))`;
}

/**
 * The neutral stroke/label colour used by the **Lineage tab's** graph nodes.
 *
 * SCOPED TO ONE TAB, not to the whole graph. The Execution Flow tab paints its nodes
 * with `kindColorVar` above, exactly as it always has — see `NodeData.kindColoured` in
 * `ExecutionFlowGraph`, which is the flag that switches the one shared node renderer
 * between the two readings.
 *
 * WHY THE LINEAGE TAB GIVES UP KIND HUE. On that tab colour is carrying three other
 * meanings — the trace's data sources, fan-in, fan-out — plus error red on the edges,
 * and the one fact a reader opens it to see ("which entities are this trace's
 * origins") was left to a ring because every hue was already spoken for. Kind is the
 * least load-bearing of them THERE: it is still stated in the node's label, its
 * `<title>` and accessible name, and in the kind-coloured `EntityPill` in the tables
 * on the same screen. So on Lineage kind gives up hue and the data sources take it
 * (`--dg-lineage-source`, see global.css).
 *
 * ON EXECUTION FLOW NOTHING COMPETES, so there is no reason to spend the signal: that
 * tab has no lineage overlay, kind is the only thing colour could mean, and a node
 * agreeing with its table row is worth having. Hence one renderer and two readings
 * rather than one compromise applied to both.
 *
 * Returns a `var(…)` reference like its sibling, so the dark theme still applies at
 * paint time, with `--dg-color-label` as the fallback. No raw hex.
 */
export function nodeNeutralColorVar(): string {
  return `var(${PF_COLOR_TO_GLOBAL_VAR.grey}, var(--dg-color-label))`;
}
