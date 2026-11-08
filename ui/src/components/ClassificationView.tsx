import { Label, LabelGroup, Title } from '@patternfly/react-core';
import { Table, Thead, Tbody, Tr, Th, Td } from '@patternfly/react-table';
import { ExclamationTriangleIcon } from '@patternfly/react-icons';
import { Link, useInRouterContext, useSearchParams } from 'react-router-dom';
import type { Classification, Finding, LabelOrigin, LegLabel, LegLabels, LineageStatus } from '../types';
import { SensitivityBadge } from './SensitivityBadge';

/**
 * What the labels read said about this leg — passed only once that read
 * succeeded. `leg` is `null` when the read has no row for the leg.
 */
export interface LegLabelsState {
  leg: LegLabels | null;
  lineageStatus: LineageStatus;
}

/**
 * The **Classification** verdict for one **Payload** (CONTEXT.md), rendered
 * inside the flow view's Req/Resp payload cells (issue #80). Read-only surface
 * over the inlined nullable `classification` field of `GET /api/payloads/{hash}`
 * (ADR-0024).
 *
 * With `labels` (the per-interaction labels read) and something carried in, the
 * verdict splits in two: **Found here** — the classifier's reading of this
 * payload — and **Carried in** — label lineage: the same text carried the label
 * earlier in the conversation. Both are facts about the payload; nothing here
 * says whether a risk decision used them. While that read is loading or failed,
 * or nothing was carried in, the view is the classifier's alone, unchanged.
 */
export function ClassificationView({
  classification,
  labels,
}: {
  classification: Classification | null | undefined;
  labels?: LegLabelsState;
}) {
  const carried = labels?.leg?.carried != null ? labels.leg : null;
  // Only an answered read can say lineage was not derived; an absent `labels`
  // (loading, failed, or no read at all) says nothing.
  const notDerived = labels != null && labels.lineageStatus === null && (
    <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.8rem', marginTop: '0.375rem' }}>
      Data lineage: not derived for this trace
    </div>
  );

  // The eventual-consistency window (ADR-0024): the payload exists but
  // P-classification has not written a verdict yet. Rendered as a distinct,
  // verdict-less state — deliberately NOT a PUBLIC badge, so it never reads as
  // a real clean verdict (every payload is classified uniformly, so a null
  // means "not yet processed", never "processed but clean" — CONTEXT.md).
  // What was carried in is still a fact, so it is listed below the note.
  if (classification == null) {
    return (
      <div>
        <div style={{ color: 'var(--dg-color-muted)', fontStyle: 'italic', fontSize: '0.85rem' }}>
          Not yet classified
        </div>
        {carried ? <LabelSources leg={carried} /> : notDerived}
      </div>
    );
  }

  if (carried) {
    return (
      <div>
        <SummaryLine caption="Found here (classifier)">
          <SensitivityBadge level={classification.sensitivity_level} />
          {classification.regulatory_tags.length > 0 && (
            <span>{classification.regulatory_tags.join(', ')}</span>
          )}
          {classification.contains_identity_bundle && (
            <Label isCompact color="red" icon={<ExclamationTriangleIcon />}>
              Identity bundle
            </Label>
          )}
        </SummaryLine>
        <LabelSources leg={carried} />
      </div>
    );
  }

  const { regulatory_tags, contains_identity_bundle, findings } = classification;

  return (
    <div>
      <SensitivityBadge level={classification.sensitivity_level} />

      {contains_identity_bundle && (
        <Label
          isCompact
          color="red"
          icon={<ExclamationTriangleIcon />}
          style={{ marginLeft: '0.375rem' }}
        >
          Identity bundle
        </Label>
      )}

      {regulatory_tags.length > 0 && (
        <div style={{ marginTop: '0.375rem' }}>
          <LabelGroup categoryName="Regulatory tags" numLabels={99}>
            {regulatory_tags.map((tag) => (
              <Label key={tag} isCompact color="grey">
                {tag}
              </Label>
            ))}
          </LabelGroup>
        </div>
      )}

      <Title headingLevel="h5" size="md" style={{ marginTop: '0.5rem' }}>
        Findings
      </Title>
      <FindingsTable findings={findings} />
      {notDerived}
    </div>
  );
}

/** One captioned summary line: a fixed-width caption, then its badges and tags. */
function SummaryLine({ caption, children }: { caption: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.375rem', marginTop: '0.25rem' }}>
      <span style={{ minWidth: '11rem', fontWeight: 600, fontSize: '0.85rem' }}>{caption}</span>
      {children}
    </div>
  );
}

/**
 * The carried-in summary line, the sentence that defines it, and the **Labels**
 * table. "from N earlier steps" counts origin legs, never edges.
 */
function LabelSources({ leg }: { leg: LegLabels }) {
  const carried = leg.carried!;
  const steps = carried.origin_count;
  return (
    <>
      <SummaryLine caption="Carried in (data lineage)">
        {carried.max_level && <SensitivityBadge level={carried.max_level} />}
        {carried.regulatory_tags.length > 0 && <span>{carried.regulatory_tags.join(', ')}</span>}
        <span style={{ color: 'var(--dg-color-muted)', fontSize: '0.85rem' }}>
          from {steps} earlier {steps === 1 ? 'step' : 'steps'}
        </span>
      </SummaryLine>
      <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.8rem', marginTop: '0.375rem' }}>
        &ldquo;Carried in&rdquo; = this same text had this label earlier in the conversation.
      </div>
      <Title headingLevel="h5" size="md" style={{ marginTop: '0.5rem' }}>
        Labels
      </Title>
      <LabelsTable labels={leg.labels} />
    </>
  );
}

