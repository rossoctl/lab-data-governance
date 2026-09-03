import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithProviders } from '../test/renderWithProviders';
import { InteractionDiagram } from './InteractionDiagram';
import { riskLevelColorVar } from '../lib/riskLevel';
import type { Entity, Interaction, InteractionLeg } from '../types';

/**
 * Render coverage for the Interaction diagram, deliberately scoped to what jsdom
 * can honestly assert.
 *
 * WHAT JSDOM CAN DO HERE: this is hand-rolled SVG whose every coordinate is
 * computed arithmetically from the lifeline/message counts, so unlike the topology
 * graph nothing here depends on `getBBox` and the ELEMENTS all mount — the head
 * boxes, the lifelines, one `<g role="button">` per message, its `seq` text, the
 * error colour and the click/keyboard handlers are all real and asserted below.
 * A couple of geometric consequences are assertable too (a response arrow's x2 <
 * x1), because the numbers are ours rather than a layout engine's.
 *
 * WHAT IT CANNOT: whether any of it LOOKS right — text metrics, whether a label
 * overflows its box, whether the scroll container actually scrolls, whether the
 * dark theme's tokens resolve to a readable contrast. jsdom computes no SVG layout
 * and resolves no `var()`, so those want a Playwright screenshot run against the real
 * browser bundle — which **does not exist yet**: `ui/e2e/` holds only smoke and
 * classification specs, neither of which opens this view. Checked by hand today.
 *
 * So the real coverage lives in `lib/sequenceDiagram.test.ts` — column assignment,
 * per-leg direction, the error tri-state, dropped interactions, isolated entities —
 * and this file asserts that the component faithfully renders that spec and is
 * operable.
 */

const ENTITIES: Entity[] = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
  { id: 'e3', kind: 'llm', natural_key: 'llm:api.example.com/gpt', display_name: 'gpt-4', detected_from: 'span' },
];

/** A leg, defaulting to a successful one. */
function mkLeg(
  legType: 'request' | 'response',
  seq: number,
  error: boolean | null = false,
): InteractionLeg {
  return { leg_type: legType, occurred_at: '2026-05-01T12:00:00Z', payload_hash: null, error, seq };
}

/**
 * A completed interaction: BOTH legs, so it renders two opposite-direction message
 * rows. `seqBase` gives them distinct trace-wide seqs, since the row ORDER and the
 * visible tags are both the seq.
 */
function mkIx(over: Partial<Interaction> & Pick<Interaction, 'id'>, seqBase = 1): Interaction {
  return {
    caller_entity_id: 'e1',
    callee_entity_id: 'e2',
    summary: `summary-${over.id}`,
    parent_interaction_id: null,
    legs: [mkLeg('request', seqBase), mkLeg('response', seqBase + 1)],
    duration_seconds: 1,
    any_error: false,
    span_count: 1,
    anchor_count: 1,
    ...over,
  };
}

const lifelineEls = () => screen.queryAllByTestId('dg-seq-lifeline');
const messageEls = () => screen.queryAllByTestId('dg-seq-message');

/**
 * The `<title>` texts in the document, read directly rather than through
 * `getByTitle`.
 *
 * Testing Library's `getByTitle` only reads an SVG `<title>` when it is a DIRECT
 * child of the `<svg>`. Every title here hangs off a `<g>` (a lifeline group or a
 * message row), which is exactly where it has to be for the browser to show it as
 * the tooltip for THAT shape — a title at the svg root would be one tooltip for the
 * whole diagram. So the query is a plain DOM read.
 */
const titles = () => [...document.querySelectorAll('title')].map((t) => t.textContent);

function renderDiagram(
  entities: Entity[],
  interactions: Interaction[],
  over: {
    selectedId?: string | null;
    onSelect?: (ix: Interaction) => void;
    riskLevelByInteraction?: ReadonlyMap<string, string>;
  } = {},
) {
  return renderWithProviders(
    <InteractionDiagram
      entities={entities}
      interactions={interactions}
      selectedId={over.selectedId ?? null}
      onSelect={over.onSelect ?? (() => {})}
      riskLevelByInteraction={over.riskLevelByInteraction}
    />,
  );
}

