import { expect, test, type Page } from '@playwright/test';

import { confirmReauth, useSampleClock } from './fixtures';
import { API_SCHEMAS } from './generated';
import { bodyProblems, CSRF_TOKEN, mockApi } from './mock-api';
import { copy, EVIDENCE, LOOKUPS, SAMPLE_TODAY } from './samples';

const READY = EVIDENCE.details['1'];
const FAILED = EVIDENCE.details['4'];
const STALE = EVIDENCE.ready_expired;
if (!READY || !FAILED) throw new Error('evidence samples missing');

function table(page: Page) {
  return page.getByRole('table', { name: 'Kanıt paketleri, en yeni önce' });
}

async function openWizard(page: Page) {
  await page.getByRole('button', { name: 'Yeni paket' }).click();
  return page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
}

async function rangeScope(page: Page, from: string, to: string) {
  const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
  await wizard.getByLabel('İlk gün').fill(from);
  await wizard.getByLabel('Son gün').fill(to);
  return wizard;
}

test.describe('Kanıt Paketleri', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('lists packages with their state and shows the detail with the verifier hash', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/evidence');
    await expect(page.getByRole('heading', { level: 1, name: 'Kanıt Paketleri' })).toBeVisible();
    await expect(table(page).getByRole('row')).toHaveCount(6);
    for (const label of ['Sırada', 'Oluşturulamadı', 'Süresi doldu', 'Hazır']) {
      await expect(table(page).getByText(label).first()).toBeVisible();
    }
    await table(page)
      .getByRole('row', { name: new RegExp(READY.package_name ?? '') })
      .click();
    const sheet = page.getByRole('dialog', { name: READY.package_name ?? '' });
    await expect(sheet.getByText(READY.verifier_sha256 ?? '-')).toBeVisible();
    await expect(
      sheet.getByText(/teslim tutanağına yazın ve alıcıya paketten ayrı bir yoldan/),
    ).toBeVisible();
    await expect(sheet.getByTestId('not-legal-grade')).toBeVisible();
    await expect(sheet.getByText('İndirmeler (2)')).toBeVisible();
    await expect(sheet.getByText('ayse.yilmaz')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();
    await expect(
      table(page).getByRole('row', { name: new RegExp(READY.package_name ?? '') }),
    ).toBeFocused();

    await table(page).getByRole('row', { name: /#4/ }).click();
    const failed = page.getByRole('dialog', { name: 'Paket #4' });
    await expect(failed.getByText(FAILED.error ?? '-')).toBeVisible();
    await expect(failed.getByRole('button', { name: 'İndir' })).toBeDisabled();
  });

  test('download asks for the password again, then checks the bytes against Repr-Digest', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/evidence');
    await table(page)
      .getByRole('row', { name: new RegExp(READY.package_name ?? '') })
      .click();
    const sheet = page.getByRole('dialog', { name: READY.package_name ?? '' });
    const download = page.waitForEvent('download');
    await sheet.getByRole('button', { name: 'İndir' }).click();
    await confirmReauth(page);
    expect((await download).suggestedFilename()).toBe(READY.file);
    await expect(
      sheet.getByText('İndirilen dosyanın özeti bu tarayıcıda yeniden hesaplandı: eşleşiyor'),
    ).toBeVisible();
    await expect(sheet.getByText('İndirmeler (3)')).toBeVisible();
    expect(api.callsTo('GET', `/evidence/${READY.id}/download`)).toHaveLength(2);

    // The package expired meanwhile: the server's 410 text is shown, nothing is saved.
    const stored = api.records.packages.get(READY.id);
    if (stored) Object.assign(stored, { status: 'expired', downloadable: false });
    await sheet.getByRole('button', { name: 'İndir' }).click();
    await expect(
      sheet.getByText('Paketin saklama süresi doldu ve silindi; gerekirse yeniden oluşturun.'),
    ).toBeVisible();
  });

  test('a package past its expiry that the nightly purge has not marked is shown as expired', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    // Captured: status "ready" (as the server still says), `downloadable: false`.
    expect([STALE.status, STALE.downloadable]).toEqual(['ready', false]);
    api.records.packages.set(STALE.id, copy(STALE));
    await page.goto('/evidence');
    const row = table(page).getByRole('row', { name: new RegExp(STALE.package_name ?? '-') });
    await expect(row.getByText('Süresi doldu', { exact: true })).toBeVisible();
    await expect(row.getByText('Hazır', { exact: true })).toHaveCount(0);
    await row.click();
    const sheet = page.getByRole('dialog', { name: STALE.package_name ?? '' });
    await expect(sheet.getByText('Süresi doldu', { exact: true })).toBeVisible();
    await expect(sheet.getByTestId('expired-unpurged')).toContainText(
      'artık indirilemez. Dosyası, her gece 03:00',
    );
    const download = sheet.getByRole('button', { name: 'İndir' });
    await expect(download).toBeDisabled();
    await expect(download).toHaveAccessibleDescription('Saklama süresi doldu; paket indirilemez.');
  });

  test('a large package warns before the download; the size is shown', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    const stored = api.records.packages.get(READY.id);
    if (!stored) throw new Error('ready package missing');
    stored.size = 3 * 1024 ** 3;
    await page.goto('/evidence');
    await table(page)
      .getByRole('row', { name: new RegExp(READY.package_name ?? '') })
      .click();
    const sheet = page.getByRole('dialog', { name: READY.package_name ?? '' });
    await expect(sheet.getByTestId('large-package')).toContainText(
      'tarayıcı dosyayı kaydetmeden önce tamamını alır',
    );
    await expect(sheet.getByText(/^Boyut: 3/)).toBeVisible();
    await expect(sheet.getByRole('button', { name: 'İndir' })).toBeEnabled();
  });

  test('keyboard focus returns to "Yeni paket" after the new package and the lookup wizard', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/evidence');
    const newPackage = page.getByRole('button', { name: 'Yeni paket' });
    const wizard = await openWizard(page);
    await rangeScope(page, '04.10.2026', '05.10.2026');
    for (let step = 0; step < 3; step += 1)
      await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
    // The new package's drawer replaces the wizard (whose buttons are gone afterwards).
    const sheet = page.getByRole('dialog', { name: /Paket #6|KP-E2E-0006/ });
    await expect(sheet).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();
    await expect(newPackage).toBeFocused();

    // Opened from the lookup page: no opener on this page.
    await page.goto('/lookup/1');
    await page.getByRole('button', { name: 'Kanıt Paketi' }).click();
    const fromLookup = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
    await expect(fromLookup).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(fromLookup).toBeHidden();
    await expect(newPackage).toBeFocused();
  });

  test('wizard: day range, own sources, password shown once until confirmed', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/evidence');
    const wizard = await openWizard(page);
    await expect(wizard.getByRole('radio', { name: /Gün aralığı/ })).toBeChecked();
    await rangeScope(page, '04.10.2026', SAMPLE_TODAY.split('-').reverse().join('.'));
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(
      wizard.getByText('Yalnızca bitmiş (mühürlenmiş) günler seçilebilir.'),
    ).toBeVisible();
    await wizard.getByLabel('Son gün').fill('05.10.2026');
    await wizard.getByLabel('Tüm kaynaklar').uncheck();
    await wizard.getByLabel('Kaynaklar', { exact: true }).fill('FortiGate');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(wizard.getByText(/Kaynak adları küçük harf/)).toBeVisible();
    await wizard.getByLabel('Kaynaklar', { exact: true }).fill('fortigate');
    await wizard.getByLabel('Talep referansı (isteğe bağlı)').fill('Savcılık 2026/301');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(wizard.getByRole('radio', { name: /Tam günler/ })).toBeChecked();
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByLabel('Paketi parola ile koru (AES-256)').check();
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(wizard.getByText('04.10.2026 – 05.10.2026')).toBeVisible();
    await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
    await confirmReauth(page);

    const created = page.getByRole('dialog', { name: 'Paket parolası' });
    await expect(created.getByText(EVIDENCE.created_password.password ?? '-')).toBeVisible();
    await expect(
      created.getByText(EVIDENCE.created_password.password_notice_tr ?? '-'),
    ).toBeVisible();
    const finish = created.getByRole('button', { name: 'Bitir' });
    await expect(finish).toBeDisabled();
    await page.keyboard.press('Escape');
    await expect(created).toBeVisible();
    await created.getByLabel('Parolayı güvenli bir yere kaydettim.').check();
    await finish.click();
    await expect(created).toBeHidden();

    const body = api.callsTo('POST', '/evidence').at(-1)?.body;
    expect(body).toEqual({
      from: '2026-10-04',
      to: '2026-10-05',
      sources: ['fortigate'],
      extract: [],
      content: 'full_days',
      password: true,
      reference: 'Savcılık 2026/301',
    });
    // The new package opens and refreshes itself until it is ready.
    const sheet = page.getByRole('dialog', { name: /Paket #6|KP-E2E-0006/ });
    await expect(sheet.getByText(/Sırada|Hazırlanıyor/).first()).toBeVisible();
    // Ready on the third read of the package: two of the drawer's 3 s polls (EVIDENCE_POLL_MS).
    await expect(sheet.getByText('Hazır', { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(sheet.getByRole('heading', { name: 'KP-E2E-0006' })).toBeVisible();
    // The one-time password is nowhere in browser storage.
    const stored = await page.evaluate(() =>
      [window.localStorage, window.sessionStorage]
        .flatMap((storage) =>
          Array.from({ length: storage.length }, (_, index) => {
            const key = storage.key(index) ?? '';
            return `${key}=${storage.getItem(key) ?? ''}`;
          }),
        )
        .join('\n'),
    );
    expect(stored).not.toContain(EVIDENCE.created_password.password ?? '-');
  });

  test('refusals of the server are shown in Turkish with their details', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/evidence');
    const cases = [
      ['rate_limited', ['Hazırlanmakta olan 1 paketiniz var', '60 saniye sonra tekrar deneyin.']],
      ['disk_space_low', ['Veri diskinde yer yetersiz']],
      [
        'days_not_sealed',
        ['Seçilen günlerin bazıları henüz mühürlenmedi', 'Günler: 11.10.2026, 12.10.2026'],
      ],
    ] as const;
    for (const [code, texts] of cases) {
      const refusal = EVIDENCE.errors[code];
      if (!refusal) throw new Error(code);
      api.records.refuseNextCreate = refusal;
      const wizard = await openWizard(page);
      await rangeScope(page, '04.10.2026', '05.10.2026');
      for (let step = 0; step < 3; step += 1)
        await wizard.getByRole('button', { name: 'İleri' }).click();
      await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
      for (const text of texts)
        await expect(wizard.getByText(text, { exact: false })).toBeVisible();
      await wizard.getByRole('button', { name: 'İptal' }).click();
      await expect(wizard).toBeHidden();
    }
  });

  test('extract of a day range: the cited lines are checked in the browser', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/evidence');
    const wizard = await openWizard(page);
    await rangeScope(page, '09.10.2026', '09.10.2026');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('radio', { name: /Alıntı/ }).check();
    await wizard.getByLabel('Alıntılanacak satırlar').fill('2026-10-09 fortigate\n');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(wizard.getByText(/Her satır "gün kaynak satır_no" biçiminde/)).toBeVisible();
    await wizard.getByLabel('Alıntılanacak satırlar').fill('2026-10-08 fortigate 2');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(
      wizard.getByText('Alıntılanan satırlar seçilen günlerin içinde olmalıdır.'),
    ).toBeVisible();
    await wizard
      .getByLabel('Alıntılanacak satırlar')
      .fill('09.10.2026 fortigate 2\n2026-10-09 core-r1 1');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
    await expect(page.getByRole('dialog', { name: /Paket #6|KP-E2E-0006/ })).toBeVisible();
    expect(api.callsTo('POST', '/evidence').at(-1)?.body).toEqual({
      from: '2026-10-09',
      to: '2026-10-09',
      sources: null,
      extract: [
        { day: '2026-10-09', source: 'fortigate', line: 2 },
        { day: '2026-10-09', source: 'core-r1', line: 1 },
      ],
      content: 'extract',
      password: false,
      reference: null,
    });
  });

  test('a full-day package from a 5651 lookup says it holds every source of the days', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/1');
    await page.getByRole('button', { name: 'Kanıt Paketi' }).click();
    const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
    for (let step = 0; step < 3; step += 1)
      await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(
      wizard.getByText(/^Tüm kaynaklar — .*yalnızca sorgunun dayandığı kaynaklarınki değil/),
    ).toBeVisible();
    await expect(wizard.getByText('sorgunun dayandığı kaynaklar', { exact: true })).toHaveCount(0);
    await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
    const sheet = page.getByRole('dialog', { name: /Paket #6|KP-E2E-0006/ });
    await expect(sheet.getByText('tüm kaynaklar', { exact: true })).toBeVisible();
    expect(api.callsTo('POST', '/evidence').at(-1)?.body).toEqual({
      lookup_id: 1,
      content: 'full_days',
      password: false,
      reference: null,
    });
  });

  test('a package from a 5651 lookup (extract of the cited lines)', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/1');
    await page.getByRole('button', { name: 'Kanıt Paketi' }).click();
    const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
    await expect(wizard.getByText(/Boş bırakılırsa sorgunun makamı/)).toBeVisible();
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('radio', { name: /Alıntı/ }).check();
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
    const sheet = page.getByRole('dialog', { name: /Paket #6|KP-E2E-0006/ });
    await expect(sheet.getByRole('link', { name: '5651 sorgusu #1' })).toBeVisible();
    expect(api.callsTo('POST', '/evidence').at(-1)?.body).toEqual({
      lookup_id: 1,
      content: 'extract',
      password: false,
      reference: null,
    });
  });

  test("a package of a 5651 lookup follows the lookup: others' are hidden, own ones link to it", async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    // Package #2 was made from lookup #1 (tolga's); add one of the auditor's own lookup.
    const theirs = EVIDENCE.details['2'];
    if (!theirs || theirs.lookup_id === null) throw new Error('lookup package sample missing');
    const own = LOOKUPS.auditor_run;
    api.records.packages.set(7, {
      ...copy(READY),
      id: 7,
      package_name: 'KP-E2E-0007',
      lookup_id: own.id,
      scope: { ...copy(READY.scope), lookup_id: own.id },
    });
    await page.goto('/evidence');
    await expect(table(page).getByRole('row', { name: /KP-E2E-0007/ })).toBeVisible();
    await expect(
      table(page).getByRole('row', { name: new RegExp(theirs.package_name ?? '-') }),
    ).toHaveCount(0);
    await table(page)
      .getByRole('row', { name: /KP-E2E-0007/ })
      .click();
    const sheet = page.getByRole('dialog', { name: 'KP-E2E-0007' });
    await sheet.getByRole('link', { name: `5651 sorgusu #${own.id}` }).click();
    await expect(page).toHaveURL(new RegExp(`/lookup/${own.id}$`));
    await expect(page.getByRole('heading', { name: `Sorgu #${own.id}` })).toBeVisible();
    await expect(page.getByText('Kayıt bulunamadı.')).toHaveCount(0);
  });

  test('the mock refuses request fields the API does not accept, as the API does (422)', async ({
    page,
  }) => {
    // Every request model of the API is extra="forbid": the mock checks the bodies against the
    // OpenAPI schemas, so a field the web app sends but the API lacks fails these tests.
    expect(bodyProblems('POST /evidence', { lookup_id: 1, content: 'full_days' })).toEqual([]);
    expect(bodyProblems('POST /evidence', { lookup_id: 1, not_in_the_api: true })).toEqual([
      {
        loc: ['body', 'not_in_the_api'],
        type: 'extra_forbidden',
        msg: 'Extra inputs are not permitted',
      },
    ]);
    expect(bodyProblems('POST /auth/login', { username: 'tolga' })).toContainEqual({
      loc: ['body', 'password'],
      type: 'missing',
      msg: 'Field required',
    });
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/evidence');
    const answer = await page.evaluate(async (csrf) => {
      const response = await fetch('/api/v1/evidence', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ lookup_id: 1, not_in_the_api: true }),
      });
      return { status: response.status, body: (await response.json()) as unknown };
    }, CSRF_TOKEN);
    expect(answer).toMatchObject({
      status: 422,
      body: { error: { code: 'validation_error', message_tr: 'Gönderilen veriler geçersiz.' } },
    });
    expect(api.records.packages.size).toBe(Object.keys(EVIDENCE.details).length);
  });

  test('the audit log option follows the API: offered to administrators only when accepted', async ({
    page,
  }) => {
    // Orchestrator decision 1: off by default; the API's EvidenceCreate (extra="forbid") must
    // accept include_audit_log before the wizard may offer it (src/lib/api-features.ts).
    const accepted = Object.hasOwn(
      API_SCHEMAS.EvidenceCreate?.properties ?? {},
      'include_audit_log',
    );
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/evidence');
    const wizard = await openWizard(page);
    await rangeScope(page, '04.10.2026', '05.10.2026');
    await wizard.getByRole('button', { name: 'İleri' }).click();
    const auditLog = wizard.getByLabel(
      'ARCILOGGER denetim kaydını ekle (yalnızca iç denetim için)',
    );
    await expect(auditLog).toHaveCount(accepted ? 1 : 0);
    if (accepted) {
      await expect(auditLog).not.toBeChecked();
      await auditLog.check();
    }
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await wizard.getByRole('button', { name: 'İleri' }).click();
    await expect(wizard.getByText(accepted ? /^Dahil — / : /^Dahil değil/)).toBeVisible();
    await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
    await expect(page.getByRole('dialog', { name: /Paket #6|KP-E2E-0006/ })).toBeVisible();
    const body = api.callsTo('POST', '/evidence').at(-1)?.body as Record<string, unknown>;
    if (accepted) expect(body.include_audit_log).toBe(true);
    else expect(body).not.toHaveProperty('include_audit_log');
  });

  test('the auditor lists and downloads but cannot create packages', async ({ page }) => {
    await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/evidence');
    await expect(page.getByText(/paket oluşturma yöneticilere aittir/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Yeni paket' })).toHaveCount(0);
    await table(page)
      .getByRole('row', { name: new RegExp(READY.package_name ?? '') })
      .click();
    const sheet = page.getByRole('dialog', { name: READY.package_name ?? '' });
    const download = page.waitForEvent('download');
    await sheet.getByRole('button', { name: 'İndir' }).click();
    expect((await download).suggestedFilename()).toBe(READY.file);
    // `/evidence?lookup=1` does not open the wizard for a role without evidence.create.
    await page.goto('/evidence?lookup=1');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(page).toHaveURL(/\/evidence$/);
  });
});
