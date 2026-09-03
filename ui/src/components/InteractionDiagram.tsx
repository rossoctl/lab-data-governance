import { useMemo } from 'react';
import {
  Alert,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
} from '@patternfly/react-core';

import { deriveSequenceDiagram, type MessageSpec } from '../lib/sequenceDiagram';
import { kindColorVar } from '../lib/entityKind';
import { riskLevelColorVar } from '../lib/riskLevel';
import type { Entity, Interaction } from '../types';

/**
 * Layout metrics, in SVG user units (= px at scale 1).
 *
 * Hand-picked constants rather than measured text, deliberately: jsdom cannot
 * measure an SVG, so anything derived from `getBBox` would be untestable AND would
 * make the diagram's width a function of the browser's font metrics. A fixed pitch
 * plus a truncated label keeps the geometry a pure function of the COUNTS —
 * lifelines and messages — which is exactly what the derivation already gives us.
 */
const COL_WIDTH = 200; // lifeline-to-lifeline horizontal pitch
const ROW_HEIGHT = 34; // message-to-message vertical pitch
const HEAD_WIDTH = 168; // entity head box width (< COL_WIDTH, so boxes never touch)
const HEAD_HEIGHT = 40;
const MARGIN_X = 24; // left/right padding, and half the first column's gutter
const HEAD_TOP = 8;
const FIRST_ROW_Y = HEAD_TOP + HEAD_HEIGHT + 34; // first arrow, clear of the boxes
const BOTTOM_PAD = 24;
/** How far a self-call's loop reaches out to the right of its own lifeline. */
const SELF_LOOP_WIDTH = 44;
/** Vertical drop of a self-call's loop, so its two horizontal legs are distinct. */
const SELF_LOOP_HEIGHT = 12;
/** Arrowhead half-height / length. */
const HEAD_SIZE = 5;

/** The x centre of a lifeline column. */
function colX(index: number): number {
  return MARGIN_X + HEAD_WIDTH / 2 + index * COL_WIDTH;
}

/** The y centre of a message row. */
function rowY(row: number): number {
  return FIRST_ROW_Y + row * ROW_HEIGHT;
}

/**
 * One message row: the arrow, its `seq` tag, its hover title and its click target.
 *
 * A `<g>` per message rather than one flat list of paths, so the whole row —
 * arrow, tag and an invisible full-width hit strip — is one focusable, clickable
 * unit. The hit strip matters: a 1px-tall arrow is a cruel click target, and
 * without it a reader has to hit the line itself.
 */
