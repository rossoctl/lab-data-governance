import { test, expect, type Page } from '@playwright/test';

/**
 * The risk layer on the trace page, driven through the real
 * production bundle with `/api/` and `/risk/` stubbed: `?risk=1` lands with the
 * layer on and the stepper on the right event; the two cross links between the
 * trace page and the risk trace page (kept for a transition period) carry the
 * open event and the selection, translating `?interaction=` ↔ `?iid=`.
 */

const TID = 'trace-risk-layer';

const LISTING_ROOT = {
  seq: 1, trace_id: TID, span_id: 'root-span', parent_id: null, name: 'root', started_at: '2026-05-01T12:00:00Z',
  attributes: {}, observed_at: '2026-05-01T12:00:00Z', arrival_seq: 1, in_time_window: true, service_name: 'svc',
  kind: 'SERVER', error: false, status_message: null, events: null, links: null, ended_at: '2026-05-01T12:00:01Z',
  otlp: null, scope: null, resource_attributes: null,
};
const ENTITIES = [
  { id: 'e1', kind: 'agent', natural_key: 'agent:(p,a)', display_name: 'agent-a', detected_from: 'span' },
  { id: 'e2', kind: 'tool', natural_key: 'tool:(p,svc)', display_name: 'search', detected_from: 'span' },
  { id: 'e3', kind: 'external', natural_key: 'external:(p,partner)', display_name: 'partner', detected_from: 'span' },
];
function legs(seq: number) {
  return [
    { leg_type: 'request', occurred_at: '2026-05-01T12:00:00Z', payload_hash: 'h1', error: false, seq },
    { leg_type: 'response', occurred_at: '2026-05-01T12:00:01Z', payload_hash: null, error: false, seq: seq + 1 },
  ];
}
const INTERACTIONS = [
  { id: 'i0', caller_entity_id: 'e1', callee_entity_id: 'e2', summary: 'agent calls search', parent_interaction_id: null, legs: legs(1), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
  { id: 'i1', caller_entity_id: 'e2', callee_entity_id: 'e3', summary: 'search calls partner', parent_interaction_id: 'i0', legs: legs(3), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
  { id: 'i2', caller_entity_id: 'e1', callee_entity_id: 'e3', summary: 'agent calls partner', parent_interaction_id: null, legs: legs(5), duration_seconds: 1, any_error: false, span_count: 2, anchor_count: 1 },
];
function risk(interactionId: string, overrides: Record<string, unknown> = {}) {
  return {
    interaction_risk_id: `ir-${interactionId}`, interaction_id: interactionId, trace_id: TID, parent_interaction_id: null,
    caller_entity_id: 'e1', callee_entity_id: 'e2', version: 1, computed_at: '2026-05-01T12:00:00Z',
    risk_level: 'low', enforcement_type: null, policy_event_count: 0, triggered_rule_ids: [], classification_summary: null,
    opa_policy_versions_used: ['v1'], overall_confidence: 0.9, ...overrides,
  };
}
const FOREST = INTERACTIONS.map((ix) => ({
  interaction_id: ix.id, trace_id: TID, parent_interaction_id: ix.parent_interaction_id,
  caller_entity_id: ix.caller_entity_id, callee_entity_id: ix.callee_entity_id, summary: ix.summary,
  legs: ix.legs.map(({ leg_type, occurred_at, payload_hash, error }) => ({ leg_type, occurred_at, payload_hash, error })),
  risk:
    ix.id === 'i1'
      ? risk('i1', { risk_level: 'critical', enforcement_type: 'block', policy_event_count: 1, triggered_rule_ids: ['DG-001'] })
      : ix.id === 'i2'
        ? risk('i2', { risk_level: 'high', enforcement_type: 'flag', policy_event_count: 1, triggered_rule_ids: ['DG-002'] })
        : risk('i0'),
  span_count: ix.span_count, anchor_count: ix.anchor_count,
}));
const RULES = ['DG-001', 'DG-002'].map((id) => ({
  rule_id: id, rule_name: `Rule ${id}`, categories: ['privacy'], risk_level: 'critical', enforcement: 'block',
  explanation: `${id} explains.`, event_type: 'tool_call', data_items: [], data_destinations: [], allowed_actions: ['block'], rule_sources: [],
}));
const ALERT = {
  alert_id: 'a1', trace_id: TID, risk_level: 'critical', enforcement_type: 'block', title: 'PII left the boundary',
  description: 'desc', status: 'open', duplicate_count: 0, superseded_by: null, triggered_rule_ids: ['DG-001'],
  timestamp: '2026-05-01T12:00:02Z',
};

async function stubApis(page: Page) {
  const json = (body: unknown) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/risk/**', async (route) => {
    // Only the API calls: the page's own URL (`/ui/risk/traces/…`) matches this glob too.
    if (!['fetch', 'xhr'].includes(route.request().resourceType())) return route.continue();
    const url = route.request().url();
    if (url.includes('/risk/rules')) return route.fulfill(json({ items: RULES, next_cursor: null }));
    if (url.includes('/risk/alerts')) return route.fulfill(json({ items: [ALERT], next_cursor: null }));
    if (url.includes(`/risk/traces/${TID}`)) return route.fulfill(json({ trace_risk: { trace_id: TID }, interactions: FOREST }));
    if (url.includes('/risk/metrics')) return route.fulfill(json({}));
    return route.fulfill(json({ items: [], next_cursor: null }));
  });
  await page.route('**/api/**', async (route) => {
    const url = route.request().url();
    if (url.includes('/labels')) return route.fulfill(json({ legs: [], lineage_status: 'complete' }));
    if (/\/interactions\/[^/]+\/spans/.test(url)) return route.fulfill(json({ spans: [] }));
    if (url.includes(`/api/traces/${TID}/interactions`)) return route.fulfill(json({ interactions: INTERACTIONS }));
    if (url.includes(`/api/traces/${TID}/entities`)) return route.fulfill(json({ entities: ENTITIES }));
    if (url.includes('/data-lineage')) return route.fulfill(json({ legs: [], status: 'complete', stopped_at_seq: null }));
    if (url.includes('/spans')) return route.fulfill(json({ spans: [] }));
    if (url.includes('/events')) return route.fulfill({ status: 503, body: '' });
    if (url.includes(`/api/traces/${TID}`)) {
      return route.fulfill(json({ trace_id: TID, listing_root: LISTING_ROOT, counts: null, in_time_window: true }));
    }
    return route.fulfill(json({}));
  });
}

test('?risk=1 lands with the layer on, the alerts above the graph and the stepper on the linked event', async ({ page }) => {
  await stubApis(page);
  await page.goto(`/ui/traces/${TID}/graph?risk=1&violation=2`);
  await expect(page.getByRole('checkbox', { name: 'Risk' })).toBeChecked();
  await expect(page.getByRole('heading', { name: 'Alerts' })).toBeVisible();
  await expect(page.getByText('PII left the boundary')).toBeVisible();
  await expect(page.getByTestId('execution-flow-graph')).toBeVisible();
  const layer = page.getByTestId('risk-layer');
  await expect(layer.getByText('2 of 2')).toBeVisible();
  await expect(layer.getByTestId('policy-decision-panel').getByText('agent calls partner')).toBeVisible();
  // Previous moves the stepper and the URL together; the default position is deleted.
  await layer.getByRole('button', { name: 'Previous' }).click();
  await expect(page).toHaveURL(new RegExp(`/ui/traces/${TID}/graph\\?risk=1$`));
  await expect(page.getByTestId('risk-layer').getByText('1 of 2')).toBeVisible();
  await expect(page.getByTestId('risk-layer').getByTestId('policy-decision-panel').getByText('search calls partner')).toBeVisible();
  // Off: the layer's cards and the alerts go, the URL is the plain view. (PF's
  // switch input is visually hidden under its toggle, so the label is the target.)
  await page.locator('label[for="risk-layer"]').click();
  await expect(page.getByRole('checkbox', { name: 'Risk' })).not.toBeChecked();
  await expect(page).toHaveURL(new RegExp(`/ui/traces/${TID}/graph$`));
  await expect(page.getByTestId('risk-layer')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Alerts' })).toHaveCount(0);
});

test('the two cross links carry the open event and the selection, translating ?interaction= and ?iid=', async ({ page }) => {
  await stubApis(page);
  // Risk page → trace page with the layer on.
  await page.goto(`/ui/risk/traces/${TID}?violation=2&interaction=i0`);
  await expect(page.getByTestId('interaction-panel').getByText('agent calls search')).toBeVisible();
  await page.getByRole('link', { name: /Open in trace view with the risk layer/ }).click();
  await expect(page).toHaveURL(new RegExp(`/ui/traces/${TID}/graph\\?risk=1&violation=2&iid=i0$`));
  await expect(page.getByRole('checkbox', { name: 'Risk' })).toBeChecked();
  const layer = page.getByTestId('risk-layer');
  await expect(layer.getByTestId('interaction-panel').getByText('agent calls search')).toBeVisible();
  await expect(layer.getByText('low')).toBeVisible();
  // Trace page → risk page: the same reading, spelled that page's way.
  await page.getByRole('link', { name: 'Risk page for this trace' }).click();
  await expect(page).toHaveURL(new RegExp(`/ui/risk/traces/${TID}\\?violation=2&interaction=i0$`));
  await expect(page.getByTestId('interaction-panel').getByText('agent calls search')).toBeVisible();
  await expect(page.getByText('2 of 2')).toBeVisible();
});
