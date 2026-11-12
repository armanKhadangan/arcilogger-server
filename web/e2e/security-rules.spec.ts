import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Güvenlik, Wave 6 additions (contract §2.6.2, §2.6.3, W6-F): the summary's 30 days and top
 * lists, an alert's assignee and "Bana atananlar", the Kanıt tab with "Ham satırı aç" through the
 * raw-line view, and the person of a person-level alert only for `usage.person`. The API is
 * `mock-alerts.ts` with `rules: true` (two rule alerts from the contract beside the captured ones)
 * until W6-A's API is captured.
 */

async function openAlert(page: Page, id: number) {
  await page.goto(`/security?alert=${id}`);
  const sheet = page.getByRole('dialog');
  await expect(sheet.getByRole('heading').first()).toBeVisible();
  return sheet;
}

test.describe('Güvenlik — Wave 6 additions', () => {
  test('the last 30 days and the top lists; a rule filters the list', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', alerts: { rules: true } });
    await page.goto('/security');
    const trends = page.getByTestId('security-trends');
    await expect(trends.getByRole('heading', { name: 'Son 30 gün' })).toBeVisible();
    await trends.getByRole('button', { name: 'Tablo görünümü' }).click();
    await expect(trends.getByRole('row')).toHaveCount(31);
    await expect(page.getByTestId('top-keys')).toContainText('172.20.41.17');
    await page.getByTestId('top-rules').getByRole('link', { name: 'R01' }).click();
    await expect(page).toHaveURL(/\/security\?rule=R01$/);
    await expect(page.getByLabel('Kural', { exact: true })).toHaveValue('R01');
  });

  test('evidence: the kept rows and the raw line of one', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', alerts: { rules: true } });
    const sheet = await openAlert(page, 9001);
    await sheet.getByRole('tab', { name: 'Kanıt (47)' }).click();
    const evidence = sheet.getByTestId('alert-evidence');
    await expect(evidence).toContainText('47 kaydın ilk 3 tanesi kanıt olarak saklanır.');
    await expect(evidence.getByTestId('evidence-row')).toHaveCount(3);
    await expect(evidence.getByTestId('evidence-row').first()).toContainText(
      'FortiGate oturum kaydı',
    );
    await evidence.getByRole('button', { name: 'Ham satırı aç' }).first().click();
    const raw = page.getByRole('dialog', { name: 'Ham kayıt satırı' });
    await expect(raw).toContainText('spool/fortigate/2026-09-01.log');
  });

  test('assignment: to me, then "Bana atananlar", then nobody', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', alerts: { rules: true } });
    const sheet = await openAlert(page, 9001);
    const assignee = sheet.getByLabel('Uyarıyı kim inceliyor');
    await expect(assignee).toHaveText('Kimseye atanmadı');
    await assignee.click();
    // Only active users whose role reads alerts (and "nobody").
    await expect(page.getByRole('option')).toHaveText(['Kimseye atanmadı', 'tolga (ben)']);
    await page.getByRole('option', { name: 'tolga (ben)' }).click();
    await expect(
      page.getByText('Uyarı tolga kullanıcısına atandı.', { exact: true }),
    ).toBeVisible();
    expect(api.callsTo('POST', '/alerts/9001/assign').at(-1)?.body).toEqual({ user_id: 1 });
    await expect(sheet.getByText('tolga kullanıcısına atandı', { exact: true })).toBeVisible();
    await sheet.getByRole('button', { name: 'Kapat', exact: true }).click();

    await page.getByRole('button', { name: 'Bana atananlar' }).click();
    await expect(page).toHaveURL(/assignee=me/);
    const rows = page.getByRole('table', { name: /Güvenlik uyarıları/ }).getByRole('row');
    await expect(rows).toHaveCount(2); // the header and R01
    await expect(rows.nth(1)).toContainText('R01');
    await expect(rows.nth(1)).toContainText('tolga');

    const again = await openAlert(page, 9001);
    await again.getByLabel('Uyarıyı kim inceliyor').click();
    await page.getByRole('option', { name: 'Kimseye atanmadı' }).click();
    await expect(page.getByText('Atama kaldırıldı.', { exact: true })).toBeVisible();
    expect(api.callsTo('POST', '/alerts/9001/assign').at(-1)?.body).toEqual({ user_id: null });
  });

  test('the person of a person-level alert only for usage.person', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga', alerts: { rules: true } });
    let sheet = await openAlert(page, 9002);
    await expect(sheet.getByTestId('alert-person')).toContainText('Kişi: Deniz Örnek');
    await expect(sheet.getByTestId('alert-person')).toContainText('denetim kaydına yazıldı');
    await expect(sheet.getByRole('tab')).toHaveCount(0); // no evidence rows kept

    await mockApi(page, {
      signedInAs: 'tolga',
      alerts: { rules: true },
      revoke: { admin: ['usage.person'] },
    });
    sheet = await openAlert(page, 9002);
    await expect(sheet.getByText('172.20.30.41 bir saatte 1,4 GB gönderdi').first()).toBeVisible();
    await expect(sheet.getByTestId('alert-person')).toHaveCount(0);
    await expect(sheet).not.toContainText('Deniz Örnek');
  });

  test('the answers of today’s API: no trends, no tabs, nobody assigned', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/security');
    await expect(page.getByTestId('security-summary')).toBeVisible();
    await expect(page.getByTestId('security-trends')).toHaveCount(0);
  });

  test('in English', async ({ page }) => {
    await usePreferences(page, { lang: 'en' });
    await mockApi(page, { signedInAs: 'tolga', alerts: { rules: true } });
    const sheet = await openAlert(page, 9001);
    await expect(sheet.getByLabel('Who is investigating the alert')).toHaveText('Not assigned');
    await sheet.getByRole('tab', { name: 'Evidence (47)' }).click();
    await expect(sheet.getByTestId('alert-evidence')).toContainText(
      'The first 3 of 47 records are kept as evidence.',
    );
  });

  for (const theme of ['light', 'dark'] as Theme[]) {
    test(`accessibility (${theme}): trends, the evidence tab and the assignee`, async ({
      page,
    }) => {
      await usePreferences(page, { theme });
      await mockApi(page, { signedInAs: 'tolga', alerts: { rules: true } });
      await page.goto('/security');
      await expect(page.getByTestId('security-trends')).toBeVisible();
      await expectNoA11yViolations(page);
      const sheet = await openAlert(page, 9001);
      await sheet.getByRole('tab', { name: 'Kanıt (47)' }).click();
      await expect(sheet.getByTestId('evidence-row')).toHaveCount(3);
      await expectNoA11yViolations(page);
    });
  }
});
