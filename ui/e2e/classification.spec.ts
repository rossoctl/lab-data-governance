import { test, expect, type Page } from '@playwright/test';

/**
 * Rendered-view coverage (ADR-0019) for the Classification verdict in the flow
 * view's Req/Resp payload cells (issue #80). Unlike the shell smoke specs, this
 * drives the real production bundle end-to-end with the `/api/` responses
 * stubbed via `page.route`: recent-traces → trace detail → Interactions and Entities →
 * select the interaction → activate the request leg's Classification tab →
 * assert the inlined Classification (sensitivity badge + regulatory tags +
 * identity bundle + Findings) renders, and that a null verdict reads as "not yet
 * classified".
 */

const TID = 'trace-classify-demo';
const IID = 'ix-1';
const REQ_HASH = 'reqhash0deadbeef';

const LISTING_ROOT = {
  seq: 1,
  trace_id: TID,
  span_id: 'root-span',
  parent_id: null,
  name: 'root',
  started_at: '2026-05-01T12:00:00Z',
  attributes: {},
  observed_at: '2026-05-01T12:00:00Z',
  arrival_seq: 1,
  in_time_window: true,
  service_name: 'svc',
  kind: 'SERVER',
  error: false,
  status_message: null,
  events: null,
  links: null,
  ended_at: '2026-05-01T12:00:01Z',
  otlp: null,
  scope: null,
  resource_attributes: null,
};

const ENTITIES = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
];

/**
 * The `GET /api/traces/{tid}/interactions` element, verbatim as
 * `retrieval.InteractionView` serializes it (data_governance/retrieval/
 * interactions.py).
 *
 * ADR-0025 split the interaction into a parent identity row plus nested
 * request/response **legs**: timing, payload hash and error are leg facts, so
 * there is NO top-level `started_at` / `ended_at` / `error` /
 * `request_payload_hash` / `response_payload_hash` on the wire any more. The UI
 * reads them exclusively through `legOfType(ix, 'request'|'response')`
 * (ui/src/lib/flow.ts), so re-adding a top-level hash here would stub a field
 * nothing reads: the detail panel would render `request_at —` with no leg block
 * at all, and the `Request: Classification` button these tests click would never
 * exist. The read-time derivations `duration_seconds` (response − request, null
 * while the response is in flight) and `any_error` (leg roll-up) are computed by
 * the API, not by the client, so they are part of the stub too.
 *
 * Only the request leg carries a `payload_hash`: the response leg's is null, so
 * the panel renders exactly one outer leg tab and `Request: …` names its inner
 * section tabs unambiguously.
 */
const INTERACTIONS = [
  {
    id: IID, caller_entity_id: 'e1', callee_entity_id: 'e2',
    summary: 'agent calls search', parent_interaction_id: null,
    legs: [
      { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: REQ_HASH, error: false, seq: 1 },
      { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: 2 },
    ],
    duration_seconds: 1, any_error: false,
    span_count: 2, anchor_count: 1,
  },
];

// Span evidence for the interaction's `/spans` sub-resource. `leg_type` is on
// the row since ADR-0025 (`retrieval.SpanEvidenceView`) — an anchor span
// evidences the request leg here.
const INTERACTION_EVIDENCE = [
  { span_id: 'span-xyz', role: 'anchor', parent_id: 'root-span', kind: 'CLIENT', service_name: 'svc', leg_type: 'request' },
];

/** Nothing carried in, lineage derived: the Classification tab is the
 *  classifier's verdict alone. */
const NO_LABELS = { legs: [], lineage_status: 'complete' };

/** Stub every `/api/` call the flow view makes; the payload's classification
 *  slice is caller-supplied so one helper serves both the populated and the
 *  null-verdict cases, and so is the labels read (found here / carried in). */
