import { defineConfig, devices } from '@playwright/test';

/**
 * Full-stack smoke test against a REAL API (local only, never in CI): start the API first with
 * `uv run python web/scripts/live_stack.py --dir <dir>` (see that file), then
 * `ARCI_LIVE_DIR=<dir> npm run e2e:live`. The Vite dev server proxies /api to 127.0.0.1:8000.
 */
export default defineConfig({
  testDir: 'e2e-live',
  outputDir: 'test-results-live',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  timeout: 120_000,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    locale: 'tr-TR',
    timezoneId: 'Europe/Istanbul',
    // No trace: the trace recorder fetches downloaded files (report PDF, evidence ZIP) a
    // second time, and the real API audits and counts every download. Debug with --trace on.
    trace: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
