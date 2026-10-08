/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';
import type { Plugin } from 'vite';

// The browser always requests /api/* as same-origin relative URLs; the dev
// server proxies them to the backend. Docker Compose sets
// BACKEND_PROXY_TARGET=http://backend:8000; local development outside
// Docker falls back to the local backend.
const backendProxyTarget =
  process.env.BACKEND_PROXY_TARGET ?? 'http://localhost:8000';

// The release this bundle is built for: the production image build
// passes it (Dockerfile build argument PARTFLOW_RELEASE); the dev server
// and a local build use `development`. `src/api/client.ts` inlines the
// same variable as BUNDLE_RELEASE.
const bundleRelease = process.env.VITE_PARTFLOW_RELEASE || 'development';

/**
 * Names the bundle release in the served shell
 * (`<meta name="partflow-release">`): the release smoke check and the
 * kiosk auto-reload read it before trusting a reload. The release
 * grammar ([A-Za-z0-9][A-Za-z0-9._-]{0,63}) needs no HTML escaping.
 */
function partflowReleaseMeta(): Plugin {
  return {
    name: 'partflow-release-meta',
    transformIndexHtml() {
      return [
        {
          tag: 'meta',
          attrs: { name: 'partflow-release', content: bundleRelease },
          injectTo: 'head',
        },
      ];
    },
  };
}

export default defineConfig({
  plugins: [react(), partflowReleaseMeta()],
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
    // hung tests are affected: they fail slower. Keep it above the
    // setupTests.ts asyncUtilTimeout (10 s).
    testTimeout: 15_000,
  },
});
