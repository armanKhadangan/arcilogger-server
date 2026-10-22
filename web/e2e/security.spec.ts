import { expect, test } from '@playwright/test';

import { usePreferences, useSampleClock } from './fixtures';
import { mockApi } from './mock-api';
import { SAMPLE_TODAY } from './samples';

test.describe('Güvenlik', () => {
  test.beforeEach(async ({ page }) => {
    await useSampleClock(page, SAMPLE_TODAY);
  });

  test('summary, list, filters, severity chips and sorting', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security');
    await expect(page.getByRole('heading', { level: 1, name: 'Güvenlik' })).toBeVisible();
    const summary = page.getByTestId('security-summary');
    await expect(summary.getByRole('heading', { name: 'Güvenlik Özeti' })).toBeVisible();
    const tiles = summary.getByRole('list', { name: 'Önem derecesine göre açık uyarılar' });
    await expect(tiles.getByRole('listitem')).toHaveText([
      'Kritik0',
      'Yüksek2',
      'Orta1',
      'Düşük0',
      'Bilgi0',
    ]);
    await expect(summary).toContainText('Açık3');
    await expect(summary).toContainText('İnceleniyor1');
    await expect(summary).toContainText('Ortalama kapanma süresi (30 gün)');
    await expect(page.getByTestId('rule-editor-note')).toContainText('Kural düzenleyici');

    const table = page.getByRole('table', { name: 'Güvenlik uyarıları' });
    await expect(table.getByRole('row')).toHaveCount(8);
    await expect(page.getByText('1–7 / 7 uyarı')).toBeVisible();

    // Severity chip: only the high ones, kept in the address.
    await page.getByRole('group', { name: 'Önem' }).getByRole('button', { name: 'Yüksek' }).click();
    await expect(table.getByRole('row')).toHaveCount(6);
    await expect(page).toHaveURL(/severity=high/);
    expect(api.callsTo('GET', '/alerts').at(-1)?.query.get('severity')).toBe('high');

    // And closed.
    await page.getByLabel('Durum', { exact: true }).click();
    await page.getByRole('option', { name: 'Kapandı' }).click();
    await expect(table.getByRole('row')).toHaveCount(3);
    await expect(page).toHaveURL(/state=resolved/);

    await page.getByRole('button', { name: 'Süzgeçleri temizle' }).click();
    await expect(table.getByRole('row')).toHaveCount(8);

    // A rule typed in any case; the search text never goes into the address.
    await page.getByLabel('Kural').fill('r20-disk');
    await page.getByLabel('Kural').press('Enter');
    await expect(page).toHaveURL(/rule=R20-disk/);
    await expect(table.getByRole('row')).toHaveCount(3);
    await page.getByRole('button', { name: 'Süzgeçleri temizle' }).click();
    await page.getByLabel('Ara', { exact: true }).fill('switch-3');
    await expect(table.getByRole('row')).toHaveCount(2);
    expect(page.url()).not.toContain('switch-3');
    expect(api.callsTo('GET', '/alerts').at(-1)?.query.get('q')).toBe('switch-3');
    await page.getByLabel('Ara', { exact: true }).fill('');
    await expect(table.getByRole('row')).toHaveCount(8);

    // Sort by severity (most severe first) and by state (Yeni first).
    await table.getByRole('button', { name: 'Önem' }).click();
    await expect(page).toHaveURL(/sort=severity&order=desc/);
    await expect(table.getByRole('row').nth(1)).toContainText('Yüksek');
    await table.getByRole('button', { name: 'Durum' }).click();
    await expect(page).toHaveURL(/sort=state&order=asc/);
    await expect(table.getByRole('row').nth(1)).toContainText('Yeni');
    await expect(table.getByRole('row').nth(7)).toContainText('Yanlış alarm');
  });

  test('acknowledge, then close with a note; the note is its own history entry', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security?alert=4');
    const drawer = page.getByRole('dialog', { name: /switch-3 kaynağından/ });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByTestId('runbook')).toContainText('syslog ayarının ARCILOGGER');
    await expect(drawer.getByRole('cell', { name: 't***@example.com' })).toBeVisible();

    // Every change the API allows from "Yeni", with the hint about closing too early.
    await expect(drawer.getByRole('button', { name: 'İncelemeye al' })).toBeVisible();
    await expect(drawer.getByRole('button', { name: 'Uyarıyı kapat' })).toBeVisible();
    await expect(drawer.getByRole('button', { name: 'Yanlış alarm' })).toBeVisible();
    await expect(drawer.getByTestId('close-hint')).toHaveText(
      "Sorun sürüyorsa yeni bir uyarı açılır; üzerinde çalışırken İnceleniyor'u kullanın.",
    );

    await drawer.getByRole('button', { name: 'İncelemeye al' }).click();
    await expect(drawer.getByText('Durum “Yeni” yerine “İnceleniyor” olacak.')).toBeVisible();
    await drawer.getByRole('button', { name: 'Durumu değiştir' }).click();
    await expect(page.getByText('Uyarının durumu: İnceleniyor', { exact: true })).toBeVisible();
    // The form is gone with the old state: the focus is on the first new action.
    await expect(drawer.getByRole('button', { name: 'Uyarıyı kapat' })).toBeFocused();
    await expect(drawer.getByRole('button', { name: 'İncelemeyi bırak' })).toBeVisible();

    await drawer.getByRole('button', { name: 'Uyarıyı kapat' }).click();
    await drawer
      .getByLabel('Not (isteğe bağlı)')
      .fill('Anahtarın güç kablosu takıldı; loglar yeniden geliyor.');
    await drawer.getByRole('button', { name: 'Durumu değiştir' }).click();
    await expect(page.getByText('Uyarının durumu: Kapandı', { exact: true })).toBeVisible();
    await expect(drawer.getByRole('button', { name: 'Yeniden aç' })).toBeVisible();
    await expect(
      drawer.getByRole('button', { name: 'Yanlış alarm olarak işaretle' }),
    ).toBeVisible();
    await expect(drawer.getByTestId('close-hint')).toHaveCount(0);

    const history = drawer.getByRole('region', { name: 'Geçmiş' });
    await expect(history.getByText('İnceleniyor → Kapandı')).toBeVisible();
    await expect(history.getByText('Yeni → İnceleniyor')).toBeVisible();
    await expect(history.getByText('tolga not ekledi')).toBeVisible();
    await expect(
      history.getByText('Anahtarın güç kablosu takıldı; loglar yeniden geliyor.'),
    ).toBeVisible();
    expect(api.callsTo('POST', '/alerts/4/state').map((call) => call.body)).toEqual([
      { state: 'acknowledged' },
      { state: 'resolved', note: 'Anahtarın güç kablosu takıldı; loglar yeniden geliyor.' },
    ]);

    // The list behind the drawer shows the new state.
    await drawer.getByRole('button', { name: 'Kapat', exact: true }).click();
    await expect(page).toHaveURL(/\/security$/);
    await expect(
      page
        .getByRole('table', { name: 'Güvenlik uyarıları' })
        .getByRole('row', { name: /switch-3/ }),
    ).toContainText('Kapandı');
  });

  test('a change someone else made meanwhile is explained and the alert reloaded', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security?alert=2');
    const drawer = page.getByRole('dialog', { name: /11\.10\.2026 günü mühürlenemedi/ });
    await expect(drawer.getByRole('button', { name: 'İncelemeye al' })).toBeVisible();
    const reads = api.callsTo('GET', '/alerts/2').length;
    const alert = api.alerts.alerts.get(2);
    if (!alert) throw new Error('sample alert 2 missing');
    alert.state = 'acknowledged';
    await drawer.getByRole('button', { name: 'İncelemeye al' }).click();
    await drawer.getByRole('button', { name: 'Durumu değiştir' }).click();
    const conflict = drawer.getByTestId('state-conflict');
    await expect(conflict).toContainText('Uyarı bu arada değişmiş');
    await expect(conflict).toContainText('Güncel durum yüklendi');
    await expect.poll(() => api.callsTo('GET', '/alerts/2').length).toBeGreaterThan(reads);
    await expect(drawer.getByRole('button', { name: 'İncelemeyi bırak' })).toBeVisible();
  });

  test('a closed alert whose problem has a new active alert links to it', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security?alert=5');
    const drawer = page.getByRole('dialog', { name: /D: diski %84 dolu/ });
    await drawer.getByRole('button', { name: 'Yeniden aç' }).click();
    await drawer.getByRole('button', { name: 'Durumu değiştir' }).click();
    const refusal = drawer.getByTestId('state-key-active');
    await expect(refusal).toContainText('Aynı sorun için etkin bir uyarı var');
    await expect(refusal).toContainText('#7');
    expect(api.alerts.alerts.get(5)?.state).toBe('resolved');
    await refusal.getByRole('button', { name: 'Etkin uyarıyı aç (#7)' }).click();
    await expect(page).toHaveURL(/\/security\?alert=7$/);
    await expect(drawer.getByRole('button', { name: 'İncelemeyi bırak' })).toBeVisible();
    await expect(drawer.getByTestId('state-key-active')).toHaveCount(0);
    await expect(
      drawer.getByRole('region', { name: 'Geçmiş' }).getByText('Disk büyütülecek'),
    ).toBeVisible();
  });

  test("a check's automatic close shows its reason; a note is added", async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security?alert=3');
    const drawer = page.getByRole('dialog', { name: /Kalan kontör 420/ });
    const history = drawer.getByRole('region', { name: 'Geçmiş' });
    await expect(history).toContainText('Yeni → Kapandı');
    await expect(history).toContainText('Sistem tarafından');
    await expect(history).toContainText('Neden: kontör eşiğin üzerinde');
    await expect(drawer.getByText('Sistem kapattı')).toBeVisible();

    await drawer.getByRole('button', { name: 'Not ekle' }).click();
    await expect(drawer.getByText('Bu alan zorunludur.')).toBeVisible();
    await drawer.getByLabel('Yeni not').fill('Kontör siparişi onaylandı.');
    await drawer.getByRole('button', { name: 'Not ekle' }).click();
    await expect(history.getByText('Kontör siparişi onaylandı.')).toBeVisible();
    expect(api.callsTo('POST', '/alerts/3/notes')[0]?.body).toEqual({
      text: 'Kontör siparişi onaylandı.',
    });
  });

  test('an alert that does not exist is said so', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security?alert=999');
    await expect(page.getByRole('dialog').getByText('Uyarı bulunamadı')).toBeVisible();
  });

  test('without alerts.manage the alerts are read-only', async ({ page }) => {
    await mockApi(page, { signedInAs: 'zeynep', grant: { viewer: ['alerts.read'] } });
    await page.goto('/security?alert=4');
    const drawer = page.getByRole('dialog', { name: /switch-3 kaynağından/ });
    await expect(drawer.getByTestId('runbook')).toBeVisible();
    await expect(drawer.getByRole('button', { name: 'İncelemeye al' })).toHaveCount(0);
    await expect(drawer.getByLabel('Yeni not')).toHaveCount(0);
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('read-only')).toBeVisible();
  });

  test('the runbook follows the interface language', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security?alert=4');
    const drawer = page.getByRole('dialog', { name: /No message from switch-3/ });
    await expect(drawer.getByRole('heading', { name: 'What to do' })).toBeVisible();
    await expect(drawer.getByTestId('runbook')).toContainText('its syslog setting points');
    await expect(drawer.getByRole('button', { name: 'Start investigating' })).toBeVisible();
  });

  test('roles without alerts.read get the forbidden page', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    await page.goto('/security');
    await expect(
      page.getByRole('heading', { level: 1, name: 'Bu sayfa için yetkiniz yok' }),
    ).toBeVisible();
    expect(api.calls.filter((call) => call.path.startsWith('/alerts'))).toEqual([]);
  });
});
