import { useLocation } from 'react-router-dom';

/**
 * A hidden sink that mirrors the router's current `pathname + search` into a
 * `data-testid="location"` node, so tests can assert the URL after an
 * interaction (the round-trip proof for URL-as-state). Mount it inside the same
 * router as the component under test.
 */
export function LocationProbe() {
  const loc = useLocation();
  return (
    <div data-testid="location" style={{ display: 'none' }}>
      {loc.pathname}
      {loc.search}
    </div>
  );
}
