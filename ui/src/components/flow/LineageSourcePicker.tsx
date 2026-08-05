import { useId, useState } from 'react';
import {
  Alert,
  MenuToggle,
  Select,
  SelectList,
  SelectOption,
} from '@patternfly/react-core';

import { lineageLabel } from '../../lib/lineageLabels';
import type { SourceChoice } from '../../lib/lineageReachability';

/**
 * The Lineage tab's **data source** picker: which ONE of the trace's derived data
 * sources the reachability walk is tracing.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY THIS CONTROL EXISTS AT ALL.
 *
 * The reachability read is `fanin(entity, source)` / `fanout(entity, source)`
 * (`docs/data_lineage_alg.md`'s `## API`): an edge is traversed only when the chosen
 * source appears in that leg's lineage `data_sources`. `source` is REQUIRED — omitting
 * it is a 400, not a broader answer — so the tab cannot ask its question until the
 * reader has named a source. There is no default the UI is entitled to pick (see
 * `resolveSourceChoice`'s note on why auto-selecting the first source would be an
 * unrequested claim), so the choice is a control.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * EXACTLY ONE SOURCE AT A TIME. DO NOT MAKE THIS A MULTI-SELECT.
 *
 * `docs/data_lineage_alg.md`'s `## deferred issues` leaves multi-source semantics
 * explicitly open: "Given multiple sources - semantics are not clear: Do we expect the
 * exact set of sources? Any of them?". A multi-select would force this component to
 * decide between a UNION of per-source walks and an INTERSECTION of them, and then
 * present its own choice as a served answer. Those differ on real traces and a reader
 * could not tell which they were shown. So this is a single-select, and the constraint
 * is upstream rather than a UI simplification — it must not be "improved" into a union.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * WHY A PF `Select` RATHER THAN A `ToggleGroup` OR A NODE CLICK.
 *
 * - A `ToggleGroup` (one button per source) was the first idea and reads beautifully
 *   for two or three sources. It does not survive the real data: sources are qualified
 *   natural keys, one per origin the trace attributed content to, and a live 25-
 *   interaction trace already rolls up enough of them to wrap the toolbar into several
 *   rows above the graph — pushing the picture itself off screen, which is the one
 *   thing this tab cannot afford. A `Select` is fixed-height regardless of N.
 * - "Click the source node on the graph" was rejected for two reasons. Node clicks are
 *   already spoken for — they select the seed ENTITY (`onSelectEntity`), which is the
 *   other half of the question — so overloading them would make one gesture mean two
 *   things depending on invisible mode. And a source whose natural key resolves to no
 *   node in this trace (an origin outside the traced set, which `deriveSourceHighlight`
 *   discloses) would then be UNCHOOSABLE, silently excluding exactly the sources a
 *   governance reader is most likely to be asking about.
 * - The toggle shows the chosen source's LABEL, not a count and not a bare "1
 *   selected": this is the subject of every highlight on screen, so it is named in
 *   full. See {@link LineageSourcePickerProps.chosen} on why the qualified key is a
 *   tooltip rather than the label.
 */
export interface LineageSourcePickerProps {
  /**
   * The reconciled choice from `resolveSourceChoice` — the four states this control
   * has to render, already decided in a pure module rather than re-derived here.
   */
  chosen: SourceChoice;
  /**
   * `natural_key → display_name` for the trace's entities (`displayNamesByKey`).
   *
   * The label shown is the friendly `display_name` and the QUALIFIED natural key is
   * the `title`, which is `lineageLabel`'s contract followed rather than re-decided:
   * two agents' tools can share a `display_name`, so the friendly label alone can name
   * two different sources identically. On a governance surface that is not acceptable,
   * so the disambiguating key stays reachable on every option and on the toggle.
   */
  namesByKey: Map<string, string>;
  /** Fired with the newly chosen source natural key. Never with `null` — see below. */
  onChange: (source: string) => void;
  /**
   * Whether to render this control's own two ALERTS ("choose a source" / "that source is
   * stale") beneath the dropdown.
   *
   * The split exists because the graph view now puts CONTROLS above the picture and all
   * informational text BELOW it. This component owns both, so a caller that wants the
   * control in one place and its prose in another renders it twice: once with
   * `showNotices={false}` in the control strip, once with `notices` only in the text
   * block ({@link LineageSourceNotices}).
   *
   * Defaults to `true`, so a caller that wants the original one-piece control-plus-prose
   * arrangement gets it unchanged.
   */
  showNotices?: boolean;
}

