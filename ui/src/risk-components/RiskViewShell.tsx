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
 */
export function RiskViewShell({
  title,
  isLoading,
  isError,
  isEmpty,
  emptyBody = 'Nothing to show yet.',
  children,
}: RiskViewShellProps) {
  return (
    <PageSection>
      <Title headingLevel="h2" size="xl">
        {title}
      </Title>
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
