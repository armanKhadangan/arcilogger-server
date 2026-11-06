import { expect, test } from '@playwright/test';

import { expectNoA11yViolations } from './fixtures';
import { mockApi } from './mock-api';

/**
 * Ayarlar → Genel, the outbound proxy (Wave 5 contract 2.5, D13): `network.https_proxy`, shown
 * once the API reports it, `http://host:port` or empty, with the "sahada doğrula" note of the
 * Zamane client. Interim: `mock-admin.ts` (W5-T's) has no `network` yet and its `GeneralBody`
 * refuses it, so this spec sends no proxy; the body of a change of it is
 * `general-network.test.ts`'s.
 */

test.describe('Ayarlar → Genel: the outbound proxy', () => {
  test('no field while the API does not report it', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    // An API before W5-T: W5-T's mock reports `network`, so it is taken out here.
    delete (api.admin.sections.general as { network?: unknown }).network;
    await page.goto('/settings/general');
    await expect(page.getByTestId('settings-clock')).toBeVisible();
    await expect(page.getByTestId('settings-network')).toHaveCount(0);
  });

  test('checked in the browser, sent only when changed', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    Object.assign(api.admin.sections.general, { network: { https_proxy: '' } });
    await page.goto('/settings/general');
    const card = page.getByTestId('settings-network');
    const proxy = card.getByLabel('HTTPS vekil sunucusu');
    await expect(proxy).toHaveValue('');
    await expect(proxy).toHaveAttribute('placeholder', 'http://172.20.0.10:3128');
    await expect(page.getByTestId('network-zamane-note')).toContainText('Sahada doğrula');
    await expect(card).not.toContainText('parolanız yeniden sorulur');

    for (const bad of ['172.20.0.10:3128', 'http://172.20.0.10', 'http://vekil@172.20.0.10:3128']) {
      await proxy.fill(bad);
      await expect(card).toContainText('Vekil sunucu değişikliği için parolanız yeniden sorulur.');
      await page.getByRole('button', { name: 'Kaydet' }).click();
      await expect(proxy).toHaveAccessibleDescription(
        'http://sunucu:bağlantı-noktası biçiminde bir adres girin (sunucu adı ya da IPv4 adresi, 1–65535 arası bağlantı noktası) ya da alanı boş bırakın.',
      );
      await expect(proxy).toBeFocused();
    }
    await expectNoA11yViolations(page);
    expect(api.callsTo('POST', '/admin/config/general/check')).toHaveLength(0);

    // Emptied again: a change of the site name sends no proxy.
    await proxy.fill('  ');
    await expect(card).not.toContainText('parolanız yeniden sorulur');
    await page.getByLabel('Kuruluş adı').fill('Örnek Holding A.Ş.');
    await page.getByRole('button', { name: 'Kaydet' }).click();
    await page
      .getByRole('dialog', { name: 'Değişiklikleri onaylayın' })
      .getByRole('button', { name: 'Onayla ve kaydet' })
      .click();
    await expect(page.getByTestId('request-progress').getByTestId('request-state')).toHaveText(
      'Uygulandı',
      { timeout: 10_000 },
    );
    const put = api.callsTo('PUT', '/admin/config/general').at(-1)?.body;
    expect(put).toMatchObject({ site_name: 'Örnek Holding A.Ş.' });
    expect(put).not.toHaveProperty('network');
  });
});
