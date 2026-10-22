import { useState, type ReactNode } from 'react';
import { CodeBlock, CodeBlockCode, Spinner, Tab, TabTitleText, Tabs } from '@patternfly/react-core';

import { useLegLabels, usePayload } from '../../api/hooks';
import { DetailList } from '../DetailList';
import { ClassificationView, type LegLabelsState } from '../ClassificationView';
import { LabelLineageTable } from '../LabelLineageTable';
import { useInRouterContext, useSearchParams, type To } from 'react-router-dom';
import type { HopLinks } from '../../lib/hopLinks';
import type { LineageHop } from '../../types';

/** One **Interaction leg**'s governance surface, as the outer tabs address it. */
export type Leg = {
  /** `Request` / `Response` — the outer tab's label and the a11y qualifier for
   *  that leg's inner tab set. */
  label: string;
  /** Which leg of the interaction this is — the key into the labels read. */
  legType: 'request' | 'response';
  /** The leg's payload content hash. Non-null by construction: a leg that
   *  carried no payload contributes no tab at all (see the note below). */
  hash: string;
  /** The role the body played on THIS leg (`llm_completion`,
   *  `agent_response`, …). It is the leg's fact, not the payload's: the same
   *  hash under two legs can carry two kinds, which is why the Payload pane
   *  reads it from here and not from the fetched payload resource (#286).
   *  Null when the protocol has no semantic body kind. */
  kind: string | null;
};

/** The three facts under a leg, in tab order. Payload leads: it is the thing a
 *  reader most often opened the panel for, and the two derived facts read as
 *  commentary on it. */
const SECTIONS = ['payload', 'classification', 'labels'] as const;
type Section = (typeof SECTIONS)[number];

const SECTION_LABEL: Record<Section, string> = {
  payload: 'Payload',
  classification: 'Classification',
  labels: 'Data lineage',
};

/**
 * The flow detail panel's per-leg governance surface: **two levels of tabs**.
 * The outer level picks the leg (`Request` / `Response`); the inner level picks
 * which of that leg's three facts to show — **Payload** (the body itself),
 * **Classification** (the verdict), and **Data lineage** (label lineage: where
 * each carried-in value came from).
 *
 * Why tabs and not one expanded blob (the pre-#121 shape): the three facts are
 * read for different reasons and are rarely wanted together — a reader chasing a
 * PII verdict does not want to scroll past a 4KB JSON body, and a reader diffing
 * bodies does not want the Findings table in the way. Tabs rather than the
 * stacked disclosures that first replaced it because three collapsibles per leg,
 * twice, is six rows of chrome competing for a ~420px-wide panel; exactly one
 * pane is ever wanted at a time, which is the tab contract, not the disclosure
 * one.
 *
 * The two derived facts arrive from *different* reads, and that asymmetry is the
 * whole reason the fetch gating below is not simply "mounted":
 *   - **Classification** is an inlined nullable field of `GET /api/payloads/{hash}`
 *     (ADR-0024) — so it rides on the *payload* read and selecting its tab must
 *     trigger that read.
 *   - The **Labels** beside the verdict (found here / carried in) and the
 *     **Data lineage** pane come from one read per *interaction*, made the
 *     first time either pane opens and shared by both legs.
 *
 * `legs` carries only the legs that actually carried a payload — the panel's
 * existing guard, kept. Labels are found on and carried into payload content,
 * so a leg with no payload has nothing to show under any of the three tabs, and
 * gets no tab at all.
 */
export function LegTabs({
  legs,
  traceId,
  interactionId = null,
}: {
  legs: Leg[];
  /** The trace and interaction the legs belong to, for the labels read (one per
   *  interaction, shared by both legs). Without them the Classification pane is
   *  the classifier's verdict alone. */
  traceId?: string;
  interactionId?: string | null;
}) {
  // Which leg's pane is showing, tracked by label so the identity survives a
  // re-render. Component-local on purpose: no URL param, and it may reset when
  // the selected row changes — matching the disclosures this replaced.
  const [activeLabel, setActiveLabel] = useState<string | null>(null);

  if (legs.length === 0) return null;

  // Default to the first leg present (`Request` when it has a payload), and fall
  // back to it whenever the remembered label is not among the current legs —
  // otherwise selecting a response-only interaction after a request-only one
  // would leave every tab inactive and the pane blank.
  const active = legs.find((l) => l.label === activeLabel) ?? legs[0];

  return (
    // No outer margin: this set now sits inside the detail panel's own
    // `.dg-detail-card`, which supplies both the separation from the section
    // above and the internal padding (global.css). A top margin here would push
    // the leg tabs off the card's own top edge. The class is what lets the card
    // bleed BOTH tab levels out to its padding edge as a unit — see the
    // `.dg-legtabs` rule's note on why the bleed cannot be applied to a
    // descendant `.pf-v5-c-tabs` directly.
    <div className="dg-legtabs">
      <Tabs
        activeKey={active.label}
        onSelect={(_e, key) => setActiveLabel(String(key))}
        // Names the outer set so its tabs are not confused with the inner set's
        // in the a11y tree.
        aria-label="Interaction leg"
      >
        {legs.map((leg) => (
          <Tab key={leg.label} eventKey={leg.label} title={<TabTitleText>{leg.label}</TabTitleText>} />
        ))}
      </Tabs>

      {/*
        Rendering ONLY the active leg is load-bearing, not a rendering economy.
        Handing both legs' panes to PF as `Tab` children would mount both
        subtrees (PF keeps inactive tab content in the DOM, merely hidden, unless
        told otherwise) — and a mounted `LegPanel` runs its `usePayload`, so the
        Response leg would fetch its body while the reader is looking at Request.
        The lazy read the collapsed disclosures gave us for free has to be
        re-established explicitly here, and `LegPanel`'s state resetting with the
        leg (a fresh mount per `key`) is the intended consequence: each leg opens
        on Payload.
      */}
      <LegPanel
        key={active.label}
        leg={active}
        traceId={traceId}
        interactionId={traceId ? interactionId : null}
      />
    </div>
  );
}

