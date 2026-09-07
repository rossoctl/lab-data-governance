import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RiskViewShell } from './RiskViewShell';

// The AC's "empty and loading states render without throwing" needs a real
// state machine to hold against, not three view stubs each asserted in
// isolation — this shell is that machine. Branch precedence is
// isLoading > isError > isEmpty > children, since the four flags are
// independent booleans a caller could otherwise set inconsistently.

describe('RiskViewShell', () => {
  const base = {
    title: 'Some risk view',
    isLoading: false,
    isError: false,
    isEmpty: false,
    emptyBody: 'Nothing here.',
  };

  it('renders a spinner when loading, regardless of other flags', () => {
    render(
      <RiskViewShell {...base} isLoading isError isEmpty>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByLabelText(/loading/i)).toBeInTheDocument();
    expect(screen.queryByText('content')).not.toBeInTheDocument();
  });

  it('renders an error empty state when not loading but isError', () => {
    render(
      <RiskViewShell {...base} isError isEmpty>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    expect(screen.queryByText('content')).not.toBeInTheDocument();
  });

  it('renders the empty state when not loading/error but isEmpty', () => {
    render(
      <RiskViewShell {...base} isEmpty>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText('Nothing here.')).toBeInTheDocument();
    expect(screen.queryByText('content')).not.toBeInTheDocument();
  });

  it('renders children when none of the flags are set', () => {
    render(
      <RiskViewShell {...base}>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText('content')).toBeInTheDocument();
    expect(screen.queryByText(/failed to load/i)).not.toBeInTheDocument();
    expect(screen.queryByText('Nothing here.')).not.toBeInTheDocument();
  });

  it('always renders the title', () => {
    render(
      <RiskViewShell {...base}>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText('Some risk view')).toBeInTheDocument();
  });
});
