import { expect, test, type Locator, type Page, type Request, type Route } from '@playwright/test';

import type { AdminConfig, AdminRequestPage, AdminStatus } from '../src/lib/settings-types';
import type { SourcesOverview } from '../src/lib/sources-types';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { ADMIN, copy, SOURCES } from './samples';

/**
 * İlk kurulum sihirbazı (`/settings/setup`, Wave 4 contract 2.8.4): a fresh installation walked
 * step by step, every step's state, what it still lacks and the Ayarlar page it opens; the sources
 * step's firewall notes and data arrival; a Kamu SM step link and the way back to the wizard.
 *
 * The captured installation (`samples/admin.json`) is not fresh: its site name is set, two sources
 * are enabled and the replica is on. Installed after `mockApi` (`page.route`: the newest route
 * wins), `FreshInstallation` answers `GET /admin/config`, `/admin/status`, `/admin/requests` and
 * `/sources` with modified copies of the captured answers, as a new installation made from
 * `deploy/windows/arcilogger.example.toml` has them; every other call goes on to the main mock.
 */

class FreshInstallation {
  /** No site name, no local time source, no source; Zamane on the test server, nothing stored. */
  config: AdminConfig;
  status: AdminStatus;
  /** The request history: no stamp test yet. */
  requests: AdminRequestPage = { items: [], next_cursor: null };
  /** `GET /sources`: nothing has sent yet. */
  overview: SourcesOverview;

  constructor() {
    const config = copy(ADMIN.config);
    config.sections.general.site_name = '';
    config.sections.sources = [];
    config.sections.replica.enabled = false;
    config.sections.replica.target = null;
    config.sections.tsa.provider = 'zamane';
    config.sections.tsa.server = 'test';
    config.sections.tsa.seal_ready = { ok: false, causes: ['zamane-jar', 'credentials', 'roots'] };
    // `config.setup_problems` of such a configuration, in the interface's two languages.
    config.problems = [
      {
        level: 'error',
        key: 'site_name',
        message_tr:
          'Kuruluş adı (site_name) ayarlanmamış: raporlar, e-postalar ve kanıt paketleri kuruluş adı olmadan oluşturulur; kurulumda -SiteName ile girin',
        message_en:
          'the organisation name (site_name) is not set: reports, e-mails and evidence packages are made without it; enter it with -SiteName',
      },
      ...copy(ADMIN.config.problems),
      {
        level: 'warning',
        key: 'sources',
        message_tr:
          "Log kaynağı ([[sources]]) tanımlanmamış: güvenlik duvarınızın ve ana yönlendiricinizin [[sources]] kayıtlarını arcilogger.toml dosyasına ekleyin (docs/DEVICE-SETUP.md bölüm 4); o zamana kadar kurulumun güvenlik duvarı 514'ü hiçbir göndericiye açmaz ve sessiz kaynak uyarısı (R15) hiçbir kaynağı izlemez",
        message_en:
          "no log source ([[sources]]) is configured: add the [[sources]] of your firewall and core router to arcilogger.toml (docs/DEVICE-SETUP.md section 4); until then the installer's firewall opens 514 to no sender and the silent-source alert (R15) watches no source",
      },
    ];
    this.config = config;

    const status = copy(ADMIN.status);
    if (status.agent.seal_ready) status.agent.seal_ready = copy(config.sections.tsa.seal_ready);
    if (status.system.firewall) status.system.firewall.syslog_addresses = [];
    this.status = status;

    this.overview = { ...copy(SOURCES.overview), items: [] };
  }

  async install(page: Page): Promise<void> {
    await this.answer(page, '/admin/config', () => this.config);
    await this.answer(page, '/admin/status', () => this.status);
    await this.answer(page, '/admin/requests', () => this.requests);
    await this.answer(page, '/sources', () => this.overview);
  }

  /** `GET path` (exactly, whatever the query) from this installation; the rest to the main mock. */
  private async answer(page: Page, path: string, body: () => unknown): Promise<void> {
    await page.route(
      (url) => url.pathname === `/api/v1${path}`,
      async (route: Route, request: Request) => {
        if (request.method() !== 'GET') {
          await route.fallback();
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: { 'Cache-Control': 'no-store' },
          body: JSON.stringify(body()),
        });
      },
    );
  }
}

async function freshInstallation(page: Page): Promise<{ api: MockApi; fresh: FreshInstallation }> {
  const api = await mockApi(page, { signedInAs: 'tolga', siteName: '' });
  const fresh = new FreshInstallation();
  await fresh.install(page);
  return { api, fresh };
}

