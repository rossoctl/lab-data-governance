import { useParams } from 'react-router-dom';
import { Label, LabelGroup, Title } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { useEntities } from '../api/hooks';
import { displayNamesByKey, lineageLabel } from '../lib/lineageLabels';
import type { LineageState } from '../types';

/**
 * The **Data lineage metadata** for one **Interaction leg**'s payload
 * (ADR-0028), rendered inside the flow view's expanded payload beside the
 * **Classification** verdict (issue #119). Read-only surface over
 * `GET /api/traces/{tid}/data-lineage`.
 *
 * Answers the two lineage questions in the order a governance reader asks them:
 * *where did this data come from* (the origins, each with the transformations
 * applied to its contribution) and *which entities did it pass through* (the
 * unordered entity set). Shape deliberately mirrors `ClassificationView` — same
 * absence handling, same "an empty result is a real result, say so" rule — so the
 * two blocks in the same payload read as one surface.
 *
 * Takes a {@link LineageState}, not a nullable triple: *derived*, *not yet
 * derived*, and *the read failed* are three separate facts a reader acts on
 * differently, and a nullable triple can only spell two of them.
 *
 * **Names are resolved here, not passed in.** Lineage stores an entity's
 * *natural key* (ADR-0028) — the identity, qualified so two same-named tools on
 * different agents cannot collapse into one source — but a reader recognises
 * `search_destinations`, not `tool:agent:(travel_advisor,travel-advisor):search_destinations`.
 * The friendly `display_name` lives on the entity rows, which this component was
 * the last surface in the flow view not to see. It reads them off the
 * trace-scoped {@link useEntities} query rather than taking them as a prop:
 * that query is already fetched and cached by the flow view around it, so this is
 * a cache hit, and prop-drilling the entity list through the panel and the leg
 * tabs would thread a presentational detail through three components that have no
 * other use for it. The `traceId` comes from the route (`/traces/:traceId/:view`),
 * which is already the source of truth for which trace is on screen.
 *
 * The consequence is that this component now needs the app's Router and
 * QueryClient in scope — hence `renderWithProviders` in its tests.
 */
export function DataLineageView({ state }: { state: LineageState }) {
  // Read unconditionally, before the early returns: hooks cannot sit behind a
  // branch, and the query is shared cache either way. `traceId` is '' only if
  // this ever renders outside the trace route, which yields an empty entity set
  // and therefore the natural-key fallback — degraded labels, never a crash.
  const { traceId = '' } = useParams<{ traceId: string }>();
  const { data: entities } = useEntities(traceId);
  // Entities load asynchronously and may not name every key the lineage cites,
  // so this map is expected to miss; `lineageLabel` falls back to the key.
  const namesByKey = displayNamesByKey(entities);

  // The read itself failed, so nothing at all is known about this leg — not the
  // triple, and not whether one exists. Kept visually and semantically apart
  // from the "not yet computed" window below, because the two prompt opposite
  // actions: an error is retried, a pending derivation is waited out. Same red
  // terse treatment (and wording shape) as the sibling payload-fetch failure in
  // `LegTabs`, so one convention covers both failures in this panel.
  if (state.kind === 'error') {
    return (
      <div role="alert" style={{ color: 'var(--dg-color-error)', fontSize: '0.85rem' }}>
        Failed to load lineage.
      </div>
    );
  }

  // The eventual-consistency window (ADR-0028): the leg exists but
  // P-data-lineage has not derived its metadata yet. Rendered as a distinct,
  // claim-less state — deliberately NOT an empty sources list, which would read
  // as the real "originates here, no upstream sources" verdict below. A
  // governance tool must never let "we don't know yet" look like "we checked".
  if (state.kind === 'pending') {
    return (
      <div style={{ color: 'var(--dg-color-muted)', fontStyle: 'italic', fontSize: '0.85rem' }}>
        Lineage not yet computed
      </div>
    );
  }

  // Renamed on destructure: `entities` above is the trace's Entity *rows* (the
  // name source), while this is the lineage triple's set of natural KEYS. Two
  // different things, so they do not share an identifier.
  // `source_transformations` is deliberately NOT destructured: the per-source
  // transformation sets used to render as this table's second ("Applied") column and
  // that column was removed by request. The field is still in the API response and
  // still derived — this view simply no longer presents it.
  const { data_sources, entities: lineageEntities } = state.lineage;

  return (
    <div>
      {data_sources.length === 0 ? (
        // A genuinely empty triple is a REAL derived value (ADR-0028 D3: the
        // payload originates at this entity), not the null state — so it is
        // stated in words rather than rendered as nothing.
        <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.85rem' }}>
          No upstream data sources — this payload originates here.
        </div>
      ) : (
        <>
          <Title headingLevel="h5" size="md">
            Data sources
          </Title>
          <SourcesTable dataSources={data_sources} namesByKey={namesByKey} />
        </>
      )}

      <Title headingLevel="h5" size="md" style={{ marginTop: '0.5rem' }}>
        Entities traversed
      </Title>
      <Entities entities={lineageEntities} namesByKey={namesByKey} />
    </div>
  );
}