function MessageRow({
  msg,
  row,
  isSelected,
  width,
  onSelect,
  riskLevel,
}: {
  msg: MessageSpec;
  row: number;
  isSelected: boolean;
  width: number;
  onSelect: () => void;
  /**
   * This message's INTERACTION's risk level (issue #170), or `undefined` when
   * no risk map was supplied, or when one was but this interaction has no entry
   * in it — see {@link InteractionDiagramProps.riskLevelByInteraction} for why
   * those two cases are collapsed into one "no treatment" outcome rather than
   * one of them painting a colour.
   */
  riskLevel: string | undefined;
}) {
  const y = rowY(row);
  const x1 = colX(msg.fromIndex);
  const x2 = colX(msg.toIndex);
  // The `--dg-*` tokens, never a raw hex — the same pairing the graph's edges use,
  // so a failed leg is the same red in both tabs.
  //
  // ERROR KEEPS PRECEDENCE (issue #170), same reasoning as the graph's edges: a
  // leg's `error` is a FACT about what happened, while a risk level is a GRADE
  // the policy engine assigned, and a fact must not be masked by a grade. Risk
  // colouring only applies when `riskLevel` is defined — see this component's
  // top-level doc comment for what an undefined value means.
  const colour = msg.isError
    ? 'var(--dg-color-error)'
    : riskLevel !== undefined
      ? riskLevelColorVar(riskLevel)
      : 'var(--dg-tree-guide)';

  // The arrowhead, as a filled triangle pointing along the direction of travel.
  // Drawn by hand rather than with an SVG `marker`: a marker's `fill` cannot
  // inherit a per-instance colour without one marker def per colour, and a
  // response leg travels LEFT, which would need a second, mirrored def as well.
  const headAt = (x: number, dir: -1 | 1, yy: number) =>
    `${x},${yy} ${x - dir * HEAD_SIZE * 2},${yy - HEAD_SIZE} ${x - dir * HEAD_SIZE * 2},${yy + HEAD_SIZE}`;

  return (
    <g
      className={`dg-seq-row${isSelected ? ' dg-seq-row--selected' : ''}`}
      // Keyboard-reachable and announced as a button, matching the a11y discipline
      // the graph's controls and LegTabs follow: an SVG `<g>` is inert by default,
      // so the role/tabIndex/onKeyDown trio is what makes the row operable without
      // a mouse. Enter and Space both activate, as a native button would.
      role="button"
      tabIndex={0}
      aria-label={`Seq ${msg.seq}, ${msg.legType}: ${msg.title}`}
      data-testid="dg-seq-message"
      data-seq={msg.seq}
      data-message-key={msg.key}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
    >
      {/* The interaction's summary plus which leg this is — the hover text, now
          that the visible label is the compact seq number. Same text the graph's
          edge titles carry. */}
      <title>{`#${msg.seq} ${msg.legType} — ${msg.title}`}</title>
      {/* The full-width hit strip. `pointer-events: all` because a transparent
          fill is not hit-testable by default, and `fill="transparent"` rather than
          `fill-opacity: 0` so the selected-row tint (global.css) can override it. */}
      <rect
        className="dg-seq-row-hit"
        x={0}
        y={y - ROW_HEIGHT / 2}
        width={width}
        height={ROW_HEIGHT}
      />
      {msg.isSelfCall ? (
        /* EDGE CASE (self-call): source === target, so a straight horizontal arrow
           has zero length and is invisible. Draw the UML self-message instead — out
           to the right, down a little, and back — so the reader sees a real mark
           and can tell it apart from a call to a neighbour. Not dropped: an agent
           recursing is a governance fact like any other. */
        <>
          <path
            d={`M ${x1} ${y - SELF_LOOP_HEIGHT / 2} H ${x1 + SELF_LOOP_WIDTH} V ${
              y + SELF_LOOP_HEIGHT / 2
            } H ${x1 + HEAD_SIZE * 2}`}
            fill="none"
            stroke={colour}
            strokeWidth={1.5}
            strokeDasharray={msg.legType === 'response' ? '4 3' : undefined}
          />
          {/* Points back LEFT at the lifeline it left from. */}
          <polygon points={headAt(x1, -1, y + SELF_LOOP_HEIGHT / 2)} fill={colour} />
        </>
      ) : (
        <>
          <line
            x1={x1}
            y1={y}
            x2={x2}
            y2={y}
            stroke={colour}
            strokeWidth={1.5}
            // A response leg is dashed, the UML convention for a return message —
            // and a second, non-colour cue for direction that survives both the
            // dark theme and a reader who cannot distinguish the arrowhead's end.
            strokeDasharray={msg.legType === 'response' ? '4 3' : undefined}
          />
          {/* The arrowhead on the DESTINATION end, pointing the way this leg
              travelled: right for a request caller → callee, LEFT for the response
              coming back. Without this the diagram's whole point is unreadable. */}
          <polygon points={headAt(x2, x2 > x1 ? 1 : -1, y)} fill={colour} />
        </>
      )}
      {/* The visible `seq`, sitting just above the arrow's midpoint (or just right
          of a self-loop). Muted via CSS for the same reason the graph's edge tags
          are: the ordinal is a reference a reader looks up, not the content. */}
      <text
        className={`dg-seq-tag${msg.isError ? ' dg-seq-tag--error' : ''}`}
        x={msg.isSelfCall ? x1 + SELF_LOOP_WIDTH + 6 : (x1 + x2) / 2}
        y={y - 6}
        textAnchor={msg.isSelfCall ? 'start' : 'middle'}
      >
        {msg.label}
      </text>
    </g>
  );
}

