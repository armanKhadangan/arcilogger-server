import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { ReactNode } from 'react';

import { BACKGROUND_HEADER } from '@/lib/api';
import type { ApiErrorBody, Role } from '@/lib/api-types';
import { SYSTEM_POLL_MS, useSystemHealth, useSystemSummary } from '@/lib/system-hooks';
import type { SystemHealth } from '@/lib/system-health-types';
import type { SystemSummary } from '@/lib/system-types';
import {
  errorResponse,
  installFetch,
  jsonResponse,
  makeMe,
  type RecordedRequest,
} from '@/test/api-mock';
import { renderApp, testQueryClient } from '@/test/render';
import { licenseSample } from '@/test/samples';

/*
 * Sistem as the app opens it (`/system`, Wave 4 contract 2.8.2, tests of §3 W4-F): the page's
 * hooks on the answers captured from the real API (`e2e/samples/system.json`,
 * `system-health.json`, a Windows host), the licence maintenance of `GET /license`, a failed
 * request of either endpoint, the 30 s background refresh and the refusal for every role without
 * `system.read`. The view's own rules are pinned in `system-view.test.tsx`.
 */

interface ErrorSample {
  status: number;
  headers: Record<string, string>;
  body: ApiErrorBody;
}

function load(name: string): unknown {
  return JSON.parse(readFileSync(join(process.cwd(), 'e2e', 'samples', `${name}.json`), 'utf8'));
}

const SYSTEM = load('system') as {
  summary: SystemSummary;
  errors: Record<'forbidden', ErrorSample>;
};
const HEALTH = load('system-health') as { health: SystemHealth };

const BLOCKS_TR = [
  'Kaynak kullanımı',
  'Diskler',
  'Veritabanı',
  'Toplayıcı',
  'İçe aktarma',
  'Hizmetler',
  'Zamanlanmış görevler',
  'Worker işleri',
  'Saat',
  'Sürüm ve güncelleme',
];

interface Serve {
  /** `GET /system` answers this instead of the summary. */
  summary?: () => Response;
  /** `GET /system/health` answers this instead of the health (a pending answer too). */
  health?: () => Response | Promise<Response>;
}

/** The API as the captured samples have it, for `role`; returns the recorded requests. */
function serve(role: Role, answers: Serve = {}): RecordedRequest[] {
  const me = makeMe(role);
  const refused = () => {
    const sample = SYSTEM.errors.forbidden;
    return jsonResponse(sample.status, sample.body, sample.headers);
  };
  return installFetch((request) => {
    switch (request.path) {
      case '/api/v1/auth/me':
        return jsonResponse(200, me);
      case '/api/v1/license':
        return jsonResponse(200, licenseSample('maintenance_expired'));
      case '/api/v1/system':
        if (!me.permissions.includes('system.read')) return refused();
        return answers.summary?.() ?? jsonResponse(200, SYSTEM.summary);
      case '/api/v1/system/health':
        if (!me.permissions.includes('system.read')) return refused();
        return answers.health?.() ?? jsonResponse(200, HEALTH.health);
      default:
        return errorResponse(404, 'not_found');
    }
  });
}

function system(calls: RecordedRequest[], path?: string): RecordedRequest[] {
  return calls.filter((call) =>
    path === undefined ? call.path.startsWith('/api/v1/system') : call.path === `/api/v1${path}`,
  );
}

const block = (id: string) => screen.getByTestId(`system-block-${id}`);

