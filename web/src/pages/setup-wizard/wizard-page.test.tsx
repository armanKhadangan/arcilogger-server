import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { queryKeys } from '@/lib/api-hooks';
import { setLanguage } from '@/lib/i18n';
import type { AdminConfig, AdminRequestOut } from '@/lib/settings-types';
import { errorResponse, installFetch, jsonResponse, makeMe } from '@/test/api-mock';
import { renderApp } from '@/test/render';
import { ADMIN, SOURCES } from '@/test/samples';
import { capturedConfig, FRESH_PROBLEMS, freshConfig, stampTest } from '@/test/w4-fixtures';

import { STEP_LINKS } from './wizard-model';

/*
 * İlk kurulum sihirbazı (`/settings/setup`, Wave 4 contract 2.8.4): the steps as a list of
 * `?step=` links with their state, each step's page opened in wizard mode, "skip" that stores
 * nothing, and a summary with the configuration's problems in the interface language.
 */

beforeAll(async () => {
  await import('@/pages/setup-wizard/wizard-page');
}, 120_000);

function serve({
  config = freshConfig(),
  tests = [],
  permissions,
}: {
  config?: AdminConfig | null;
  tests?: AdminRequestOut[];
  permissions?: string[];
} = {}) {
  const me = makeMe('admin');
  return installFetch((request) => {
    if (request.path === '/api/v1/auth/me')
      return jsonResponse(200, permissions ? { ...me, permissions } : me);
    if (request.path === '/api/v1/admin/config')
      return config ? jsonResponse(200, config) : errorResponse(500, 'internal_error');
    if (request.path === '/api/v1/admin/status') return jsonResponse(200, ADMIN.status);
    if (request.path === '/api/v1/admin/requests')
      return jsonResponse(200, { items: tests, next_cursor: null });
    if (request.path === '/api/v1/sources') return jsonResponse(200, SOURCES.overview);
    return errorResponse(404, 'not_found');
  });
}

const steps = () => screen.findByRole('navigation', { name: 'Sihirbaz adımları' });

async function stepLink(name: RegExp) {
  return within(await steps()).getByRole('link', { name });
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.clear();
  act(() => {
    setLanguage('tr');
  });
});

