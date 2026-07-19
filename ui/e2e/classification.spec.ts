import { test, expect, type Page } from '@playwright/test';

/**
 * Rendered-view coverage (ADR-0019) for the Classification verdict in the flow
 * view's Req/Resp payload cells (issue #80). Unlike the shell smoke specs, this
 * drives the real production bundle end-to-end with the `/api/` responses
 * stubbed via `page.route`: recent-traces → trace detail → Interaction flow →
 * select the interaction → expand the request payload → assert the inlined
 * Classification (sensitivity badge + regulatory tags + identity bundle +
 * Findings) renders, and that a null verdict reads as "not yet classified".
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

const INTERACTIONS = [
  {
    id: IID, caller_entity_id: 'e1', callee_entity_id: 'e2',
    started_at: '2026-05-01T12:00:00Z', ended_at: '2026-05-01T12:00:01Z',
    error: false, request_payload_hash: REQ_HASH, response_payload_hash: null,
    summary: 'agent calls search', parent_interaction_id: null,
    span_count: 2, anchor_count: 1,
  },
];

const INTERACTION_EVIDENCE = [
  { span_id: 'span-xyz', role: 'anchor', parent_id: 'root-span', kind: 'CLIENT', service_name: 'svc' },
];

/** Stub every `/api/` call the flow view makes; the payload's classification
 *  slice is caller-supplied so one helper serves both the populated and the
 *  null-verdict cases. */
async function stubFlowApi(page: Page, classification: unknown) {
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
    if (url.includes(`/api/traces/${TID}/interactions`)) {
      return route.fulfill(json({ interactions: INTERACTIONS }));
    }
    if (url.includes(`/api/traces/${TID}/entities`)) {
      return route.fulfill(json({ entities: ENTITIES }));
    }
    if (url.includes(`/api/payloads/${REQ_HASH}`)) {
      return route.fulfill(
        json({
          content_hash: REQ_HASH, content_kind: 'json',
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

/** Deep-link to the flow tab, select the interaction, and expand its request
 *  payload — the shared drive-through both tests need. */
async function openRequestPayload(page: Page) {
  await page.goto(`/ui/traces/${TID}/flow`);
  await page.getByText(/2 \(1 anchor\)/).click();
  await page.getByRole('button', { name: /Request: reqhash0/i }).click();
}

test('the flow view renders the payload Classification verdict on expand', async ({ page }) => {
  await stubFlowApi(page, {
    sensitivity_level: 'CONFIDENTIAL',
    regulatory_tags: ['PII'],
    contains_identity_bundle: true,
    is_personalized: true,
    primary_domain: 'person',
    findings: [{ entity_type: 'EMAIL', start: 8, end: 22, text: 'jo@example.com' }],
    model_version: 1,
  });
  await openRequestPayload(page);

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
  await openRequestPayload(page);

  await expect(page.getByText(/not yet classified/i)).toBeVisible();
  await expect(page.getByText('PUBLIC')).toHaveCount(0);
});
