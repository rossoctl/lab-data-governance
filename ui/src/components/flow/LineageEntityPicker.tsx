import { useId, useState } from 'react';
import { MenuToggle, Select, SelectList, SelectOption } from '@patternfly/react-core';

import type { Entity } from '../../types';

/**
 * The Lineage view's **entity** picker: which entity the reachability walk is seeded
 * from — the other half of `fanin(entity, source)` / `fanout(entity, source)`.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY THIS CONTROL EXISTS: IT IS THE KEYBOARD ROUTE, AND FOR A WHILE THERE WAS NONE.
 *
 * The seed entity used to be picked either by clicking a graph NODE or by clicking a row
 * in the flow view's **Entities table**. When that table was scoped to the Tree|Flat
 * presentations, the node click became the ONLY way to select one on this view — and an
 * SVG circle is not tabbable, so a keyboard or screen-reader user could no longer ask
 * this view's question at all. `?eid` in the URL was the sole remaining route, which is
 * not a control.
 *
 * That is the gap this closes. It is emphatically NOT a second notion of "the selected
 * entity": it writes through the same `onChange` → `selectEntity` → `?eid` path the node
 * click and the table row always used, so the picker, the lit node and the detail panel
 * cannot disagree about what the reader picked.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY IT SITS ABOVE THE SOURCE PICKER.
 *
 * The two controls assemble one question, and this is the half a reader thinks of first:
 * "what happened to THIS entity's data" is the question, "traced from which origin" is
 * the qualifier. Ordering entity-then-source matches how the question reads aloud, and
 * matches the walk's own signature (`fanin(entity, source)` — entity first).
 *
 * Note the two are still independent: neither is a default the UI may pick on the
 * reader's behalf, and the view asks for whichever is missing rather than guessing.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY A PF `Select`, matching {@link LineageSourcePicker}.
 *
 * Same reasoning, same trade-offs, deliberately the same component so the two halves of
 * one question do not look like two unrelated widgets: fixed height regardless of how
 * many entities the trace holds (a 25-interaction trace has dozens, which would wrap a
 * `ToggleGroup` into several rows and push the picture off screen), and a real
 * `MenuToggle` that is focusable, arrow-key navigable and announced — which is the whole
 * point here.
 *
 * The options are labelled by `display_name` with the entity's KIND as the description,
 * because `display_name` alone is not unique (two agents can both own a `create_booking`)
 * and a governance surface must not render two distinct entities as one indistinguishable
 * row. That mirrors the source picker's use of the qualified natural key as its option
 * description, for the same reason.
 */
export function LineageEntityPicker({
  entities,
  selectedEntityId,
  onChange,
}: {
  /**
   * The trace's entities, already derived and already held by the caller — the same
   * array the graph's nodes come from, so the picker cannot offer an entity the reader
   * would not see on screen.
   */
  entities: readonly Entity[];
  /** The `?eid` selection, or `null` for "none chosen yet". THE one notion of it. */
  selectedEntityId: string | null;
  /** Fired with the chosen entity's id, routed to the same `selectEntity` a node click calls. */
  onChange: (entityId: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  // Generated, not a hardcoded literal: a second instance would otherwise emit a
  // duplicate id and every `aria-labelledby` would resolve to whichever came first in
  // the document, silently mislabelling one of them. Same fix the source picker carries.
  const labelId = useId();

  // NO ENTITIES: render nothing rather than an empty dropdown. A control over an empty
  // list looks broken, and the STATEMENT of an empty trace is not this component's to
  // make — the graph's own read states (`graphReadState`'s empty state) already own it,
  // and a second wording of one fact is the drift that reads as two problems.
  if (entities.length === 0) return null;

  const selected = entities.find((e) => e.id === selectedEntityId) ?? null;

  return (
    // The same layout class as the source picker, so the two controls line up as one
    // pair rather than two independently-styled rows.
    <div className="dg-lineage-source-picker">
      {/* A VISIBLE label, not only an accessible name: the selected entity is the
          subject of every highlight on the graph, so a reader looking at a lit picture
          has to be able to see which entity it is about. */}
      <span className="dg-lineage-source-picker-label" id={labelId}>
        Selected entity
      </span>
      <Select
        isOpen={isOpen}
        selected={selectedEntityId}
        onOpenChange={setIsOpen}
        onSelect={(_e, value) => {
          setIsOpen(false);
          // `SelectOption`'s `value` is `any` in PF's types; every option below is given
          // an entity id, so this is a narrowing of our own data rather than a guess.
          // Guarded anyway so a future non-string option cannot send "[object Object]"
          // down the selection path.
          if (typeof value === 'string') onChange(value);
        }}
        toggle={(toggleRef) => (
          <MenuToggle
            ref={toggleRef}
            onClick={() => setIsOpen((o) => !o)}
            isExpanded={isOpen}
            aria-labelledby={labelId}
            // The natural key on the toggle, since once the menu is closed this is the
            // only place the choice is stated and a `display_name` can name two
            // entities. `MenuToggle` does forward `title` to the DOM (`SelectOption`
            // does not — see the source picker's note).
            title={selected?.natural_key ?? undefined}
            className="dg-lineage-source-toggle"
          >
            {selected === null ? 'Choose an entity…' : selected.display_name}
          </MenuToggle>
        )}
      >
        <SelectList>
          {entities.map((e) => (
            <SelectOption
              key={e.id}
              value={e.id}
              isSelected={e.id === selectedEntityId}
              // The KIND as the description, so two same-named entities are visibly two
              // options rather than one apparently-duplicated row.
              description={e.kind}
            >
              {e.display_name}
            </SelectOption>
          ))}
        </SelectList>
      </Select>
    </div>
  );
}