async function stubFlowApi(page: Page, classification: unknown, labels: unknown = NO_LABELS) {
  const json = (body: unknown) => ({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes(`/api/traces/${TID}/interactions/${IID}/spans`)) {
      return route.fulfill(json({ spans: INTERACTION_EVIDENCE }));
    }
    // Before the generic `/interactions` branch, which would answer it with
    // `{interactions}`.
    if (url.includes(`/api/traces/${TID}/interactions/${IID}/labels`)) {
      return route.fulfill(json(labels));
    }
    if (url.includes(`/api/traces/${TID}/interactions`)) {
      return route.fulfill(json({ interactions: INTERACTIONS }));
    }
    if (url.includes(`/api/traces/${TID}/entities`)) {
      return route.fulfill(json({ entities: ENTITIES }));
    }
    // The flow view reads trace-scoped Data lineage once on first paint (ADR-0028,
    // issue #120). These tests are about Classification, so the leg has no
    // derived lineage — spelled the way the server does: the leg row is present
    // with a null `lineage` (the eventual-consistency window) and coverage is
    // unknown, which must NOT be reported as 'complete'.
    if (url.includes(`/api/traces/${TID}/data-lineage`)) {
      return route.fulfill(
        json({
          legs: [
            { interaction_id: IID, leg_type: 'request', payload_hash: REQ_HASH, lineage: null },
            { interaction_id: IID, leg_type: 'response', payload_hash: null, lineage: null },
          ],
          status: null,
          stopped_at_seq: null,
        }),
      );
    }
    if (url.includes(`/api/payloads/${REQ_HASH}`)) {
      return route.fulfill(
        json({
          content_hash: REQ_HASH,
          content: { note: 'contact jo@example.com' }, byte_size: 42,
          classification,
        }),
      );
    }
    // The span tree stays mounted (hidden) under the flow tab and pages the
    // root's children; unwrapping `r.spans` on a bare `{}` would throw and blank
    // the page, so any `/spans` path returns an empty span list.
    if (url.includes('/spans')) {
      return route.fulfill(json({ spans: [] }));
    }
    if (url.includes(`/api/traces/${TID}`)) {
      return route.fulfill(
        json({ trace_id: TID, listing_root: LISTING_ROOT, counts: null, in_time_window: true }),
      );
    }
    return route.fulfill(json({}));
  });
}

/**
 * Deep-link to the flow tab, select the interaction, and activate the request
 * leg's **Classification** tab — the shared drive-through both tests need.
 *
 * The `Request` outer tab now carries three inner section tabs (Payload /
 * Classification / Data lineage), so the verdict has its own tab rather than
 * riding on the payload body's. Clicking Classification is also the end-to-end
 * proof of the fetch coupling in the real bundle: the verdict is an inlined field
 * of `GET /api/payloads/{hash}` (ADR-0024), so activating that tab must be enough
 * to make the stubbed payload response arrive and render.
 */
async function openRequestClassification(page: Page) {
  await page.goto(`/ui/traces/${TID}/flow`);
  await page.getByText(/2 \(1 anchor\)/).click();
  // The leg tab is active by default (it is the only leg with a payload); pick
  // its Classification section.
  await expect(page.getByRole('tab', { name: 'Request', exact: true })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page.getByRole('tab', { name: /Request: Classification/i }).click();
}

test('the flow view renders the payload Classification verdict on its tab', async ({ page }) => {
  await stubFlowApi(page, {
    sensitivity_level: 'CONFIDENTIAL',
    regulatory_tags: ['PII'],
    contains_identity_bundle: true,
    is_personalized: true,
    primary_domain: 'person',
    findings: [{ entity_type: 'EMAIL', start: 8, end: 22, text: 'jo@example.com' }],
    model_version: 1,
  });
  await openRequestClassification(page);

  // Sensitivity level badge + regulatory tags + identity-bundle indicator.
  await expect(page.getByText('CONFIDENTIAL')).toBeVisible();
  await expect(page.getByText('PII')).toBeVisible();
  await expect(page.getByText(/identity bundle/i)).toBeVisible();
  // The Finding's detected type (NER tag) + the flagged text region.
  const findings = page.getByLabel('Findings');
  await expect(findings.getByText('EMAIL')).toBeVisible();
  await expect(findings.getByText('jo@example.com')).toBeVisible();
});

test('a null classification renders as "not yet classified", not a PUBLIC verdict', async ({ page }) => {
  await stubFlowApi(page, null);
  await openRequestClassification(page);

  await expect(page.getByText(/not yet classified/i)).toBeVisible();
  await expect(page.getByText('PUBLIC')).toHaveCount(0);
});

test('labels carried in show beside what the classifier found here', async ({ page }) => {
  await stubFlowApi(
    page,
    {
      sensitivity_level: 'INTERNAL',
      regulatory_tags: ['PI'],
      contains_identity_bundle: false,
      is_personalized: false,
      primary_domain: 'person',
      findings: [{ entity_type: 'OPID', start: 16, end: 25, text: 'BK-2850EB' }],
      model_version: 2,
    },
    {
      lineage_status: 'complete',
      legs: [
        {
          leg_type: 'request', payload_hash: REQ_HASH, seq: 1,
          found: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] },
          carried: { max_level: 'RESTRICTED', regulatory_tags: ['PI', 'PII'], origin_count: 2 },
          labels: [
            {
              text: 'BK-2850EB', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED',
              regulatory_tags: ['PII'], found_here: false,
              carried_in: [{
                origin_interaction_id: 'ix-0', origin_leg_type: 'response', origin_seq: 90574,
                caller: 'booking-agent', callee: 'create-booking',
                unit_text: 'BK-2850EB', unit_value: 'BK-2850EB', match_kind: 'whole', origin_trace_id: null,
              }],
            },
            {
              text: 'BK-2850EB', start: 16, end: 25, entity_type: 'OPID', sensitivity_level: 'INTERNAL',
              regulatory_tags: ['PI'], found_here: true,
              carried_in: [{
                origin_interaction_id: 'ix-00', origin_leg_type: 'response', origin_seq: 91707,
                caller: 'booking-agent', callee: 'qwen2.5:7b',
                unit_text: 'BK-2850EB', unit_value: 'BK-2850EB', match_kind: 'whole', origin_trace_id: null,
              }],
            },
          ],
        },
      ],
    },
  );
  await openRequestClassification(page);

  await expect(page.getByText('Found here (classifier)')).toBeVisible();
  await expect(page.getByText('Carried in (label lineage)')).toBeVisible();
  await expect(page.getByText('from 2 earlier steps')).toBeVisible();
  const labels = page.getByLabel('Labels');
  await expect(labels.getByText('PID', { exact: true })).toBeVisible();
  await expect(labels.getByText('booking-agent → create-booking, response #90574')).toBeVisible();
});

