/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The browser always requests /api/* as same-origin relative URLs; the dev
// server proxies them to the backend. Docker Compose sets
// BACKEND_PROXY_TARGET=http://backend:8000; local development outside
// Docker falls back to the local backend.
const backendProxyTarget =
  process.env.BACKEND_PROXY_TARGET ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
    // Headroom over Vitest's 5 s default for slow environments — the
    // Docker dev container transforms bind-mounted sources several
    // times slower than a native checkout; setupTests.ts raises the
    // testing-library async-utility timeout to match. Only genuinely
    // hung tests are affected: they fail slower.
    testTimeout: 15_000,
  },
});
