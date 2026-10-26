import { expect, test, type Locator, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi } from './mock-api';
import { copy, EVIDENCE, SAMPLE_TODAY } from './samples';

/**
 * WCAG 2.1 AA scans of the 5651 lookup, evidence package, sealing, devices/persons, dashboard
 * (Panel), security, reports and usage pages, light and dark.
 */
for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(`accessibility — ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      // Each test scans several pages (20–30 s alone): room for a busy machine.
      test.setTimeout(90_000);
      await useSampleClock(page, SAMPLE_TODAY);
      await usePreferences(page, { theme });
    });

    test('5651 Sorgu: form errors, chain, IP conflict, candidates, raw line, history', async ({
      page,
    }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/lookup');
      await expect(page.getByRole('heading', { level: 1, name: '5651 Sorgu' })).toBeVisible();
      await page.getByRole('button', { name: 'Sorgula' }).click();
      await expect(page.getByText('Bu alan zorunludur.').first()).toBeVisible();
      await expectNoA11yViolations(page);

      await page.goto('/lookup/1');
      await expect(page.getByTestId('chain-summary').first()).toBeVisible();
      await expectNoA11yViolations(page);
      await page
        .getByRole('button', { name: /satır 2 / })
        .first()
        .click();
      await expect(page.getByRole('dialog', { name: 'Ham kayıt satırı' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await page.goto('/lookup/3');
      await expect(page.getByTestId('ip-conflict')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.goto('/lookup/2');
      await expect(page.getByTestId('match')).toHaveCount(3);
      await expectNoA11yViolations(page);
      await page.getByRole('tab', { name: 'Önceki sorgular' }).click();
      await expect(
        page.getByRole('table', { name: 'Önceki 5651 sorguları, en yeni önce' }),
      ).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Kanıt Paketleri: list, detail, wizard', async ({ page }) => {
      const api = await mockApi(page, { signedInAs: 'tolga' });
      // A package past its expiry (not purged yet) and a large one: their notes are scanned too.
      const stale = EVIDENCE.ready_expired;
      api.records.packages.set(stale.id, copy(stale));
      const large = EVIDENCE.details['1'];
      const stored = api.records.packages.get(large?.id ?? 1);
      if (!large || !stored) throw new Error('evidence samples missing');
      stored.size = 3 * 1024 ** 3;
      await page.goto('/evidence');
      const table = page.getByRole('table', { name: 'Kanıt paketleri, en yeni önce' });
      await expect(table.getByRole('row')).toHaveCount(6);
      await expectNoA11yViolations(page);
      await table.getByRole('row', { name: /#1/ }).click();
      await expect(page.getByRole('dialog')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');
      await table.getByRole('row', { name: /#4/ }).click();
      await expect(page.getByRole('dialog', { name: 'Paket #4' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');
      await table.getByRole('row', { name: new RegExp(stale.package_name ?? '-') }).click();
      await expect(page.getByTestId('expired-unpurged')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');
      await table.getByRole('row', { name: new RegExp(large.package_name ?? '-') }).click();
      await expect(page.getByTestId('large-package')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await page.getByRole('button', { name: 'Yeni paket' }).click();
      const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
      await wizard.getByRole('button', { name: 'İleri' }).click();
      await expect(wizard.getByText('Bu alan zorunludur.').first()).toBeVisible();
      await expectNoA11yViolations(page);
      await wizard.getByLabel('İlk gün').fill('04.10.2026');
      await wizard.getByLabel('Son gün').fill('05.10.2026');
      await wizard.getByRole('button', { name: 'İleri' }).click();
      await expect(wizard.getByRole('radio', { name: /Tam günler/ })).toBeChecked();
      await expectNoA11yViolations(page);
      await wizard.getByRole('button', { name: 'İleri' }).click();
      await wizard.getByLabel('Paketi parola ile koru (AES-256)').check();
      await wizard.getByRole('button', { name: 'İleri' }).click();
      await expect(wizard.getByRole('button', { name: 'Paketi oluştur' })).toBeVisible();
      await expectNoA11yViolations(page);
      await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
      await expect(page.getByRole('dialog', { name: 'Paket parolası' })).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Cihazlar ve Kişiler: list, selection, assignment, drawers, departments, VLANs, import', async ({
      page,
    }) => {
      const api = await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/devices');
      const table = page.getByRole('table', { name: 'Ağda görülen cihazlar' });
      await expect(table.getByRole('row')).toHaveCount(7);
      await table.getByRole('checkbox', { name: 'Seç: 02:00:5e:00:53:64' }).check();
      await expect(page.getByTestId('bulk-bar')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.getByTestId('bulk-bar').getByRole('button', { name: 'Kişiye ata' }).click();
      const assign = page.getByRole('dialog', { name: 'Kişiye ata' });
      await assign.getByLabel('Belirli bir zamandan itibaren').check();
      await assign.getByRole('button', { name: 'Ata', exact: true }).click();
      await expect(assign.getByText('Bir kişi seçin.')).toBeVisible();
      await expect(assign.getByTestId('randomized-notice')).toBeVisible();
      await expectNoA11yViolations(page);
      await assign.getByRole('combobox', { name: 'Kişi' }).click();
      await expect(page.getByRole('option', { name: /Can Öztürk/ })).toBeVisible();
      await expectNoA11yViolations(page);
      // The first Escape closes the list, the second the dialog: pressed back to back, the second
      // can reach the list before it has closed.
      await page.keyboard.press('Escape');
      await expect(page.getByRole('option', { name: /Can Öztürk/ })).toBeHidden();
      await page.keyboard.press('Escape');
      await expect(assign).toBeHidden();

      await table.getByRole('button', { name: '02:00:5e:00:53:64' }).click();
      const drawer = page.getByRole('dialog', { name: '02:00:5e:00:53:64' });
      await expect(drawer.getByRole('table', { name: 'Son kira kayıtları' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await page.goto('/devices?tab=persons');
      await expect(page.getByRole('table', { name: 'Kişiler' }).getByRole('row')).toHaveCount(3);
      await expectNoA11yViolations(page);
      await page
        .getByRole('table', { name: 'Kişiler' })
        .getByRole('button', { name: 'Selin Kaya', exact: true })
        .click();
      await expect(page.getByRole('dialog', { name: 'Selin Kaya' })).toContainText(
        'Şu anki cihazları (2)',
      );
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Kişi ekle' }).click();
      const person = page.getByRole('dialog', { name: 'Kişi ekle' });
      await person.getByRole('button', { name: 'Kişiyi ekle' }).click();
      await expect(person.getByText('Bu alan zorunludur.')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await page.goto('/devices?tab=departments');
      await expect(page.getByRole('combobox', { name: 'Departman: VLAN 43' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.getByRole('button', { name: 'VLAN ekle' }).click();
      const vlan = page.getByRole('dialog', { name: 'VLAN ekle' });
      await vlan.getByLabel('Ağ', { exact: true }).fill('10.0.20.5/24');
      await vlan.getByLabel('DHCP sunucusu').fill('dhcp 20');
      await vlan.getByRole('button', { name: "VLAN'ı ekle" }).click();
      await expect(vlan.getByText(/Ana bilgisayar bitleri sıfır olmalı/)).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');
      await expect(vlan).toBeHidden();
      await page.getByRole('button', { name: 'İşlemler: VLAN 43' }).click();
      await page.getByRole('menuitem', { name: 'Sil' }).click();
      await expect(page.getByRole('dialog', { name: "VLAN'ı sil" })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await page.goto('/devices?tab=transfer');
      await page.getByLabel('CSV dosyası').setInputFiles({
        name: 'kisiler.csv',
        mimeType: 'text/csv',
        buffer: Buffer.from(
          'name;department;title;email;employee_no;active;notes\r\nBurak Çelik;İdari İşler;;;2002;evet;\r\n;İdari İşler;;;;evet;\r\n',
          'utf8',
        ),
      });
      await page.getByRole('button', { name: 'Kontrol et (deneme)' }).click();
      await expect(page.getByText('1 satır kabul edilmedi')).toBeVisible();
      await expectNoA11yViolations(page);

      // A new installation: no VLANs yet.
      api.devices.vlans.splice(0);
      await page.goto('/devices?tab=departments');
      await expect(page.getByText('Henüz VLAN yok: ağınızı ekleyin')).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Panel: every block of the administrator', async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/');
      await expect(page.getByTestId('card-calendar')).toBeVisible();
      await expect(page.getByTestId('card-usage')).toContainText('717,3 KB');
      await expectNoA11yViolations(page);
      await page.getByTestId('card-usage').getByRole('button', { name: 'Tablo görünümü' }).click();
      await expect(
        page
          .getByTestId('card-usage')
          .getByRole('table', { name: 'İndirilen / yüklenen (saatlik)' }),
      ).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Güvenlik: summary, filtered list, drawer, state form and note errors', async ({
      page,
    }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/security?severity=high');
      await expect(
        page.getByRole('table', { name: 'Güvenlik uyarıları' }).getByRole('row'),
      ).toHaveCount(6);
      await expectNoA11yViolations(page);
      await page.goto('/security?alert=7');
      const drawer = page.getByRole('dialog', { name: /D: diski %84 dolu/ });
      await expect(drawer.getByRole('region', { name: 'Geçmiş' })).toContainText(
        'Disk büyütülecek',
      );
      await drawer.getByRole('button', { name: 'Uyarıyı kapat' }).click();
      await drawer.getByRole('button', { name: 'Not ekle' }).click();
      await expect(drawer.getByText('Bu alan zorunludur.')).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Raporlar: schedules, history, the schedule form with errors', async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/reports');
      await expect(
        page.getByRole('table', { name: 'Zamanlanmış raporlar' }).getByRole('row'),
      ).toHaveCount(6);
      await expectNoA11yViolations(page);

      await page.getByRole('button', { name: 'Yeni zamanlama' }).click();
      const form = page.getByRole('dialog', { name: 'Yeni zamanlanmış rapor' });
      await form.getByLabel('Tür', { exact: true }).click();
      await page.getByRole('option', { name: 'Aylık' }).click();
      await form.getByLabel('Ayın günü').fill('31');
      await form.getByRole('textbox', { name: 'Alıcılar' }).fill('tolga@ornek.com.tr');
      await form.getByRole('textbox', { name: 'Alıcılar' }).press('Enter');
      await form.getByRole('textbox', { name: 'Alıcılar' }).fill('yanlis@');
      await form.getByRole('button', { name: 'Zamanlamayı ekle' }).click();
      await expect(form.getByText('1 ile 28 arasında bir gün yazın.')).toBeVisible();
      await expect(form.getByText('Geçerli bir e-posta adresi girin.')).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Raporlar: run now with a custom period, preview, run details', async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/reports');
      await page.getByRole('button', { name: 'İşlemler: Gün sonu raporu' }).click();
      await page.getByRole('menuitem', { name: 'Şimdi çalıştır' }).click();
      const run = page.getByRole('dialog', { name: 'Şimdi çalıştır' });
      await run.getByRole('radio', { name: /Özel dönem/ }).check();
      await run.getByRole('button', { name: 'Çalıştır' }).click();
      await expect(run.getByText('Bu alan zorunludur.').first()).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      await page.getByRole('button', { name: 'İşlemler: Gün sonu raporu' }).click();
      await page.getByRole('menuitem', { name: 'Önizle' }).click();
      const preview = page.getByRole('dialog', { name: 'Önizle: Gün sonu raporu' });
      await preview.getByRole('button', { name: 'E-posta önizlemesi (HTML)' }).click();
      await expect(preview.getByTestId('preview-frame')).toBeVisible();
      // The frame holds the server's e-mail HTML, sandboxed without scripts (axe cannot run in it).
      await expectNoA11yViolations(page, { exclude: ['[data-testid="preview-frame"]'] });
      await page.keyboard.press('Escape');

      await page
        .getByRole('table', { name: 'Rapor çalışmaları, en yeni önce' })
        .getByRole('button', { name: 'Çalışma #3 ayrıntıları' })
        .click();
      await expect(page.getByRole('dialog', { name: 'Çalışma #3' })).toContainText(
        'mailbox unavailable',
      );
      await expectNoA11yViolations(page);
    });

    test('Mühürleme: calendar, sealed day with the mock TSA, failed day, verification', async ({
      page,
    }) => {
      await mockApi(page, { signedInAs: 'tolga', records: { credits: 'kamusm_low' } });
      await page.goto('/sealing');
      await expect(page.getByRole('heading', { name: '12.10.2026' })).toBeVisible();
      await expect(page.getByText('Eşiğin altında')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.goto('/sealing?day=2026-10-04');
      await expect(page.getByTestId('not-legal-grade')).toBeVisible();
      await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
      await expect(page.getByTestId('verify-result')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.goto('/sealing?day=2026-10-11');
      await expect(
        page.getByText('mock TSA unreachable (injected)', { exact: true }),
      ).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Kullanım: every tab and detail, the site drawer, the period and rebuild dialogs', async ({
      page,
    }) => {
      test.setTimeout(240_000);
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/usage');
      await expect(page.getByRole('group', { name: 'Şirket geneli özet' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.getByTestId('period').click();
      await expect(page.getByRole('button', { name: 'Geçen hafta' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');
      await page.getByRole('button', { name: 'Diğer işlemler' }).click();
      await page.getByRole('menuitem', { name: 'Yeniden hesapla…' }).click();
      const rebuild = page.getByRole('dialog', { name: 'Kullanımı yeniden hesapla' });
      await rebuild.getByLabel('İlk gün').fill('31.02.2026');
      await rebuild.getByRole('button', { name: 'Hesaplamayı başlat' }).click();
      await expect(rebuild.getByText('Günü GG.AA.YYYY biçiminde yazın.')).toBeVisible();
      await expectNoA11yViolations(page);
      await rebuild.getByLabel('İlk gün').fill('12.10.2026');
      await rebuild.getByRole('button', { name: 'Hesaplamayı başlat' }).click();
      await expect(rebuild.getByRole('progressbar')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.keyboard.press('Escape');

      // The captured day (12.10.2026) and generated periods.
      const day = 'from=2026-10-12&to=2026-10-12';
      const views: [string, (p: Page) => Locator][] = [
        [`/usage?${day}`, (p) => p.getByRole('table', { name: 'Departmanlar' })],
        [`/usage?tab=departments&${day}`, (p) => p.getByRole('table', { name: 'Departmanlar' })],
        [
          `/usage?tab=departments&${day}&department=Finans`,
          (p) => p.getByRole('table', { name: 'Finans departmanının kişileri' }),
        ],
        ['/usage?tab=persons', (p) => p.getByRole('table', { name: 'Kişiler' })],
        [
          `/usage?tab=devices&${day}`,
          (p) => p.getByRole('table', { name: 'Cihazlar ve IP adresleri' }),
        ],
        [
          `/usage?tab=devices&${day}&subject=device%3A3c%3A52%3A82%3A00%3A10%3A02`,
          (p) => p.getByRole('heading', { level: 2, name: 'PC-02' }),
        ],
        [`/usage?tab=sites&${day}`, (p) => p.getByRole('table', { name: 'Siteler' })],
        [
          `/usage?tab=sites&${day}&site=portal.example`,
          (p) => p.getByRole('table', { name: 'portal.example kullanıcıları' }),
        ],
        ['/usage?tab=categories', (p) => p.getByRole('table', { name: 'Kategoriler' })],
        [
          `/usage?tab=categories&${day}&category=Video`,
          (p) => p.getByRole('table', { name: 'Video kategorisinin siteleri' }),
        ],
        ['/usage?from=2026-01-05&to=2026-01-05', (p) => p.getByText('Bu aralıkta trafik yok')],
        [
          '/usage?tab=persons&subject=person%3A1&from=2026-10-05&to=2026-10-11',
          (p) => p.getByRole('table', { name: 'Günlere göre' }),
        ],
      ];
      for (const [url, ready] of views) {
        await page.goto(url);
        await expect(ready(page).first()).toBeVisible();
        await expectNoA11yViolations(page);
      }

      // A person's day: header, timeline, heat map, then every section and the site drawer.
      await page.goto(`/usage?tab=persons&${day}&subject=person%3A1`);
      await expect(page.getByRole('table', { name: /ısı haritası$/ })).toBeVisible();
      await expectNoA11yViolations(page);
      const sections = page.getByRole('tablist', { name: 'Ayrıntılar' });
      for (const name of [
        /^URL'ler/,
        /^Kategoriler/,
        /^Uygulamalar/,
        /^Engellenenler/,
        /^Cihazlar/,
      ]) {
        await sections.getByRole('tab', { name }).click();
        await expect(page.getByRole('tabpanel').last()).toBeVisible();
        await expectNoA11yViolations(page);
      }
      await sections.getByRole('tab', { name: /^Siteler/ }).click();
      await page
        .getByRole('table', { name: 'Ayşe Yılmaz kullanıcısının siteleri' })
        .locator('tbody tr')
        .first()
        .getByRole('button')
        .first()
        .click();
      await expect(page.getByRole('button', { name: 'Site ayrıntısına git' })).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('Kullanım: pseudonyms and the KVKK wait', async ({ page }) => {
      await mockApi(page, { signedInAs: 'zeynep', usage: { pseudonymize: true } });
      await page.goto('/usage?tab=persons&from=2026-10-12&to=2026-10-12');
      await expect(
        page.getByRole('table', { name: 'Kişiler' }).getByText('anonim').first(),
      ).toBeVisible();
      await expectNoA11yViolations(page);
      const hr = await mockApi(page, { signedInAs: 'mehmet.kaya' });
      hr.get('mehmet.kaya').privacy_accepted = false;
      await page.goto('/usage?tab=persons');
      await expect(page.getByText('KVKK bilgilendirmesinin onayı bekleniyor')).toBeVisible();
      await expectNoA11yViolations(page);
    });
  });
}