/**
 * The Interaction diagram: a UML-style **sequence diagram** of a trace's
 * **Interaction legs** — entities as vertical lifelines across the top, one
 * horizontal arrow per leg, ordered top-to-bottom by the trace-wide leg `seq`.
 *
 * The fourth presentation of the same two reads the Interaction flow tables hold,
 * and specifically the FLAT list's own rows: the arrow set comes from
 * `lib/sequenceDiagram`, which consumes `flow.flatLegRows`, so the Nth arrow down
 * this page is the Nth row of the Flat tab by construction. The Flat table answers
 * "what happened, in order" as text; this answers the same question as a picture
 * whose horizontal axis is *who*.
 *
 * HAND-ROLLED SVG, no new dependency and specifically NOT react-topology: that
 * package is a ~388kB lazy chunk owned by the Execution Flow tab, and its value is
 * force/dagre LAYOUT — the one thing a sequence diagram must not have, since its
 * geometry is dictated entirely by the column and row assignment
 * `deriveSequenceDiagram` already computed. Rendering it here is a few dozen lines
 * of `<line>` and `<polygon>`; routing it through a topology model would be more
 * code, more bytes, and a fight with a layout engine over placement.
 *
 * All derivation — including every edge case — lives in
 * `lib/sequenceDiagram.deriveSequenceDiagram` so it is testable without laying out
 * an SVG (jsdom cannot measure one). This component places rectangles and paths.
 *
 * Empty state and disclosure `Alert`s deliberately mirror `ExecutionFlowGraph`'s
 * (same components, same "may still be draining" tone, same
 * disclose-rather-than-drop rule), because these are two tabs over one dataset and
 * they must not disagree about what "nothing here" looks like. Loading and read
 * errors are not restated here: this component is fed the rows its parent
 * (`FlowTables`) has ALREADY loaded and gated on, so a second spinner would be a
 * spinner over data that is present. `ExecutionFlowGraph` owns its own reads (it
 * is lazily mounted and holds no props but the trace id), which is why the split
 * falls here.
 */
/**
 * Props `InteractionDiagram` was given before issue #170, restated as a named
 * type so {@link InteractionDiagramProps.riskLevelByInteraction}'s doc comment
 * has somewhere to point back to `entities`/`interactions`/`selectedId`/
 * `onSelect` without repeating each of their own comments.
 */
export interface InteractionDiagramProps {
  entities: readonly Entity[];
  interactions: readonly Interaction[];
  /** The selected INTERACTION's id — legs have no selection of their own. */
  selectedId: string | null;
  /**
   * Fired with the clicked message's parent INTERACTION, the same callback
   * contract `FlatLegsTable` uses: a click anywhere on a leg opens that
   * interaction's detail panel.
   */
  onSelect: (ix: Interaction) => void;
  /**
   * Interaction id -> risk level (issue #170's Alert Execution view), from
   * `lib/riskForestAdapter.ts`'s `riskLevelByInteraction`. Omitted by the Flow
   * tables' own mount of this component — a colour the risk trace-detail view
   * needs, not something the ordinary Interactions tab renders — which is what
   * keeps that tab's arrow colours byte-for-byte unchanged: the risk-colour
   * branch in `MessageRow` is reached only when this is present.
   *
   * An interaction ABSENT from a supplied map (its `risk` was `null` — not yet
   * computed) gets no arrow treatment at all, same as an absent map entirely;
   * only an explicit entry paints a colour, so "not yet computed" is never
   * drawn as a colour of its own (see `riskLevelByInteraction`'s docstring).
   *
   * Lifeline HEAD boxes keep `kindColorVar` regardless — this seam only
   * touches the message arrows, mirroring the mockup's kind-coloured
   * participants over risk-coloured messages.
   */
  riskLevelByInteraction?: ReadonlyMap<string, string>;
}

