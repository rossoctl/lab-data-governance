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
// without pretending jsdom laid anything out.
//
// CORRECTION (issue #170 follow-up): this comment used to claim the zero size
// makes PF skip the tag's background rect AND the node label alike. That's
// only half true. Read from PF's own source
// (DefaultConnectorTag.js/useSize.js): `useSize`'s zero-size state object is
// TRUTHY, so `DefaultConnectorTag`'s `textSize &&` guard — which gates only
// the `<rect>` — passes; the `<text>` holding the tag string has no guard at
// all and renders unconditionally. So the edge tag's TEXT (and its `<rect>`)
// ARE observable under jsdom — verified both by reading the source and by an
// empirical probe (see ExecutionFlowGraph.test.tsx's header note) — and are
// asserted directly in that file's tests. `NodeLabel` (node labels) is the
// one that stays unobservable: it wraps its `<text>` in a `Tippy` tooltip on
// the zero-bbox measurement, which never attaches under jsdom's tooltip stub.
//
// What remains genuinely unobservable is GEOMETRY — position, real size,
// visual collision, whether a label visually fits. That's the true scope of
// "not asserted here", not "tag text doesn't render". Node labels, node
// geometry, and anything about visual layout are therefore not observable
// here and are NOT asserted; that is still verified BY HAND — see
// ExecutionFlowGraph.test.tsx's note on the split, and the PR's "Verify by
// hand" list for what that means in practice.
//
// IT IS NOT COVERED ANYWHERE ELSE EITHER, and this comment used to also claim
// it was "asserted in Playwright against a real browser". There is no such
// spec: `ui/e2e/` holds only smoke and classification specs, neither of
// which opens the graph.
if (typeof SVGElement !== 'undefined') {
  const proto = SVGElement.prototype as unknown as { getBBox?: () => DOMRect };
  if (!proto.getBBox) {
    proto.getBBox = () => ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
  }
}
