import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { ClassificationView } from './ClassificationView';
import type { Classification } from '../types';

/** A fully-populated verdict fixture; individual tests override slices of it. */
function verdict(over: Partial<Classification> = {}): Classification {
  return {
    sensitivity_level: 'CONFIDENTIAL',
    regulatory_tags: ['PII', 'GDPR'],
    contains_identity_bundle: true,
    is_personalized: true,
    primary_domain: 'person',
    findings: [
      { entity_type: 'EMAIL', start: 5, end: 20, text: 'jo@example.com' },
    ],
    model_version: 1,
    ...over,
  };
}

describe('ClassificationView', () => {
  it('shows the document-level sensitivity level as a badge', () => {
    render(<ClassificationView classification={verdict({ sensitivity_level: 'RESTRICTED' })} />);
    expect(screen.getByText('RESTRICTED')).toBeInTheDocument();
  });

  it('renders a null classification distinctly as "not yet classified", with no sensitivity verdict', () => {
    render(<ClassificationView classification={null} />);
    // The eventual-consistency window (P-classification has not run yet) is
    // shown as a distinct, verdict-less state — not a real PUBLIC verdict.
    expect(screen.getByText(/not yet classified/i)).toBeInTheDocument();
    // Crucially it must NOT read as a PUBLIC / zero-findings verdict: no
    // sensitivity level of any kind is rendered.
    expect(screen.queryByText('PUBLIC')).toBeNull();
    expect(screen.queryByText('RESTRICTED')).toBeNull();
  });

  it('colors the sensitivity badge by level (PUBLIC→green … RESTRICTED→red)', () => {
    const cases: Array<[Classification['sensitivity_level'], string]> = [
      ['PUBLIC', 'pf-m-green'],
      ['INTERNAL', 'pf-m-blue'],
      ['CONFIDENTIAL', 'pf-m-orange'],
      ['RESTRICTED', 'pf-m-red'],
    ];
    for (const [level, colorClass] of cases) {
      const { unmount } = render(
        <ClassificationView classification={verdict({ sensitivity_level: level })} />,
      );
      // The PF Label carries the level-appropriate color modifier so the
      // escalation PUBLIC→RESTRICTED reads at a glance.
      const badge = screen.getByText(level).closest('.pf-v5-c-label');
      expect(badge).toHaveClass(colorClass);
      unmount();
    }
  });

  it('lists the regulatory tags the payload carries', () => {
    render(
      <ClassificationView
        classification={verdict({ regulatory_tags: ['PII', 'GDPR', 'PCI'] })}
      />,
    );
    expect(screen.getByText('PII')).toBeInTheDocument();
    expect(screen.getByText('GDPR')).toBeInTheDocument();
    expect(screen.getByText('PCI')).toBeInTheDocument();
  });

  it('does not render a regulatory-tags section when there are none', () => {
    render(<ClassificationView classification={verdict({ regulatory_tags: [] })} />);
    expect(screen.queryByText(/regulatory tags/i)).toBeNull();
  });

  it('shows an identity-bundle indicator when the payload holds one', () => {
    render(<ClassificationView classification={verdict({ contains_identity_bundle: true })} />);
    expect(screen.getByText(/identity bundle/i)).toBeInTheDocument();
  });

  it('omits the identity-bundle indicator when the payload holds none', () => {
    render(<ClassificationView classification={verdict({ contains_identity_bundle: false })} />);
    expect(screen.queryByText(/identity bundle/i)).toBeNull();
  });

  it('lists each finding with its detected type (NER tag) and flagged text region', () => {
    render(
      <ClassificationView
        classification={verdict({
          findings: [
            { entity_type: 'SSN', start: 10, end: 21, text: '123-45-6789' },
            { entity_type: 'EMAIL', start: 30, end: 44, text: 'jo@example.com' },
          ],
        })}
      />,
    );
    const findings = screen.getByLabelText('Findings');
    // Each finding's detected NER-tag type is shown …
    expect(within(findings).getByText('SSN')).toBeInTheDocument();
    expect(within(findings).getByText('EMAIL')).toBeInTheDocument();
    // … alongside the flagged text region it detected.
    expect(within(findings).getByText('123-45-6789')).toBeInTheDocument();
    expect(within(findings).getByText('jo@example.com')).toBeInTheDocument();
  });

  it('shows a "no findings" note for a real verdict that detected nothing', () => {
    render(<ClassificationView classification={verdict({ findings: [] })} />);
    // A zero-Findings verdict is a real result, distinct from the null "not yet
    // classified" state — say so explicitly rather than rendering nothing.
    expect(screen.getByText(/no findings/i)).toBeInTheDocument();
  });

  it('distinguishes a real PUBLIC / zero-findings verdict from "not yet classified"', () => {
    render(
      <ClassificationView
        classification={verdict({
          sensitivity_level: 'PUBLIC',
          regulatory_tags: [],
          contains_identity_bundle: false,
          findings: [],
        })}
      />,
    );
    // A clean payload is a real PUBLIC verdict (CONTEXT.md), NOT the null state.
    expect(screen.getByText('PUBLIC')).toBeInTheDocument();
    expect(screen.queryByText(/not yet classified/i)).toBeNull();
  });
});
