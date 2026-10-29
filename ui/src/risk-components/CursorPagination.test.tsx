import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CursorPagination } from './CursorPagination';

describe('CursorPagination', () => {
  it('renders nothing when there is no next page', () => {
    const { container } = render(
      <CursorPagination hasNextPage={false} isFetchingNextPage={false} onNextPage={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('calls onNextPage once per click', async () => {
    const onNextPage = vi.fn();
    render(
      <CursorPagination hasNextPage isFetchingNextPage={false} onNextPage={onNextPage} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /load more/i }));
    expect(onNextPage).toHaveBeenCalledTimes(1);
  });

  it('is disabled while fetching and a click does not fire', async () => {
    const onNextPage = vi.fn();
    render(
      <CursorPagination hasNextPage isFetchingNextPage onNextPage={onNextPage} />,
    );
    const button = screen.getByRole('button', { name: /loading/i });
    // PF's Button uses aria-disabled (stays focusable while blocking clicks
    // via its own onClick guard), not the native `disabled` attribute.
    expect(button).toHaveAttribute('aria-disabled', 'true');
    await userEvent.click(button);
    expect(onNextPage).not.toHaveBeenCalled();
  });

  it('honors a label override', () => {
    render(
      <CursorPagination
        hasNextPage
        isFetchingNextPage={false}
        onNextPage={vi.fn()}
        label="Load more rules"
      />,
    );
    expect(screen.getByRole('button', { name: 'Load more rules' })).toBeInTheDocument();
  });
});