describe('InteractionDiagram', () => {
  it('renders one lifeline per participating entity, labelled and titled', () => {
    // Two of the three entities participate; the head box carries the display name
    // and the `kind — naturalKey` title the graph's nodes carry, so the two visual
    // tabs identify an entity identically.
    renderDiagram(ENTITIES, [mkIx({ id: 'i1' })]);

    expect(lifelineEls()).toHaveLength(2);
    expect(lifelineEls().map((el) => el.getAttribute('data-entity-id'))).toEqual(['e1', 'e2']);
    expect(screen.getByText('agent-a')).toBeInTheDocument();
    expect(screen.getByText('search')).toBeInTheDocument();
    expect(titles()).toContain('agent — agent:(p,a)');
    expect(titles()).toContain('tool — tool:(p,svc)');
  });

  it('renders one message row per LEG, in seq order, each tagged with its seq', () => {
    // Two completed interactions → FOUR rows, not two. The rows are top-to-bottom
    // in trace-wide seq order, the same order the Flat tab lists.
    renderDiagram(ENTITIES, [
      mkIx({ id: 'i1', callee_entity_id: 'e2' }, 1),
      mkIx({ id: 'i2', callee_entity_id: 'e3' }, 3),
    ]);

    expect(messageEls()).toHaveLength(4);
    expect(messageEls().map((el) => el.getAttribute('data-message-key'))).toEqual([
      'i1:request',
      'i1:response',
      'i2:request',
      'i2:response',
    ]);
    expect(messageEls().map((el) => el.getAttribute('data-seq'))).toEqual(['1', '2', '3', '4']);
    // The seq is visible on the arrow, not only in the DOM attribute.
    for (const seq of ['1', '2', '3', '4']) {
      expect(screen.getByText(seq)).toBeInTheDocument();
    }
  });

  it('renders each row lower than the last, so the vertical axis really is time', () => {
    // The one geometric claim worth making here: the pitch is ours, not a layout
    // engine's, so it is honestly assertable.
    renderDiagram(ENTITIES, [mkIx({ id: 'i1' }, 1), mkIx({ id: 'i2' }, 3)]);

    const ys = messageEls().map((el) =>
      Number(el.querySelector('line, path')!.getAttribute('y1')),
    );
    for (let i = 1; i < ys.length; i += 1) expect(ys[i]).toBeGreaterThan(ys[i - 1]);
  });

  it('points a response arrow back LEFTWARDS, toward its caller', () => {
    // The substance of the leg model at the RENDERED level: the request runs
    // rightwards from e1's column to e2's, and the response retraces it backwards.
    // Both the line's endpoints and the arrowhead's must flip.
    renderDiagram(ENTITIES, [mkIx({ id: 'i1' })]);

    const [req, resp] = messageEls();
    const x = (el: Element, attr: string) => Number(el.querySelector('line')!.getAttribute(attr));
    expect(x(req, 'x2')).toBeGreaterThan(x(req, 'x1'));
    // Swapped: same two columns, opposite ends.
    expect(x(resp, 'x1')).toBe(x(req, 'x2'));
    expect(x(resp, 'x2')).toBe(x(req, 'x1'));
    // …and both carry an arrowhead, so the direction is visible and not merely
    // implied by which end the line starts at.
    expect(req.querySelector('polygon')).toBeInTheDocument();
    expect(resp.querySelector('polygon')).toBeInTheDocument();
  });

  it('carries the interaction summary as each row\'s hover title', () => {
    renderDiagram(ENTITIES, [mkIx({ id: 'i1', summary: 'agent calls search' })]);

    expect(titles()).toContain('#1 request — agent calls search');
    expect(titles()).toContain('#2 response — agent calls search');
  });

  it('selects the clicked message\'s parent INTERACTION, not the leg', () => {
    // The same `onSelect(ix)` contract FlatLegsTable uses — legs have no selection
    // of their own, so clicking either leg opens the interaction's detail panel.
    const onSelect = vi.fn();
    renderDiagram(ENTITIES, [mkIx({ id: 'i1' })], { onSelect });

    return userEvent.click(messageEls()[1]).then(() => {
      expect(onSelect).toHaveBeenCalledTimes(1);
      expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'i1' });
    });
  });

  it('is operable from the keyboard, since an SVG group is inert by default', async () => {
    // The a11y half of the click contract: each row is a focusable `role="button"`
    // with an accessible name, and Enter activates it. Without the explicit
    // role/tabIndex/onKeyDown trio a `<g>` cannot be reached or fired at all.
    const onSelect = vi.fn();
    renderDiagram(ENTITIES, [mkIx({ id: 'i1' })], { onSelect });

    const row = screen.getByRole('button', { name: /Seq 1, request: summary-i1/i });
    row.focus();
    await userEvent.keyboard('{Enter}');

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({ id: 'i1' });
  });

  it('highlights BOTH legs of the selected interaction', async () => {
    // The selection is the INTERACTION; lighting only the clicked leg would imply
    // legs are separately selectable.
    renderDiagram(ENTITIES, [mkIx({ id: 'i1' }, 1), mkIx({ id: 'i2' }, 3)], {
      selectedId: 'i1',
    });

    const selected = messageEls().filter((el) =>
      el.classList.contains('dg-seq-row--selected'),
    );
    expect(selected.map((el) => el.getAttribute('data-message-key'))).toEqual([
      'i1:request',
      'i1:response',
    ]);
  });

  it('colours only the failed LEG with the error token, never a raw hex', () => {
    // One interaction whose RESPONSE failed. Reddening the request too would report
    // a failure ABOVE where it happened, in a view whose vertical axis is time.
    renderDiagram(ENTITIES, [
      mkIx({
        id: 'i1',
        legs: [mkLeg('request', 1, false), mkLeg('response', 2, true)],
        any_error: true,
      }),
    ]);

    const [req, resp] = messageEls();
    expect(resp.innerHTML).toContain('var(--dg-color-error)');
    expect(resp.innerHTML).not.toMatch(/#[0-9a-f]{6}/i);
    expect(req.innerHTML).not.toContain('var(--dg-color-error)');
    // The tag matches its arrow, so the colour reads as one signal about one leg.
    expect(resp.querySelector('.dg-seq-tag--error')).toBeInTheDocument();
    expect(req.querySelector('.dg-seq-tag--error')).not.toBeInTheDocument();
  });

  it('leaves a null leg error uncoloured — unknown is not a failure', () => {
    renderDiagram(ENTITIES, [
      mkIx({ id: 'i1', legs: [mkLeg('request', 1, null), mkLeg('response', 2, null)] }),
    ]);

    expect(document.querySelector('.dg-seq-tag--error')).not.toBeInTheDocument();
  });

  it('draws a self-call as a visible loop rather than a zero-length line', () => {
    // Source === target, so a straight horizontal arrow would be invisible. It must
    // not be silently dropped: an agent recursing is a governance fact.
    renderDiagram(ENTITIES, [mkIx({ id: 'self', caller_entity_id: 'e1', callee_entity_id: 'e1' })]);

    expect(lifelineEls()).toHaveLength(1); // one participant, not two
    expect(messageEls()).toHaveLength(2); // both legs kept
    for (const row of messageEls()) {
      // A path (the loop), not a line — and it has real extent.
      expect(row.querySelector('line')).not.toBeInTheDocument();
      const d = row.querySelector('path')!.getAttribute('d')!;
      expect(d).toMatch(/^M \d/);
      expect(row.querySelector('polygon')).toBeInTheDocument();
    }
  });

  it('renders exactly one row for an in-flight interaction with only a request leg', () => {
    // No phantom response arrow for a call that has not been answered.
    renderDiagram(ENTITIES, [
      mkIx({ id: 'i1', legs: [mkLeg('request', 1)], duration_seconds: null }),
    ]);

    expect(messageEls()).toHaveLength(1);
    expect(messageEls()[0]).toHaveAttribute('data-message-key', 'i1:request');
  });

  it('labels the SVG for a screen reader with its participant and message counts', () => {
    renderDiagram(ENTITIES, [mkIx({ id: 'i1' })]);

    expect(
      screen.getByRole('img', { name: /2 participants, 2 messages/i }),
    ).toBeInTheDocument();
  });

  it('singularises that label for a one-participant, one-message diagram', () => {
    renderDiagram(ENTITIES, [
      mkIx({
        id: 'i1',
        caller_entity_id: 'e1',
        callee_entity_id: 'e1',
        legs: [mkLeg('request', 1)],
      }),
    ]);

    expect(screen.getByRole('img', { name: /1 participant, 1 message/i })).toBeInTheDocument();
  });

  // --- Empty state and disclosures, mirroring the Execution Flow tab's.

  it('renders an empty state — not a blank box — when nothing is drawable', () => {
    renderDiagram([], []);

    expect(screen.getByText(/No interaction diagram/i)).toBeInTheDocument();
    expect(screen.getByText(/no lifelines to draw/i)).toBeInTheDocument();
    // The same "may still be draining" tone the sibling tabs use.
    expect(screen.getByText(/may still be draining/i)).toBeInTheDocument();
    expect(document.querySelector('.dg-seq-svg')).not.toBeInTheDocument();
  });

  it('discloses an interaction with an unresolved participant instead of dropping it silently', () => {
    renderDiagram(ENTITIES, [
      mkIx({ id: 'good' }, 1),
      mkIx({ id: 'orphan', callee_entity_id: null, summary: 'calls the unknown' }, 3),
    ]);

    expect(screen.getByText(/1 interaction not shown as messages/i)).toBeInTheDocument();
    // Counted ONCE for the interaction even though BOTH its legs were lost, with
    // the leg count spelled out so the arrow arithmetic still adds up.
    expect(screen.getByText(/calls the unknown \(missing callee, 2 legs\)/i)).toBeInTheDocument();
    // …and both legs of the drawable one are still drawn.
    expect(messageEls()).toHaveLength(2);
  });

  it('still shows the disclosures when the drop is why there is nothing to draw', () => {
    // The empty state and the drawn diagram share one `Disclosures` block precisely
    // for this case: a reader staring at "no lifelines" most needs to be told why.
    renderDiagram(ENTITIES, [mkIx({ id: 'orphan', callee_entity_id: null })]);

    expect(screen.getByText(/No interaction diagram/i)).toBeInTheDocument();
    expect(screen.getByText(/1 interaction not shown as messages/i)).toBeInTheDocument();
  });

  it('discloses an entity that gets no lifeline because no interaction names it', () => {
    // The deliberate divergence from the graph, which draws such an entity as a
    // bare dashed node: a lifeline no arrow touches would add a column and assert a
    // participation that did not happen. Omitted — and said so.
    renderDiagram(ENTITIES, [mkIx({ id: 'i1', caller_entity_id: 'e1', callee_entity_id: 'e2' })]);

    expect(screen.getByText(/1 entity without a lifeline/i)).toBeInTheDocument();
    expect(screen.getByText(/gpt-4/)).toBeInTheDocument();
    expect(lifelineEls()).toHaveLength(2);
  });

  it('pluralises and names several entities without a lifeline', () => {
    renderDiagram(ENTITIES, []);

    expect(screen.getByText(/3 entities without a lifeline/i)).toBeInTheDocument();
  });

  it('says nothing about drops or missing lifelines when there are none', () => {
    renderDiagram([ENTITIES[0], ENTITIES[1]], [mkIx({ id: 'i1' })]);

    expect(screen.queryByText(/not shown as messages/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/without a lifeline/i)).not.toBeInTheDocument();
  });

  // --- Risk colouring (issue #170's Alert Execution view). The Flow tables'
  // own mount of this component (above) never passes `riskLevelByInteraction`,
  // so every case above this point already pins that the arrow colours are
  // unaffected by this seam existing at all.

  describe('risk colouring (issue #170)', () => {
    it("colours BOTH legs of an interaction by ITS OWN risk level, calling riskLevelColorVar rather than a hardcoded value", () => {
      // "Same colour scheme as RiskBadge" (AC #2) is only a real assertion if
      // the test calls the same function production code does.
      renderDiagram(ENTITIES, [mkIx({ id: 'i1' })], {
        riskLevelByInteraction: new Map([['i1', 'critical']]),
      });

      const [req, resp] = messageEls();
      expect(req.innerHTML).toContain(riskLevelColorVar('critical'));
      expect(req.innerHTML).not.toMatch(/#[0-9a-f]{6}/i);
      expect(resp.innerHTML).toContain(riskLevelColorVar('critical'));
    });

    it('gives a low-risk interaction a visibly different colour than a critical one', () => {
      renderDiagram(ENTITIES, [mkIx({ id: 'i1' })], {
        riskLevelByInteraction: new Map([['i1', 'low']]),
      });

      const [req] = messageEls();
      expect(req.innerHTML).toContain(riskLevelColorVar('low'));
      expect(req.innerHTML).not.toContain(riskLevelColorVar('critical'));
    });

    it("treats an interaction ABSENT from a SUPPLIED map as no treatment at all, never as a safe verdict", () => {
      // `riskForestAdapter.riskLevelByInteraction` omits `risk: null` entries
      // (not yet computed), and this component's contract collapses that into
      // the SAME outcome as no map at all — the ordinary tree-guide colour —
      // rather than a distinct 'unknown' grey or, worse, a green 'safe' one.
      renderDiagram(ENTITIES, [mkIx({ id: 'i1' })], { riskLevelByInteraction: new Map() });

      const [req] = messageEls();
      expect(req.innerHTML).toContain('var(--dg-tree-guide)');
      expect(req.innerHTML).not.toContain(riskLevelColorVar('unknown'));
      expect(req.innerHTML).not.toContain(riskLevelColorVar('low'));
    });

    it('keeps ERROR precedence over risk colouring — a fact outranks a grade', () => {
      renderDiagram(
        ENTITIES,
        [
          mkIx({
            id: 'i1',
            legs: [mkLeg('request', 1, true), mkLeg('response', 2, false)],
            any_error: true,
          }),
        ],
        { riskLevelByInteraction: new Map([['i1', 'critical']]) },
      );

      const [req, resp] = messageEls();
      expect(req.innerHTML).toContain('var(--dg-color-error)');
      expect(req.innerHTML).not.toContain(riskLevelColorVar('critical'));
      // The sibling leg did not fail, so it takes the risk colour, not the
      // error one — the same per-LEG (not per-interaction) precedence rule the
      // pre-existing error test above pins.
      expect(resp.innerHTML).toContain(riskLevelColorVar('critical'));
    });

    it('keeps lifeline head boxes kind-coloured regardless of risk — the seam only touches arrows', () => {
      renderDiagram(ENTITIES, [mkIx({ id: 'i1' })], {
        riskLevelByInteraction: new Map([['i1', 'critical']]),
      });

      const head = document.querySelector('[data-testid="dg-seq-lifeline"] .dg-seq-head')!;
      // Unaffected: still the entity-kind variable, not a risk one.
      expect(head.getAttribute('stroke')).not.toBe(riskLevelColorVar('critical'));
    });

    it('reproduces exactly the pre-#170 colouring when no risk map is passed — a pure regression guard', () => {
      renderDiagram(ENTITIES, [mkIx({ id: 'i1' })]);

      const [req] = messageEls();
      expect(req.innerHTML).toContain('var(--dg-tree-guide)');
      expect(req.innerHTML).not.toContain(riskLevelColorVar('critical'));
    });
  });
});