export function InteractionDiagram({
  entities,
  interactions,
  selectedId,
  onSelect,
  riskLevelByInteraction,
}: InteractionDiagramProps) {
  const spec = useMemo(
    () => deriveSequenceDiagram(entities, interactions),
    [entities, interactions],
  );
  const ixById = useMemo(() => {
    const m = new Map<string, Interaction>();
    interactions.forEach((ix) => m.set(ix.id, ix));
    return m;
  }, [interactions]);

  // Geometry from the COUNTS alone (see the metrics block above). The viewBox is
  // the natural size; `.dg-seq-scroll` scrolls rather than clips when a wide trace
  // overflows the container, so a 12-participant diagram stays readable at 1:1
  // instead of being shrunk to illegibility by a `width: 100%` SVG.
  const width = Math.max(
    MARGIN_X * 2 + HEAD_WIDTH,
    MARGIN_X * 2 + HEAD_WIDTH + (spec.lifelines.length - 1) * COL_WIDTH,
    // A self-call in the rightmost column loops out beyond its own lifeline.
    MARGIN_X * 2 + HEAD_WIDTH + (spec.lifelines.length - 1) * COL_WIDTH + SELF_LOOP_WIDTH,
  );
  const height =
    spec.messages.length === 0
      ? FIRST_ROW_Y + BOTTOM_PAD
      : rowY(spec.messages.length - 1) + BOTTOM_PAD;

  // No lifeline means no participant any message touches — nothing to draw. Note
  // this is NOT the same as "no entities": a trace can have entities that are all
  // isolated, or interactions that are all dropped, and both land here with a
  // notice attached below rather than a bare box.
  if (spec.lifelines.length === 0) {
    return (
      <div data-testid="interaction-diagram">
        <Disclosures spec={spec} />
        <EmptyState>
          <EmptyStateHeader titleText="No interaction diagram" headingLevel="h4" />
          <EmptyStateBody>
            {'No interaction has two resolved participants for this trace yet, so there are no lifelines to draw. The interactions processor derives them from the spans table as spans arrive; this trace may still be draining.'}
          </EmptyStateBody>
        </EmptyState>
      </div>
    );
  }

  return (
    <div data-testid="interaction-diagram">
      <Disclosures spec={spec} />
      <div className="dg-seq-scroll">
        <svg
          className="dg-seq-svg"
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`Interaction sequence diagram: ${spec.lifelines.length} participant${
            spec.lifelines.length === 1 ? '' : 's'
          }, ${spec.messages.length} message${spec.messages.length === 1 ? '' : 's'}`}
        >
          {spec.lifelines.map((ll) => {
            const x = colX(ll.index);
            const colour = kindColorVar(ll.kind);
            return (
              <g key={ll.id} data-testid="dg-seq-lifeline" data-entity-id={ll.id}>
                {/* kind — natural key, the same hover text the graph's nodes carry
                    (KindColouredNode), so the two tabs identify an entity
                    identically. */}
                <title>{`${ll.kind} — ${ll.naturalKey}`}</title>
                {/* The vertical dashed lifeline, dropping the full height of the
                    message area. Behind the head box in document order so the box
                    covers its top end. */}
                <line
                  className="dg-seq-lifeline"
                  x1={x}
                  y1={HEAD_TOP + HEAD_HEIGHT}
                  x2={x}
                  y2={height - BOTTOM_PAD / 2}
                  stroke={colour}
                />
                {/* The head box, tinted by kind through lib/entityKind — the SINGLE
                    source of the kind→colour decision, shared with the entity pills
                    and the graph's nodes, so a lifeline and its table row can never
                    disagree about what colour an `agent` is. The fill is the same
                    reference at low opacity, so the box reads as tinted rather than
                    saturated in the dark theme. */}
                <rect
                  className="dg-seq-head"
                  x={x - HEAD_WIDTH / 2}
                  y={HEAD_TOP}
                  width={HEAD_WIDTH}
                  height={HEAD_HEIGHT}
                  rx={4}
                  stroke={colour}
                  fill={colour}
                />
                <text
                  className="dg-seq-head-label"
                  x={x}
                  y={HEAD_TOP + HEAD_HEIGHT / 2 + 4}
                  textAnchor="middle"
                >
                  {/* Truncated in code, not by CSS: SVG `<text>` has no
                      text-overflow, and a long display_name would otherwise run
                      straight through the neighbouring box. */}
                  {ll.label.length > 22 ? `${ll.label.slice(0, 21)}…` : ll.label}
                </text>
              </g>
            );
          })}
          {spec.messages.map((msg, row) => (
            <MessageRow
              key={msg.key}
              msg={msg}
              row={row}
              // Highlights ALL of the selected interaction's messages — both its
              // legs — because the selection is the INTERACTION, and lighting only
              // the clicked leg would imply legs are separately selectable.
              isSelected={selectedId === msg.interactionId}
              width={width}
              onSelect={() => {
                const ix = ixById.get(msg.interactionId);
                if (ix) onSelect(ix);
              }}
              riskLevel={riskLevelByInteraction?.get(msg.interactionId)}
            />
          ))}
        </svg>
      </div>
    </div>
  );
}

