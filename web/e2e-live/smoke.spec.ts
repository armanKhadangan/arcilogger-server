/**
 * Full-stack smoke test: the web app against a REAL ARCILOGGER API and PostgreSQL database
 * (local only, never in CI). Prepare and start the API first (see web/scripts/live_stack.py):
 *
 *     uv run python web/scripts/live_stack.py --dir /tmp/arci-live --drop
 *     cd web && ARCI_LIVE_DIR=/tmp/arci-live npm run e2e:live
 *
 * It creates the first administrator with the console setup token, enrols TOTP with a code
 * computed here from the key the page shows (RFC 6238), accepts the KVKK notice, checks the audit
 * log (chain verified by the server, entry hashes recomputed by the browser), creates a user,
 * signs in as that user with the temporary password and changes it.
 *
 * With a seeded stack (`live_stack.py --seed`: one sealed day of real-format logs, mock TSA) it
 * goes on: a 5651 lookup through the browser (exact chain WAN → internal IP → MAC → device →
 * person), a raw line from the sealed vault, the official PDF report (its SHA-256 checked here
 * and in the browser), an evidence package from that lookup (built by the API, downloaded and
 * checked against `Repr-Digest`), and the day on the sealing page verified again.
 */

import { createHash, createHmac } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

const LIVE_DIR = process.env.ARCI_LIVE_DIR ?? '';
const ADMIN_PASSWORD = 'mavi bisiklet kırk iki limon';
const HR_PASSWORD = 'yeşil tren on üç portakal';

function base32(text: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of text.replace(/[\s=]/g, '').toUpperCase()) {
    const value = alphabet.indexOf(char);
    if (value < 0) throw new Error(`not base32: ${char}`);
    bits += value.toString(2).padStart(5, '0');
  }
  const bytes = bits.match(/.{8}/g) ?? [];
  return Buffer.from(bytes.map((byte) => parseInt(byte, 2)));
}

/** RFC 6238 TOTP (HMAC-SHA1, 6 digits, 30 s), `step` steps after now. */
function totp(secret: string, step = 0): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30_000) + step));
  const mac = createHmac('sha1', base32(secret)).update(counter).digest();
  const offset = (mac[mac.length - 1] ?? 0) & 0x0f;
  return String((mac.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, '0');
}

interface Seed {
  day: string;
  wan: string;
  port: number;
  time: string;
  pc: string;
  mac: string;
  hostname: string;
  person: string;
  department: string;
}

/** `seed.json` of `live_stack.py --seed`, or null for a stack without log data. */
function readSeed(): Seed | null {
  const path = join(LIVE_DIR, 'seed.json');
  return existsSync(path) ? (JSON.parse(readFileSync(path, 'utf8')) as Seed) : null;
}

/** `2026-10-12` → `12.10.2026`. */
function dayText(day: string): string {
  return day.split('-').reverse().join('.');
}

