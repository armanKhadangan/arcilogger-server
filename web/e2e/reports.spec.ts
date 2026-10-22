import { expect, test, type Page } from '@playwright/test';

import { useSampleClock } from './fixtures';
import { mockApi } from './mock-api';
import { SAMPLE_TODAY } from './samples';

/** Open a schedule's row menu and choose an item. */
async function rowAction(page: Page, name: string, item: string) {
  await page.getByRole('button', { name: `İşlemler: ${name}` }).click();
  await page.getByRole('menuitem', { name: item }).click();
}

const HISTORY = 'Rapor çalışmaları, en yeni önce';

test.describe('Raporlar', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('schedules with timing, recipients, formats, detail, next and last run', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/reports');
    await expect(page.getByRole('heading', { level: 1, name: 'Raporlar' })).toBeVisible();
    await expect(page.getByTestId('person-level-note')).toContainText('kişi düzeyinde ayrıntı');
    // The captured schedules: the four shipped ones (one switched on with recipients) and a new one.
    const table = page.getByRole('table', { name: 'Zamanlanmış raporlar' });
    await expect(table.getByRole('row')).toHaveCount(6);
    const fullDay = table.getByRole('row', { name: /Tam gün raporu/ });
    for (const text of [
      'Tam gün',
      'Her gün 07:00',
      'yonetim@ornek.example',
      '+1 alıcı daha',
      'E-posta',
      'PDF',
      'Excel',
      'Kişi düzeyinde',
      '14.10.2026 07:00:00',
      'Kısmen gönderildi',
    ])
      await expect(fullDay).toContainText(text);
    await expect(fullDay.getByRole('switch', { name: 'Etkin: Tam gün raporu' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    const weekly = table.getByRole('row', { name: /Haftalık rapor/ });
    await expect(weekly).toContainText('Pazartesi, 07:15');
    await expect(weekly).toContainText('Alıcı yok');
    await expect(weekly.getByRole('switch')).toBeDisabled();
    // A run without e-mail finished: not "Gönderildi".
    await expect(table.getByRole('row', { name: /Aylık rapor/ })).toContainText(
      'Tamamlandı (e-posta yok)',
    );
    await expect(table.getByRole('row', { name: /Yönetim haftalık özeti/ })).toContainText(
      'Cuma, 08:10',
    );

    const history = page.getByRole('table', { name: HISTORY });
    await expect(history.getByRole('row')).toHaveCount(4);
    const custom = history.getByRole('row', { name: /Çalışma #2 ayrıntıları/ });
    await expect(custom).toContainText('Özel dönem');
    await expect(custom).toContainText('05.10.2026 – 11.10.2026');
    await expect(custom).toContainText('E-posta yok');

    await history.getByRole('button', { name: 'Çalışma #3 ayrıntıları' }).click();
    await expect(page).toHaveURL(/\/reports\?run=3$/);
    const run = page.getByRole('dialog', { name: 'Çalışma #3' });
    await expect(run).toContainText('Tam gün raporu');
    await expect(run).toContainText('Sıraya alındı');
    const deliveries = run.getByRole('table', { name: 'Alıcılara teslim' });
    await expect(deliveries.getByRole('row', { name: /yonetim@ornek.example/ })).toContainText(
      'Gönderildi',
    );
    await expect(deliveries.getByRole('row', { name: /ik@ornek.example/ })).toContainText(
      '550 5.1.1 mailbox unavailable',
    );
    const download = page.waitForEvent('download');
    await run
      .getByRole('button', { name: 'İndir: ARCILOGGER-kullanim-tam-gun-20261012-r3.pdf' })
      .click();
    expect((await download).suggestedFilename()).toBe(
      'ARCILOGGER-kullanim-tam-gun-20261012-r3.pdf',
    );
    await run.getByRole('button', { name: 'Kapat', exact: true }).click();
    await expect(page).toHaveURL(/\/reports$/);
  });

  test("the e-mail's link opens the run directly (/reports?run=<id>)", async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/reports?run=3');
    const run = page.getByRole('dialog', { name: 'Çalışma #3' });
    await expect(run.getByRole('table', { name: 'Dosyalar' }).getByRole('row')).toHaveCount(4);
    expect(api.callsTo('GET', '/reports/runs/3').length).toBeGreaterThan(0);
    // A file deleted after reports.keep_days is said so.
    api.reports.expired.add(3);
    await run
      .getByRole('button', { name: 'İndir: ARCILOGGER-kullanim-tam-gun-20261012-r3.xlsx' })
      .click();
    await expect(
      page.getByText(
        'Bu raporun dosyaları saklama süresi (reports.keep_days) dolduğu için silindi.',
        { exact: true },
      ),
    ).toBeVisible();
  });

  test('create a schedule, run it now and see the run in the history', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/reports');
    await page.getByRole('button', { name: 'Yeni zamanlama' }).click();
    const dialog = page.getByRole('dialog', { name: 'Yeni zamanlanmış rapor' });
    await dialog.getByRole('button', { name: 'Zamanlamayı ekle' }).click();
    await expect(dialog.getByText('Bu alan zorunludur.')).toBeVisible();
    await expect(dialog.getByText('Etkin bir rapor için en az bir alıcı gerekir.')).toBeVisible();
    await expect(dialog.getByLabel('Ad', { exact: true })).toBeFocused();

    await dialog.getByLabel('Ad', { exact: true }).fill('Haftalık İK raporu');
    await dialog.getByLabel('Tür', { exact: true }).click();
    await page.getByRole('option', { name: 'Haftalık' }).click();
    await expect(dialog.getByLabel('Saat', { exact: true })).toHaveValue('07:15');
    await dialog.getByLabel('Gün', { exact: true }).click();
    await page.getByRole('option', { name: 'Cuma', exact: true }).click();
    await expect(dialog.getByTestId('next-run-preview')).toHaveText(
      'Sonraki çalışma: 16.10.2026 07:15 · kapsadığı dönem: 05.10.2026 – 11.10.2026',
    );

    const recipients = dialog.getByRole('textbox', { name: 'Alıcılar' });
    await recipients.fill('ik@ornek');
    await recipients.press('Enter');
    await expect(dialog.getByText('Geçerli bir e-posta adresi girin.')).toBeVisible();
    await recipients.fill('ik@ornek.com.tr');
    await recipients.press('Enter');
    await recipients.fill('yonetim@ornek.com.tr');
    await recipients.press(',');
    await expect(dialog.getByRole('list', { name: 'Alıcılar' }).getByRole('listitem')).toHaveText([
      'ik@ornek.com.tr',
      'yonetim@ornek.com.tr',
    ]);
    await dialog.getByLabel('Excel eki').check();
    await expect(dialog.getByLabel('Kişi düzeyinde ayrıntı')).toBeChecked();
    await expect(dialog.getByLabel('Adlar yerine takma ad')).not.toBeChecked();
    await dialog.getByRole('button', { name: 'Zamanlamayı ekle' }).click();
    await expect(page.getByText('Haftalık İK raporu eklendi.', { exact: true })).toBeVisible();
    expect(api.callsTo('POST', '/reports/schedules')[0]?.body).toEqual({
      name: 'Haftalık İK raporu',
      kind: 'weekly',
      enabled: true,
      time: '07:15',
      weekday: 5,
      recipients: ['ik@ornek.com.tr', 'yonetim@ornek.com.tr'],
      formats: { email_html: true, pdf: true, xlsx: true },
      person_level: true,
      pseudonymize: false,
    });
    const row = page
      .getByRole('table', { name: 'Zamanlanmış raporlar' })
      .getByRole('row', { name: /Haftalık İK raporu/ });
    await expect(row).toContainText('Cuma, 07:15');
    await expect(row).toContainText('16.10.2026 07:15:00');

    await rowAction(page, 'Haftalık İK raporu', 'Şimdi çalıştır');
    const run = page.getByRole('dialog', { name: 'Şimdi çalıştır' });
    // The API's own period of a weekly report run now: the previous week.
    await expect(run).toContainText('Şimdi çalışırsa kapsadığı dönem: 05.10.2026 – 11.10.2026');
    await expect(run.getByLabel('Alıcılara e-postayla gönder (2 adres)')).toBeChecked();
    await run.getByRole('button', { name: 'Çalıştır' }).click();
    await expect(
      page.getByText('Rapor sıraya alındı (çalışma #4).', { exact: true }),
    ).toBeVisible();
    expect(api.callsTo('POST', '/reports/schedules/6/run')[0]?.body).toEqual({ send: true });

    const history = page.getByRole('table', { name: HISTORY });
    const newest = history.getByRole('row').nth(1);
    await expect(newest).toContainText('Haftalık İK raporu');
    await expect(newest).toContainText('Elle: tolga');
    await expect(newest).toContainText('Gönderildi', { timeout: 15_000 });
    await expect(newest).toContainText('2/2 gönderildi');
    await history.getByRole('button', { name: 'Çalışma #4 ayrıntıları' }).click();
    const detail = page.getByRole('dialog', { name: 'Çalışma #4' });
    await expect(
      detail.getByRole('table', { name: 'Alıcılara teslim' }).getByRole('row'),
    ).toHaveCount(3);
    await expect(detail.getByRole('table', { name: 'Dosyalar' }).getByRole('row')).toHaveCount(4);
  });

  test('run now over a chosen period without e-mail', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/reports');
    await rowAction(page, 'Tam gün raporu', 'Şimdi çalıştır');
    const run = page.getByRole('dialog', { name: 'Şimdi çalıştır' });
    await run.getByRole('radio', { name: /Özel dönem/ }).check();
    await run.getByLabel('Başlangıç günü').fill('12.10.2026');
    await run.getByLabel('Bitiş günü').fill('11.10.2026');
    await run.getByLabel('Alıcılara e-postayla gönder (2 adres)').uncheck();
    await run.getByRole('button', { name: 'Çalıştır' }).click();
    await expect(run.getByText('Bitiş, başlangıçtan sonra olmalıdır.')).toBeVisible();
    // More than 92 days is refused before sending.
    await run.getByLabel('Başlangıç günü').fill('01.07.2026');
    await run.getByLabel('Bitiş günü').fill('12.10.2026');
    await run.getByRole('button', { name: 'Çalıştır' }).click();
    await expect(run.getByText('Dönem en fazla 92 gün olabilir.')).toBeVisible();
    await run.getByLabel('Başlangıç günü').fill('12.10.2026');
    await run.getByRole('button', { name: 'Çalıştır' }).click();
    await expect(run).toBeHidden();
    expect(api.callsTo('POST', '/reports/schedules/2/run')[0]?.body).toEqual({
      from: '2026-10-12T00:00:00+03:00',
      to: '2026-10-12T21:00:00.000Z',
      send: false,
    });
    const newest = page.getByRole('table', { name: HISTORY }).getByRole('row').nth(1);
    await expect(newest).toContainText('Özel dönem');
    await expect(newest).toContainText('12.10.2026');
    await expect(newest).toContainText('Tamamlandı (e-posta yok)', { timeout: 15_000 });
    await expect(newest).toContainText('E-posta yok');
  });

  test('e-mail switched off on the server is explained, files only still work', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.reports.smtpEnabled = false;
    await page.goto('/reports');
    await rowAction(page, 'Tam gün raporu', 'Şimdi çalıştır');
    const run = page.getByRole('dialog', { name: 'Şimdi çalıştır' });
    await run.getByRole('button', { name: 'Çalıştır' }).click();
    const problem = run.getByTestId('report-problem-smtp');
    await expect(problem).toContainText('E-posta gönderimi kapalı');
    await expect(problem).toContainText('UYARILAR.md bölüm 4');
    await run.getByLabel('Alıcılara e-postayla gönder (2 adres)').uncheck();
    await run.getByRole('button', { name: 'Çalıştır' }).click();
    await expect(run).toBeHidden();
    expect(api.callsTo('POST', '/reports/schedules/2/run').map((call) => call.body)).toEqual([
      { send: true },
      { send: false },
    ]);
  });

  test('without the usage engine the preview and the run say so', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.reports.usageEngine = false;
    await page.goto('/reports');
    await rowAction(page, 'Tam gün raporu', 'Önizle');
    const preview = page.getByRole('dialog', { name: 'Önizle: Tam gün raporu' });
    await preview.getByRole('button', { name: 'E-posta önizlemesi (HTML)' }).click();
    await expect(preview.getByTestId('report-problem-engine')).toContainText(
      'Kullanım motoru henüz hazır değil',
    );
    await expect(preview.getByTestId('preview-frame')).toHaveCount(0);
    await page.keyboard.press('Escape');

    await rowAction(page, 'Tam gün raporu', 'Şimdi çalıştır');
    const run = page.getByRole('dialog', { name: 'Şimdi çalıştır' });
    await run.getByLabel('Alıcılara e-postayla gönder (2 adres)').uncheck();
    await run.getByRole('button', { name: 'Çalıştır' }).click();
    const newest = page.getByRole('table', { name: HISTORY }).getByRole('row').nth(1);
    await expect(newest).toContainText('Başarısız', { timeout: 15_000 });
    await page.getByRole('button', { name: 'Çalışma #4 ayrıntıları' }).click();
    await expect(
      page.getByRole('dialog', { name: 'Çalışma #4' }).getByTestId('run-engine-missing'),
    ).toContainText('Kullanım motoru henüz hazır değil');
  });

  test("preview: the kind's own period, the e-mail in a sandboxed frame, the PDF in a new tab", async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/reports');
    await rowAction(page, 'Gün sonu raporu', 'Önizle');
    const dialog = page.getByRole('dialog', { name: 'Önizle: Gün sonu raporu' });
    await expect(dialog).toContainText('kaydedilmez ve kimseye gönderilmez');
    await expect(dialog).toContainText('Şimdi çalışırsa kapsadığı dönem: 13.10.2026 00:00–15:00');
    await dialog.getByRole('button', { name: 'E-posta önizlemesi (HTML)' }).click();
    const frame = dialog.getByTestId('preview-frame');
    await expect(frame).toHaveAttribute('sandbox', '');
    await expect(
      dialog
        .frameLocator('[data-testid="preview-frame"]')
        .getByRole('heading', { name: 'ARCILOGGER kullanım raporu' }),
    ).toBeVisible();
    const html = api.callsTo('GET', '/reports/preview').at(-1)?.query;
    expect(html?.get('format')).toBe('html');
    expect(html?.get('kind')).toBe('end_of_day');
    expect(html?.get('person_level')).toBe('true');
    expect(html?.get('pseudonymize')).toBe('false');
    // The schedule's own period is the API's: no from/to.
    expect(html?.get('from')).toBeNull();
    expect(html?.get('to')).toBeNull();

    const popup = page.waitForEvent('popup');
    await dialog.getByRole('button', { name: "PDF'i yeni sekmede aç" }).click();
    await popup;
    await expect
      .poll(() => api.callsTo('GET', '/reports/preview').at(-1)?.query.get('format'))
      .toBe('pdf');
  });

  test('edit, switch off and delete a schedule (its runs stay)', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/reports');
    await rowAction(page, 'Tam gün raporu', 'Düzenle');
    const dialog = page.getByRole('dialog', { name: 'Zamanlanmış raporu düzenle' });
    await expect(dialog.getByLabel('Ad', { exact: true })).toHaveValue('Tam gün raporu');
    await dialog.getByLabel('Saat', { exact: true }).fill('07:30');
    await dialog.getByRole('button', { name: 'Kaydet' }).click();
    await expect(page.getByText('Tam gün raporu kaydedildi.', { exact: true })).toBeVisible();
    expect(api.callsTo('PATCH', '/reports/schedules/2')[0]?.body).toEqual({ time: '07:30' });

    await page.getByRole('switch', { name: 'Etkin: Tam gün raporu' }).click();
    await expect(page.getByText('Tam gün raporu durduruldu.', { exact: true })).toBeVisible();
    expect(api.callsTo('PATCH', '/reports/schedules/2')[1]?.body).toEqual({ enabled: false });
    await expect(page.getByRole('switch', { name: 'Etkin: Tam gün raporu' })).toHaveAttribute(
      'aria-checked',
      'false',
    );

    await rowAction(page, 'Aylık rapor', 'Sil');
    const confirm = page.getByRole('dialog', { name: 'Zamanlama silinsin mi?' });
    await expect(confirm).toContainText('Geçmiş çalışmalar ve dosyaları saklanır.');
    await confirm.getByRole('button', { name: 'Sil' }).click();
    await expect(page.getByText('Aylık rapor silindi.', { exact: true })).toBeVisible();
    expect(api.callsTo('DELETE', '/reports/schedules/4')).toHaveLength(1);
    await expect(
      page.getByRole('table', { name: 'Zamanlanmış raporlar' }).getByRole('row'),
    ).toHaveCount(5);
    await expect(page.getByRole('table', { name: HISTORY }).getByRole('row')).toHaveCount(4);
  });

  test('a user without reports.manage gets the forbidden page', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/reports');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
    expect(api.calls.filter((call) => call.path.startsWith('/reports'))).toEqual([]);
  });
});
