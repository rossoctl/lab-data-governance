import { Label, LabelGroup, Title } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import type { DataLineage } from '../types';

/**
 * The **Data lineage metadata** for one **Interaction leg**'s payload
 * (ADR-0027), rendered inside the flow view's expanded payload beside the
 * **Classification** verdict (issue #119). Read-only surface over the nullable
 * `lineage` field of `GET /api/traces/{tid}/data-lineage`.
 *
 * Answers the two lineage questions in the order a governance reader asks them:
 * *where did this data come from* (the origins, each with the transformations
 * applied to its contribution) and *which entities did it pass through* (the
 * unordered entity set). Shape deliberately mirrors `ClassificationView` — same
 * null handling, same "an empty result is a real result, say so" rule — so the two
 * blocks in the same payload read as one surface.
 */
export function DataLineageView({ lineage }: { lineage: DataLineage | null | undefined }) {
  // The eventual-consistency window (ADR-0027): the leg exists but
  // P-data-lineage has not derived its metadata yet. Rendered as a distinct,
  // claim-less state — deliberately NOT an empty sources list, which would read
  // as the real "originates here, no upstream sources" verdict below. A
  // governance tool must never let "we don't know yet" look like "we checked".
  if (lineage == null) {
    return (
      <div style={{ color: '#888', fontStyle: 'italic', fontSize: '0.85rem' }}>
        Lineage not yet computed
      </div>
    );
  }

  const { data_sources, source_transformations, entities } = lineage;

  return (
    <div>
      {data_sources.length === 0 ? (
        // A genuinely empty triple is a REAL derived value (ADR-0027 D3: the
        // payload originates at this entity), not the null state — so it is
        // stated in words rather than rendered as nothing.
        <div style={{ color: '#888', fontSize: '0.85rem' }}>
          No upstream data sources — this payload originates here.
        </div>
      ) : (
        <>
          <Title headingLevel="h5" size="md">
            Data sources
          </Title>
          <SourcesTable
            dataSources={data_sources}
            sourceTransformations={source_transformations}
          />
        </>
      )}

      <Title headingLevel="h5" size="md" style={{ marginTop: '0.5rem' }}>
        Entities traversed
      </Title>
      <Entities entities={entities} />
    </div>
  );
}

/**
 * The origins, one row each, with the transformations applied to *that* source's
 * contribution (`data_source → set<transformation>`, ADR-0027). The row set is
 * driven off `data_sources`, not the map's keys: the source list is the
 * authority on which origins exist, and a source legitimately carries no map
 * entry (nothing recorded for it) — which must not silently drop the source.
 */
function SourcesTable({
  dataSources,
  sourceTransformations,
}: {
  dataSources: string[];
  sourceTransformations: Record<string, string[]>;
}) {
  return (
    <Table aria-label="Data sources" variant="compact">
      <Thead>
        <Tr>
          <Th>Source</Th>
          {/* "Transformations" truncates to "Tra…" in the narrow detail panel
              once a long natural key claims the Source column. "Applied" says
              the same thing in a width the panel actually has. */}
          <Th>Applied</Th>
        </Tr>
      </Thead>
      <Tbody>
        {dataSources.map((source) => {
          const transformations = sourceTransformations[source] ?? [];
          return (
            <Tr key={source}>
              {/* `dg-lineage-key` grants the long-unbreakable-natural-key wrap
                  (global.css) — without it this table pushes past the narrow
                  detail panel and clips its own Transformations column. */}
              <Td dataLabel="Source" className="dg-mono dg-lineage-key">
                {source}
              </Td>
              <Td dataLabel="Applied">
                {transformations.length === 0 ? (
                  <span style={{ color: '#888', fontSize: '0.85rem' }}>None</span>
                ) : (
                  <LabelGroup numLabels={99}>
                    {transformations.map((t) => (
                      <Label key={t} isCompact color="purple">
                        {t}
                      </Label>
                    ))}
                  </LabelGroup>
                )}
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
 * `LabelGroup`, the same presentation the transformations sets above use.
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
function Entities({ entities }: { entities: string[] }) {
  if (entities.length === 0) {
    return (
      <div
        aria-label="Entities traversed"
        style={{ color: '#888', fontSize: '0.85rem', marginTop: '0.25rem' }}
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
        {entities.map((entity) => (
          // The natural key is the identity here: a set has no positions, so no
          // index rides in the React key.
          //
          // `dg-lineage-key` (global.css) still earns its keep: entity natural
          // keys are long unbreakable tokens and PF clips `.pf-v5-c-label__text`
          // with nowrap+ellipsis, so the wrap has to be granted through the class
          // rather than an inline style — otherwise the group clips inside the
          // narrow floating detail panel exactly as the arrow chain used to.
          <Label key={entity} isCompact color="blue" className="dg-mono dg-lineage-key">
            {entity}
          </Label>
        ))}
      </LabelGroup>
    </div>
  );
}