function sha256(path: string): string {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

async function signIn(page: Page, username: string, password: string) {
  await page.getByLabel('Kullanıcı adı').fill(username);
  await page.getByLabel('Parola', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Giriş yap' }).click();
}

test.skip(!LIVE_DIR, 'ARCI_LIVE_DIR is not set: start web/scripts/live_stack.py first');

test('first administrator, TOTP, KVKK, audit log, a second user — against the real API', async ({
  page,
  context,
}) => {
  test.setTimeout(300_000);
  const token = readFileSync(join(LIVE_DIR, 'setup-token.txt'), 'utf8').trim();

  // 1. First-run setup with the token printed by `arcilogger-api setup-token`.
  await page.goto('/login');
  await expect(page).toHaveURL(/\/setup$/);
  await page.getByLabel('Kurulum anahtarı').fill(token);
  await page.getByLabel('Kullanıcı adı').fill('tolga');
  await page.getByLabel('Ad soyad').fill('Tolga Yılmaz');
  await page.getByLabel('Yeni parola', { exact: true }).fill(ADMIN_PASSWORD);
  await page.getByLabel('Yeni parola (tekrar)').fill(ADMIN_PASSWORD);
  await page.getByRole('button', { name: 'Yönetici hesabını oluştur' }).click();
  await expect(page).toHaveURL(/\/login\?setup=done$/);

  // 2. Sign-in: TOTP enrolment is required for administrators.
  await signIn(page, 'tolga', ADMIN_PASSWORD);
  await expect(
    page.getByRole('heading', { level: 1, name: 'İki adımlı doğrulamayı kurun' }),
  ).toBeVisible();
  await expect(page.getByRole('img', { name: /QR kodu \(tolga\)/ })).toBeVisible();
  const secret = (await page.getByLabel('Elle giriş anahtarı').textContent()) ?? '';
  expect(secret.replace(/\s/g, '')).toMatch(/^[A-Z2-7]{32}$/);
  await page.getByLabel('Uygulamadaki kod').fill(totp(secret));
  await page.getByRole('button', { name: 'Doğrula ve etkinleştir' }).click();

  // 3. Recovery codes from the server (10, shown once).
  const codes = page.getByRole('list', { name: 'Kurtarma kodları' }).getByRole('listitem');
  await expect(codes).toHaveCount(10);
  await expect(codes.first()).toHaveText(/^1\.\s?[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}$/);
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Devam et' }).click();

  // 4. KVKK notice (administrators see person-level data).
  const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
  await expect(notice).toBeVisible();
  await notice.getByRole('checkbox').check();
  await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
  await expect(notice).toBeHidden();
  await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
  // The organisation's name comes from the server's configuration (live_stack.py sets one).
  await expect(page.locator('aside').getByTestId('site-name')).toHaveText(
    'Örnek A.Ş. (smoke test)',
  );

  const cookies = await context.cookies();
  const session = cookies.find((cookie) => cookie.name === 'arci_session');
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe('Strict');
  expect(cookies.find((cookie) => cookie.name === 'arci_csrf')?.httpOnly).toBe(false);

  // 5. Audit log: the server verifies the chain, the browser recomputes entry hashes.
  await page
    .getByRole('navigation', { name: 'Ana menü' })
    .getByRole('link', { name: 'Denetim Kaydı' })
    .click();
  await expect(page.getByText(/^Zincir doğrulandı · \d+ kayıt$/)).toBeVisible();
  const table = page.getByRole('table', { name: 'Denetim kaydı, en yeni kayıt önce' });
  for (const label of [
    'Kurulum anahtarı oluşturuldu',
    'İlk yönetici oluşturuldu',
    'Giriş (parola)',
    '2FA etkinleştirildi',
    'KVKK bilgilendirmesi onaylandı',
  ]) {
    await expect(table.getByText(label, { exact: true })).toBeVisible();
  }
  for (const label of ['KVKK bilgilendirmesi onaylandı', 'Kurulum anahtarı oluşturuldu']) {
    await table.getByRole('row', { name: new RegExp(label) }).click();
    const drawer = page.getByRole('dialog', { name: label });
    await expect(
      drawer.getByText('Özet bu tarayıcıda yeniden hesaplandı: eşleşiyor'),
    ).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(drawer).toBeHidden();
  }

  // 6. A second user (the sign-in a moment ago counts as a recent re-authentication).
  await page.goto('/settings/users');
  await page.getByRole('button', { name: 'Kullanıcı ekle' }).click();
  const dialog = page.getByRole('dialog', { name: 'Kullanıcı ekle' });
  await dialog.getByLabel('Kullanıcı adı').fill('ayse.yilmaz');
  await dialog.getByLabel('Ad soyad').fill('Ayşe Yılmaz');
  await dialog.getByLabel('Rol').click();
  await page.getByRole('option', { name: 'İK Raporları' }).click();
  await dialog.getByRole('button', { name: 'Kullanıcıyı oluştur' }).click();
  const temporary = page.getByRole('dialog', { name: 'Geçici parola' });
  const temporaryPassword = ((await temporary.locator('pre').textContent()) ?? '').trim();
  expect(temporaryPassword.length).toBeGreaterThanOrEqual(12);
  await temporary.getByRole('button', { name: 'Kapat' }).first().click();
  await expect(page.getByRole('row').filter({ hasText: 'ayse.yilmaz' })).toBeVisible();

  // 7. Sign out; the new user signs in, must change the password, accepts the notice.
  await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
  await page.getByRole('menuitem', { name: 'Çıkış yap' }).click();
  await expect(page).toHaveURL(/\/login$/);
  await signIn(page, 'ayse.yilmaz', temporaryPassword);
  await expect(
    page.getByRole('heading', { level: 1, name: 'Parolanızı değiştirin' }),
  ).toBeVisible();
  await page.getByLabel('Mevcut (geçici) parola').fill(temporaryPassword);
  await page.getByLabel('Yeni parola', { exact: true }).fill(HR_PASSWORD);
  await page.getByLabel('Yeni parola (tekrar)').fill(HR_PASSWORD);
  await page.getByRole('button', { name: 'Parolayı değiştir' }).click();
  const hrNotice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
  await hrNotice.getByRole('checkbox').check();
  await hrNotice.getByRole('button', { name: 'Onaylıyorum' }).click();
  await expect(page.getByRole('navigation', { name: 'Ana menü' }).getByRole('link')).toHaveText([
    'Panel',
    'Kullanım',
  ]);

  // 8. Back as the administrator: the next TOTP step (the enrolment code cannot be replayed).
  await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
  await page.getByRole('menuitem', { name: 'Çıkış yap' }).click();
  await signIn(page, 'tolga', ADMIN_PASSWORD);
  await page.getByLabel('Doğrulama kodu').fill(totp(secret, 1));
  await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
  // Wait for the answer: it carries the rotated session cookie (the old token is revoked).
  await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
  await page.goto('/audit');
  await expect(page.getByText(/^Zincir doğrulandı · \d+ kayıt$/)).toBeVisible();
  await expect(table.getByText('Parola değiştirildi', { exact: true })).toBeVisible();

  const seed = readSeed();
  if (!seed) return;
  // The administrator signed in a moment ago: that counts as the recent re-authentication the
  // package request and download need (5 minutes).
  await fiveSixFiveOne(page, seed);
});

async function fiveSixFiveOne(page: Page, seed: Seed) {
  // 9. The 5651 lookup of the seeded request.
  await page.goto('/lookup');
  await page.getByLabel('Makam').fill('İzmir Cumhuriyet Başsavcılığı');
  await page.getByLabel('Dosya / evrak no').fill('2026/48213 Soruşturma');
  await page.getByLabel('Talep tarihi').fill(dayText(seed.day));
  await page.getByLabel('Gerekçe').fill('5651 kapsamında IP bilgisi talebi (canlı duman testi)');
  await page.getByLabel('Genel / WAN IP').fill(seed.wan);
  await page.getByLabel('Port', { exact: true }).fill(String(seed.port));
  await page.getByLabel('Protokol').click();
  await page.getByRole('option', { name: 'TCP (6)' }).click();
  await page.getByLabel('Tarih', { exact: true }).fill(dayText(seed.day));
  await page.getByLabel('Saat', { exact: true }).fill(seed.time);
  await page.getByRole('button', { name: 'Sorgula' }).click();
  await expect(page).toHaveURL(/\/lookup\/\d+$/);
  const results = page.getByRole('region', { name: 'Sorgu sonuçları' });
  await expect(results.getByText('Kesin eşleşme', { exact: true })).toBeVisible();
  const summary = results.getByTestId('chain-summary').first();
  await expect(summary).toContainText(
    `WAN ${seed.wan}:${String(seed.port)} → ${seed.pc}:${String(seed.port)} → MAC ${seed.mac} (${seed.hostname})`,
  );
  await expect(summary).toContainText(`Kişi ${seed.person} / ${seed.department}`);

  // 10. A cited raw line, read back from the sealed vault.
  const evidenceRow = results.getByRole('button', { name: /satır \d+/ }).first();
  await evidenceRow.click();
  const raw = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
  await expect(raw.getByText('Mühürlü arşivden okundu')).toBeVisible();
  await expect(raw.locator('pre').first()).toContainText(seed.pc);
  await page.keyboard.press('Escape');
  await expect(evidenceRow).toBeFocused();

  // 11. The official report: the bytes, the hash shown and the browser's own check agree.
  const pdf = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Resmi Rapor (PDF)' }).click();
  const report = await pdf;
  expect(report.suggestedFilename()).toMatch(/^ARCILOGGER-5651-rapor-\d+\.pdf$/);
  const reportPath = await report.path();
  expect(readFileSync(reportPath).subarray(0, 5).toString('latin1')).toBe('%PDF-');
  await expect(page.getByText(sha256(reportPath))).toBeVisible();
  await expect(
    page.getByText('İndirilen dosyanın özeti bu tarayıcıda yeniden hesaplandı: eşleşiyor'),
  ).toBeVisible();
  await expect(page.getByText(/ · 1 indirme · /)).toBeVisible();
  // A second download: the same bytes, counted by the server, and no further audited read of
  // the lookup (`GET /lookup/{id}` is recorded as `lookup.view` every time).
  const lookupReads: string[] = [];
  page.on('request', (request) => {
    const path = new URL(request.url()).pathname;
    if (/^\/api\/v1\/lookup\/\d+$/.test(path)) lookupReads.push(path);
  });
  const again = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Resmi Rapor (PDF)' }).click();
  expect(sha256(await (await again).path())).toBe(sha256(reportPath));
  await expect(page.getByText(/ · 2 indirme · /)).toBeVisible();
  expect(lookupReads).toEqual([]);

  // 12. An evidence package of this lookup, built by the API, downloaded and checked.
  await page.getByRole('button', { name: 'Kanıt Paketi' }).click();
  const wizard = page.getByRole('dialog', { name: 'Yeni kanıt paketi' });
  await expect(wizard.getByRole('radio', { name: /5651 sorgusundan/ })).toBeChecked();
  for (let step = 0; step < 3; step += 1)
    await wizard.getByRole('button', { name: 'İleri' }).click();
  await wizard.getByRole('button', { name: 'Paketi oluştur' }).click();
  const sheet = page.getByRole('dialog', { name: /^(Paket #\d+|KP-.+)$/ });
  await expect(sheet.getByText('Hazır', { exact: true })).toBeVisible({ timeout: 90_000 });
  const zip = page.waitForEvent('download');
  await sheet.getByRole('button', { name: 'İndir' }).click();
  const evidencePackage = await zip;
  expect(evidencePackage.suggestedFilename()).toMatch(/\.zip$/);
  const zipPath = await evidencePackage.path();
  expect(readFileSync(zipPath).subarray(0, 2).toString('latin1')).toBe('PK');
  await expect(
    sheet.getByText('İndirilen dosyanın özeti bu tarayıcıda yeniden hesaplandı: eşleşiyor'),
  ).toBeVisible();
  await expect(sheet.getByText(sha256(zipPath))).toBeVisible();
  // One click, one download in the server's own record.
  await expect(sheet.getByText('İndirmeler (1)')).toBeVisible();
  await page.keyboard.press('Escape');

  // 13. The sealed day on the sealing page: mock TSA reported in red, verified once more.
  await page.goto(`/sealing?day=${seed.day}`);
  await expect(page.getByRole('heading', { name: dayText(seed.day) })).toBeVisible();
  await expect(page.getByTestId('not-legal-grade')).toBeVisible();
  // The core's task state in Turkish ("sealed" → "Mühürlendi").
  await expect(page.getByText('Mühürlendi', { exact: true })).toBeVisible();
  await expect(page.getByText('sealed', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
  await expect(page.getByTestId('verify-result')).toContainText(
    `${dayText(seed.day)} günü doğrulandı`,
  );

  // 14. Everything above is in the audit log.
  await page.goto('/audit');
  const audit = page.getByRole('table', { name: 'Denetim kaydı, en yeni kayıt önce' });
  for (const label of [
    '5651 sorgusu',
    'Ham satır görüntülendi',
    'Resmi rapor indirildi',
    'Kanıt paketi oluşturuldu',
    'Kanıt paketi indirildi',
    'Gün doğrulandı',
  ]) {
    await expect(audit.getByText(label, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText(/^Zincir doğrulandı · \d+ kayıt$/)).toBeVisible();
}
