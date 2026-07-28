import { Label, LabelGroup, Title } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { LongArrowAltRightIcon } from '@patternfly/react-icons';
import type { DataLineage } from '../types';

/**
 * The **Data lineage metadata** for one **Interaction leg**'s payload
 * (ADR-0027), rendered inside the flow view's expanded payload beside the
 * **Classification** verdict (issue #119). Read-only surface over the nullable
 * `lineage` field of `GET /api/traces/{tid}/data-lineage`.
 *
 * Answers the two lineage questions in the order a governance reader asks them:
 * *where did this data come from* (the origins, each with the transformations
 * applied to its contribution) and *what did it pass through* (the ordered
 * entity path). Shape deliberately mirrors `ClassificationView` — same null
 * handling, same "an empty result is a real result, say so" rule — so the two
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

  const { data_sources, source_transformations, entity_path } = lineage;

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
        Entity path
      </Title>
      <EntityPath entityPath={entity_path} />
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
 * The ordered entities the data passed through, as an arrow-separated chain.
 * Order is the whole point (ADR-0027), so the chain is rendered in sequence
 * rather than as an unordered label group. An empty path is legitimate for an
 * origin, and is stated rather than rendered as a blank line.
 */
function EntityPath({ entityPath }: { entityPath: string[] }) {
  if (entityPath.length === 0) {
    return (
      <div
        aria-label="Entity path"
        style={{ color: '#888', fontSize: '0.85rem', marginTop: '0.25rem' }}
      >
        No entities traversed — this payload has not moved yet.
      </div>
    );
  }
  return (
    <div
      aria-label="Entity path"
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: '0.25rem',
        marginTop: '0.25rem',
      }}
    >
      {entityPath.map((entity, i) => (
        // The natural key can repeat in a path (an agent re-entered), so the
        // index is part of the key — the position is what identifies a hop.
        <span
          key={`${entity}-${i}`}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '0.25rem',
            // A flex item defaults to `min-width: auto` (its content's intrinsic
            // width), which would keep this hop from ever shrinking below its
            // long unbreakable key — defeating the wrap granted by
            // `dg-lineage-key` and pushing the chain past the panel edge.
            minWidth: 0,
          }}
        >
          {i > 0 && (
            <LongArrowAltRightIcon
              aria-hidden="true"
              style={{ color: '#888', fontSize: '0.75rem' }}
            />
          )}
          {/* Same long-key problem as the sources table, and worse here: PF
              clips `.pf-v5-c-label__text` with nowrap+ellipsis, so the wrap has
              to be granted through the class (global.css), not inline. */}
          <Label isCompact color="blue" className="dg-mono dg-lineage-key">
            {entity}
          </Label>
        </span>
      ))}
    </div>
  );
}
