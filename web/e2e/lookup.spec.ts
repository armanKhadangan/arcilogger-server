import { expect, test, type Page } from '@playwright/test';

import type { LookupOut } from '../src/lib/api-types';
import { outcomeOf } from '../src/lib/lookup-types';

import { expectNoA11yViolations, usePreferences, useSampleClock, type Theme } from './fixtures';
import { mockApi, type MockApi } from './mock-api';
import { copy, LOOKUPS, RAW, SAMPLE_TODAY } from './samples';

/** Explanation texts exactly as the API sent them (captured samples). */
function explanation(lookup: keyof typeof LOOKUPS, key: string): string {
  interface Texts {
    explanations: { key: string; text_tr: string }[];
  }
  const sample = LOOKUPS[lookup] as unknown as { outcome: Texts & { attribution: Texts | null } };
  const all = [...sample.outcome.explanations, ...(sample.outcome.attribution?.explanations ?? [])];
  const found = all.find((item) => item.key === key);
  if (!found) throw new Error(`no ${key} in ${lookup}`);
  return found.text_tr;
}

async function fillRequest(page: Page) {
  await page.getByLabel('Makam').fill('İzmir Cumhuriyet Başsavcılığı');
  await page.getByLabel('Dosya / evrak no').fill('2026/48213 Soruşturma');
  await page.getByLabel('Talep tarihi').fill('10.10.2026');
  await page.getByLabel('Gerekçe').fill('5651 kapsamında IP bilgisi talebi\n(müzekkere 2026/771)');
}

