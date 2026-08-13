/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { execSync } from 'child_process';

// Build-time version stamp: short commit SHA + commit date. The build runs on
// every deployment, so this string is fresh each deploy without any manual
// bump. Resolution order:
//  1. `APP_VERSION` env var — how the container build gets it: the .git tree is
//     NOT in the image build context, so build-and-load.sh computes the value
//     on the host and passes it in as a --build-arg (see Containerfile).
//  2. a direct git call — for local `npm run dev` / `npm run build`, where .git
//     and the git binary ARE present.
//  3. 'dev' — a source tarball with neither.
function appVersion(): string {
  if (process.env.APP_VERSION) return process.env.APP_VERSION;
  try {
    const sha = execSync('git rev-parse --short HEAD').toString().trim();
    const date = execSync('git show -s --format=%cd --date=short HEAD')
      .toString()
      .trim();
    return `${sha} (${date})`;
  } catch {
    return 'dev';
  }
}

// The SPA is served by the Python backend under /ui/ (ADR-0019), so the built
// asset URLs must be /ui/-prefixed. Vite emits content-hashed bundles into
// dist/assets/, which the backend serves via a StaticFiles mount at /ui/assets.
export default defineConfig({
  base: '/ui/',
  // Inject the build-time version as a global constant (also applied under
  // Vitest, so tests that render the masthead see a defined __APP_VERSION__).
  define: {
    __APP_VERSION__: JSON.stringify(appVersion()),
  },
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 3000,
    // In dev, proxy /api to the backend so the same-origin fetches the SPA
    // issues in production also work against `npm run dev`.
    proxy: {
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: false,
    // e2e/ holds Playwright specs (a different test runner); keep them out of
    // Vitest's discovery so `npm run test:unit` covers only src/.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['e2e/**', 'node_modules/**'],
    server: {
      deps: {
        // @patternfly/react-topology's published ESM imports `.css` files
        // directly (via @patternfly/react-styles). Vitest EXTERNALISES
        // node_modules by default and hands those imports to Node's ESM loader,
        // which has no CSS handler — so the graph view's test file died on
        // `Unknown file extension ".css"`.
        //
        // Inlining routes the package through Vite's own transform pipeline,
        // where this config's `css: false` turns those imports into no-ops (the
        // browser build already handled them, which is why `npm run build` never
        // saw this). Scoped to the two packages actually involved rather than
        // blanket-inlining node_modules.
        inline: ['@patternfly/react-topology', '@patternfly/react-styles'],
      },
    },
  },
});
