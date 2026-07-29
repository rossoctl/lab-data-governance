import { useState, type ReactNode } from 'react';
import { Button, CodeBlock, CodeBlockCode, Spinner, Title } from '@patternfly/react-core';

import { usePayload } from '../../api/hooks';
import { DetailList } from '../DetailList';
import { ClassificationView } from '../ClassificationView';
import { DataLineageView } from '../DataLineageView';
import type { LineageState } from '../../types';

/**
 * One **Interaction leg**'s governance surface in the flow detail panel: a
 * `Request` / `Response` heading over three independently collapsible sections —
 * **Payload** (the body itself), **Classification** (the verdict), and **Data
 * lineage** (the provenance triple). All three start collapsed, so opening the
 * panel costs no reads at all.
 *
 * Why three sections and not one expanded blob (the pre-#121 shape): the three
 * facts are read for different reasons and are rarely wanted together — a reader
 * chasing a PII verdict does not want to scroll past a 4KB JSON body, and a
 * reader diffing bodies does not want the Findings table in the way.
 *
 * The two derived facts arrive from *different* reads, and that asymmetry is the
 * whole reason the fetch gating below is not simply "open":
 *   - **Classification** is an inlined nullable field of `GET /api/payloads/{hash}`
 *     (ADR-0024) — so it rides on the *payload* read and expanding it must
 *     trigger that read.
 *   - **Data lineage** is keyed by the leg, not the content hash (ADR-0027 D5),
 *     and comes from the one trace-scoped read the panel already holds. It is
 *     passed *in*, so expanding it fetches nothing.
 *
 * Rendered per leg only when that leg carried a payload — the panel's existing
 * guard, kept. Lineage is keyed on the leg rather than the hash, so a `Data
 * lineage` section for a payload-less leg would *typecheck*; it would just lie.
 * P-data-lineage derives the triple from payload content, so a leg with no
 * payload has no provenance to derive and would sit on "Lineage not yet
 * computed" forever — the eventual-consistency wording promising a derivation
 * that is never coming. That is precisely the "we don't know yet" / "there is
 * nothing" conflation the tri-state discipline exists to prevent, so the whole
 * leg block is omitted instead.
 */
