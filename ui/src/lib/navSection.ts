/**
 * Which top-level nav item a pathname belongs to (issue #165's horizontal
 * `Nav` in `App.tsx`), so the current item can be marked `isActive`.
 *
 * Matches `/risk` exactly or `/risk/...` — NOT a bare `startsWith('/risk')`,
 * which would wrongly claim a hypothetical `/risky-business` route.
 */
export type NavSection = 'risk' | 'traces';

export function navSectionFor(pathname: string): NavSection {
  return pathname === '/risk' || pathname.startsWith('/risk/') ? 'risk' : 'traces';
}