const STEPS = [
  ['admin', 'Yönetici ve iki adımlı doğrulama', 'Tamam'],
  ['site', 'Site ayarları', 'Eksik'],
  ['sources', 'Log kaynakları', 'Eksik'],
  ['kamusm_test', 'Kamu SM: test sunucusu', 'Eksik'],
  ['kamusm_production', 'Kamu SM: üretim', 'Eksik'],
  ['replica', 'Replika', 'Eksik'],
  ['smtp', 'E-posta', 'Eksik'],
] as const;

const LEGAL =
  "Test sunucusunun damgalarının yasal geçerliliği yoktur (Kamu SM); üretime geçmeden önce kök sertifika parmak izlerini Kamu SM'nin yayımladığı değerlerle karşılaştırın (sahada doğrula).";

/** The wizard's step list. */
const stepList = (page: Page) => page.getByRole('navigation', { name: 'Sihirbaz adımları' });

/** The shown step: its card with the "n. Title" heading, its state and its actions. */
async function expectStep(
  page: Page,
  id: string,
  title: string,
  state: string | null,
  { inUrl = true }: { inUrl?: boolean } = {},
) {
  const index = STEPS.findIndex(([step]) => step === id);
  const number = index === -1 ? STEPS.length + 1 : index + 1;
  await expect(page).toHaveURL(
    new RegExp(inUrl ? `/settings/setup\\?step=${id}$` : '/settings/setup$'),
  );
  const card = page.getByTestId(`wizard-step-${id}`);
  await expect(card.getByRole('heading', { level: 2 })).toHaveText(`${number}. ${title}`);
  await expect(stepList(page).getByRole('link', { name: `${number}. ${title}` })).toHaveAttribute(
    'aria-current',
    'step',
  );
  if (state) await expect(card.getByText(state, { exact: true })).toBeVisible();
  return card;
}

/** "Bu adımı şimdilik atla": the next step (the skipped one keeps its state). */
async function skip(card: Locator) {
  await card.getByRole('link', { name: 'Bu adımı şimdilik atla' }).click();
}

