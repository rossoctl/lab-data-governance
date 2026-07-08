// Vitest setup: register jest-dom matchers (toBeInTheDocument, etc.) for the
// jsdom-based component tests.
import '@testing-library/jest-dom/vitest';

// jsdom lacks ResizeObserver, which @xyflow/react (React Flow) requires to
// measure its canvas. A no-op polyfill lets the graph view mount under jsdom;
// real layout is exercised by entityGraph.test.ts (pure dagre) and Playwright.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
