/// <reference types="vitest/config" />
import { readFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The web interface is served by the ARCILOGGER server itself (Caddy on Windows).
// Everything is bundled locally: no CDN, no external fonts, no telemetry.
const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as {
  version: string;
};

// Optional request fields the API accepts, from its generated OpenAPI contract
// (src/lib/api-features.ts): an option of the web app appears only once the API takes its field.
const contract = JSON.parse(
  readFileSync(new URL('./src/generated/api-schemas.json', import.meta.url), 'utf8'),
) as { schemas: Record<string, { properties?: Record<string, unknown> } | undefined> };
function accepts(schema: string, field: string): boolean {
  const found = contract.schemas[schema];
  if (!found) throw new Error(`api-schemas.json has no schema ${schema}`);
  return Object.hasOwn(found.properties ?? {}, field);
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
    __API_EVIDENCE_AUDIT_LOG__: JSON.stringify(accepts('EvidenceCreate', 'include_audit_log')),
  },
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
    assetsInlineLimit: 0, // keep fonts/icons as files so the CSP can stay strict
    chunkSizeWarningLimit: 700,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          ui: [
            '@radix-ui/react-dialog',
            '@radix-ui/react-dropdown-menu',
            '@radix-ui/react-popover',
            '@radix-ui/react-select',
            '@radix-ui/react-tabs',
            '@radix-ui/react-toast',
            '@radix-ui/react-tooltip',
          ],
          charts: ['recharts'],
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    // See `asyncUtilTimeout` in src/test/setup.ts (busy machines).
    testTimeout: 20_000,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/main.tsx'],
      reporter: ['text', 'lcov'],
    },
  },
});
