import { Alert } from '@patternfly/react-core';

import type { LineageStatus } from '../../types';

/**
 * The trace-level **Data lineage** coverage warning (issue #120, ADR-0027 D6).
 *
 * When a leg's payload is absent, lineage is derived only up to that point and the
 * trace is marked `partial`. Rendered at the top of the flow view and outside the
 * tables' right-hand gutter, because the failure mode is a reader taking the rows
 * that DO carry lineage for the full set of data sources — so the warning has to
 * be on screen before anything is clicked, and stay there while the detail panel
 * is open. A message hidden behind a payload expansion would not prevent the
 * mistake; it would only be available to someone who already suspected it.
 *
 * Only `partial` warns about a truncation. `complete` needs no banner (the
 * absence of a warning is the "no truncation" statement, and a permanent green
 * box trains the reader to stop reading the strip). `null` — not yet derived —
 * deliberately says nothing either: there is no established truncation to report,
 * and the per-payload blocks already state "lineage not yet computed" for that
 * window. Claiming a truncation nobody has established would make the banner
 * noise, which is how a real one gets ignored.
 *
 * `isError` and `isLoading` are the two exceptions to that silence, and neither is
 * a `status` value: a
 * failed read establishes *nothing*, including that coverage is complete — and in
 * this view silence IS the complete-coverage statement. So an error gets its own
 * banner, deliberately worded as *unknown* rather than as a truncation: it must
 * not let a broken read look like clean coverage, and equally must not invent a
 * prefix claim it never obtained. Distinct from the `null` case, where the read
 * did succeed and simply has nothing yet.
 *
 * `isLoading` is the same argument one step earlier: the tables paint before this
 * query settles, so without its own arm an in-flight read reaches `status === null`
 * and goes silent — publishing the clean-coverage signal while the answer is still
 * unknown. Worded as *not yet known* and left non-assertive, since it resolves on
 * its own in the common case.
 */
export function LineageCoverageAlert({
  status,
  stoppedAtSeq,
  isError,
  isLoading,
}: {
  status: LineageStatus;
  stoppedAtSeq: number | null;
  /** The trace-scoped lineage read failed — coverage is unknown, not fine. */
  isError: boolean;
  /**
   * The trace-scoped lineage read is still in flight. Needs its own arm for the
   * same reason as `isError`: the tables render before this query settles (their
   * spinner gates on the interactions/entities reads only), and an in-flight read
   * has established nothing about coverage — but in this view silence is read as
   * the affirmative "no truncation" statement. Falling through to `status ===
   * null` would emit exactly the clean-coverage signal a `partial` trace exists
   * to prevent, for as long as the read takes.
   */
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <Alert
        variant="info"
        isInline
        title="Checking data lineage coverage for this trace"
        // Not role="alert": unlike the error and truncation arms this is
        // transient status chatter, and an assertive interrupt on every trace
        // open would train the reader to tune the strip out. PF's default
        // aria-live="polite" is the right register.
        style={{ marginBottom: '0.75rem' }}
      >
        Whether the data sources shown below are complete is not yet known.
      </Alert>
    );
  }
  if (isError) {
    return (
      <Alert
        variant="warning"
        isInline
        title="Data lineage coverage for this trace is unknown"
        // Same assertive upgrade as the truncation warning below: a reader must
        // learn that the coverage claim is missing before they act on whatever
        // lineage the per-leg blocks do or do not show.
        role="alert"
        style={{ marginBottom: '0.75rem' }}
      >
        {/* No stop position and no prefix claim — deliberately. Nothing about
            this trace's coverage was established, so the banner reports the
            failure and stops there rather than borrowing `partial`'s wording. */}
        The lineage coverage for this trace could not be loaded, so whether the
        data sources shown are complete is <strong>unknown</strong>. Reload to
        retry.
      </Alert>
    );
  }
  if (status !== 'partial') return null;
  return (
    <Alert
      variant="warning"
      isInline
      title="Data lineage for this trace is incomplete"
      // PF's Alert announces itself `aria-live="polite"` and carries no role.
      // An explicit `role="alert"` upgrades that to assertive: this is not
      // status chatter, it is "what you are about to read is not the whole
      // picture", and a screen-reader user must get it before they act on the
      // source list — the same reason it is rendered before the tables.
      role="alert"
      style={{ marginBottom: '0.75rem' }}
    >
      {/* Naming the stop position matters as much as the warning: it tells the
          reader WHERE the picture ends, so they can see which rows are covered
          rather than distrusting all of them equally. The seq is the leg
          sequence shown in the flat view's Seq column, so it is a position the
          reader can actually locate. The null fallback is unreachable against a
          current server (the status table CHECK-pairs `partial` with a stop seq),
          but the warning must survive an older one rather than render "leg
          null". */}
      {stoppedAtSeq === null
        ? 'Lineage was derived only up to the first leg missing a payload.'
        : `Lineage was derived only up to leg seq ${stoppedAtSeq}, where a payload is missing.`}{' '}
      Legs from that point on have no lineage, so what is shown is{' '}
      <strong>not the complete set of data sources</strong> for this trace.
    </Alert>
  );
}