describe('İlk kurulum sihirbazı', () => {
  it('opens the first step not done, with a badge of icon and text per step', async () => {
    serve();
    renderApp('/settings/setup');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent(
      'İlk kurulum sihirbazı',
    );
    const current = within(await steps()).getByRole('link', { current: 'step' });
    expect(current).toHaveTextContent('2. Site ayarları');
    expect(current).toHaveTextContent('Eksik');
    expect(await stepLink(/Yönetici ve iki adımlı doğrulama/)).toHaveTextContent('Tamam');
    expect(await stepLink(/Özet/)).toHaveTextContent('1/7 tamam');
    for (const link of within(await steps()).getAllByRole('link'))
      expect(link.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
    const card = screen.getByTestId('wizard-step-site');
    expect(card).toHaveTextContent('Kuruluş adı ya da saat sunucusu ayarlanmamış');
    expect(card).toHaveTextContent('site_name — Kuruluş adı (site_name) ayarlanmamış');
  });

  it('navigates by ?step= and marks the current step', async () => {
    serve({ config: capturedConfig() });
    const { router } = renderApp('/settings/setup?step=replica');
    expect(await screen.findByTestId('wizard-step-replica')).toHaveTextContent('Tamam');
    await userEvent.click(await stepLink(/E-posta/));
    expect(router.state.location.search).toBe('?step=smtp');
    const card = await screen.findByTestId('wizard-step-smtp');
    expect(card).toHaveTextContent('SMTP sunucusu ayarlanmamış.');
    expect(card).toHaveTextContent('Gönderen adresi ayarlanmamış.');
    expect(await stepLink(/E-posta/)).toHaveAttribute('aria-current', 'step');
    expect(await stepLink(/Replika/)).not.toHaveAttribute('aria-current');
  });

  it('an unknown ?step= opens the first step not done', async () => {
    serve({ config: capturedConfig() });
    renderApp('/settings/setup?step=nothing');
    expect(await screen.findByTestId('wizard-step-kamusm_test')).toBeInTheDocument();
  });

  it('opens each step’s Ayarlar page with wizard=1', async () => {
    serve();
    const { router } = renderApp('/settings/setup?step=admin');
    await screen.findByTestId('wizard-step-admin');
    expect(screen.queryByRole('link', { name: "Ayarlar'da aç" })).not.toBeInTheDocument();
    for (const [step, href] of Object.entries(STEP_LINKS)) {
      await act(() => router.navigate(`/settings/setup?step=${step}`));
      const card = await screen.findByTestId(`wizard-step-${step}`);
      expect(within(card).getByRole('link', { name: "Ayarlar'da aç" })).toHaveAttribute(
        'href',
        href,
      );
    }
    expect(STEP_LINKS.kamusm_test).toBe('/settings/kamusm?step=test&wizard=1');
  });

  it('"Bu adımı şimdilik atla" goes to the next step; the skipped one stays Eksik', async () => {
    const calls = serve();
    const { router } = renderApp('/settings/setup?step=site');
    const card = await screen.findByTestId('wizard-step-site');
    await userEvent.click(within(card).getByRole('link', { name: 'Bu adımı şimdilik atla' }));
    expect(router.state.location.search).toBe('?step=sources');
    expect(await screen.findByTestId('wizard-step-sources')).toBeInTheDocument();
    expect(await stepLink(/Site ayarları/)).toHaveTextContent('Eksik');
    // Nothing is stored: skipping sends no request that changes anything.
    expect(calls.filter((call) => call.method !== 'GET')).toEqual([]);
  });

  it('reads the configuration again when the agent has applied a change', async () => {
    // The wizard was opened again before the agent wrote the new file: replica still off.
    let config = capturedConfig();
    config.sections.replica.enabled = false;
    let status = { ...ADMIN.status, config_sha256: config.config_sha256 };
    installFetch((request) => {
      if (request.path === '/api/v1/auth/me') return jsonResponse(200, makeMe('admin'));
      if (request.path === '/api/v1/admin/config') return jsonResponse(200, config);
      if (request.path === '/api/v1/admin/status') return jsonResponse(200, status);
      if (request.path === '/api/v1/admin/requests')
        return jsonResponse(200, { items: [], next_cursor: null });
      return errorResponse(404, 'not_found');
    });
    const { client } = renderApp('/settings/setup?step=replica');
    expect(await stepLink(/Replika/)).toHaveTextContent('Eksik');

    // The agent applies it; the next status poll names the file's new hash.
    config = capturedConfig();
    config.sections.replica.enabled = true;
    config.config_sha256 = 'b'.repeat(64);
    status = { ...status, config_sha256: config.config_sha256 };
    await act(() => client.invalidateQueries({ queryKey: queryKeys.adminStatus }));
    await waitFor(async () => {
      expect(await stepLink(/Replika/)).toHaveTextContent('Tamam');
    });
  });

  it('a step changed from inside the panel takes the focus along', async () => {
    serve();
    const { router } = renderApp('/settings/setup?step=smtp');
    const card = await screen.findByTestId('wizard-step-smtp');
    await userEvent.click(within(card).getByRole('link', { name: 'Bu adımı şimdilik atla' }));
    const summary = await screen.findByTestId('wizard-step-summary');
    expect(document.activeElement).not.toBe(document.body);
    expect(document.activeElement).toContainElement(summary);
    await userEvent.click(within(summary).getByRole('link', { name: '3. Log kaynakları' }));
    expect(router.state.location.search).toBe('?step=sources');
    expect(document.activeElement).toContainElement(
      await screen.findByTestId('wizard-step-sources'),
    );
  });

  it('lists the problems once: in the step, not above it', async () => {
    serve();
    renderApp('/settings/setup?step=summary');
    await screen.findByTestId('wizard-step-summary');
    expect(screen.getAllByTestId('config-problems')).toHaveLength(1);
  });

  it('asks GET /sources only on the sources step', async () => {
    const calls = serve({ config: capturedConfig() });
    const { router } = renderApp('/settings/setup?step=replica');
    await screen.findByTestId('wizard-step-replica');
    expect(calls.some((call) => call.path === '/api/v1/sources')).toBe(false);
    await act(() => router.navigate('/settings/setup?step=sources'));
    await screen.findAllByText('Veri geldi');
    expect(calls.some((call) => call.path === '/api/v1/sources')).toBe(true);
  });

  it('opens the sources page only with sources.manage', async () => {
    serve({
      permissions: makeMe('admin').permissions.filter((item) => item !== 'sources.manage'),
    });
    renderApp('/settings/setup?step=sources');
    const card = await screen.findByTestId('wizard-step-sources');
    expect(within(card).queryByRole('link', { name: "Ayarlar'da aç" })).not.toBeInTheDocument();
    expect(within(card).getByRole('link', { name: 'Bu adımı şimdilik atla' })).toBeInTheDocument();
  });

  it('the sources step: firewall note, device settings and whether data arrived', async () => {
    serve({ config: capturedConfig() });
    renderApp('/settings/setup?step=sources');
    const card = await screen.findByTestId('wizard-step-sources');
    expect(within(card).getByTestId('wizard-firewall-managed')).toHaveTextContent(
      "Etkin kaynakların adreslerine 514 numaralı bağlantı noktasını ARCILOGGER'ın güvenlik duvarı kuralı kendisi açar.",
    );
    expect(card).toHaveTextContent(
      'Cihaz ayarları: DEVICE-SETUP.md bölüm 1 ve 4 (komutlar sahada doğrulanmalıdır).',
    );
    const list = within(card).getByTestId('wizard-arrivals');
    const rows = await within(list).findAllByText('Veri geldi');
    expect(rows).toHaveLength(2);
    expect(list).toHaveTextContent('fortigate');
    expect(list).toHaveTextContent('core-r1');
    // Only the configured sources: no unknown sender, no device list of its own.
    expect(list).not.toHaveTextContent('switch-3');
    expect(list).not.toHaveTextContent('198.51.100.23');
  });

  it('the sources step without a managed firewall says to open port 514 by hand', async () => {
    const config = capturedConfig();
    for (const source of config.sections.sources) source.firewall = 'not_managed';
    serve({ config });
    renderApp('/settings/setup?step=sources');
    expect(await screen.findByTestId('wizard-firewall-not_managed')).toHaveTextContent(
      'Güvenlik duvarı ARCILOGGER tarafından yönetilmiyor; 514 numaralı bağlantı noktasını bu adreslere elle açın (KURULUM.md bölüm 12).',
    );
    expect(screen.queryByTestId('wizard-firewall-managed')).not.toBeInTheDocument();
  });

  it('the Kamu SM steps: a verified stamp test of the history, and the test server note', async () => {
    serve({ config: capturedConfig(), tests: [stampTest()] });
    const { router } = renderApp('/settings/setup?step=kamusm_test');
    const card = await screen.findByTestId('wizard-step-kamusm_test');
    expect(card).toHaveTextContent('Tamam');
    expect(within(card).getByTestId('wizard-kamusm-legal')).toHaveTextContent(
      "Test sunucusunun damgalarının yasal geçerliliği yoktur (Kamu SM); üretime geçmeden önce kök sertifika parmak izlerini Kamu SM'nin yayımladığı değerlerle karşılaştırın (sahada doğrula).",
    );
    // Without ?step= the wizard opens the production step now.
    await act(() => router.navigate('/settings/setup'));
    const current = within(await steps()).getByRole('link', { current: 'step' });
    expect(current).toHaveTextContent('Kamu SM: üretim');
    expect(current).toHaveTextContent('Eksik');
  });

  it('the summary lists every step and the problems in the interface language', async () => {
    serve();
    renderApp('/settings/setup?step=summary');
    const card = await screen.findByTestId('wizard-step-summary');
    const list = within(card).getByTestId('wizard-summary-steps');
    expect(within(list).getAllByRole('listitem')).toHaveLength(7);
    expect(within(list).getAllByText('Eksik')).toHaveLength(6);
    const problems = within(card).getByTestId('config-problems');
    for (const problem of FRESH_PROBLEMS) expect(problems).toHaveTextContent(problem.message_tr);
    expect(card).toHaveTextContent('Kurulum sonrası denetim listesi: KURULUM.md bölüm 12');
    expect(within(card).getByRole('link', { name: 'Uyum Durumu sayfasını aç' })).toHaveAttribute(
      'href',
      '/compliance',
    );
    expect(within(card).queryByRole('link', { name: "Ayarlar'da aç" })).not.toBeInTheDocument();

    act(() => {
      setLanguage('en');
    });
    for (const problem of FRESH_PROBLEMS) {
      expect(problems).toHaveTextContent(problem.message_en);
      expect(problems).not.toHaveTextContent(problem.message_tr);
    }
    expect(card).toHaveTextContent('Checks after installation: INSTALL.md section 12');
    expect(within(list).getAllByText('Missing')).toHaveLength(6);
  });

  it('the summary links Uyum Durumu only with compliance.read', async () => {
    serve({
      permissions: makeMe('admin').permissions.filter((item) => item !== 'compliance.read'),
    });
    renderApp('/settings/setup?step=summary');
    const card = await screen.findByTestId('wizard-step-summary');
    expect(within(card).queryByRole('link', { name: /Uyum Durumu/ })).not.toBeInTheDocument();
  });

  it('a configuration that does not load: the error with "Tekrar dene"', async () => {
    serve({ config: null });
    renderApp('/settings/setup');
    expect(await screen.findByText('Yapılandırma okunamadı')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Tekrar dene' })).toBeInTheDocument();
    expect(screen.queryByRole('navigation', { name: 'Sihirbaz adımları' })).not.toBeInTheDocument();
  });

  it('ends wizard mode; its way back leads to Ayarlar', async () => {
    window.sessionStorage.setItem('arci.setupWizard', '1');
    serve();
    renderApp('/settings/setup');
    await steps();
    expect(window.sessionStorage.getItem('arci.setupWizard')).toBeNull();
    expect(screen.getByTestId('settings-back')).toHaveAttribute('href', '/settings');
  });
});

describe('Ayarlar — the wizard’s entry', () => {
  it('a card "İlk kurulum sihirbazı" opens the wizard; the overview ends wizard mode', async () => {
    window.sessionStorage.setItem('arci.setupWizard', '1');
    serve();
    renderApp('/settings');
    const card = await screen.findByTestId('settings-setup');
    expect(within(card).getByRole('link')).toHaveAttribute('href', '/settings/setup');
    expect(card).toHaveTextContent('İlk kurulum sihirbazı');
    expect(window.sessionStorage.getItem('arci.setupWizard')).toBeNull();
  });
});
