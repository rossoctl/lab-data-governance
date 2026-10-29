import { Button } from '@patternfly/react-core';

interface CursorPaginationProps {
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onNextPage: () => void;
  label?: string;
}

/**
 * "Load more" control for a keyset-cursor list — the `RecentTracesPage`
 * block, extracted so every risk list view (issue #165's shared component
 * list) shares it rather than restating the button. Forward-only by design:
 * `data_governance/risk/api/http.py`'s `encode_keyset_cursor` never emits a
 * previous-page token, so there is no "Previous" to build.
 *
 * Renders nothing when `hasNextPage` is false, matching
 * `RecentTracesPage`'s `hasNextPage &&` guard. Disabled while fetching so a
 * double-click cannot re-request the same cursor.
 */
export function CursorPagination({
  hasNextPage,
  isFetchingNextPage,
  onNextPage,
  label = 'Load more',
}: CursorPaginationProps) {
  if (!hasNextPage) return null;
  return (
    <Button
      variant="secondary"
      isBlock
      isLoading={isFetchingNextPage}
      isAriaDisabled={isFetchingNextPage}
      onClick={onNextPage}
      style={{ marginTop: '1rem' }}
    >
      {isFetchingNextPage ? 'Loading…' : label}
    </Button>
  );
}
