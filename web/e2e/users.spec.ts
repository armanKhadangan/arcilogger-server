import { expect, test, type Page } from '@playwright/test';

import { expectNoA11yViolations, usePreferences, type Theme } from './fixtures';
import { CSRF_TOKEN, mockApi, TEMPORARY_PASSWORD, VALID_TOTP } from './mock-api';

const PASSWORD = 'Uzun-ve-Gizli-Parola-2026'; // gitleaks:allow — fake test credential (mocked API)

async function openActions(page: Page, name: string) {
  await page.getByRole('button', { name: `İşlemler: ${name}` }).click();
}

async function confirmReauth(page: Page) {
  const reauth = page.getByRole('dialog', { name: 'Kimliğinizi doğrulayın' });
  await expect(reauth).toBeVisible();
  await reauth.getByLabel('Parola', { exact: true }).fill(PASSWORD);
  await reauth.getByLabel('Doğrulama kodu').fill(VALID_TOTP);
  await reauth.getByRole('button', { name: 'Doğrula', exact: true }).click();
  await expect(reauth).toBeHidden();
}

test.describe('Ayarlar → Kullanıcılar ve Roller', () => {
  test('lists the accounts with their state; self has no rights-changing actions', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings');
    await page.getByRole('link', { name: /Kullanıcılar ve Roller/ }).click();
    await expect(
      page.getByRole('heading', { level: 1, name: 'Kullanıcılar ve Roller' }),
    ).toBeVisible();
    const table = page.getByRole('table', { name: 'Kullanıcı hesapları' });
    await expect(table.getByRole('row')).toHaveCount(5);
    const zeynep = table.getByRole('row').filter({ hasText: 'Zeynep Demir' });
    await expect(zeynep.getByText('Kilitli')).toBeVisible();
    await expect(zeynep.getByText('Rapor İzleyici')).toBeVisible();
    await expect(
      table.getByRole('row').filter({ hasText: 'Tolga Yılmaz' }).getByText('(siz)'),
    ).toBeVisible();

    await openActions(page, 'Tolga Yılmaz');
    const menu = page.getByRole('menu');
    await expect(menu.getByRole('menuitem', { name: 'Düzenle' })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: 'Rolü değiştir' })).toHaveCount(0);
    await expect(menu.getByRole('menuitem', { name: 'Devre dışı bırak' })).toHaveCount(0);
    await page.keyboard.press('Escape');

    await page.getByRole('combobox', { name: 'Rol' }).click();
    await page.getByRole('option', { name: 'Denetçi' }).click();
    await expect(table.getByRole('row')).toHaveCount(2);
  });

  test('adds a user after re-authentication and shows the temporary password once', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/users');
    await page.getByRole('button', { name: 'Kullanıcı ekle' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kullanıcı ekle' });
    await dialog.getByRole('button', { name: 'Kullanıcıyı oluştur' }).click();
    await expect(dialog.getByText('Bu alan zorunludur.')).toHaveCount(3);

    await dialog.getByLabel('Kullanıcı adı').fill('ayse.yilmaz');
    await dialog.getByLabel('Ad soyad').fill('Ayşe Yılmaz');
    await dialog.getByLabel('Rol').click();
    await page.getByRole('option', { name: 'İK Raporları' }).click();
    await dialog.getByRole('button', { name: 'Kullanıcıyı oluştur' }).click();
    await confirmReauth(page);
    await expect(dialog.getByText('Bu kullanıcı adı kullanılıyor.')).toBeVisible();

    await dialog.getByLabel('Kullanıcı adı').fill('Can.Ozturk');
    await dialog.getByLabel('E-posta').fill('can@ornek.com.tr');
    await dialog.getByRole('button', { name: 'Kullanıcıyı oluştur' }).click();
    const temporary = page.getByRole('dialog', { name: 'Geçici parola' });
    await expect(temporary).toBeVisible();
    await expect(temporary.getByText(TEMPORARY_PASSWORD)).toBeVisible();
    await expect(temporary.getByText(/yalnızca bir kez gösterilir/)).toBeVisible();
    await temporary.getByRole('button', { name: 'Kapat' }).first().click();
    await expect(page.getByText(TEMPORARY_PASSWORD)).toHaveCount(0);
    // The temporary password followed the "add" dialog: the focus goes back to the button.
    await expect(page.getByRole('button', { name: 'Kullanıcı ekle' })).toBeFocused();
    await expect(page.getByRole('row').filter({ hasText: 'can.ozturk' })).toBeVisible();

    const creates = api.callsTo('POST', '/users');
    expect(creates.map((call) => call.body)).toEqual([
      { username: 'ayse.yilmaz', display_name: 'Ayşe Yılmaz', email: null, role: 'hr' },
      { username: 'ayse.yilmaz', display_name: 'Ayşe Yılmaz', email: null, role: 'hr' },
      {
        username: 'can.ozturk',
        display_name: 'Ayşe Yılmaz',
        email: 'can@ornek.com.tr',
        role: 'hr',
      },
    ]);
    expect(creates.every((call) => call.csrf === CSRF_TOKEN)).toBe(true);
    expect(api.callsTo('POST', '/auth/reauth')).toHaveLength(1);
  });

  test('changes a role, disables, resets password and 2FA, unlocks', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/users');

    await openActions(page, 'Mehmet Kaya');
    await page.getByRole('menuitem', { name: 'Rolü değiştir' }).click();
    const roleDialog = page.getByRole('dialog', { name: 'Rolü değiştir' });
    await roleDialog.getByLabel('Rol').click();
    await page.getByRole('option', { name: 'Rapor İzleyici' }).click();
    await roleDialog.getByRole('button', { name: 'Rolü değiştir' }).click();
    await expect(
      page.getByText('Mehmet Kaya artık Rapor İzleyici.', { exact: true }),
    ).toBeVisible();
    expect(api.get('mehmet.kaya').role).toBe('viewer');

    await openActions(page, 'Mehmet Kaya');
    await page.getByRole('menuitem', { name: 'Devre dışı bırak' }).click();
    const confirm = page.getByRole('dialog', { name: 'Kullanıcıyı devre dışı bırak' });
    await expect(confirm.getByText(/artık giriş yapamaz/)).toBeVisible();
    await confirm.getByRole('button', { name: 'Devre dışı bırak' }).click();
    await expect(
      page.getByText('Mehmet Kaya devre dışı bırakıldı.', { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('row').filter({ hasText: 'Mehmet Kaya' }).getByText('Devre dışı'),
    ).toBeVisible();

    await openActions(page, 'Ayşe Yılmaz');
    await page.getByRole('menuitem', { name: 'Parolayı sıfırla' }).click();
    await page
      .getByRole('dialog', { name: 'Parolayı sıfırla' })
      .getByRole('button', { name: 'Parolayı sıfırla' })
      .click();
    await expect(
      page.getByRole('dialog', { name: 'Geçici parola' }).getByText(TEMPORARY_PASSWORD),
    ).toBeVisible();
    await page.keyboard.press('Escape');

    await openActions(page, 'Ayşe Yılmaz');
    await page.getByRole('menuitem', { name: "2FA'yı sıfırla" }).click();
    await page
      .getByRole('dialog', { name: 'İki adımlı doğrulamayı sıfırla' })
      .getByRole('button', { name: "2FA'yı sıfırla" })
      .click();
    await expect(
      page.getByText('Ayşe Yılmaz için iki adımlı doğrulama sıfırlandı.', { exact: true }),
    ).toBeVisible();
    expect(api.get('ayse.yilmaz').totp_enabled).toBe(false);

    await openActions(page, 'Zeynep Demir');
    await page.getByRole('menuitem', { name: 'Kilidi aç' }).click();
    await page
      .getByRole('dialog', { name: 'Hesap kilidini aç' })
      .getByRole('button', { name: 'Kilidi aç' })
      .click();
    await expect(
      page.getByText('Zeynep Demir hesabının kilidi açıldı.', { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('row').filter({ hasText: 'Zeynep Demir' }).getByText('Kilitli'),
    ).toHaveCount(0);

    expect(
      api.calls
        .filter((call) => call.method !== 'GET')
        .map((call) => `${call.method} ${call.path}`),
    ).toEqual([
      'PATCH /users/3',
      'PATCH /users/3',
      'POST /users/2/reset-password',
      'POST /users/2/reset-totp',
      'POST /users/4/unlock',
    ]);
    expect(api.callsTo('PATCH', '/users/3').map((call) => call.body)).toEqual([
      { role: 'viewer' },
      { active: false },
    ]);
  });

  test('shows a server refusal inside the confirmation and keeps it open', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/users');
    await openActions(page, 'Ayşe Yılmaz');
    await page.getByRole('menuitem', { name: 'Devre dışı bırak' }).click();
    // E.g. another administrator demoted everyone else in the meantime (409 last_admin).
    api.failNext = { route: 'PATCH /users/2', status: 409, code: 'last_admin' };
    const confirm = page.getByRole('dialog', { name: 'Kullanıcıyı devre dışı bırak' });
    await confirm.getByRole('button', { name: 'Devre dışı bırak' }).click();
    await expect(confirm.getByRole('alert')).toHaveText(
      'Son etkin yönetici hesabının rolü değiştirilemez ve hesap devre dışı bırakılamaz.',
    );
    await confirm.getByRole('button', { name: 'Devre dışı bırak' }).click();
    await expect(confirm).toBeHidden();
    expect(api.get('ayse.yilmaz').active).toBe(false);
  });

  test('a refused confirmation keeps the keyboard focus on its button (busy, not disabled)', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/users');
    await openActions(page, 'Ayşe Yılmaz');
    await page.getByRole('menuitem', { name: 'Devre dışı bırak' }).click();
    api.failNext = { route: 'PATCH /users/2', status: 409, code: 'last_admin' };
    const confirm = page.getByRole('dialog', { name: 'Kullanıcıyı devre dışı bırak' });
    const button = confirm.getByRole('button', { name: 'Devre dışı bırak' });
    await button.focus();
    await page.keyboard.press('Enter');
    await expect(confirm.getByRole('alert')).toHaveText(
      'Son etkin yönetici hesabının rolü değiştirilemez ve hesap devre dışı bırakılamaz.',
    );
    // A button that became `disabled` while the request ran would have dropped it to <body>.
    await expect(button).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(confirm).toBeHidden();
    expect(api.get('ayse.yilmaz').active).toBe(false);
  });

  test('dialogs give the focus back to the button that opened them (keyboard only)', async ({
    page,
  }) => {
    await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/settings/users');
    const add = page.getByRole('button', { name: 'Kullanıcı ekle' });
    await add.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog', { name: 'Kullanıcı ekle' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(add).toBeFocused();

    // From the row's menu: the menu item is gone when the dialog closes; its button gets the focus.
    const actions = page.getByRole('button', { name: 'İşlemler: Mehmet Kaya' });
    for (const [item, dialog] of [
      ['Düzenle', 'Kullanıcıyı düzenle'],
      ['Rolü değiştir', 'Rolü değiştir'],
      ['Parolayı sıfırla', 'Parolayı sıfırla'],
      ['Devre dışı bırak', 'Kullanıcıyı devre dışı bırak'],
    ] as const) {
      await actions.focus();
      await page.keyboard.press('Enter');
      await page.getByRole('menuitem', { name: item }).focus();
      await page.keyboard.press('Enter');
      const opened = page.getByRole('dialog', { name: dialog });
      await expect(opened).toBeVisible();
      // Escape closes the topmost layer: wait until the menu has gone and the dialog has the
      // focus, else a slow runner hands the key to the closing menu.
      await expect(page.getByRole('menu')).toHaveCount(0);
      await expect
        .poll(() => opened.evaluate((element) => element.contains(document.activeElement)))
        .toBe(true);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(actions, `after "${item}"`).toBeFocused();
    }

    // Re-authentication on top of a confirmation, then the temporary password: the focus stays
    // in the confirmation meanwhile and ends on the row's button again.
    await actions.focus();
    await page.keyboard.press('Enter');
    await page.getByRole('menuitem', { name: 'Parolayı sıfırla' }).focus();
    await page.keyboard.press('Enter');
    const confirm = page.getByRole('dialog', { name: 'Parolayı sıfırla' });
    await confirm.getByRole('button', { name: 'Parolayı sıfırla' }).focus();
    await page.keyboard.press('Enter');
    const reauth = page.getByRole('dialog', { name: 'Kimliğinizi doğrulayın' });
    await reauth.getByLabel('Parola', { exact: true }).fill(PASSWORD);
    await reauth.getByLabel('Doğrulama kodu').fill(VALID_TOTP);
    await page.keyboard.press('Enter');
    const temporary = page.getByRole('dialog', { name: 'Geçici parola' });
    await expect(temporary.getByText(TEMPORARY_PASSWORD)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await expect(actions).toBeFocused();
  });

  test('role matrix is read-only and marks personal data', async ({ page }) => {
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/users');
    await page.getByRole('tab', { name: 'Rol matrisi' }).click();
    const matrix = page.getByRole('table', { name: 'Rol ve yetki matrisi' });
    const header = matrix.getByRole('row').first();
    await expect(header.getByRole('columnheader')).toHaveText([
      'Yetki',
      'Yönetici',
      'Denetçi',
      'İK Raporları',
      'Rapor İzleyici',
    ]);
    const person = matrix.getByRole('row').filter({ hasText: 'usage.person' });
    await expect(person.getByText('Kişisel veri')).toBeVisible();
    await expect(person.getByRole('cell')).toHaveText(['var', 'yok', 'var', 'yok']);
    const evidence = matrix.getByRole('row').filter({ hasText: 'evidence.create' });
    await expect(evidence.getByRole('cell')).toHaveText(['var', 'yok', 'yok', 'yok']);
    await expect(matrix.getByRole('button')).toHaveCount(0);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test(`users page and dialogs have no accessibility violations — ${theme}`, async ({ page }) => {
    await usePreferences(page, { theme });
    await mockApi(page, { signedInAs: 'tolga' });
    await page.goto('/settings/users');
    await expect(
      page.getByRole('table', { name: 'Kullanıcı hesapları' }).getByRole('row'),
    ).toHaveCount(5);
    await expectNoA11yViolations(page);
    await page.getByRole('button', { name: 'Kullanıcı ekle' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Kullanıcıyı oluştur' }).click();
    await expectNoA11yViolations(page);
    await page.keyboard.press('Escape');
    await page.getByRole('tab', { name: 'Rol matrisi' }).click();
    await expectNoA11yViolations(page);
    await page.goto('/settings');
    await expectNoA11yViolations(page);
  });
}