/**
 * The origins, one row each.
 *
 * ONE COLUMN. This used to carry a second, "Applied", listing the transformations
 * applied to *that* source's contribution (`data_source → set<transformation>`,
 * ADR-0028); it was removed by request. The `source_transformations` map is still
 * derived and still returned by the API — it is simply not presented here, so a reader
 * who needs it reads the response. Removing the column is therefore a presentation
 * change, not a narrowing of what the trace claims.
 *
 * The row set is driven off `data_sources`: it is the authority on which origins exist,
 * so the list cannot silently drop one. (That was true when the map existed too — a
 * source legitimately carried no map entry — and remains the reason the array, not a
 * key set, is iterated.)
 *
 * The Source column shows the entity's friendly name; the *natural key* remains the
 * React key, because `display_name` is not unique (two agents can both own a
 * `create_booking`) and two distinct origins must not collapse into one row.
 */
function SourcesTable({
  dataSources,
  namesByKey,
}: {
  dataSources: string[];
  namesByKey: Map<string, string>;
}) {
  return (
    <Table aria-label="Data sources" variant="compact">
      <Thead>
        <Tr>
          <Th>Source</Th>
        </Tr>
      </Thead>
      <Tbody>
        {dataSources.map((source) => {
          const { label, qualified } = lineageLabel(source, namesByKey);
          return (
            <Tr key={source}>
              {/* `dg-lineage-key` grants the long-unbreakable-natural-key wrap
                  (global.css) — without it a long key pushes this table past the
                  narrow detail panel. It is kept even for a resolved short label,
                  because the fallback path renders a full key into this same cell. */}
              <Td
                dataLabel="Source"
                className="dg-mono dg-lineage-key"
                // The full natural key stays reachable whenever the visible text
                // is the shortened name: two sources can share a display_name,
                // and a governance surface must not make two distinct origins
                // look identical. Native `title`, matching EntityPill / SpanTree
                // rather than introducing a PF Tooltip convention here. Omitted
                // when unresolved — the label already IS the key.
                title={qualified ? source : undefined}
              >
                {label}
              </Td>
            </Tr>
          );
        })}
      </Tbody>
    </Table>
  );
}

/**
 * The **set** of entities the data passed through — rendered as an unordered
 * `LabelGroup`. (This used to say "the same presentation the transformations sets above
 * use"; those sets were the Sources table's "Applied" column, which has been removed, so
 * this is now the only `LabelGroup` in the view.)
 *
 * This deliberately does NOT render an `a → b → c` chain. The spec
 * (`docs/data_lineage_alg.md` "Lineage metadata") defines this element as
 * unordered and defers ordering to a future trace-derived API, so an arrow chain
 * would assert a sequence the data does not carry — and a merge of two branches
 * has no truthful interleaving to draw anyway. The array arrives sorted only so a
 * re-derivation is byte-identical; that is not an order to visualise.
 *
 * An empty set is legitimate for an origin, and is stated rather than rendered as
 * a blank line.
 */
function Entities({
  entities,
  namesByKey,
}: {
  entities: string[];
  namesByKey: Map<string, string>;
}) {
  if (entities.length === 0) {
    return (
      <div
        aria-label="Entities traversed"
        style={{ color: 'var(--dg-color-muted)', fontSize: '0.85rem', marginTop: '0.25rem' }}
      >
        No entities traversed — this payload has not moved yet.
      </div>
    );
  }
  return (
    <div aria-label="Entities traversed" style={{ marginTop: '0.25rem' }}>
      {/* `numLabels={99}` so the panel never hides members behind an overflow
          toggle — a governance reader needs the whole set, same as the
          transformations group above. */}
      <LabelGroup numLabels={99}>
        {entities.map((entity) => {
          const { label, qualified } = lineageLabel(entity, namesByKey);
          return (
            // The natural key is the identity here: a set has no positions, so no
            // index rides in the React key — and the KEY, not the resolved label,
            // is what stays unique when two entities share a display_name.
            //
            // `dg-lineage-key` (global.css) still earns its keep: entity natural
            // keys are long unbreakable tokens and PF clips `.pf-v5-c-label__text`
            // with nowrap+ellipsis, so the wrap has to be granted through the class
            // rather than an inline style — otherwise the group clips inside the
            // narrow floating detail panel exactly as the arrow chain used to.
            <Label
              key={entity}
              isCompact
              color="blue"
              className="dg-mono dg-lineage-key"
              // Same rule as the Source cell: the shortened label is ambiguous
              // between two agents' same-named tools, so the full key rides along
              // in the tooltip. PF forwards `title` to the outer label element.
              title={qualified ? entity : undefined}
            >
              {label}
            </Label>
          );
        })}
      </LabelGroup>
    </div>
  );
}
