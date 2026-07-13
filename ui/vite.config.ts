/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';

// The SPA is served by the Python backend under /ui/ (ADR-0019), so the built
// asset URLs must be /ui/-prefixed. Vite emits content-hashed bundles into
// dist/assets/, which the backend serves via a StaticFiles mount at /ui/assets.
export default defineConfig({
  base: '/ui/',
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
  },
});
