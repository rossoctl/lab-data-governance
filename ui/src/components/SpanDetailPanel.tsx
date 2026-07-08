import {
  Title,
  Button,
  DescriptionList,
  DescriptionListGroup,
  DescriptionListTerm,
  DescriptionListDescription,
  CodeBlock,
  CodeBlockCode,
} from '@patternfly/react-core';
import { durationMs } from '../lib/flow';
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

function dl(pairs: Array<[string, string]>) {
  return (
    <DescriptionList isCompact isHorizontal>
      {pairs.map(([k, v]) => (
        <DescriptionListGroup key={k}>
          <DescriptionListTerm>{k}</DescriptionListTerm>
          <DescriptionListDescription className="dg-mono">{v}</DescriptionListDescription>
        </DescriptionListGroup>
      ))}
    </DescriptionList>
  );
}

export interface SpanDetailPanelProps {
  span: Span;
  onRefresh: () => void;
}

/**
 * The span detail panel — the nine sections ported from the vanilla trace-tree
 * shell (Identity / Timing / Status / Resource / Scope / OTLP envelope /
 * Attributes / Events / Links), with the tri-state status, the `(not
 * finalized)` ended_at fallback, and a duration when both ends exist. A
 * Refresh button re-fetches the span (the parent owns the fetch).
 */
export function SpanDetailPanel({ span, onRefresh }: SpanDetailPanelProps) {
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
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Title headingLevel="h3" size="md">
          Span detail
        </Title>
        <Button variant="secondary" isInline onClick={onRefresh} title="Re-fetch this span">
          Refresh
        </Button>
      </div>

      <Section title="Identity">
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
      </Section>

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