test('the Label lineage tab shows where a carried-in value came from, its chains on demand', async ({ page }) => {
  const hop = (seq: number, role: string, iid: string, leg: string, text: string, start: number, end: number) => ({
    interaction_id: iid, leg_type: leg, seq, trace_id: TID, caller: 'booking-agent', callee: 'create-booking',
    text, start, end, role,
    excerpt: role === 'origin' ? { text: '{"id": "BK-2850EB", "guest_name": "<name>"}', start: 8, end: 17 } : null,
  });
  await stubFlowApi(
    page,
    {
      sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], contains_identity_bundle: false,
      is_personalized: false, primary_domain: 'person', findings: [], model_version: 2,
    },
    {
      lineage_status: 'complete',
      legs: [{
        leg_type: 'request', payload_hash: REQ_HASH, seq: 1,
        found: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] },
        carried: { max_level: 'RESTRICTED', regulatory_tags: ['PII'], origin_count: 1 },
        labels: [{
          text: 'BK-2850EB', start: 16, end: 25, entity_type: 'PID', sensitivity_level: 'RESTRICTED',
          regulatory_tags: ['PII'], found_here: false,
          carried_in: [{
            origin_interaction_id: 'ix-0', origin_leg_type: 'response', origin_seq: 90574,
            caller: 'booking-agent', callee: 'create-booking',
            unit_text: 'BK-2850EB', unit_value: 'BK-2850EB', match_kind: 'whole', origin_trace_id: null,
            chains: [
              [hop(90574, 'origin', 'ix-0', 'response', 'BK-2850EB', 8, 17), hop(1, 'here', IID, 'request', 'BK-2850EB', 16, 25)],
              [hop(90574, 'origin', 'ix-0', 'response', 'BK-2850EB', 8, 17), hop(90600, 'hop', 'ix-llm', 'response', 'BK-2850EB', 0, 9),
                hop(1, 'here', IID, 'request', 'BK-2850EB', 16, 25)],
            ],
          }],
        }],
      }],
    },
  );
  await openRequestClassification(page);
  // The Classification tab stays the classifier's plus the carried-in line; the chains are on their own tab.
  await expect(page.getByText('Carried in (label lineage)')).toBeVisible();
  await expect(page.getByTestId('label-lineage')).toHaveCount(0);
  await page.getByRole('tab', { name: /Request: Label lineage/i }).click();

  const table = page.getByTestId('label-lineage-table');
  await expect(table).toBeVisible();
  // Born at and the previous hop are links that select the interaction on this view.
  await expect(table.getByRole('link', { name: /#90574/ }).first()).toHaveAttribute('href', /\?iid=ix-0$/);
  await expect(table.getByRole('link', { name: /#90600/ })).toHaveAttribute('href', /\?iid=ix-llm$/);
  // The chains open on demand: born (the origin payload, unit marked), the carriers, here.
  await table.getByRole('button', { name: 'Show 2 chains' }).click();
  const lineage = page.getByTestId('label-lineage');
  await expect(lineage.getByText(/^born/).first()).toBeVisible();
  await expect(lineage.getByTestId('origin-excerpt').first().locator('mark')).toHaveText('BK-2850EB');
  await expect(lineage.getByRole('list', { name: 'Lineage chain' })).toHaveCount(1);
  await lineage.getByRole('button', { name: '1 more chain' }).click();
  await expect(lineage.getByRole('list', { name: 'Lineage chain' })).toHaveCount(2);
});