test.describe('İlk kurulum sihirbazı', () => {
  for (const theme of ['light', 'dark'] as Theme[]) {
    test(`a fresh installation walked step by step — ${theme}`, async ({ page }) => {
      await usePreferences(page, { theme });
      const { api } = await freshInstallation(page);

      // Ayarlar's card opens the wizard on the first step that is not done.
      await page.goto('/settings');
      await page.getByTestId('settings-setup').getByRole('link').click();
      await expect(
        page.getByRole('heading', { level: 1, name: 'İlk kurulum sihirbazı' }),
      ).toBeVisible();
      await expect(page.getByTestId('settings-back')).toHaveText('Ayarlar');
      let card = await expectStep(page, 'site', 'Site ayarları', 'Eksik', { inUrl: false });

      // Every step with its state: icon and text, never colour alone.
      const links = stepList(page).getByRole('link');
      await expect(links).toHaveCount(STEPS.length + 1);
      for (const [index, [, title, state]] of STEPS.entries())
        await expect(links.nth(index)).toHaveText(`${index + 1}. ${title}${state}`);
      await expect(links.nth(STEPS.length)).toHaveText(`${STEPS.length + 1}. Özet1/7 tamam`);
      await expect(links.nth(STEPS.length).locator('svg')).toHaveCount(1);

      // 1. The administrator: done, as whoever opens the wizard signed in with two steps.
      await links.nth(0).click();
      card = await expectStep(page, 'admin', 'Yönetici ve iki adımlı doğrulama', 'Tamam');
      await expect(card.getByText('Bu adımda eksik bir şey yok.')).toBeVisible();
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveCount(0);
      await expectNoA11yViolations(page);
      await skip(card);

      // 2. Site settings: the site name is an error, the missing local time source a warning.
      card = await expectStep(page, 'site', 'Site ayarları', 'Eksik');
      await expect(card.getByTestId('wizard-missing')).toContainText(
        'Kuruluş adı ya da saat sunucusu ayarlanmamış (aşağıdaki sorunlar).',
      );
      const problems = card.getByTestId('config-problems').getByRole('listitem');
      await expect(problems).toHaveCount(2);
      await expect(problems.nth(0)).toContainText(
        'site_name — Kuruluş adı (site_name) ayarlanmamış',
      );
      await expect(problems.nth(1)).toContainText(
        'clock.ntp_server — Yerel saat kaynağı (clock.ntp_server) ayarlanmamış',
      );
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveAttribute(
        'href',
        '/settings/general?wizard=1',
      );
      await expectNoA11yViolations(page);
      await skip(card);

      // 3. Log sources: none yet; where the devices are described, no command, no device list.
      card = await expectStep(page, 'sources', 'Log kaynakları', 'Eksik');
      await expect(card.getByTestId('wizard-missing')).toContainText(
        'Henüz log kaynağı tanımlanmadı.',
      );
      await expect(
        card.getByText(
          'Cihaz ayarları: DEVICE-SETUP.md bölüm 1 ve 4 (komutlar sahada doğrulanmalıdır).',
        ),
      ).toBeVisible();
      await expect(card.getByTestId('wizard-firewall-managed')).toHaveCount(0);
      await expect(card.getByTestId('wizard-firewall-not_managed')).toHaveCount(0);
      await expect(card.getByTestId('wizard-arrivals')).toHaveCount(0);
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveAttribute(
        'href',
        '/settings/sources?wizard=1',
      );
      await expectNoA11yViolations(page);
      await skip(card);

      // 4. Kamu SM test server: no stamp test in the history; the test stamps' legal note.
      card = await expectStep(page, 'kamusm_test', 'Kamu SM: test sunucusu', 'Eksik');
      await expect(card.getByTestId('wizard-missing')).toContainText(
        'Başarılı ve doğrulanmış bir damga testi yok.',
      );
      await expect(card.getByTestId('wizard-kamusm-legal')).toHaveText(LEGAL);
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveAttribute(
        'href',
        '/settings/kamusm?step=test&wizard=1',
      );
      await expectNoA11yViolations(page);
      await skip(card);

      // 5. Kamu SM production: the test server, and sealing's causes in Kamu SM's words.
      card = await expectStep(page, 'kamusm_production', 'Kamu SM: üretim', 'Eksik');
      const lacking = card.getByTestId('wizard-missing').getByRole('listitem');
      await expect(lacking).toHaveText([
        "Zaman damgası Kamu SM'nin üretim sunucusundan alınmıyor.",
        'Zamane istemcisi ayarlı değil ya da çalıştırılamıyor.',
        'Kamu SM müşteri numarası ya da parolası kayıtlı değil.',
        'Kurulu kök sertifika yok.',
      ]);
      await expect(card.getByTestId('wizard-kamusm-legal')).toHaveText(LEGAL);
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveAttribute(
        'href',
        '/settings/kamusm?step=production&wizard=1',
      );
      await expectNoA11yViolations(page);
      await skip(card);

      // 6. Replica: off.
      card = await expectStep(page, 'replica', 'Replika', 'Eksik');
      await expect(card.getByTestId('wizard-missing')).toContainText('Replika kapalı.');
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveAttribute(
        'href',
        '/settings/replica?wizard=1',
      );
      await expectNoA11yViolations(page);
      await skip(card);

      // 7. E-mail: neither server nor sender.
      card = await expectStep(page, 'smtp', 'E-posta', 'Eksik');
      await expect(card.getByTestId('wizard-missing').getByRole('listitem')).toHaveText([
        'SMTP sunucusu ayarlanmamış.',
        'Gönderen adresi ayarlanmamış.',
      ]);
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveAttribute(
        'href',
        '/settings/email?wizard=1',
      );
      await expectNoA11yViolations(page);
      await skip(card);

      // 8. Summary: the skipped steps stay "Eksik"; every problem of the configuration.
      card = await expectStep(page, 'summary', 'Özet', null);
      const summary = card.getByTestId('wizard-summary-steps').getByRole('listitem');
      await expect(summary).toHaveCount(STEPS.length);
      for (const [index, [, title, state]] of STEPS.entries())
        await expect(summary.nth(index)).toHaveText(`${index + 1}. ${title}${state}`);
      await expect(card.getByTestId('config-problems').getByRole('listitem')).toHaveCount(3);
      await expect(card.getByTestId('config-problems')).toContainText(
        'sources — Log kaynağı ([[sources]]) tanımlanmamış',
      );
      await expect(card.getByTestId('wizard-checklist')).toHaveText(
        'Kurulum sonrası denetim listesi: KURULUM.md bölüm 12',
      );
      await expect(card.getByRole('link', { name: 'Bu adımı şimdilik atla' })).toHaveCount(0);
      await expect(card.getByRole('link', { name: "Ayarlar'da aç" })).toHaveCount(0);
      await expectNoA11yViolations(page);

      // A summary row opens its step.
      await summary.nth(2).getByRole('link').click();
      await expectStep(page, 'sources', 'Log kaynakları', 'Eksik');

      // The wizard only reads: nothing was sent to the server.
      expect(api.calls.filter((call) => call.method !== 'GET')).toEqual([]);
    });
  }

  test('the sources step: the firewall note by the sources’ state, and whether data arrived', async ({
    page,
  }) => {
    const { fresh } = await freshInstallation(page);
    const sample = copy(ADMIN.config.sections.sources);
    const core = sample.find((item) => item.name === 'core-r1');
    if (!core) throw new Error('the captured configuration has no core-r1');
    // The core router sends already; a new firewall waits for its first line; a switch is off.
    fresh.config.sections.sources = [
      { ...core, firewall: 'open' },
      {
        ...core,
        name: 'kenar-fw',
        type: 'fortigate',
        address: '172.20.10.2',
        firewall: 'pending',
        last_seen: null,
      },
      {
        ...core,
        name: 'switch-3',
        type: 'generic',
        address: '172.20.10.24',
        enabled: false,
        firewall: 'closed',
        last_seen: null,
      },
    ];
    fresh.config.problems = fresh.config.problems.filter((item) => item.key !== 'sources');
    fresh.overview.items = SOURCES.overview.items
      .filter((item) => item.name === 'core-r1')
      .map((item) => copy(item));

    await page.goto('/settings/setup?step=sources');
    const card = await expectStep(page, 'sources', 'Log kaynakları', 'Tamam');
    await expect(card.getByText('Bu adımda eksik bir şey yok.')).toBeVisible();
    await expect(card.getByTestId('wizard-firewall-managed')).toHaveText(
      "Etkin kaynakların adreslerine 514 numaralı bağlantı noktasını ARCILOGGER'ın güvenlik duvarı kuralı kendisi açar.",
    );
    await expect(card.getByTestId('wizard-firewall-not_managed')).toHaveCount(0);
    const rows = card.getByTestId('wizard-arrivals').getByRole('listitem');
    await expect(rows).toHaveCount(3);
    await expect(rows.nth(0)).toContainText('core-r1172.20.10.1Veri geldison ileti');
    await expect(rows.nth(1)).toContainText('kenar-fw172.20.10.2Henüz veri gelmedi');
    await expect(rows.nth(2)).toContainText('switch-3172.20.10.24Kapalı');
    await expect(card.getByRole('link', { name: 'Bu adımı şimdilik atla' })).toBeVisible();
    await expectNoA11yViolations(page);

    // A firewall ARCILOGGER does not manage: port 514 by hand.
    for (const item of fresh.config.sections.sources)
      if (item.enabled) item.firewall = 'not_managed';
    await page.reload();
    await expect(card.getByTestId('wizard-firewall-not_managed')).toHaveText(
      'Güvenlik duvarı ARCILOGGER tarafından yönetilmiyor; 514 numaralı bağlantı noktasını bu adreslere elle açın (KURULUM.md bölüm 12).',
    );
    await expect(card.getByTestId('wizard-firewall-managed')).toHaveCount(0);
  });

  test('a Kamu SM step link leads back to the wizard, also after the page drops the query', async ({
    page,
  }) => {
    await freshInstallation(page);
    await page.goto('/settings/setup?step=kamusm_test');
    const card = await expectStep(page, 'kamusm_test', 'Kamu SM: test sunucusu', 'Eksik');
    await card.getByRole('link', { name: "Ayarlar'da aç" }).click();

    await expect(page).toHaveURL(/\/settings\/kamusm\?step=test&wizard=1$/);
    await expect(
      page.getByRole('heading', { level: 1, name: 'Kamu SM zaman damgası' }),
    ).toBeVisible();
    await expect(page.getByTestId('kamusm-step-test')).toBeVisible();
    const back = page.getByTestId('settings-back');
    await expect(back).toHaveText('Kurulum sihirbazına dön');
    await expect(back).toHaveAttribute('href', '/settings/setup');

    // Kamu SM's own step links replace the whole query: wizard mode is kept for this tab.
    await page
      .getByRole('navigation', { name: 'Kurulum adımları' })
      .getByRole('link', { name: /^6\. Üretime geç/ })
      .click();
    await expect(page).toHaveURL(/\/settings\/kamusm\?step=production$/);
    await expect(page.getByTestId('kamusm-step-production')).toBeVisible();
    await expect(back).toHaveText('Kurulum sihirbazına dön');

    await back.click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'İlk kurulum sihirbazı' }),
    ).toBeVisible();
    await expectStep(page, 'site', 'Site ayarları', 'Eksik', { inUrl: false });

    // Back in the wizard, wizard mode ended: the Kamu SM page leads to Ayarlar again.
    await page.goto('/settings/kamusm');
    await expect(page.getByTestId('kamusm-steps')).toBeVisible();
    await expect(page.getByTestId('settings-back')).toHaveText('Ayarlar');
  });
});