/**
 * The picker plus the "nothing chosen yet" / "no sources" instruction beside it.
 *
 * NO "CLEAR" / "ALL SOURCES" OPTION, deliberately. `'unchosen'` is reachable on first
 * open and by a stale URL, and it is rendered as an instruction — but it is not
 * offered as a *destination*, because an option that reads "all sources" would be the
 * multi-source union this whole design refuses, and one that reads "none" would be a
 * button whose only effect is to remove the answer the reader came for.
 */
export function LineageSourcePicker({
  chosen,
  namesByKey,
  onChange,
  showNotices = true,
}: LineageSourcePickerProps) {
  const [isOpen, setIsOpen] = useState(false);
  // A generated id, not the literal `dg-lineage-source-label` this used to hardcode.
  // Only one picker is rendered today, so the literal was correct-by-accident; a second
  // instance would emit a duplicate id and every `aria-labelledby` would resolve to
  // whichever came first in the document, silently mislabelling one of them.
  const labelId = useId();

  // ZERO SOURCES: NOTHING AT ALL FROM THIS COMPONENT. A dropdown over an empty list is
  // a control that looks broken, so the picker is not drawn — and the STATEMENT of the
  // fact is deliberately not made here either. `LineageGraph`'s source roll-up alert
  // already owns it (see its `sources.totalSources === 0` arm, which says both
  // consequences: nothing marked, nothing to trace), and a second alert wording the
  // same fact differently is the drift that would let a reader read two problems into
  // one. One fact, one statement.
  //
  // Note this arm is only reached once the summary has actually LANDED and named
  // nothing — `resolveSourceChoice` returns `'unchosen'` while it is in flight or after
  // it failed — so it can never be what a reader sees during a load or an error. Those
  // two have their own wording, and conflating them here would be the "absence vs
  // unknown" collapse ADR-0028 is disciplined against.
  if (chosen.state === 'no-sources') return null;

  const label = (key: string) => lineageLabel(key, namesByKey);
  const chosenLabel = chosen.source === null ? null : label(chosen.source);

  return (
    <>
      {/* The control and its own label on one line. A visible `<label>`-shaped span
          rather than only an `aria-label`, because the chosen source is the SUBJECT of
          every highlight below it: a reader looking at a lit graph must be able to see
          which source's flow it is, and an accessible name only a screen reader can
          reach does not do that. */}
      <div className="dg-lineage-source-picker">
        <span className="dg-lineage-source-picker-label" id={labelId}>
          Tracing data source
        </span>
        <Select
          isOpen={isOpen}
          // Single-valued, never an array — see this module's header on why multi-source
          // is refused rather than merely unimplemented.
          selected={chosen.source}
          onOpenChange={setIsOpen}
          onSelect={(_e, value) => {
            setIsOpen(false);
            // A `SelectOption`'s `value` is `any` in PF's types; every option below is
            // given a source natural key, so the cast is a narrowing of our own data
            // rather than a guess. Guarded anyway so a future non-string option cannot
            // silently send `"[object Object]"` to the server as a source.
            if (typeof value === 'string') onChange(value);
          }}
          toggle={(toggleRef) => (
            <MenuToggle
              ref={toggleRef}
              onClick={() => setIsOpen((o) => !o)}
              isExpanded={isOpen}
              aria-labelledby={labelId}
              // The qualified key on the toggle too, not only on the options: once the
              // menu is closed this is the only place the choice is stated, and the
              // friendly label alone can name two distinct sources identically.
              title={chosen.source ?? undefined}
              className="dg-lineage-source-toggle"
            >
              {chosenLabel === null ? 'Choose a data source…' : chosenLabel.label}
            </MenuToggle>
          )}
        >
          <SelectList>
            {chosen.available.map((key) => {
              const { label: text, qualified } = label(key);
              return (
                <SelectOption
                  key={key}
                  value={key}
                  isSelected={key === chosen.source}
                  // The qualified natural key as the option's DESCRIPTION when it
                  // differs from the label, so two same-named sources are visibly two
                  // options rather than one apparently-duplicated row — which is
                  // `lineageLabel`'s `qualified` flag doing exactly the job it exists
                  // for.
                  //
                  // A `title` attribute is deliberately NOT set here: PF's
                  // `SelectOption` is a `MenuItem` and does not forward `title` to the
                  // DOM (verified — the attribute is simply absent), so it would be a
                  // prop that reads as a tooltip and is not one. The description is the
                  // disclosure that actually renders. The TOGGLE does carry a real
                  // `title`, because `MenuToggle` does forward it.
                  description={qualified ? key : undefined}
                >
                  {text}
                </SelectOption>
              );
            })}
          </SelectList>
        </Select>
      </div>

      {showNotices && <LineageSourceNotices chosen={chosen} />}
    </>
  );
}

