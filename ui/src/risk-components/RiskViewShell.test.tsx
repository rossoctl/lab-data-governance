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

// Issue #215: a `toolbar` slot that survives every state branch. The window
// selector on the dashboard lived inside `children`, so the loading / error /
// empty branches — each of which replaces `children` wholesale — left the user
// with no control to change the window that produced the empty result. The
// toolbar renders above the branch, so it is present in all four states, while
// `children` keeps the existing precedence exactly as before.

describe('RiskViewShell toolbar slot (#215)', () => {
  const base = {
    title: 'Some risk view',
    isLoading: false,
    isError: false,
    isEmpty: false,
    emptyBody: 'Nothing here.',
    toolbar: <div>toolbar</div>,
  };

  it('renders the toolbar while loading', () => {
    render(
      <RiskViewShell {...base} isLoading>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText('toolbar')).toBeInTheDocument();
    expect(screen.getByLabelText(/loading/i)).toBeInTheDocument();
    expect(screen.queryByText('content')).not.toBeInTheDocument();
  });

  it('renders the toolbar in the error state', () => {
    render(
      <RiskViewShell {...base} isError>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText('toolbar')).toBeInTheDocument();
    expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
    expect(screen.queryByText('content')).not.toBeInTheDocument();
  });

  it('renders the toolbar in the empty state', () => {
    render(
      <RiskViewShell {...base} isEmpty>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText('toolbar')).toBeInTheDocument();
    expect(screen.getByText('Nothing here.')).toBeInTheDocument();
    expect(screen.queryByText('content')).not.toBeInTheDocument();
  });

  it('renders the toolbar alongside children in the content state', () => {
    render(
      <RiskViewShell {...base}>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.getByText('toolbar')).toBeInTheDocument();
    expect(screen.getByText('content')).toBeInTheDocument();
  });

  it('renders the toolbar above the state branch, and below the title', () => {
    render(
      <RiskViewShell {...base} isEmpty>
        <div>content</div>
      </RiskViewShell>,
    );
    const title = screen.getByText('Some risk view');
    const toolbar = screen.getByText('toolbar');
    const empty = screen.getByText('Nothing here.');
    expect(title.compareDocumentPosition(toolbar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(toolbar.compareDocumentPosition(empty) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('omits the toolbar region entirely when no toolbar is given', () => {
    render(
      <RiskViewShell {...base} toolbar={undefined} isEmpty>
        <div>content</div>
      </RiskViewShell>,
    );
    expect(screen.queryByText('toolbar')).not.toBeInTheDocument();
    expect(screen.getByText('Nothing here.')).toBeInTheDocument();
  });
});
