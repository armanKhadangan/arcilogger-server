/// <reference types="vitest/config" />
import { readFileSync } from 'node:fs';
import { fileURLToPath, URL } from 'node:url';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

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

/**
 * Fails a build whose output must not ship (Build-ArciBundle.ps1 copies what `npm run build`
 * writes): never a source map, the app's readable source; without the UI kit, no module of
 * src/pages/ui-kit or src/locales/ui-kit, no `ui-kit` path (its route, its menu entry) and none
 * of its names ("Bileşen Kiti", "UI kit") in any chunk. The production build is that bundle, so
 * it is checked without the UI kit whatever `uiKit` says.
 */
function shippedBundleCheck(uiKit: boolean): Plugin {
  let kitAllowed = uiKit;
  return {
    name: 'arci-shipped-bundle-check',
    apply: 'build',
    configResolved(config) {
      if (config.mode === 'production') kitAllowed = false;
    },
    generateBundle(_options, bundle) {
      const problems: string[] = [];
      for (const file of Object.values(bundle)) {
        if (file.fileName.endsWith('.map')) problems.push(`${file.fileName}: source map`);
        if (file.type !== 'chunk') continue;
        if (file.code.includes('sourceMappingURL=')) problems.push(`${file.fileName}: source map`);
        const kit =
          /ui-kit|Bileşen Kit|UI kit/i.test(file.code) ||
          file.moduleIds.some((id) =>
            /\/src\/(pages|locales)\/ui-kit\//.test(id.replaceAll('\\', '/')),
          );
        if (kit && !kitAllowed) problems.push(`${file.fileName}: UI kit`);
      }
      if (problems.length > 0) this.error(`not for the shipped bundle: ${problems.join('; ')}`);
    },
  };
}

export default defineConfig(({ command, mode }) => {
  // The UI kit (`/ui-kit`, every component on one page) is a developer page: the dev server, the
  // unit tests and the E2E build (`npm run build:e2e`, mode `e2e`) have it; the bundle the
  // installer ships (`npm run build`) has neither the page nor its menu entry.
  const uiKit = command === 'serve' || mode === 'e2e';
  return {
    define: {
      __APP_VERSION__: JSON.stringify(pkg.version),
      __API_EVIDENCE_AUDIT_LOG__: JSON.stringify(accepts('EvidenceCreate', 'include_audit_log')),
      __UI_KIT__: JSON.stringify(uiKit),
    },
    plugins: [react(), tailwindcss(), shippedBundleCheck(uiKit)],
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
      // No source maps in the bundle (the dev server has its own); shippedBundleCheck enforces it.
      sourcemap: false,
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
  };
});
