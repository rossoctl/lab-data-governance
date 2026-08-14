import { Button, Title } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import type { UseQueryResult } from '@tanstack/react-query';

import type { PinStore } from '../../lib/pins';
import type { TraceDataLineage } from '../../types';
import { DetailList } from '../DetailList';
import { RoleIcon } from '../RoleIcon';
import { LegTabs, type Leg } from './LegTabs';
import { SpanLink } from './SpanLink';
import { lineageOfLeg, type Selection } from './selection';

/**
 * The flow view's detail panel for the one selected row: caption + pin toggle +
 * close, the selection's fields, a two-level tab set (leg → payload /
 * Classification / Data lineage) over the legs that carried a payload, and the
 * span evidence that produced it.
 *
 * Floats as a fixed overlay on the right of the viewport instead of occupying a
 * layout column, so the tables use the full width — geometry lives in
 * `.dg-detail-panel` (global.css), single-sourced with the gutter the tables
 * reserve (`.dg-detail-gutter`) so the two cannot drift. Rendered only when
 * something is selected: an empty float is just clutter.
 */
export function FlowDetailPanel({
  selection,
  pins,
  lineageQ,
  onTogglePin,
  onClose,
  onNavigateToSpan,
}: {
  selection: Selection;
  pins: PinStore;
  /** The one trace-scoped lineage read; each leg's block is keyed off it. */
  lineageQ: Pick<UseQueryResult<TraceDataLineage>, 'data' | 'isError'>;
  onTogglePin: () => void;
  onClose: () => void;
  onNavigateToSpan?: (spanId: string) => void;
}) {
  // The legs that actually carried a payload — built here rather than inline so
  // the middle card can be skipped when there are none (an entity selection, or
  // an interaction with no payload on either leg). LegTabs itself already renders
  // nothing in that case; without the guard the card would be an empty bordered
  // box. Which legs get tabs is unchanged: still exactly the ones with a hash.
  const legs: Leg[] = [
    selection.requestPayloadHash && {
      label: 'Request',
      hash: selection.requestPayloadHash,
      lineage: lineageOfLeg(lineageQ, selection, 'request'),
    },
    selection.responsePayloadHash && {
      label: 'Response',
      hash: selection.responsePayloadHash,
      lineage: lineageOfLeg(lineageQ, selection, 'response'),
    },
  ].filter((l): l is Leg => Boolean(l));

  return (
    <div className="dg-detail-panel">
      {/* Each of the panel's three regions — identity (caption + fields), the
          per-leg governance tabs, and the span evidence — is wrapped in a
          `.dg-detail-card` (global.css). Margins alone left them reading as one
          undifferentiated column; the subtle border makes the grouping explicit.
          The class is shared, so the styling is stated once for all three. */}
      <div className="dg-detail-card">
        {/* Caption row: the selection's own name ('Entity'/'Interaction')
            on the left — folding in what used to be a separate leading
            section header — with the pin toggle glued to the right, matching
            SpanDetailPanel's Refresh layout. */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            // Keep a gap between caption and button so they never butt
            // together when the narrow (30%) detail column squeezes the row.
            gap: '0.5rem',
            // A little breathing room between the caption and the first
            // field below (e.g. 'Interaction' → 'summary').
            marginBottom: '0.5rem',
          }}
        >
          <Title headingLevel="h3" size="md">
            {selection.sectionTitle}
          </Title>
          {/* Pin toggle + a × to dismiss the floating panel, kept together
              on the right; both refuse to shrink below their labels. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexShrink: 0 }}>
            <Button
              variant="secondary"
              isInline
              onClick={onTogglePin}
              // A swatch of the highlight color: the current color once
              // pinned, else a preview of the next-free color the pin would
              // take.
              icon={
                <span
                  data-testid="highlight-swatch"
                  aria-hidden="true"
                  style={{
                    display: 'inline-block',
                    width: 10,
                    height: 10,
                    borderRadius: 2,
                    border: '1px solid var(--dg-scrim-chip)',
                    // Extra gap beyond PF's default icon spacing so the color
                    // chip doesn't crowd the label text.
                    marginRight: '0.375rem',
                    background:
                      pins.slotColorFor(selection.pinKey) ?? pins.nextFreeColor(),
                  }}
                />
              }
            >
              {pins.isPinned(selection.pinKey) ? 'Unpin' : 'Add to highlights'}
            </Button>
            <Button
              variant="plain"
              aria-label="Close details"
              onClick={onClose}
              style={{ color: 'var(--dg-color-muted)', fontSize: '1.1rem', lineHeight: 1, padding: 0 }}
            >
              ×
            </Button>
          </div>
        </div>
        <DetailList pairs={selection.fields} />
      </div>

      {/* One outer tab per leg that actually carried a payload, named by the leg
          ('Request' / 'Response') — there is no umbrella 'Payloads' heading,
          because the leg is what a reader navigates by and the payload is only
          one of the three facts under it. A leg with no payload hash contributes
          no tab at all, so a request-only interaction shows a single `Request`
          tab (see LegTabs' note on why a lineage-only tab would misrepresent
          such a leg). */}
      {legs.length > 0 && (
        <div className="dg-detail-card">
          <LegTabs legs={legs} />
        </div>
      )}

      <div className="dg-detail-card">
        {/* No `marginTop` any more: the separation from the tabs above is now the
            card's own border + gap, and an inline top margin inside a bordered
            card just pushes the heading off its own edge. */}
        <Title headingLevel="h4" size="md">
          Spans
        </Title>
        {/* `dg-span-evidence` (global.css) lets the span-id links wrap. Without
            it the two monospace id columns are unbreakable ~150px words, the
            table's intrinsic minimum overflows this card, and PF's unconditional
            `thead` truncate rule clips the last header to `Servi…`. The class
            attacks that cause; it is not a width tweak. */}
        <Table aria-label="Span evidence" variant="compact" className="dg-span-evidence">
          <Thead>
            <Tr>
              <Th>Role</Th>
              <Th>Span</Th>
              <Th>Parent</Th>
              <Th>Kind</Th>
              {/* Interaction evidence carries the span's own name (issue #155);
                  entity evidence does not — the server left that view
                  un-widened — so the column renders as an em dash there rather
                  than being conditionally removed, which would make the two
                  evidence tables different shapes for no reader benefit. */}
              <Th>Name</Th>
              <Th>Service</Th>
            </Tr>
          </Thead>
          <Tbody>
            {selection.evidence.map((ev, i) => (
              <Tr key={`${ev.span_id}-${i}`}>
                <Td dataLabel="Role"><RoleIcon role={ev.role} /></Td>
                <Td dataLabel="Span">
                  <SpanLink spanId={ev.span_id} onNavigate={onNavigateToSpan} />
                </Td>
                <Td dataLabel="Parent">
                  <SpanLink spanId={ev.parent_id} onNavigate={onNavigateToSpan} />
                </Td>
                <Td dataLabel="Kind">{ev.kind ?? '—'}</Td>
                <Td dataLabel="Name">{ev.name ?? '—'}</Td>
                <Td dataLabel="Service">{ev.service_name ?? '—'}</Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </div>
    </div>
  );
}
