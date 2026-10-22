import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ViolationStepper } from './ViolationStepper';

describe('ViolationStepper', () => {
  it('shows "N of M"', () => {
    render(<ViolationStepper position={1} count={3} onStep={vi.fn()} />);
    expect(screen.getByText('1 of 3')).toBeInTheDocument();
  });

  it('never disables a control: stepping wraps at the ends', () => {
    for (const [position, count] of [[1, 3], [2, 3], [3, 3], [1, 1]]) {
      const { unmount } = render(<ViolationStepper position={position} count={count} onStep={vi.fn()} />);
      expect(screen.getByRole('button', { name: /previous/i })).not.toHaveAttribute('aria-disabled', 'true');
      expect(screen.getByRole('button', { name: /next/i })).not.toHaveAttribute('aria-disabled', 'true');
      unmount();
    }
  });

  it('calls onStep(-1) when Previous is clicked', async () => {
    const onStep = vi.fn();
    render(<ViolationStepper position={2} count={3} onStep={onStep} />);
    await userEvent.click(screen.getByRole('button', { name: /previous/i }));
    expect(onStep).toHaveBeenCalledTimes(1);
    expect(onStep).toHaveBeenCalledWith(-1);
  });

  it('calls onStep(+1) when Next is clicked', async () => {
    const onStep = vi.fn();
    render(<ViolationStepper position={2} count={3} onStep={onStep} />);
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(onStep).toHaveBeenCalledTimes(1);
    expect(onStep).toHaveBeenCalledWith(1);
  });

  it('steps at "1 of 1" too, either way', async () => {
    const onStep = vi.fn();
    render(<ViolationStepper position={1} count={1} onStep={onStep} />);
    expect(screen.getByText('1 of 1')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /previous/i }));
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(onStep.mock.calls).toEqual([[-1], [1]]);
  });
});