/**
 * One row per (span, type), in the server's order (by position, highest level
 * first). Region indexes the text the classifier read, not the JSON on the
 * Payload tab, so it stays a muted column. The chains a value travelled are not
 * here: the Data lineage tab shows them (`LabelLineageTable`).
 */
function LabelsTable({ labels }: { labels: LegLabel[] }) {
  return (
    <Table aria-label="Labels" variant="compact">
      <Thead>
        {/* `wrap`: six columns share the ~500px panel, and PF's default
            header truncation would clip `Sensitivity · tags` and `Region`. */}
        <Tr>
          <Th modifier="wrap">Text</Th>
          <Th modifier="wrap">Type</Th>
          <Th modifier="wrap">Sensitivity · tags</Th>
          <Th modifier="wrap">Source</Th>
          <Th modifier="wrap">Origin</Th>
          <Th modifier="wrap">Region</Th>
        </Tr>
      </Thead>
      <Tbody>
        {labels.map((l, i) => (
          <Tr key={`${l.entity_type}-${l.start}-${l.end}-${i}`}>
            <Td dataLabel="Text" className="dg-mono">
              {l.text}
            </Td>
            <Td dataLabel="Type">
              <Label isCompact color="orange">
                {l.entity_type}
              </Label>
            </Td>
            <Td dataLabel="Sensitivity · tags">
              {l.sensitivity_level && <SensitivityBadge level={l.sensitivity_level} />}{' '}
              {l.regulatory_tags.join(', ')}
            </Td>
            <Td dataLabel="Source">
              <span style={{ display: 'inline-flex', gap: '0.25rem', flexWrap: 'wrap' }}>
                {l.found_here && (
                  <Label isCompact color="blue">
                    Found here
                  </Label>
                )}
                {l.carried_in.length > 0 && (
                  <Label isCompact variant="outline" color="purple">
                    Carried in
                  </Label>
                )}
              </span>
            </Td>
            <Td dataLabel="Origin">
              {l.carried_in.map((o, j) => (
                <div key={`${o.origin_interaction_id}-${o.origin_leg_type}-${j}`}>
                  <OriginLink origin={o} />
                  {o.unit_text !== o.unit_value && (
                    <span style={{ color: 'var(--dg-color-muted)' }}>
                      {' '}
                      (classifier marked &ldquo;{o.unit_text}&rdquo; there)
                    </span>
                  )}
                </div>
              ))}
            </Td>
            <Td dataLabel="Region" className="dg-mono" style={{ color: 'var(--dg-color-muted)' }}>
              {l.start == null ? '—' : `${l.start}–${l.end}`}
            </Td>
          </Tr>
        ))}
      </Tbody>
    </Table>
  );
}

/** `caller → callee, request|response #seq`, as far as the origin leg is still known. */
function originText(o: LabelOrigin): string {
  const hop = o.caller != null || o.callee != null ? `${o.caller ?? '?'} → ${o.callee ?? '?'}, ` : '';
  return `${hop}${o.origin_leg_type}${o.origin_seq != null ? ` #${o.origin_seq}` : ''}`;
}

/**
 * An origin, linked to its interaction on the current view (`?iid=`, keeping
 * the other params). Plain text outside a router, so the view still renders
 * where there is nothing to navigate, and for an origin in another trace,
 * whose interaction this view does not hold.
 */
function OriginLink({ origin }: { origin: LabelOrigin }) {
  const linkable = useInRouterContext() && origin.origin_trace_id == null;
  return linkable ? <RoutedOriginLink origin={origin} /> : <>{originText(origin)}</>;
}

function RoutedOriginLink({ origin }: { origin: LabelOrigin }) {
  const [params] = useSearchParams();
  const next = new URLSearchParams(params);
  next.delete('eid');
  next.set('iid', origin.origin_interaction_id);
  return <Link to={{ search: next.toString() }}>{originText(origin)}</Link>;
}

/**
 * The **Findings** the NER model detected (CONTEXT.md): each row is the
 * finding's detected *type* — its NER tag (`SSN`, `EMAIL`, … — deliberately
 * NOT an **Entity**, which is the interaction participant) — and the flagged
 * *text region* it matched. An empty list is a real zero-Findings verdict
 * (distinct from the null "not yet classified" state the parent guards), so it
 * is stated explicitly rather than rendered as nothing.
 */
function FindingsTable({ findings }: { findings: Finding[] }) {
  if (findings.length === 0) {
    return (
      <div style={{ color: 'var(--dg-color-muted)', fontSize: '0.85rem', marginTop: '0.25rem' }}>
        No findings — no sensitive text detected.
      </div>
    );
  }
  return (
    <Table aria-label="Findings" variant="compact">
      <Thead>
        <Tr>
          <Th>Type</Th>
          <Th>Flagged text</Th>
          <Th>Region</Th>
        </Tr>
      </Thead>
      <Tbody>
        {findings.map((f, i) => (
          <Tr key={`${f.entity_type}-${f.start}-${f.end}-${i}`}>
            <Td dataLabel="Type">
              <Label isCompact color="orange">
                {f.entity_type}
              </Label>
            </Td>
            <Td dataLabel="Flagged text" className="dg-mono">
              {f.text}
            </Td>
            <Td dataLabel="Region" className="dg-mono" style={{ color: 'var(--dg-color-muted)' }}>
              {f.start}–{f.end}
            </Td>
          </Tr>
        ))}
      </Tbody>
    </Table>
  );
}
