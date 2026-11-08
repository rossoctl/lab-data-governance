import type { ReactNode } from 'react';
import {
  PageSection,
  Title,
  Spinner,
  EmptyState,
  EmptyStateBody,
  EmptyStateHeader,
} from '@patternfly/react-core';

interface RiskViewShellProps {
  title: string;
  isLoading: boolean;
  isError: boolean;
  isEmpty: boolean;
  emptyBody?: ReactNode;
  /**
   * Controls that select *what* the view shows — a window toggle, filter
   * selects — rendered above the state branch and therefore present in all
   * four states (issue #215).
   *
   * Anything here must not depend on loaded data, because it renders while
   * there is none: a filter's *options* may come from a query (and can be
   * empty), but the control itself has to stand on its own.
   */
  toolbar?: ReactNode;
  children: ReactNode;
}

/**
 * Shared loading/error/empty/content state machine for every risk view
 * (issue #165's dashboard/trace-detail/rules/rule-detail shells, plus
 * #166-#168's real content). A 4th shared component beyond the issue's
 * named three (`RiskBadge`, `EnforcementChip`, cursor pagination) — added
 * because the AC's "empty and loading states render without throwing" needs
 * a real state machine to test against, mirroring `RecentTracesPage`'s
 * inline ternary but made reusable and independently testable.
 *
 * Precedence is isLoading > isError > isEmpty > children: the four flags
 * are independent booleans a caller (a TanStack Query result) could set
 * inconsistently, e.g. stale `isEmpty` data alongside a fresh `isError`.
 *
 * `toolbar` sits OUTSIDE that precedence, between the title and the branch
 * (issue #215). Each of the three non-content branches replaces `children`
 * wholesale, so a control placed in `children` disappeared in precisely the
 * states where the user most needs it — an empty 24h window hid the very
 * window selector that could widen it, and a failed load hid it too. Keeping
 * the selector in the toolbar makes the empty state a dead end no longer.
 * `children` keeps the original precedence untouched, so a view that passes
 * no toolbar renders exactly as it did before.
 */
export function RiskViewShell({
  title,
  isLoading,
  isError,
  isEmpty,
  emptyBody = 'Nothing to show yet.',
  toolbar,
  children,
}: RiskViewShellProps) {
  return (
    <PageSection>
      <Title headingLevel="h2" size="xl">
        {title}
      </Title>
      {toolbar}
      {isLoading ? (
        <Spinner aria-label={`Loading ${title}`} />
      ) : isError ? (
        <EmptyState>
          <EmptyStateHeader titleText="Failed to load" headingLevel="h4" />
          <EmptyStateBody>Could not load this view. Try again later.</EmptyStateBody>
        </EmptyState>
      ) : isEmpty ? (
        <EmptyState>
          <EmptyStateHeader titleText="Nothing to show" headingLevel="h4" />
          <EmptyStateBody>{emptyBody}</EmptyStateBody>
        </EmptyState>
      ) : (
        children
      )}
    </PageSection>
  );
}
