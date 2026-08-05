// Vitest setup: register jest-dom matchers (toBeInTheDocument, etc.) for the
// jsdom-based component tests.
import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement Element.scrollIntoView; the SpanTree reveal scrolls
// the first revealed row into view. A no-op keeps that path from throwing.
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

// jsdom implements no SVG layout at all, so `SVGGraphicsElement.getBBox` is
// simply ABSENT — not, as is often assumed, present and returning zeros.
//
// PatternFly topology measures its own text with `useSize` → `node.getBBox()`
// (utils/useSize.js) for both node labels and the per-edge connector tag the
// Execution Flow graph uses for a leg's `seq`. The tag calls it from a ref
// callback during the commit phase, where a throw is NOT swallowed: it surfaces
// as `TypeError: node.getBBox is not a function` and unmounts the whole tree, so
// every test that merely mounts the flow view (not just the graph's own) fails
// with an unrelated-looking "Unable to find …" error.
//
// A zero-size stub is the honest minimum: it makes the measurement DEFINED
// without pretending jsdom laid anything out. PF treats a zero size as "not
// measured yet" and skips the tag's background rect and the node label, so the
// zeros are not mistaken for real geometry. Everything geometric therefore
// remains unobservable here by design.
//
// IT IS NOT COVERED ANYWHERE ELSE EITHER, and this comment used to claim it was
// "asserted in Playwright against a real browser". There is no such spec: `ui/e2e/`
// holds only smoke and classification specs, neither of which opens the graph. So
// the geometry is verified BY HAND today — see ExecutionFlowGraph.test.tsx's note on
// the split, and the PR's "Verify by hand" list for what that means in practice.
if (typeof SVGElement !== 'undefined') {
  const proto = SVGElement.prototype as unknown as { getBBox?: () => DOMRect };
  if (!proto.getBBox) {
    proto.getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
  }
}
