import { describe, it, expect } from 'vitest';
import {
  classifiedLegs,
  classifiedLegEntries,
  regulatoryTagsOf,
  classificationLevelsOf,
  edgeTagLabel,
  EDGE_TAG_MAX_TAGS,
  carriedOf,
  carriedOfSummary,
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

// Label lineage's `inherited` block (`risk/engine/utils.py`'s
// `classification_summary()`): what a leg carried in, apart from what the
// classifier found here. Shaped on the live partner leg (trace e06cd67d…,
// notify-partner → api.travel-partner.example:8000, request #92738).
describe('carriedOf / carriedOfSummary', () => {
  const INHERITED = { count: 5, kinds: ['whole'], max_level: 'RESTRICTED', recombined: false, regulatory_tags: ['PI', 'PII'] };

  it('reads the block on a classified leg, leaving count out', () => {
    const leg = { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'], inherited: INHERITED };
    expect(carriedOf(leg)).toEqual({ tags: ['PI', 'PII'], level: 'RESTRICTED' });
  });

  it('reads the block on a pending leg and on a payload-less leg', () => {
    // Through `classifiedLegEntries`, the way the panels reach a leg.
    const legs = classifiedLegEntries({
      request: { classification_pending: true, inherited: INHERITED },
      response: { payload: null, inherited: INHERITED },
    });
    expect(legs.map(([, leg]) => carriedOf(leg))).toEqual([
      { tags: ['PI', 'PII'], level: 'RESTRICTED' },
      { tags: ['PI', 'PII'], level: 'RESTRICTED' },
    ]);
  });

  it('returns null when the block is absent, not an object, or names nothing', () => {
    expect(carriedOf({ sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] })).toBeNull();
    expect(carriedOf({ inherited: 'PII' })).toBeNull();
    expect(carriedOf({ inherited: null })).toBeNull();
    expect(carriedOf({ inherited: { count: 5, regulatory_tags: [] } })).toBeNull();
  });

  it('drops non-string tags and a non-string max_level', () => {
    expect(carriedOf({ inherited: { regulatory_tags: ['PII', 42, null], max_level: 3 } })).toEqual({
      tags: ['PII'],
      level: null,
    });
  });

  it('returns null for a null summary, an absent leg, or legs without the block', () => {
    expect(carriedOfSummary(null)).toBeNull();
    expect(carriedOfSummary({})).toBeNull();
    expect(carriedOfSummary({ request: { sensitivity_level: 'INTERNAL', regulatory_tags: ['PI'] } })).toBeNull();
  });

  it('unions tags first-seen across both legs and keeps the highest level', () => {
    const summary = {
      request: { inherited: { regulatory_tags: ['PII', 'PI'], max_level: 'CONFIDENTIAL' } },
      response: { classification_pending: true, inherited: { regulatory_tags: ['PI', 'PCI'], max_level: 'RESTRICTED' } },
    };
    expect(carriedOfSummary(summary)).toEqual({ tags: ['PII', 'PI', 'PCI'], level: 'RESTRICTED' });
  });

  it('orders levels PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED, whatever the leg order', () => {
    const at = (a: string, b: string) =>
      carriedOfSummary({ request: { inherited: { max_level: a } }, response: { inherited: { max_level: b } } })?.level;
    expect(at('RESTRICTED', 'INTERNAL')).toBe('RESTRICTED');
    expect(at('PUBLIC', 'CONFIDENTIAL')).toBe('CONFIDENTIAL');
    expect(at('INTERNAL', 'PUBLIC')).toBe('INTERNAL');
    // A level off the ladder never hides a known one, but shows when alone.
    expect(at('SECRET-ISH', 'PUBLIC')).toBe('PUBLIC');
    expect(carriedOfSummary({ request: { inherited: { max_level: 'SECRET-ISH' } } })?.level).toBe('SECRET-ISH');
  });
});
