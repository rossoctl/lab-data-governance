import { Button, Flex, FlexItem } from '@patternfly/react-core';
import { AngleLeftIcon, AngleRightIcon } from '@patternfly/react-icons';

interface ViolationStepperProps {
  /** The current 1-based violation position — see `lib/riskViolation.ts`. */
  position: number;
  /** Total number of violations for this trace. */
  count: number;
  /** Fired with `-1` (Previous) or `+1` (Next); the caller runs it through `stepViolation` and writes `?violation=`. */
  onStep: (delta: -1 | 1) => void;
}

/**
 * "N of M" plus Previous/Next controls for stepping through a trace's policy
 * violations (issue #170's AC #3). Extracted from `RiskTraceDetailPage` so
 * the lockstep between this control, the URL, the policy panel and the
 * diagrams' selection has one control surface to drive in tests, rather than
 * three call sites each re-deriving disabled state.
 *
 * Disables at the ends rather than wrapping — see `stepViolation`'s doc
 * comment for why a wrapping Next would make "N of N" ambiguous. This
 * component holds no state of its own: `position` is always derived from
 * `?violation=`, never local state (per the plan's "Selection derives from
 * `?violation`, never component state").
 */
export function ViolationStepper({ position, count, onStep }: ViolationStepperProps) {
  return (
    <Flex alignItems={{ default: 'alignItemsCenter' }} spaceItems={{ default: 'spaceItemsSm' }}>
      <FlexItem>
        <Button
          variant="secondary"
          icon={<AngleLeftIcon />}
          isAriaDisabled={position <= 1}
          onClick={() => onStep(-1)}
        >
          Previous
        </Button>
      </FlexItem>
      <FlexItem>
        {position} of {count}
      </FlexItem>
      <FlexItem>
        <Button
          variant="secondary"
          icon={<AngleRightIcon />}
          iconPosition="end"
          isAriaDisabled={position >= count}
          onClick={() => onStep(1)}
        >
          Next
        </Button>
      </FlexItem>
    </Flex>
  );
}

export default ViolationStepper;