const unavailable = () =>
  errorResponse(503, 'database_unavailable', {
    tr: 'Veritabanına ulaşılamıyor.',
    en: 'The database cannot be reached.',
  });

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Sistem page', () => {
  it('shows the administrator every block of the captured Windows host', async () => {
    const calls = serve('admin');
    renderApp('/system');
    expect(await screen.findByRole('heading', { level: 1, name: 'Sistem' })).toBeInTheDocument();
    await screen.findByTestId('system-block-clock');
    expect(
      within(screen.getByTestId('system-blocks'))
        .getAllByRole('heading', { level: 2 })
        .map((heading) => heading.textContent),
    ).toEqual(BLOCKS_TR);
    const [data, systemDrive] = within(block('disks')).getAllByRole('meter');
    expect(data).toHaveAccessibleName('D: sürücüsünün doluluğu');
    expect(data).toHaveAttribute('aria-valuenow', '59');
    expect(systemDrive).toHaveAccessibleName('C: sürücüsünün doluluğu');
    expect(block('services')).toHaveTextContent('ArciCollector');
    expect(block('clock')).toHaveTextContent('time.ume.tubitak.gov.tr');
    // The licence maintenance comes from `GET /license`.
    expect(await within(block('version')).findByText(/bakım süresi doldu/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    // The first load is the user's own request.
    expect(system(calls).map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([null, null]);
  });

  it("keeps the summary's blocks when the health fails: the error is in the Saat block", async () => {
    const calls = serve('admin', { health: unavailable });
    renderApp('/system');
    const error = await screen.findByTestId('system-health-error');
    expect(within(block('clock')).getByTestId('system-health-error')).toBe(error);
    expect(error).toHaveTextContent('Veritabanına ulaşılamıyor. (database_unavailable)');
    expect(within(block('services')).getByTestId('system-health-missing')).toHaveTextContent(
      'Durum alınamadı; hata Saat bölümünde.',
    );
    expect(within(block('disks')).getAllByRole('meter')).toHaveLength(2);
    // Not the page's error: the page has no "Sistem durumu alınamadı." alert.
    expect(screen.queryByText('Sistem durumu alınamadı.')).toBeNull();

    // "Tekrar dene" asks both again, as the user.
    await userEvent.click(within(error).getByRole('button', { name: 'Tekrar dene' }));
    await waitFor(() => {
      expect(system(calls, '/system/health')).toHaveLength(2);
    });
    expect(system(calls, '/system')).toHaveLength(2);
    expect(system(calls).every((call) => call.headers.get(BACKGROUND_HEADER) === null)).toBe(true);
  });

  it('shows skeletons, not "Durum henüz alınmadı.", while the health is still loading', async () => {
    let answer: (response: Response) => void = () => undefined;
    const pending = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    serve('admin', { health: () => pending });
    renderApp('/system');
    await screen.findByTestId('system-block-disks');
    expect(within(block('disks')).getAllByRole('meter')).toHaveLength(2);
    // The health has not answered yet: its four blocks are skeletons, not "not received".
    expect(screen.queryByTestId('system-health-missing')).toBeNull();
    for (const id of ['services', 'tasks', 'jobs', 'clock'])
      expect(screen.queryByTestId(`system-block-${id}`)).toBeNull();
    answer(jsonResponse(200, HEALTH.health));
    expect(await screen.findByTestId('system-block-clock')).toHaveTextContent(
      'time.ume.tubitak.gov.tr',
    );
    expect(block('services')).toHaveTextContent('ArciCollector');
    expect(screen.queryByTestId('system-health-missing')).toBeNull();
  });

  it("shows a failed summary as the page's error and keeps the health's blocks", async () => {
    serve('admin', { summary: unavailable });
    renderApp('/system');
    expect(await screen.findByText('Sistem durumu alınamadı.')).toBeInTheDocument();
    expect(await screen.findByTestId('system-block-clock')).toHaveTextContent(
      'time.ume.tubitak.gov.tr',
    );
    expect(block('services')).toHaveTextContent('ArciCollector');
    expect(screen.queryByTestId('system-block-disks')).toBeNull();
  });

  it('"Yenile" asks both again as the user, not in the background', async () => {
    const calls = serve('admin');
    renderApp('/system');
    await screen.findByTestId('system-block-clock');
    await userEvent.click(screen.getByRole('button', { name: 'Yenile' }));
    await waitFor(() => {
      expect(system(calls)).toHaveLength(4);
    });
    expect(system(calls).map((call) => call.headers.get(BACKGROUND_HEADER))).toEqual([
      null,
      null,
      null,
      null,
    ]);
  });

  it.each(['auditor', 'hr', 'viewer'] as const)(
    'refuses %s the page by URL, without asking the API',
    async (role) => {
      const calls = serve(role);
      renderApp('/system');
      expect(
        await screen.findByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
      ).toBeInTheDocument();
      expect(system(calls)).toEqual([]);
    },
  );
});

describe('Sistem — polling', () => {
  function wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={testQueryClient()}>{children}</QueryClientProvider>;
  }

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('refreshes the summary and the health every 30 s in the background; "Yenile" is the user\'s', async () => {
    expect(SYSTEM_POLL_MS).toBe(30_000);
    const calls = installFetch((request) =>
      request.path === '/api/v1/system'
        ? jsonResponse(200, SYSTEM.summary)
        : jsonResponse(200, HEALTH.health),
    );
    const { result } = renderHook(
      () => ({ summary: useSystemSummary(), health: useSystemHealth() }),
      {
        wrapper,
      },
    );
    await act(() => vi.advanceTimersByTimeAsync(10));
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    await act(async () => {
      await Promise.all([result.current.summary.refresh(), result.current.health.refresh()]);
    });
    for (const path of ['/system', '/system/health']) {
      expect(
        system(calls, path).map((call) => call.headers.get(BACKGROUND_HEADER)),
        path,
      ).toEqual([null, '1', '1', null]);
    }
  });
});
