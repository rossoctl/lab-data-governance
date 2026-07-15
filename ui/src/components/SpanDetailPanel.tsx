import {
  Title,
  Button,
  CodeBlock,
  CodeBlockCode,
} from '@patternfly/react-core';
import { durationMs } from '../lib/flow';
import { DetailList } from './DetailList';
import type { Span } from '../types';

/** JSON block with the vanilla `(none)` fallback for a null value. */
function JsonBlock({ value }: { value: unknown }) {
  return (
    <CodeBlock>
      <CodeBlockCode>
        {value == null ? '(none)' : JSON.stringify(value, null, 2)}
      </CodeBlockCode>
    </CodeBlock>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <>
      <Title headingLevel="h4" size="md" style={{ marginTop: '0.75rem' }}>
        {title}
      </Title>
      {children}
    </>
  );
}

const dl = (pairs: Array<[string, string]>) => <DetailList pairs={pairs} />;

export interface SpanDetailPanelProps {
  span: Span | null;
  onRefresh: () => void;
}

/** Panel header: title left, Refresh glued to the right. Always rendered, even
 *  in the empty state, so the panel is a stable fixture in the tree layout —
 *  but Refresh is disabled when there is no span to re-fetch (else it would be
 *  a clickable no-op, since the parent's refresh handler early-returns).
 *
 *  The title names the selection itself ('Span') once a span is loaded, folding
 *  in what used to be a redundant leading section header; with nothing selected
 *  it falls back to the generic 'Details' (no target to name yet). */
function PanelHeader({
  title,
  onRefresh,
  canRefresh,
}: {
  title: string;
  onRefresh: () => void;
  canRefresh: boolean;
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        // A little breathing room between the caption and the first field below.
        marginBottom: '0.5rem',
      }}
    >
      <Title headingLevel="h3" size="md">
        {title}
      </Title>
      <Button
        variant="secondary"
        isInline
        isDisabled={!canRefresh}
        onClick={onRefresh}
        title="Re-fetch this span"
      >
        Refresh
      </Button>
    </div>
  );
}

/**
 * The span detail panel — always rendered (a fixed fixture in the tree
 * layout). With a selected span it shows the nine sections ported from the
 * vanilla trace-tree shell (Span / Timing / Status / Resource / Scope / OTLP
 * envelope / Attributes / Events / Links), with the tri-state status, the
 * `(not finalized)` ended_at fallback, and a duration when both ends exist.
 * With no span it shows a placeholder under the same header. The Refresh
 * button re-fetches the span (the parent owns the fetch).
 */
export function SpanDetailPanel({ span, onRefresh }: SpanDetailPanelProps) {
  if (!span) {
    return (
      <div>
        <PanelHeader title="Details" onRefresh={onRefresh} canRefresh={false} />
        {/* The header already carries the caption→content gap (marginBottom),
            so the placeholder needs no extra top margin of its own. */}
        <div style={{ color: '#888', fontStyle: 'italic' }}>
          Select a span to view its details.
        </div>
      </div>
    );
  }

  const statusText =
    span.error === true
      ? `Error: ${span.status_message ?? ''}`
      : span.error === false
        ? 'OK'
        : 'Unset';

  const timing: Array<[string, string]> = [
    ['started_at', span.started_at],
    ['ended_at', span.ended_at == null ? '(not finalized)' : span.ended_at],
  ];
  // Duration only when both ends exist; same helper the flow view uses.
  const dur = durationMs(span.started_at, span.ended_at);
  if (dur !== null) timing.push(['duration', `${dur} ms`]);
  timing.push(['observed_at', span.observed_at]);

  return (
    <div>
      {/* Header names the target ('Span'); the identity fields sit directly
          beneath it — no separate 'Span' section header (that would just echo
          the caption). Timing/Status/etc. remain their own sections below. */}
      <PanelHeader title="Span" onRefresh={onRefresh} canRefresh />

      {dl([
        ['name', span.name],
        ['trace_id', span.trace_id],
        ['span_id', span.span_id],
        ['parent_id', span.parent_id ?? '—'],
        ['kind', span.kind ?? '—'],
        ['service_name', span.service_name ?? '—'],
        ['seq', String(span.seq)],
        ['arrival_seq', String(span.arrival_seq)],
      ])}

      <Section title="Timing">{dl(timing)}</Section>

      <Section title="Status">
        <div style={{ fontSize: '0.85rem' }}>{statusText}</div>
      </Section>

      <Section title="Resource">
        <JsonBlock value={span.resource_attributes} />
      </Section>
      <Section title="Scope">
        <JsonBlock value={span.scope} />
      </Section>
      <Section title="OTLP envelope">
        <JsonBlock value={span.otlp} />
      </Section>
      <Section title="Attributes">
        <JsonBlock value={span.attributes ?? {}} />
      </Section>
      <Section title="Events">
        <JsonBlock value={span.events} />
      </Section>
      <Section title="Links">
        <JsonBlock value={span.links} />
      </Section>
    </div>
  );
}
