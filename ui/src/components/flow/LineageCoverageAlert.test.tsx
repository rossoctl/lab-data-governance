import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { LineageCoverageAlert } from './LineageCoverageAlert';

/**
 * The component's contract is unusual and worth pinning explicitly: **silence is a
 * claim**. Rendering nothing is this view's affirmative "lineage coverage for this
 * trace is complete" statement, so every arm that has NOT established completeness
 * has to say something. These tests exist to stop a future refactor from collapsing
 * an unknown-coverage state back into the silent path.
 */
describe('LineageCoverageAlert', () => {
  it('says nothing for a complete trace — the absence IS the no-truncation claim', () => {
    const { container } = render(
      <LineageCoverageAlert
        status="complete"
        stoppedAtSeq={null}
        isError={false}
        isLoading={false}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('warns about the truncation on a partial trace, and cites the stop position', () => {
    render(
      <LineageCoverageAlert
        status="partial"
        stoppedAtSeq={42}
        isError={false}
        isLoading={false}
      />,
    );
    expect(
      screen.getByText(/Data lineage for this trace is incomplete/),
    ).toBeInTheDocument();
    // Assertive, not polite: this must land before the reader acts on the source list.
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  it('reports a failed read as unknown coverage rather than borrowing partial wording', () => {
    render(
      <LineageCoverageAlert status={null} stoppedAtSeq={null} isError isLoading={false} />,
    );
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
    // The body emphasises the word; the title also contains it, hence the tag match.
    expect(screen.getByText('unknown', { selector: 'strong' })).toBeInTheDocument();
    // A broken read establishes nothing — including that coverage is fine.
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });

  /**
   * The regression this file was added for. The flow tables' spinner gates on the
   * interactions/entities reads only, so they paint while the lineage read is still
   * in flight. Without a dedicated arm, `status` collapses `undefined → null` and
   * the component goes silent — publishing "coverage is complete" about a trace
   * that may well be `partial`, for as long as the read takes.
   */
  it('does not claim clean coverage while the lineage read is still in flight', () => {
    const { container } = render(
      <LineageCoverageAlert
        status={null}
        stoppedAtSeq={null}
        isError={false}
        isLoading
      />,
    );
    expect(container).not.toBeEmptyDOMElement();
    expect(
      screen.getByText(/Checking data lineage coverage for this trace/),
    ).toBeInTheDocument();
    expect(screen.getByText(/not yet known/)).toBeInTheDocument();
  });

  it('stays quiet-but-present while loading: informational, not an assertive interrupt', () => {
    render(
      <LineageCoverageAlert
        status={null}
        stoppedAtSeq={null}
        isError={false}
        isLoading
      />,
    );
    // Unlike the error/partial arms this resolves on its own; an assertive
    // interrupt on every trace open would train the reader to ignore the strip.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('prefers the loading arm over the not-yet-derived silence when both apply', () => {
    // `isLoading` with a null status is the exact combination FlowTables passes on
    // first paint; loading must win, since `null` is only meaningful once settled.
    const { container } = render(
      <LineageCoverageAlert
        status={null}
        stoppedAtSeq={null}
        isError={false}
        isLoading
      />,
    );
    expect(container).not.toBeEmptyDOMElement();
  });

  it('says nothing once a settled read reports no lineage yet', () => {
    const { container } = render(
      <LineageCoverageAlert
        status={null}
        stoppedAtSeq={null}
        isError={false}
        isLoading={false}
      />,
    );
    // Distinct from the loading case: the read DID succeed and simply has nothing
    // to report, and the per-payload blocks already say "not yet computed".
    expect(container).toBeEmptyDOMElement();
  });
});