test.describe('5651 Sorgu', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('required fields are announced as required; a failed lookup keeps the focus', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup');
    for (const label of ['Makam', 'Dosya / evrak no', 'Talep tarihi', 'Gerekçe']) {
      await expect(page.getByLabel(label, { exact: true })).toHaveAttribute(
        'aria-required',
        'true',
      );
    }
    await expect(page.getByLabel('Tarih', { exact: true })).toHaveAttribute(
      'aria-required',
      'true',
    );
    await expect(page.getByLabel('Saat', { exact: true })).toHaveAttribute('aria-required', 'true');
    await expect(page.getByLabel('Genel / WAN IP')).not.toHaveAttribute('aria-required');

    await fillRequest(page);
    await page.getByLabel('Genel / WAN IP').fill('100.64.12.9');
    await page.getByLabel('Tarih', { exact: true }).fill('09.10.2026');
    await page.getByLabel('Saat', { exact: true }).fill('11:23:41');
    api.failNext = { route: 'POST /lookup/5651', status: 504, code: 'lookup_timeout' };
    const submit = page.getByRole('button', { name: 'Sorgula' });
    await submit.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByText(/^Sorgu 1 saniye içinde tamamlanamadı/)).toBeVisible();
    await expect(submit).toBeFocused();
  });

  test('checks the form in the browser, then runs an exact lookup with its chain', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup');
    await expect(page.getByRole('heading', { level: 1, name: '5651 Sorgu' })).toBeVisible();
    await expect(page.getByText('Talep bilgilerini ve sorguyu girin')).toBeVisible();

    // Nothing filled: required fields and "nothing to look for", no request sent.
    await page.getByRole('button', { name: 'Sorgula' }).click();
    await expect(
      page.getByText(
        'Genel/WAN IP, iç IP, MAC, hedef IP veya hedef alan adından en az birini girin.',
      ),
    ).toBeVisible();
    await expect(page.getByText('Bu alan zorunludur.')).toHaveCount(6);
    await page.getByLabel('Genel / WAN IP').fill('100.64.12');
    await page.getByLabel('Talep tarihi').fill('31.12.2026');
    await page.getByLabel('Saat', { exact: true }).fill('11:23 AM');
    await page.getByRole('button', { name: 'Sorgula' }).click();
    await expect(page.getByText('Geçerli bir IPv4 adresi girin.')).toBeVisible();
    await expect(page.getByText('Talep tarihi bugünden sonra olamaz.')).toBeVisible();
    await expect(page.getByText('Saati SS:DD veya SS:DD:ss olarak girin.')).toBeVisible();
    expect(api.callsTo('POST', '/lookup/5651')).toHaveLength(0);

    await fillRequest(page);
    await page.getByLabel('Genel / WAN IP').fill('100.64.12.9');
    await page.getByLabel('Port', { exact: true }).fill('51001');
    await page.getByLabel('Protokol').click();
    await page.getByRole('option', { name: 'TCP (6)' }).click();
    await page.getByLabel('Tarih', { exact: true }).fill('09.10.2026');
    await page.getByLabel('Saat', { exact: true }).fill('11:23:41');
    await expect(page.getByLabel('Saat dilimi')).toHaveText('Europe/Istanbul (UTC+03:00)');
    await expect(page.getByLabel('Tolerans ± sn')).toHaveValue('60');
    await page.getByRole('button', { name: 'Sorgula' }).click();

    await expect(page).toHaveURL(/\/lookup\/9$/);
    const [call] = api.callsTo('POST', '/lookup/5651');
    expect(call?.body).toEqual({
      request: {
        authority: 'İzmir Cumhuriyet Başsavcılığı',
        file_no: '2026/48213 Soruşturma',
        request_date: '2026-10-10',
        reason: '5651 kapsamında IP bilgisi talebi\n(müzekkere 2026/771)',
      },
      query: {
        at: '2026-10-09T11:23:41',
        time_zone: 'Europe/Istanbul',
        public_ip: '100.64.12.9',
        public_port: 51001,
        protocol: 6,
        dest_ip: null,
        dest_port: null,
        dest_domain: null,
        tolerance_seconds: 60,
        internal_ip: null,
        mac: null,
      },
    });
    const results = page.getByRole('region', { name: 'Sorgu sonuçları' });
    await expect(results).toBeFocused();
    await expect(results.getByRole('heading', { name: 'Sorgu #9' })).toBeVisible();
    await expect(results.getByText('Kesin eşleşme', { exact: true })).toBeVisible();
    await expect(results.getByText(explanation('exact', 'exact_nat'))).toBeVisible();
    await expect(results.getByTestId('chain-summary')).toHaveText(
      'WAN 100.64.12.9:51001 → 172.20.42.57:51001 → MAC 00:00:5e:00:53:57 (PLN-PC14) → ' +
        'Cihaz "Planlama-PC-14" → Kişi Selin Kaya / Planlama',
    );
    await expect(results.getByText('Skor 100/100')).toBeVisible();
    await expect(results.getByText('Zaman farkı 0 sn (oturum içinde)')).toBeVisible();
    // The POST answer is shown as is: no second request (GET /lookup/{id} is audited).
    expect(api.callsTo('GET', '/lookup/9')).toHaveLength(0);
  });

  test('an IPv6 public or internal address is refused before sending, in the words of the API', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    const refusal = LOOKUPS.errors.ipv6_not_supported;
    if (!refusal) throw new Error('no ipv6_not_supported sample');
    const words = refusal.body.error.message_tr;
    await page.goto('/lookup');
    await fillRequest(page);
    await page.getByLabel('Tarih', { exact: true }).fill('09.10.2026');
    await page.getByLabel('Saat', { exact: true }).fill('11:23:41');
    const publicIp = page.getByLabel('Genel / WAN IP');
    const internalIp = page.getByLabel('İç IP', { exact: true });
    await publicIp.fill('2001:db8::44');
    await internalIp.fill('fd00::57');
    // The destination is only a filter: an IPv6 destination stays allowed.
    await page.getByLabel('Hedef IP').fill('2001:db8:1::10');
    await page.getByRole('button', { name: 'Sorgula' }).click();
    await expect(page.getByText(words, { exact: true })).toHaveCount(2);
    await expect(publicIp).toHaveAttribute('aria-invalid', 'true');
    await expect(internalIp).toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByLabel('Hedef IP')).not.toHaveAttribute('aria-invalid');
    await expect(publicIp).toBeFocused();
    expect(api.callsTo('POST', '/lookup/5651')).toHaveLength(0);

    // Should the server get one all the same (an older page), it answers with the same text.
    await publicIp.fill('100.64.12.9');
    await internalIp.fill('');
    await page.getByLabel('Hedef IP').fill('');
    api.failNext = {
      route: 'POST /lookup/5651',
      status: refusal.status,
      code: 'ipv6_not_supported',
    };
    await page.getByRole('button', { name: 'Sorgula' }).click();
    await expect(page.getByText(words, { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/lookup$/);
    expect(api.callsTo('POST', '/lookup/5651')).toHaveLength(1);
  });

  test('every evidence row opens its raw line; the focus comes back', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/1');
    const link = page.getByRole('button', {
      name: /FortiGate oturum kaydı · fortigate · 09\.10\.2026 · satır 2 /,
    });
    await link.click();
    const sheet = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
    await expect(sheet.getByText('fortigate · 09.10.2026 · satır 2')).toBeVisible();
    const raw = RAW.lines['fortigate|2026-10-09|2'];
    if (!raw) throw new Error('raw sample missing');
    await expect(sheet.locator('pre').first()).toHaveText(raw.text);
    await expect(sheet.getByText('Mühürlü arşivden okundu')).toBeVisible();
    await expect(sheet.getByText(raw.file)).toBeVisible();
    await expect(sheet.getByText(raw.sha256)).toBeVisible();
    // Line 2 starts right after line 1 and its line feed (byte offset in the day's file).
    const first = RAW.lines['fortigate|2026-10-09|1'];
    if (!first) throw new Error('raw sample missing');
    const offset = Buffer.byteLength(first.text, 'utf8') + 1;
    expect(api.callsTo('GET', '/raw').at(-1)?.query.toString()).toBe(
      `source=fortigate&day=2026-10-09&line=2&offset=${String(offset)}`,
    );
    await page.keyboard.press('Escape');
    await expect(sheet).toBeHidden();
    await expect(link).toBeFocused();
  });

  test('double NAT: candidates with the explanation of spec 12.4; IP conflict lists every device', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/2');
    await expect(
      page.getByText(
        'Kesin eşleşme için ISS modeminin NAT kaydı gerekir; aşağıda zaman ve hedefe göre adaylar listelenmiştir.',
      ),
    ).toBeVisible();
    await expect(page.getByTestId('match')).toHaveCount(3);
    await expect(page.getByTestId('match').first()).toContainText('Aday');
    await expect(page.getByTestId('match').first()).toContainText('Hedef IP ve port eşleşti');

    await page.goto('/lookup/3');
    const conflict = page.getByTestId('ip-conflict');
    await expect(conflict.getByText('IP çakışması: 2 olası cihaz')).toBeVisible();
    await expect(conflict.getByText(explanation('conflict', 'double_nat'))).toHaveCount(0);
    const first = conflict.getByRole('region', { name: 'Olasılık 1' });
    const second = conflict.getByRole('region', { name: 'Olasılık 2' });
    await expect(first).toContainText('MAC 00:00:5e:00:53:57 (PLN-PC14)');
    await expect(first).toContainText('Selin Kaya / Planlama');
    await expect(second).toContainText('MAC 00:0c:29:00:00:99');
    await expect(second).toContainText('bağlı kişi yok · Departman Planlama');
    await expect(page.getByText('Düşük güven').first()).toBeVisible();
  });

  test('no result, data only in the vault, truncation and an internal address', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/5');
    await expect(page.getByText('Eşleşme bulunamadı')).toBeVisible();
    await expect(page.getByText(explanation('none', 'destination_not_found'))).toBeVisible();
    await expect(page.getByText(explanation('none', 'double_nat_no_candidates'))).toBeVisible();

    await page.goto('/lookup/6');
    await expect(page.getByText(explanation('vault_only', 'only_in_vault'))).toBeVisible();
    await expect(page.getByText(/veritabanında: yok · mühürlü arşivde: var/)).toBeVisible();

    await page.goto('/lookup/7');
    await expect(page.getByText(explanation('truncated', 'truncated'))).toBeVisible();
    await expect(page.getByTestId('match')).toHaveCount(5);

    await page.goto('/lookup/4');
    const chain = page.getByTestId('attribution');
    await expect(
      chain.getByRole('heading', { name: '172.20.60.142 adresinin zinciri' }),
    ).toBeVisible();
    await expect(chain.getByText(explanation('internal', 'randomized_mac'))).toBeVisible();
  });

  test('official report: download, signature hash and time-stamp state', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/1');
    await expect(page.getByText('İmza özeti')).toHaveCount(0);
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Resmi Rapor (PDF)' }).click();
    expect((await download).suggestedFilename()).toBe('ARCILOGGER-5651-rapor-1.pdf');
    const report = api.records.lookups.get(1)?.report;
    await expect(page.getByText(report?.sha256 ?? '-')).toBeVisible();
    await expect(
      page.getByText('İndirilen dosyanın özeti bu tarayıcıda yeniden hesaplandı: eşleşiyor'),
    ).toBeVisible();
    await expect(
      page.getByText('Rapora zaman damgası alınmadı (Ayarlar: lookup.report_timestamp kapalı).'),
    ).toBeVisible();
    await expect(page.getByText(/ · 1 indirme · /)).toBeVisible();
    // The first download stored the report: the lookup was read once more to show it. A later
    // download only counts — no further audited `GET /lookup/1` (`lookup.view`).
    expect(api.callsTo('GET', '/lookup/1')).toHaveLength(2);
    // Keyboard: Enter on the button downloads and the focus stays on it (a busy button is not
    // `disabled`, which would drop the focus to <body>; that activations are ignored while busy is
    // a unit test of the button).
    const pdf = page.getByRole('button', { name: 'Resmi Rapor (PDF)' });
    await pdf.focus();
    const again = page.waitForEvent('download');
    await page.keyboard.press('Enter');
    await again;
    await expect(page.getByText(/ · 2 indirme · /)).toBeVisible();
    await expect(pdf).toBeFocused();
    expect(api.callsTo('GET', '/lookup/1/report.pdf')).toHaveLength(2);
    expect(api.callsTo('GET', '/lookup/1')).toHaveLength(2);

    // A stamped report of the mock TSA: red banner, token download.
    await page.goto('/lookup/2');
    await page.getByRole('button', { name: 'Resmi Rapor (PDF)' }).click();
    await expect(page.getByTestId('not-legal-grade')).toContainText('mock');
    await expect(page.getByText('Damgalandı ve doğrulandı')).toBeVisible();
    const token = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Zaman damgasını indir (.zd)' }).click();
    expect((await token).suggestedFilename()).toBe('ARCILOGGER-5651-rapor-2.pdf.zd');
  });

  test('refusals of the server: time-out, invalid settings, unreadable raw and report files', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    const said = (errors: typeof LOOKUPS.errors, code: string) => {
      const found = errors[code];
      if (!found) throw new Error(`no ${code} sample`);
      return found;
    };
    await page.goto('/lookup');
    await fillRequest(page);
    await page.getByLabel('Genel / WAN IP').fill('100.64.12.9');
    await page.getByLabel('Tarih', { exact: true }).fill('09.10.2026');
    await page.getByLabel('Saat', { exact: true }).fill('11:23:41');
    for (const code of ['lookup_timeout', 'lookup_settings_invalid']) {
      const refusal = said(LOOKUPS.errors, code);
      api.failNext = { route: 'POST /lookup/5651', status: refusal.status, code };
      await page.getByRole('button', { name: 'Sorgula' }).click();
      await expect(page.getByText(refusal.body.error.message_tr)).toBeVisible();
      await expect(page).toHaveURL(/\/lookup$/);
    }
    expect(said(LOOKUPS.errors, 'lookup_timeout').status).toBe(504);
    expect(said(LOOKUPS.errors, 'lookup_settings_invalid').status).toBe(503);

    await page.goto('/lookup/1');
    for (const code of ['raw_access_denied', 'raw_io_error']) {
      const refusal = said(RAW.errors, code);
      expect(refusal.status).toBe(503);
      api.failNext = { route: 'GET /raw', status: refusal.status, code };
      await page
        .getByRole('button', {
          name: /FortiGate oturum kaydı · fortigate · 09\.10\.2026 · satır 2 /,
        })
        .click();
      const sheet = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
      await expect(sheet.getByText(refusal.body.error.message_tr)).toBeVisible();
      await page.keyboard.press('Escape');
      await expect(sheet).toBeHidden();
      // Read again next time (an error is not kept as the line's answer).
      await page.reload();
    }

    await page.goto('/lookup/3');
    for (const code of ['report_access_denied', 'report_evidence_unreadable']) {
      const refusal = said(LOOKUPS.errors, code);
      expect(refusal.status).toBe(503);
      api.failNext = { route: 'GET /lookup/3/report.pdf', status: refusal.status, code };
      await page.getByRole('button', { name: 'Resmi Rapor (PDF)' }).click();
      await expect(page.getByText(refusal.body.error.message_tr)).toBeVisible();
    }
    expect(api.records.lookups.get(3)?.report).toBeNull();
  });

  test('earlier lookups: administrators see everyone, reopen fills the form', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup');
    await page.getByRole('tab', { name: 'Önceki sorgular' }).click();
    const table = page.getByRole('table', { name: 'Önceki 5651 sorguları, en yeni önce' });
    await expect(table.getByRole('row')).toHaveCount(9);
    await expect(table.getByRole('row').nth(1)).toContainText('ayse.yilmaz');
    await page.getByLabel('Yalnızca benim sorgularım').check();
    await expect(table.getByRole('row')).toHaveCount(8);
    expect(api.callsTo('GET', '/lookups').at(-1)?.query.get('mine')).toBe('true');
    await table.getByRole('row', { name: /#5/ }).click();
    await expect(page).toHaveURL(/\/lookup\/5$/);
    await expect(page.getByRole('tab', { name: 'Sorgu', exact: true })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await expect(page.getByLabel('Hedef IP')).toHaveValue('203.0.113.50');
    await expect(page.getByLabel('Tarih', { exact: true })).toHaveValue('09.10.2026');
    await expect(page.getByLabel('Saat', { exact: true })).toHaveValue('11:23:41');
    await expect(page.getByLabel('Talep tarihi')).toHaveValue('10.10.2026');
  });

  test('the auditor sees only own lookups and has no evidence package button', async ({ page }) => {
    await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/lookup');
    await page.getByRole('tab', { name: 'Önceki sorgular' }).click();
    const table = page.getByRole('table', { name: 'Önceki 5651 sorguları, en yeni önce' });
    await expect(table.getByRole('row')).toHaveCount(2);
    await expect(page.getByText('Yalnızca kendi sorgularınız.', { exact: false })).toBeVisible();
    await expect(table.getByRole('columnheader', { name: 'Kullanıcı' })).toHaveCount(0);
    await table.getByRole('row', { name: /#8/ }).click();
    await expect(page.getByRole('button', { name: 'Resmi Rapor (PDF)' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Kanıt Paketi' })).toHaveCount(0);
    // Another user's lookup by URL: 404 like the API, no data.
    await page.goto('/lookup/1');
    await expect(page.getByText('Kayıt bulunamadı.')).toBeVisible();
  });

  test('"Kanıt Paketi" opens the package wizard for this lookup', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/lookup/1');
    await page.getByRole('button', { name: 'Kanıt Paketi' }).click();
    await expect(page).toHaveURL(/\/evidence$/);
    const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
    await expect(wizard.getByRole('radio', { name: /5651 sorgusundan/ })).toBeChecked();
    await expect(wizard.getByRole('combobox', { name: '5651 sorgusu' })).toContainText(
      '#1 · İzmir Cumhuriyet',
    );
  });

  test('a user who has not accepted the KVKK notice gets it before any result', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    api.get('ayse.yilmaz').privacy_accepted = false;
    await page.goto('/lookup/8');
    const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    await expect(notice).toBeVisible();
    await notice.getByRole('checkbox').check();
    await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(notice).toBeHidden();
    await expect(page.getByRole('heading', { name: 'Sorgu #8' })).toBeVisible();
  });
});

/**
 * The result view of ADR-0016 (Wave 3 contract 2.13): FortiGate DHCP leases, srcmac steps and
 * the internet exits, on the captured Wave 3 lookups (`samples/lookups.json`); each is stored in
 * the mocked API and opened by its id.
 */
type Wave3Lookup =
  | 'fortigate_internal'
  | 'hat2_candidates'
  | 'fortigate_renewal_low'
  | 'srcmac_only'
  | 'srcmac_conflict'
  | 'direct_quiet_none'
  | 'no_egress'
  | 'internal_exit'
  | 'unconverted_dhcp'
  | 'untrusted_match';

/** The Wave 3 captures; `samples.ts` types the Wave 2 keys only. */
const W3 = LOOKUPS as typeof LOOKUPS & Record<Wave3Lookup, LookupOut>;

async function openStored(page: Page, lookup: LookupOut, title = 'Sorgu'): Promise<MockApi> {
  const api = await mockApi(page, { signedInAs: 'tolga' });
  api.records.lookups.set(lookup.id, copy(lookup));
  await page.goto(`/lookup/${String(lookup.id)}`);
  await expect(page.getByRole('heading', { name: `${title} #${String(lookup.id)}` })).toBeVisible();
  return api;
}

/** The lease step (or the srcmac step in its place) of the first chain on the page. */
function leaseStep(page: Page) {
  return page
    .getByRole('listitem')
    .filter({ has: page.getByText(/^MAC \((DHCP\/ARP|srcmac)\)$/) })
    .first();
}

const UNCONVERTED_TR =
  'FortiGate DHCP kaydı (kira zaman çizelgesine girmedi; zincir kurulmadı, güven düzeyi verilmez)';
const UNCONVERTED_EN =
  'FortiGate DHCP record (not in the lease timeline; no chain built; no confidence given)';

test.describe('5651 Sorgu — FortiGate DHCP, srcmac and internet exits', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('FortiGate-only: the lease time and last DHCP evidence; a low chain says why', async ({
    page,
  }) => {
    await openStored(page, W3.fortigate_internal);
    const step = leaseStep(page);
    await expect(step.getByTestId('lease-fortigate')).toHaveText(
      'kira süresi (FortiGate kaydı): 86400 sn · son DHCP kanıtı: 01.09.2026 10:42:18',
    );
    await expect(step.getByTestId('lease-source')).toHaveText('Kaynak: DHCP kaydı (FortiGate)');

    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await openStored(page, W3.fortigate_renewal_low);
    const low = leaseStep(page);
    await expect(low.getByText('Düşük güven', { exact: true })).toBeVisible();
    await expect(low.locator('[data-explanation="lease_renewal_not_seen"]')).toContainText(
      'Bu kiranın istenen andan önceki son DHCP kaydı 03.09.2026 08:00:00 anında',
    );
  });

  test('srcmac: partial evidence in place of the lease; a conflict shows both chains', async ({
    page,
  }) => {
    await openStored(page, W3.srcmac_only);
    const step = leaseStep(page);
    await expect(step.getByText('Kısmi kanıt: srcmac')).toBeVisible();
    await expect(step).toContainText('FortiGate oturum kaydının srcmac alanı (vlan42 arayüzü)');
    await expect(page.getByText('Bu anda geçerli kira kaydı yok')).toHaveCount(0);

    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await openStored(page, W3.srcmac_conflict);
    const conflict = page.getByTestId('ip-conflict');
    await expect(conflict.getByText('srcmac farklı: 2 olası cihaz')).toBeVisible();
    await expect(conflict.getByRole('region', { name: 'Olasılık 1' })).toContainText(
      '00:00:5e:00:53:77',
    );
    const srcmac = conflict.getByRole('region', { name: 'Olasılık 2 (srcmac, kısmi kanıt)' });
    await expect(srcmac).toContainText('00:00:5e:00:53:57');
    await expect(srcmac.getByText('Kısmi kanıt: srcmac', { exact: true })).toBeVisible();
    await expect(srcmac.getByText('MAC (DHCP/ARP)')).toHaveCount(0);
  });

  test('hat-2: the exit line and the match on hat-2; the unconfigured sender', async ({ page }) => {
    await openStored(page, W3.hat2_candidates);
    await expect(page.getByTestId('lookup-exit')).toHaveText(
      "Genel IP — hat-2 (Yedek hat), Servis sağlayıcı NAT'ı",
    );
    const matches = page.getByTestId('match');
    await expect(matches).toHaveCount(1);
    await expect(matches.getByTestId('match-exit')).toHaveText(
      'Çıkış: hat-2 (Yedek hat) · dstintf wan2',
    );
    await expect(matches.getByTestId('lease-fortigate')).toHaveText(
      'kira süresi (FortiGate kaydı): 86400 sn · son DHCP kanıtı: 01.09.2026 09:37:52',
    );
    await expect(matches.getByTestId('untrusted-sender')).toHaveCount(0);

    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await openStored(page, W3.untrusted_match);
    const listed = page.getByTestId('match');
    await expect(listed).toHaveCount(4);
    await expect(listed.getByTestId('untrusted-sender')).toHaveCount(1);
    await expect(listed.nth(1).getByTestId('untrusted-sender')).toHaveText(
      'Tanımsız gönderici: unknown-192.0.2.7',
    );
    await expect(
      listed.nth(0).getByText('Zincir kurulamadı: o anda bu iç IP için kira kaydı yok'),
    ).toBeVisible();
  });

  test('nobody listed: no exit, a quiet direct line, the DMZ NAT address', async ({ page }) => {
    await openStored(page, W3.no_egress);
    await expect(page.getByTestId('lookup-exit')).toHaveText('Çıkış yok');
    await expect(
      page.getByText(/^192\.0\.2\.44 hiçbir internet çıkışına tanımlı değil/),
    ).toBeVisible();
    await expect(page.getByTestId('match')).toHaveCount(0);

    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await openStored(page, W3.direct_quiet_none);
    await expect(page.getByTestId('lookup-exit')).toHaveText('Genel IP — hat-1, Doğrudan');
    await expect(
      page.getByText(/^FortiGate bu zaman aralığında 203\.0\.113\.200:51001 için oturum/),
    ).toBeVisible();
    await expect(page.getByTestId('match')).toHaveCount(0);

    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await openStored(page, W3.internal_exit);
    await expect(page.getByTestId('lookup-exit')).toHaveText(
      'NAT adresi — dmz, İnternet çıkışı değil',
    );
    await expect(page.getByText(/^172\.20\.99\.1, dmz tanımına göre/)).toBeVisible();
  });

  test('an unconverted FortiGate DHCP record: the fixed label, never the stored summary', async ({
    page,
  }) => {
    await openStored(page, W3.unconverted_dhcp);
    const card = page.getByTestId('unconverted-dhcp');
    await expect(card).toContainText('Kira zaman çizelgesine girmemiş FortiGate DHCP kaydı');
    await expect(card.getByRole('button', { name: /^FortiGate olay kaydı · / })).toContainText(
      UNCONVERTED_TR,
    );

    await page.unrouteAll({ behavior: 'ignoreErrors' });
    await usePreferences(page, { lang: 'en' });
    await openStored(page, W3.unconverted_dhcp, 'Lookup');
    await expect(page.getByTestId('unconverted-dhcp').getByRole('button')).toContainText(
      UNCONVERTED_EN,
    );
    await expect(page.getByTestId('unconverted-dhcp')).not.toContainText(
      'kira zaman çizelgesine girmedi',
    );
  });

  test('a lookup stored before ADR-0016 shows no exit rows', async ({ page }) => {
    // As stored before ADR-0016: the captured exact lookup without the members it added.
    const stored = copy(LOOKUPS.exact);
    const found = outcomeOf(stored);
    if (!found) throw new Error('no outcome in the exact lookup');
    delete found.egress;
    delete found.unconverted_dhcp;
    for (const match of found.matches) {
      delete match.egress;
      delete match.untrusted_sources;
      delete match.traffic.srcintf;
      delete match.traffic.dstintf;
      delete match.traffic.srcmac;
      if (match.chain) {
        delete match.chain.mac_source;
        if (match.chain.lease) {
          delete match.chain.lease.source_table;
          delete match.chain.lease.lease_seconds;
          delete match.chain.lease.last_dhcp_evidence;
        }
      }
    }
    await openStored(page, stored);
    await expect(page.getByTestId('chain-summary')).toContainText('00:00:5e:00:53:57');
    await expect(page.getByTestId('lookup-exit')).toHaveCount(0);
    await expect(page.getByTestId('match-exit')).toHaveCount(0);
    await expect(page.getByTestId('lease-source')).toHaveCount(0);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  for (const name of [
    'hat2_candidates',
    'srcmac_conflict',
    'fortigate_renewal_low',
    'untrusted_match',
    'unconverted_dhcp',
  ] as const) {
    test(`5651 Sorgu ADR-0016 result accessibility — ${name}, ${theme}`, async ({ page }) => {
      await usePreferences(page, { theme });
      await useSampleClock(page, SAMPLE_TODAY);
      await openStored(page, W3[name]);
      await expectNoA11yViolations(page);
    });
  }
}