export function LegSections({
  label,
  hash,
  lineage,
}: {
  label: string;
  /** The leg's payload content hash. Non-null by construction: the panel does
   *  not render this block for a leg that carried no payload. */
  hash: string;
  /**
   * What is currently known about this leg's lineage: a derived triple, "not
   * derived yet", or "the read failed" — three states, never one overloaded
   * `null` (see {@link LineageState}).
   */
  lineage: LineageState;
}) {
  const [payloadOpen, setPayloadOpen] = useState(false);
  const [classificationOpen, setClassificationOpen] = useState(false);
  const [lineageOpen, setLineageOpen] = useState(false);

  // ONE payload read per leg, shared by the two sections that need it, gated on
  // "either of them is open". Three `usePayload` calls would be three cache
  // entries' worth of bookkeeping for one resource; hoisting it here keeps the
  // fetch single-sourced and means a read triggered by Classification instantly
  // satisfies Payload (and vice versa) with no second request.
  const needsPayload = payloadOpen || classificationOpen;
  const { data, isLoading, isError } = usePayload(needsPayload ? hash : null);

  /**
   * Render `body(payload)` only once the payload is actually in hand; until then
   * (or on failure) render the read's own status instead. Both payload-backed
   * sections funnel through this, so the loading/error treatment is stated once
   * for both — and, more importantly, neither can render a derived-looking
   * answer out of an undelivered read.
   *
   * That second part is why the Classification section goes through here rather
   * than passing `data?.classification` straight down: **"we have not fetched it"
   * is not "the server has no verdict yet"**. `classification == null` is a real
   * answer — the ADR-0024 eventual-consistency window — and rendering
   * "Not yet classified" while the read is merely in flight (or never started)
   * would put that claim on screen for a payload nobody has asked about. Same
   * discipline `lineageOfLeg` applies to the lineage triple: an unknown is never
   * dressed up as a derivation.
   */
  const whenLoaded = (body: (payload: NonNullable<typeof data>) => ReactNode): ReactNode => {
    if (isLoading) return <Spinner size="md" aria-label={`Loading ${label} payload`} />;
    if (isError || !data)
      return (
        <div style={{ color: 'var(--dg-color-error)', fontSize: '0.85rem' }}>
          Failed to load payload.
        </div>
      );
    return body(data);
  };

  return (
    <div style={{ marginTop: '0.75rem' }}>
      <Title headingLevel="h4" size="md">
        {label}
      </Title>

      <DisclosureSection
        // The hash prefix stays on the Payload row (it identifies the body, not
        // the verdict or the provenance) — the same 8 chars the pre-restructure
        // single toggle showed.
        label={`Payload  ${hash.slice(0, 8)}`}
        ariaLabel={`${label}: Payload ${hash.slice(0, 8)}`}
        open={payloadOpen}
        onToggle={() => setPayloadOpen((o) => !o)}
      >
        {whenLoaded((payload) => (
          <>
            <DetailList
              pairs={[
                ['kind', payload.content_kind],
                ['hash', payload.content_hash],
                ['bytes', String(payload.byte_size)],
              ]}
            />
            <CodeBlock>
              <CodeBlockCode>
                {payload.content == null ? '(none)' : JSON.stringify(payload.content, null, 2)}
              </CodeBlockCode>
            </CodeBlock>
          </>
        ))}
      </DisclosureSection>

      {/* The P-classification Classification verdict for this payload
          (issue #80): sensitivity level, regulatory tags, identity bundle, and
          the Findings. `null` renders as "not yet classified" (the
          eventual-consistency window, ADR-0024), distinct from a real PUBLIC /
          zero-Findings verdict — and, per `whenLoaded` above, distinct again
          from a read that has not landed. */}
      <DisclosureSection
        label="Classification"
        ariaLabel={`${label}: Classification`}
        open={classificationOpen}
        onToggle={() => setClassificationOpen((o) => !o)}
      >
        {whenLoaded((payload) => (
          <ClassificationView classification={payload.classification} />
        ))}
      </DisclosureSection>

      {/* The P-data-lineage metadata for THIS LEG (issue #119): the payload's
          data sources, the transformations applied per source, and the unordered
          set of entities it passed through. An undelivered derivation renders as
          "lineage not yet computed" (the eventual-consistency window, ADR-0027)
          and a failed read as an explicit error — never as each other, and never
          as a derived answer. No payload fetch here: the state is already in
          hand from the trace-scoped read (ADR-0027 D5). */}
      <DisclosureSection
        label="Data lineage"
        ariaLabel={`${label}: Data lineage`}
        open={lineageOpen}
        onToggle={() => setLineageOpen((o) => !o)}
      >
        <DataLineageView state={lineage} />
      </DisclosureSection>
    </div>
  );
}

/**
 * One collapsed-by-default disclosure row. A `variant="link"` Button with the
 * `▶/▼` glyph, matching the flow view's existing disclosures (`SpanTree`'s
 * expand toggles, and the payload toggle this replaces) rather than importing
 * PF's `ExpandableSection`: that component brings its own heavier chrome and
 * padding, which reads wrong at this density inside the narrow floating panel
 * where three of these stack per leg.
 *
 * `aria-expanded` carries the state to assistive tech (the glyph is decorative),
 * and the accessible name is leg-qualified so `Request: Classification` and
 * `Response: Classification` are distinguishable in the same panel.
 */
function DisclosureSection({
  label,
  ariaLabel,
  open,
  onToggle,
  children,
}: {
  label: string;
  ariaLabel: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div style={{ marginTop: '0.25rem' }}>
      <Button
        variant="link"
        isInline
        onClick={onToggle}
        aria-expanded={open}
        aria-label={ariaLabel}
        className="dg-mono"
      >
        <span aria-hidden="true">{open ? '▼' : '▶'} </span>
        {label}
      </Button>
      {open && <div style={{ marginTop: '0.25rem' }}>{children}</div>}
    </div>
  );
}
