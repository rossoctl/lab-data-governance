import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EnforcementChip } from './EnforcementChip';

const ALL_ENFORCEMENT_TYPES = [
  'block',
  'quarantine',
  'require_approval',
  'redact',
  'mask',
  'anonymize',
  'encrypt',
  'escalate',
  'notify',
  'warn',
  'log_only',
  'audit',
  'allow',
];

describe('EnforcementChip', () => {
  it.each(ALL_ENFORCEMENT_TYPES)('renders %s without throwing', (type) => {
    expect(() => render(<EnforcementChip type={type} />)).not.toThrow();
    expect(screen.getByText(type)).toBeInTheDocument();
  });

  it('renders an unlisted type as grey without throwing', () => {
    expect(() => render(<EnforcementChip type="brand_new_type" />)).not.toThrow();
    expect(screen.getByText('brand_new_type')).toBeInTheDocument();
  });

  it('carries the shared bordered-label class', () => {
    render(<EnforcementChip type="block" />);
    const label = screen.getByText('block').closest('.pf-v5-c-label') as HTMLElement;
    expect(label).toHaveClass('dg-ent-pill', 'pf-m-red');
  });
});