/**
 * The disclosure notices, extracted so the empty state and the drawn diagram show
 * the SAME ones: a trace whose every interaction was dropped has no lifelines, and
 * that is precisely when the reader most needs to be told why.
 */
function Disclosures({ spec }: { spec: ReturnType<typeof deriveSequenceDiagram> }) {
  return (
    <>
      {/* EDGE CASE, disclosed in the UI rather than only in a comment: an
          interaction whose caller or callee could not be resolved to an entity has
          no lifeline at one end, so neither of its legs can be an arrow. Counted
          once per INTERACTION (the unresolved participant is one defect on the
          identity row, shared by both legs — see lib/sequenceDiagram's
          DroppedInteraction), with the lost leg count spelled out separately so the
          arrow arithmetic still adds up for a reader comparing this to the Flat
          tab. Same wording as the Execution Flow tab's notice, on purpose. */}
      {spec.dropped.length > 0 && (
        <Alert
          variant="info"
          isInline
          title={`${spec.dropped.length} interaction${spec.dropped.length === 1 ? '' : 's'} not shown as messages`}
          style={{ marginBottom: '0.5rem' }}
        >
          {`These interactions have an unresolved participant (no caller and/or callee entity), so they have no second lifeline to draw an arrow to: ${spec.dropped
            .map(
              (d) =>
                `${d.label} (missing ${d.missing}, ${d.legCount} leg${d.legCount === 1 ? '' : 's'})`,
            )
            .join('; ')}. They are still listed in full on the Flat tab.`}
        </Alert>
      )}
      {/* EDGE CASE: entities that no interaction names. Unlike the Execution Flow
          graph, which draws them as bare dashed NODES, this view gives them no
          lifeline at all — a lifeline exists to be the thing arrows land on, and an
          untouched one would add a column and assert a participation that did not
          happen (see lib/sequenceDiagram's `isolated`). Omitted, therefore, but
          never silently: the count and names are stated here, and the Entities
          table above remains the complete participant list. */}
      {spec.isolated.length > 0 && (
        <Alert
          variant="info"
          isInline
          title={`${spec.isolated.length} entit${spec.isolated.length === 1 ? 'y' : 'ies'} without a lifeline`}
          style={{ marginBottom: '0.5rem' }}
        >
          {`No interaction names ${spec.isolated.length === 1 ? 'this entity' : 'these entities'} as caller or callee, so no message touches ${
            spec.isolated.length === 1 ? 'it' : 'them'
          } and ${spec.isolated.length === 1 ? 'it has' : 'they have'} no lifeline here: ${spec.isolated
            .map((e) => e.label)
            .join(', ')}. They are listed in full in the Entities table above, and the Execution Flow tab draws them as unconnected nodes.`}
        </Alert>
      )}
    </>
  );
}

export default InteractionDiagram;
