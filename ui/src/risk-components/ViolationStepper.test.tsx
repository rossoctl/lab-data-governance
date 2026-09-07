import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ViolationStepper } from './ViolationStepper';

describe('ViolationStepper', () => {
  it('shows "N of M"', () => {
    render(<ViolationStepper position={1} count={3} onStep={vi.fn()} />);
    expect(screen.getByText('1 of 3')).toBeInTheDocument();
  });

  it('disables Previous at the first violation', () => {
    render(<ViolationStepper position={1} count={3} onStep={vi.fn()} />);
    expect(screen.getByRole('button', { name: /previous/i })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: /next/i })).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('disables Next at the last violation', () => {
    render(<ViolationStepper position={3} count={3} onStep={vi.fn()} />);
    expect(screen.getByRole('button', { name: /next/i })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: /previous/i })).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('enables both controls in the middle', () => {
    render(<ViolationStepper position={2} count={3} onStep={vi.fn()} />);
    expect(screen.getByRole('button', { name: /previous/i })).not.toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: /next/i })).not.toHaveAttribute('aria-disabled', 'true');
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

  it('does not call onStep when Previous is disabled and clicked', async () => {
    const onStep = vi.fn();
    render(<ViolationStepper position={1} count={3} onStep={onStep} />);
    await userEvent.click(screen.getByRole('button', { name: /previous/i }));
    expect(onStep).not.toHaveBeenCalled();
  });

  it('does not call onStep when Next is disabled and clicked', async () => {
    const onStep = vi.fn();
    render(<ViolationStepper position={3} count={3} onStep={onStep} />);
    await userEvent.click(screen.getByRole('button', { name: /next/i }));
    expect(onStep).not.toHaveBeenCalled();
  });

  it('disables both controls when there is exactly one violation', () => {
    render(<ViolationStepper position={1} count={1} onStep={vi.fn()} />);
    expect(screen.getByRole('button', { name: /previous/i })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: /next/i })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText('1 of 1')).toBeInTheDocument();
  });
});
