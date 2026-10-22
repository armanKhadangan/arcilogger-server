import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';

import { Toaster } from '@/components/ui/toast';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ThemeProvider } from '@/lib/theme';
import { routes } from '@/routes';

/*
 * The app loads its pages lazily (route-level code splitting). Load them once per test file
 * before its tests, so that a test's `findBy…`/`waitFor` time never includes transforming a page's
 * module graph (seconds on a busy machine); the lazy routes then resolve at once. The production
 * build's lazy loading is covered by the E2E tests.
 */
beforeAll(async () => {
  await Promise.all([
    import('@/pages/audit/audit-page'),
    import('@/pages/dashboard/dashboard-page'),
    import('@/pages/devices/devices-page'),
    import('@/pages/evidence/evidence-page'),
    import('@/pages/lookup/lookup-page'),
    import('@/pages/reports/reports-page'),
    import('@/pages/sealing/sealing-page'),
    import('@/pages/security/security-page'),
    import('@/pages/settings/settings-page'),
    import('@/pages/settings/users-page'),
    import('@/pages/settings/license-page'),
  ]);
}, 120_000);

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
  });
}

/** The whole app (real routes) at `path`, with a fresh query cache. */
export function renderApp(path: string) {
  const client = testQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const view = render(
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <TooltipProvider>
          <RouterProvider router={router} />
          <Toaster />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>,
  );
  return { ...view, router, client };
}