/**
 * This control's two ALERTS, on their own so they can be rendered apart from the
 * dropdown — see {@link LineageSourcePickerProps.showNotices}.
 *
 * They stay in THIS module rather than moving to the graph view, because the wording of
 * "nothing chosen" and "stale choice" belongs with the control whose states they are: a
 * second copy in the caller is exactly the drift that lets a control and its explanation
 * describe different things.
 */
export function LineageSourceNotices({ chosen }: { chosen: SourceChoice }) {
  // Same guard as the picker: with zero sources this component says nothing, because
  // `LineageGraph`'s roll-up alert already owns that fact in one wording.
  if (chosen.state === 'no-sources') return null;
  return (
    <>
      {/* NOTHING CHOSEN YET — an INSTRUCTION, not an empty result, and distinct from
          every state `DirectionNotice` words. The graph is drawn at full strength
          and claims nothing about any source's flow, because no such question has been
          asked. Kept separate from the "select an entity" prompt because they are two
          different missing halves of one question and a reader who has supplied one
          needs to be told which one is still missing. */}
      {chosen.state === 'unchosen' && (
        <Alert
          variant="info"
          isInline
          title="Choose a data source to trace"
          style={{ marginBottom: '0.5rem' }}
        >
          {`This tab traces ONE data source's flow at a time: pick a source above, then select an entity, and the graph highlights where that source's data reached that entity from and where it went. Nothing is highlighted or dimmed until a source is chosen — no question has been asked yet.`}
        </Alert>
      )}

      {/* A STALE CHOICE — a `?src` this trace's roll-up does not contain (a bookmark
          from another trace, a hand-edited URL, or a source that has dropped out of the
          roll-up). Reported rather than silently blanked, for the same reason `?legs`
          coerces instead of throwing: the reader asked for something specific and
          deserves to know it did not restore, rather than wondering why their bookmark
          shows a bare prompt. Worded as "not among THIS trace's sources", never as "no
          such source" — it may well be a real source of a different trace. */}
      {chosen.state === 'stale' && (
        <Alert
          variant="warning"
          isInline
          role="alert"
          title="That data source is not one of this trace’s sources"
          style={{ marginBottom: '0.5rem' }}
        >
          {`The requested source ${chosen.requested ?? ''} is not in this trace’s derived roll-up, so it cannot be traced here — nothing was asked of the server. Pick one of the ${chosen.available.length} source${chosen.available.length === 1 ? '' : 's'} above. A link from a different trace, or a source that has since dropped out of this trace’s lineage, both land here.`}
        </Alert>
      )}
    </>
  );
}