/**
 * One leg's inner tab set plus the selected pane. Expressed once and reused for
 * both legs — `Request` and `Response` are structurally identical, so there is
 * exactly one statement of the three sections in the codebase.
 */
function LegPanel({
  leg,
  traceId,
  interactionId,
}: {
  leg: Leg;
  traceId?: string;
  interactionId: string | null;
}) {
  const { label, legType, hash, kind } = leg;
  const [section, setSection] = useState<Section>('payload');
  // The labels read (found here / carried in) is per INTERACTION and lazy: it
  // fires the first time a leg's Classification or Data lineage pane opens and
  // stays enabled after, so tabbing away and back does not flip it off and on.
  // The other leg reads the same cache entry, enabled or not.
  const [labelsWanted, setLabelsWanted] = useState(false);
  const labelsQ = useLegLabels(traceId ?? '', interactionId, labelsWanted);
  // Only an answered read reaches the view: loading or failed is not "nothing
  // carried in", so the view stays the classifier's alone until then. The read
  // is pinned to the leg's current payload and to whether the classifier had
  // read it; the payload read is not refreshed live and the labels read is, so
  // when either disagrees with the payload this pane shows (one of the two
  // reads is stale), it is treated as not answered. The classifier's verdict is
  // never set beside another payload's labels, nor "Not yet classified" beside
  // rows found here.
  const legLabels = labelsQ.data?.byLeg.get(legType) ?? null;
  const labelsFor = (classified: boolean): LegLabelsState | undefined =>
    labelsQ.isSuccess &&
    (legLabels == null ||
      (legLabels.payload_hash === hash && (legLabels.found != null) === classified))
      ? { leg: legLabels, lineageStatus: labelsQ.data.lineageStatus }
      : undefined;

  // ONE payload read per leg, shared by the two sections that need it (Payload
  // and Classification — the verdict is an inlined field of the payload
  // resource, ADR-0024). Three `usePayload` calls would be three cache entries'
  // worth of bookkeeping for one resource; hoisting it here keeps the fetch
  // single-sourced, so whichever of the two panes the reader opens first pays
  // for the read and the other is satisfied from cache.
  //
  // Deliberately NOT gated on which section is active, unlike the collapsibles
  // this replaced. There, all three could be shut at once, so `payloadOpen ||
  // classificationOpen` had a reachable false state worth gating on. With tabs
  // exactly one section is always active and `Payload` is the default, so a
  // mounted LegPanel has already needed the body — any such gate would be
  // unreachable code asserting an invariant it cannot enforce.
  //
  // Laziness therefore lives one level up, where it is actually observable:
  // `LegTabs` mounts ONLY the active leg, so an untouched leg issues no read at
  // all (see the `key={active.label}` note above, and the LegTabs tests that pin
  // both halves).
  const { data, isLoading, isError } = usePayload(hash);

  /**
   * Render `body(payload)` only once the payload is actually in hand; until then
   * (or on failure) render the read's own status instead. Both payload-backed
   * sections funnel through this, so the loading/error treatment is stated once
   * for both — and, more importantly, neither can render a derived-looking
   * answer out of an undelivered read.
   *
   * That second part is why the Classification pane goes through here rather
   * than passing `data?.classification` straight down: **"we have not fetched
   * it" is not "the server has no verdict yet"**. `classification == null` is a
   * real answer — the ADR-0024 eventual-consistency window — and rendering
   * "Not yet classified" while the read is merely in flight (or never started)
   * would put that claim on screen for a payload nobody has asked about: an
   * unknown is never dressed up as a derivation.
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
    <>
      <Tabs
        activeKey={section}
        onSelect={(_e, key) => {
          setSection(key as Section);
          if (key === 'classification' || key === 'labels') setLabelsWanted(true);
        }}
        // `isSecondary` is what makes the nesting legible: PF draws the inner set
        // in its lighter, tighter secondary treatment against the primary leg
        // tabs above, so the two levels do not read as one flat row of five.
        isSecondary
        // At the panel's old 320px floor the section labels did not fit on one
        // line and PF fell back to its horizontal scroll buttons, hiding the last
        // tab behind a forward arrow. The floor is now 520px
        // (`--dg-detail-panel-min`), which fits all three — so these labels
        // should never be reachable in practice.
        // Kept because PF renders the buttons off its own width measurement, not
        // off our var: at an extreme zoom or font-size they can still appear, and
        // an unlabelled scroll button is an a11y hole.
        backScrollAriaLabel={`Scroll ${label} sections back`}
        forwardScrollAriaLabel={`Scroll ${label} sections forward`}
        // Leg-qualified, because `Payload` would otherwise be an ambiguous
        // accessible name the moment both legs are reachable — the same
        // qualification the `Request: Payload` disclosures carried.
        aria-label={`${label} sections`}
      >
        {SECTIONS.map((s) => (
          <Tab
            key={s}
            eventKey={s}
            title={<TabTitleText>{SECTION_LABEL[s]}</TabTitleText>}
            // The a11y name each level's tabs are found by. The visible label
            // stays short — three labels have to share the panel's width — while
            // the accessible name stays unambiguous across legs,
            // and carries the hash prefix on Payload the way the old disclosure
            // row did (the hash identifies the body, not the verdict or the
            // provenance; it is also spelled in full in the pane's DetailList).
            aria-label={
              s === 'payload'
                ? `${label}: Payload ${hash.slice(0, 8)}`
                : `${label}: ${SECTION_LABEL[s]}`
            }
          />
        ))}
      </Tabs>

      <div style={{ marginTop: '0.5rem' }}>
        {section === 'payload' &&
          whenLoaded((payload) => (
            <>
              <DetailList
                pairs={[
                  ['kind', kind ?? '—'],
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

        {/* The P-classification Classification verdict for this payload
            (issue #80): sensitivity level, regulatory tags, identity bundle, and
            the Findings. `null` renders as "not yet classified" (the
            eventual-consistency window, ADR-0024), distinct from a real PUBLIC /
            zero-Findings verdict — and, per `whenLoaded` above, distinct again
            from a read that has not landed. */}
        {section === 'classification' &&
          whenLoaded((payload) => (
            <ClassificationView
              classification={payload.classification}
              labels={labelsFor(payload.classification != null)}
            />
          ))}

        {/* Data lineage (label lineage) for THIS LEG: per carried-in value, its
            origin, the previous hop, and on demand the full chains.
            Its own read (the same per-interaction labels read the
            Classification tab shares), so a failed or unanswered read says so
            here rather than reading as "nothing carried in". The row is pinned
            to this pane's payload hash, as the Classification tab pins it. */}
        {section === 'labels' &&
          (labelsQ.isSuccess ? (
            <LabelLineagePane
              leg={(() => {
                const row = labelsQ.data.byLeg.get(legType) ?? null;
                return row != null && row.payload_hash !== hash ? null : row;
              })()}
              lineageStatus={labelsQ.data.lineageStatus}
              traceId={traceId ?? ''}
              interactionId={interactionId ?? undefined}
            />
          ) : labelsQ.isError ? (
            <div style={{ color: 'var(--dg-color-error)', fontSize: '0.85rem' }}>Failed to load data lineage.</div>
          ) : (
            <Spinner size="md" aria-label={`Loading ${label} data lineage`} />
          ))}
      </div>
    </>
  );
}

/**
 * `LabelLineageTable` with this view's hop links: a hop in this trace selects its
 * interaction here (`?iid=`, the other params kept, the entity selection dropped),
 * the same convention the Classification tab's origin links use. Plain text
 * outside a router.
 */
function LabelLineagePane(props: {
  leg: Parameters<typeof LabelLineageTable>[0]['leg'];
  lineageStatus: Parameters<typeof LabelLineageTable>[0]['lineageStatus'];
  traceId: string;
  interactionId?: string;
}) {
  const routed = useInRouterContext();
  return routed ? <RoutedLabelLineagePane {...props} /> : <LabelLineageTable {...props} />;
}

function RoutedLabelLineagePane(props: Parameters<typeof LabelLineagePane>[0]) {
  const [params] = useSearchParams();
  const links: HopLinks = {
    sameTrace: (hop: LineageHop): To => {
      const next = new URLSearchParams(params);
      next.delete('eid');
      next.set('iid', hop.interaction_id);
      return { search: next.toString() };
    },
  };
  return <LabelLineageTable {...props} links={links} />;
}
