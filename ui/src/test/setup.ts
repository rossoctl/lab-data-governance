// Vitest setup: register jest-dom matchers (toBeInTheDocument, etc.) for the
// jsdom-based component tests.
import '@testing-library/jest-dom/vitest';

// jsdom doesn't implement Element.scrollIntoView; the SpanTree reveal scrolls
// the first revealed row into view. A no-op keeps that path from throwing.
if (typeof Element !== 'undefined' && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
