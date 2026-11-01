import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { StrictMode } from 'react';
import { createMemoryRouter, RouterProvider, type RouteObject } from 'react-router-dom';

import { Toaster } from '@/components/ui/toast';
import { TooltipProvider } from '@/components/ui/tooltip';
import { ThemeProvider } from '@/lib/theme';
import { routes } from '@/routes';

/*
 * The app loads its pages lazily (route-level code splitting). Load every lazy route once per test
 * file before its tests, so that a test's `findBy…`/`waitFor` time never includes transforming a
 * page's module graph (seconds on a busy machine): while a matched route is still lazy the router
 * renders none of the app, not even the shell and its user menu. The routes are walked, not
 * listed, so a page added later is preloaded too. The production build's lazy loading is covered
 * by the E2E tests.
 */
function lazyRouteLoads(list: RouteObject[]): Promise<unknown>[] {
  return list.flatMap(({ lazy, children }) => [
    ...(typeof lazy === 'function' ? [lazy()] : Object.values(lazy ?? {}).map((load) => load())),
    ...lazyRouteLoads(children ?? []),
  ]);
}

beforeAll(async () => {
  await Promise.all(lazyRouteLoads(routes));
}, 120_000);

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 }, mutations: { retry: false } },
  });
}

/**
 * The whole app (real routes) at `path`, with a fresh query cache; `strict` wraps it in
 * StrictMode as `main.tsx` does (effects run twice when a page mounts).
 */
export function renderApp(path: string, { strict = false }: { strict?: boolean } = {}) {
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
    { wrapper: strict ? StrictMode : undefined },
  );
  return { ...view, router, client };
}
