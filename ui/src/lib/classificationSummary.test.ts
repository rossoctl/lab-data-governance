import { describe, it, expect } from 'vitest';
import {
  classifiedLegs,
  regulatoryTagsOf,
  classificationLevelsOf,
  edgeTagLabel,
  EDGE_TAG_MAX_TAGS,
} from './classificationSummary';

// These three functions moved here from `risk-components/PolicyDecisionPanel.tsx`
// (issue #170 follow-up), where they were previously covered only INDIRECTLY
// through that component's rendered "none"/tag-text assertions
// (`PolicyDecisionPanel.test.tsx`). Extraction is the moment to cover the
// wire-shape narrowing directly, including the both-legs union/dedup case
// that indirect coverage never exercised.
describe('classifiedLegs / regulatoryTagsOf / classificationLevelsOf', () => {
  it('returns [] for a null summary', () => {
    expect(classifiedLegs(null)).toEqual([]);
    expect(regulatoryTagsOf(null)).toEqual([]);
    expect(classificationLevelsOf(null)).toEqual([]);
  });

  it('ignores a leg whose value is not an object', () => {
    const summary = { request: 'not-an-object' };
    expect(classifiedLegs(summary)).toEqual([]);
    expect(regulatoryTagsOf(summary)).toEqual([]);
  });

  it('ignores a classification_pending leg', () => {
    const summary = { request: { classification_pending: true } };
    expect(regulatoryTagsOf(summary)).toEqual([]);
    expect(classificationLevelsOf(summary)).toEqual([]);
  });

  it('ignores a payload-less leg', () => {
    const summary = { request: { payload: null } };
    expect(regulatoryTagsOf(summary)).toEqual([]);
    expect(classificationLevelsOf(summary)).toEqual([]);
  });

  it('treats an absent leg key the same as a pending/payload-less one', () => {
    expect(regulatoryTagsOf({})).toEqual([]);
    expect(classificationLevelsOf({})).toEqual([]);
  });

  it('extracts tags and level from a single classified leg', () => {
    const summary = { request: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII', 'GDPR'] } };
    expect(regulatoryTagsOf(summary)).toEqual(['PII', 'GDPR']);
    expect(classificationLevelsOf(summary)).toEqual(['RESTRICTED']);
  });

  it('unions and dedupes tags across both legs, first-seen order', () => {
    const summary = {
      request: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['PII', 'GDPR'] },
      response: { sensitivity_level: 'RESTRICTED', regulatory_tags: ['GDPR', 'HIPAA'] },
    };
    expect(regulatoryTagsOf(summary)).toEqual(['PII', 'GDPR', 'HIPAA']);
  });

  it('collects distinct sensitivity levels across both legs, deduplicated', () => {
    const summary = {
      request: { sensitivity_level: 'INTERNAL', regulatory_tags: [] },
      response: { sensitivity_level: 'RESTRICTED', regulatory_tags: [] },
    };
    expect(classificationLevelsOf(summary)).toEqual(['INTERNAL', 'RESTRICTED']);
  });

  it('drops non-string entries inside regulatory_tags rather than throwing', () => {
    const summary = { request: { regulatory_tags: ['PII', 42, null, 'GDPR'] } };
    expect(regulatoryTagsOf(summary)).toEqual(['PII', 'GDPR']);
  });

  it('treats a non-array regulatory_tags as no tags', () => {
    expect(regulatoryTagsOf({ request: { regulatory_tags: 'PII' } })).toEqual([]);
  });

  it('treats a non-string sensitivity_level as no level', () => {
    expect(classificationLevelsOf({ request: { sensitivity_level: 42 } })).toEqual([]);
  });
});

describe('edgeTagLabel', () => {
  it('returns an empty string for no tags — deliberately not "none" (see docstring)', () => {
    // A graph mark is a claim; an empty tag draws no `<g>` at all (`showTag`'s
    // truthiness gate in ExecutionFlowGraph.tsx), which is the point: "none"
    // would assert a verdict the data may not support (classification could
    // simply be pending). This diverges from `joinOrNone`'s "none" fallback
    // used elsewhere in this codebase — intentionally, per this divergence
    // being documented on both sides (see the back-reference comment beside
    // PolicyDecisionPanel's regulatory-tags row).
    expect(edgeTagLabel([])).toBe('');
  });

  it('returns the single tag unmodified when there is exactly one', () => {
    expect(edgeTagLabel(['PII'])).toBe('PII');
  });

  it('joins two tags with no overflow indicator — exactly at the cap', () => {
    expect(edgeTagLabel(['PII', 'GDPR'])).toBe('PII, GDPR');
  });

  it('caps at EDGE_TAG_MAX_TAGS and appends a +1 overflow indicator for one extra tag', () => {
    expect(edgeTagLabel(['PII', 'GDPR', 'HIPAA'])).toBe('PII, GDPR +1');
  });

  it('caps at EDGE_TAG_MAX_TAGS and appends a +2 overflow indicator for two extra tags', () => {
    expect(edgeTagLabel(['PII', 'GDPR', 'HIPAA', 'PCI'])).toBe('PII, GDPR +2');
  });

  it('shows exactly EDGE_TAG_MAX_TAGS tags before any overflow indicator appears', () => {
    // Asserted against the exported constant, not a hardcoded `2` — so a
    // future change to the cap doesn't leave this test silently checking the
    // wrong number.
    expect(EDGE_TAG_MAX_TAGS).toBe(2);
    const shown = edgeTagLabel(['PII', 'GDPR', 'HIPAA']).split(' +')[0].split(', ');
    expect(shown).toHaveLength(EDGE_TAG_MAX_TAGS);
  });
});
