import { readFile } from 'node:fs/promises';

import { expect, test } from '@playwright/test';

import { expectNoA11yViolations, submitCredentials, usePreferences, type Theme } from './fixtures';
import {
  CSRF_TOKEN,
  LOCKOUT_ATTEMPTS,
  mockApi,
  RECOVERY_CODES,
  TEMPORARY_PASSWORD,
  VALID_TOTP,
} from './mock-api';

const PASSWORD = 'Uzun-ve-Gizli-Parola-2026'; // gitleaks:allow — fake test credential (mocked API)

test.describe('sign-in', () => {
  test('administrator: password, TOTP code, back to the requested page', async ({ page }) => {
    const api = await mockApi(page);
    await page.goto('/audit');
    await expect(page).toHaveURL(/\/login\?next=%2Faudit$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Giriş yap' })).toBeVisible();
    await expect(page.getByText('Kayıt · Mühür · Rapor')).toBeVisible();

    await submitCredentials(page, 'tolga', PASSWORD);
    await expect(
      page.getByRole('heading', { level: 1, name: 'İki adımlı doğrulama' }),
    ).toBeVisible();
    await expect(page.getByText('Oturum 15 dk hareketsizlikte kapanır.')).toBeVisible();
    await page.getByLabel('Doğrulama kodu').fill('123 456');
    await page.getByRole('button', { name: 'Doğrula', exact: true }).click();

    await expect(page).toHaveURL(/\/audit$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Denetim Kaydı' })).toBeVisible();
    // Every mutating request carried the CSRF header from the cookie.
    const posts = api.calls.filter((call) => call.method === 'POST');
    expect(posts.map((call) => call.path)).toEqual(['/auth/login', '/auth/totp']);
    expect(posts.every((call) => call.csrf === CSRF_TOKEN)).toBe(true);
    expect(api.callsTo('POST', '/auth/totp')[0]?.body).toEqual({ code: VALID_TOTP });

    // User menu: name, role, real idle minutes, sign-out.
    await page.getByRole('button', { name: 'Kullanıcı menüsü: Tolga Yılmaz' }).click();
    const menu = page.getByRole('menu');
    await expect(menu.getByText('Yönetici')).toBeVisible();
    await expect(menu.getByText('Oturum 15 dk hareketsizlikte kapanır.')).toBeVisible();
    await menu.getByRole('menuitem', { name: 'Çıkış yap' }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect(api.callsTo('POST', '/auth/logout')).toHaveLength(1);
    await page.goto('/audit');
    await expect(page).toHaveURL(/\/login\?next=%2Faudit$/);
  });

  test('a recovery code replaces the TOTP code once', async ({ page }) => {
    const api = await mockApi(page);
    await page.goto('/login');
    await submitCredentials(page, 'tolga', PASSWORD);
    await page.getByRole('button', { name: 'Kurtarma kodu kullan' }).click();
    await page.getByLabel('Kurtarma kodu').fill(RECOVERY_CODES[0]?.toUpperCase() ?? '');
    await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
    await expect(page.getByText('Kurtarma kodu kullanıldı', { exact: true })).toBeVisible();
    expect(api.callsTo('POST', '/auth/totp')[0]?.body).toEqual({ code: 'a1b2c3d4e5f6g7h8' });
    expect(api.byName('tolga')?.recovery_codes).toHaveLength(9);
  });

  test('wrong password and lockout show the server messages', async ({ page }) => {
    await mockApi(page);
    await page.goto('/login');
    // Keyboard on the button: after the refusal the focus is still on "Giriş yap".
    await page.getByLabel('Kullanıcı adı').fill('tolga');
    await page.getByLabel('Parola', { exact: true }).fill('yanlış parola');
    const signIn = page.getByRole('button', { name: 'Giriş yap' });
    await signIn.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('alert')).toHaveText(
      'Giriş başarısız: kullanıcı adı veya parola hatalı ya da hesap geçici olarak kilitli.',
    );
    await expect(page.getByLabel('Parola', { exact: true })).toHaveValue('');
    await expect(signIn).toBeFocused();

    await submitCredentials(page, 'tolga', PASSWORD);
    const code = page.getByLabel('Doğrulama kodu');
    // Failures 2-4 (the wrong password was the first): a wrong code keeps the step.
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await code.fill('000000');
      await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
      await expect(page.getByRole('alert')).toHaveText('Doğrulama kodu hatalı veya süresi geçmiş.');
    }
    // The fifth failure locks the account and closes the session.
    await code.fill('000000');
    await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Giriş yap' })).toBeVisible();
    await expect(page.getByRole('alert')).toContainText('hesabınız geçici olarak kilitlendi');
  });

  test('first sign-in: TOTP enrolment, recovery codes once, password change, KVKK', async ({
    page,
  }) => {
    const api = await mockApi(page);
    api.users.push({
      ...api.get('ayse.yilmaz'),
      id: 9,
      username: 'can.ozturk',
      display_name: 'Can Öztürk',
      role: 'auditor',
      password: TEMPORARY_PASSWORD,
      must_change_password: true,
      totp_enabled: false,
      privacy_accepted: false,
    });
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/login');
    await submitCredentials(page, 'can.ozturk', TEMPORARY_PASSWORD);

    // Enrolment: QR as an image, key for manual entry, confirmation code.
    await expect(
      page.getByRole('heading', { level: 1, name: 'İki adımlı doğrulamayı kurun' }),
    ).toBeVisible();
    const qr = page.getByRole('img', { name: /İki adımlı doğrulama QR kodu \(can\.ozturk\)/ });
    await expect(qr).toBeVisible();
    expect(await qr.getAttribute('src')).toMatch(/^data:image\/svg\+xml;charset=utf-8,/);
    await expect(page.getByText('JBSW Y3DP EHPK 3PXP JBSW Y3DP EHPK 3PXP')).toBeVisible();
    await page.getByLabel('Uygulamadaki kod').fill('111111');
    await page.getByRole('button', { name: 'Doğrula ve etkinleştir' }).click();
    await expect(page.getByRole('alert')).toHaveText('Doğrulama kodu hatalı veya süresi geçmiş.');
    await page.getByLabel('Uygulamadaki kod').fill(VALID_TOTP);
    await page.getByRole('button', { name: 'Doğrula ve etkinleştir' }).click();

    // Recovery codes: shown once, copy and download, explicit confirmation.
    await expect(
      page.getByRole('heading', { level: 1, name: 'Kurtarma kodlarınız' }),
    ).toBeVisible();
    const list = page.getByRole('list', { name: 'Kurtarma kodları' });
    for (const code of RECOVERY_CODES) await expect(list.getByText(code)).toBeVisible();
    const proceed = page.getByRole('button', { name: 'Devam et' });
    await expect(proceed).toBeDisabled();
    await page.getByRole('button', { name: 'Kodları kopyala' }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(
      RECOVERY_CODES.join('\n'),
    );
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'İndir (.txt)' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('arcilogger-kurtarma-kodlari-can.ozturk.txt');
    const text = await readFile(await download.path(), 'utf8');
    expect(text).toContain('Hesap: can.ozturk\r\n');
    expect(text).toContain(` 1. ${RECOVERY_CODES[0]}\r\n`);
    expect(text).toContain(`10. ${RECOVERY_CODES[9]}\r\n`);
    await page
      .getByRole('checkbox', { name: 'Kurtarma kodlarını güvenli bir yere kaydettim.' })
      .check();
    await proceed.click();

    // Forced password change: the server's policy answer, then success.
    await expect(
      page.getByRole('heading', { level: 1, name: 'Parolanızı değiştirin' }),
    ).toBeVisible();
    await page.getByLabel('Mevcut (geçici) parola').fill(TEMPORARY_PASSWORD);
    await page.getByLabel('Yeni parola', { exact: true }).fill('Galatasaray1905!');
    await page.getByLabel('Yeni parola (tekrar)').fill('Galatasaray1905!');
    await page.getByRole('button', { name: 'Parolayı değiştir' }).click();
    await expect(page.getByText('Bu parola çok yaygın veya kolay tahmin edilir.')).toBeVisible();
    await page.getByLabel('Yeni parola', { exact: true }).fill('mavi bisiklet kırk iki limon');
    await page.getByLabel('Yeni parola (tekrar)').fill('mavi bisiklet kırk iki limon');
    await page.getByRole('button', { name: 'Parolayı değiştir' }).click();

    // KVKK notice: blocks the app until accepted.
    const notice = page.getByRole('dialog', { name: /KVKK bilgilendirmesi/ });
    await expect(notice).toBeVisible();
    await expect(notice.getByText('Taslak — hukuk müşaviri onayı gerekir')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(notice).toBeVisible();
    await notice.getByRole('checkbox', { name: 'Bu bilgilendirmeyi okudum ve anladım.' }).check();
    await notice.getByRole('button', { name: 'Onaylıyorum' }).click();
    await expect(notice).toBeHidden();
    await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
    expect(api.callsTo('POST', '/auth/privacy-notice')[0]?.body).toEqual({ version: '1' });
    expect(api.byName('can.ozturk')?.password).toBe('mavi bisiklet kırk iki limon');
  });

  test('keyboard focus follows the sign-in steps', async ({ page }) => {
    const api = await mockApi(page);
    api.users.push({
      ...api.get('ayse.yilmaz'),
      id: 9,
      username: 'can.ozturk',
      display_name: 'Can Öztürk',
      role: 'auditor',
      password: TEMPORARY_PASSWORD,
      must_change_password: true,
      totp_enabled: false,
      privacy_accepted: false,
    });
    await page.goto('/login');
    await expect(page.getByLabel('Kullanıcı adı')).toBeFocused();
    await page.keyboard.type('can.ozturk');
    await page.keyboard.press('Tab');
    await page.keyboard.type(TEMPORARY_PASSWORD);
    await page.keyboard.press('Enter');

    // Enrolment has no field first: the step's heading takes the focus (and is announced); the
    // next Tab stays in the card instead of starting at the language switch again.
    const enrol = page.getByRole('heading', { level: 1, name: 'İki adımlı doğrulamayı kurun' });
    await expect(enrol).toBeFocused();
    // (The key and the code field appear once the enrolment data has loaded.)
    await expect(page.getByRole('img', { name: /QR kodu/ })).toBeVisible();
    await expect(enrol).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Anahtarı kopyala' })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(page.getByLabel('Uygulamadaki kod')).toBeFocused();
    await page.keyboard.type(VALID_TOTP);
    await page.keyboard.press('Enter');

    // Recovery codes: the heading again, so the warning and the codes are read first.
    await expect(
      page.getByRole('heading', { level: 1, name: 'Kurtarma kodlarınız' }),
    ).toBeFocused();
    await page
      .getByRole('checkbox', { name: 'Kurtarma kodlarını güvenli bir yere kaydettim.' })
      .check();
    await page.getByRole('button', { name: 'Devam et' }).click();

    // A step that focuses its own first field keeps that.
    await expect(
      page.getByRole('heading', { level: 1, name: 'Parolanızı değiştirin' }),
    ).toBeVisible();
    await expect(page.getByLabel('Mevcut (geçici) parola')).toBeFocused();
  });

  test('an idle session ends with "zaman aşımı" and resumes the page after sign-in', async ({
    page,
  }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    await usePreferences(page, { collapsed: false });
    await page.goto('/');
    await expect(page.getByRole('heading', { level: 1, name: 'Panel' })).toBeVisible();
    // The Panel's own first requests are answered before the session "expires".
    await expect(page.getByTestId('card-ingest')).toBeVisible();
    await expect(page.getByTestId('card-usage')).toContainText('Aktif kullanıcı');
    api.expireWith = 'session_idle';
    await page
      .getByRole('navigation', { name: 'Ana menü' })
      .getByRole('link', { name: 'Denetim Kaydı' })
      .click();
    await expect(page).toHaveURL(/\/login\?next=%2Faudit&reason=session_idle$/);
    await expect(page.getByRole('status').filter({ hasText: 'zaman aşımına uğradı' })).toHaveText(
      'Oturumunuz zaman aşımına uğradı. Lütfen yeniden giriş yapın.',
    );
    await submitCredentials(page, 'tolga', PASSWORD);
    await page.getByLabel('Doğrulama kodu').fill(VALID_TOTP);
    await page.getByRole('button', { name: 'Doğrula', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Denetim Kaydı' })).toBeVisible();
  });

  test('new recovery codes from the user menu need a re-authentication', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
    await page.goto('/');
    await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
    await page.getByRole('menuitem', { name: 'Kurtarma kodlarını yenile' }).click();
    const dialog = page.getByRole('dialog', { name: 'Kurtarma kodlarını yenile' });
    await expect(dialog.getByText('Kalan kullanılmamış kod: 10.')).toBeVisible();
    await dialog.getByRole('button', { name: 'Yeni kodlar oluştur' }).click();
    const reauth = page.getByRole('dialog', { name: 'Kimliğinizi doğrulayın' });
    await reauth.getByLabel('Parola', { exact: true }).fill(PASSWORD);
    await reauth.getByLabel('Doğrulama kodu').fill(VALID_TOTP);
    await reauth.getByRole('button', { name: 'Doğrula', exact: true }).click();
    await expect(
      dialog.getByRole('list', { name: 'Kurtarma kodları' }).getByRole('listitem'),
    ).toHaveCount(10);
    expect(api.callsTo('POST', '/auth/recovery-codes')).toHaveLength(2); // refused, then repeated
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(page.getByRole('button', { name: /Kullanıcı menüsü/ })).toBeFocused();
  });

  test('a signed-in user changes the own password from the user menu', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'ayse.yilmaz' });
    const current = api.get('ayse.yilmaz').password;
    await page.goto('/sealing');
    await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
    await page.getByRole('menuitem', { name: 'Parolayı değiştir' }).click();
    const dialog = page.getByRole('dialog', { name: 'Parolayı değiştir' });
    // The own password: not the temporary one of the forced change after sign-in.
    await expect(dialog.getByLabel('Mevcut parola')).toBeFocused();
    await expect(dialog.getByLabel(/geçici/)).toHaveCount(0);
    await dialog.getByLabel('Mevcut parola').fill('yanlış parola bir iki üç');
    await dialog.getByLabel('Yeni parola', { exact: true }).fill('yeşil tren yirmi üç portakal');
    await dialog.getByLabel('Yeni parola (tekrar)').fill('yeşil tren yirmi üç portakal');
    await dialog.getByRole('button', { name: 'Parolayı değiştir' }).click();
    await expect(dialog.getByText('Parola veya doğrulama kodu hatalı.')).toBeVisible();
    await expect(dialog.getByLabel('Mevcut parola')).toHaveValue('');

    await dialog.getByLabel('Mevcut parola').fill(current);
    await dialog.getByRole('button', { name: 'Parolayı değiştir' }).click();
    await expect(dialog).toBeHidden();
    await expect(
      page.getByText('Parolanız değiştirildi. Diğer oturumlarınız kapatıldı.', { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole('button', { name: /Kullanıcı menüsü/ })).toBeFocused();
    await expect(page.getByRole('heading', { level: 1, name: 'Mühürleme' })).toBeVisible();
    const changes = api.callsTo('POST', '/auth/password');
    expect(changes.map((call) => call.body)).toEqual([
      {
        current_password: 'yanlış parola bir iki üç',
        new_password: 'yeşil tren yirmi üç portakal',
      },
      { current_password: current, new_password: 'yeşil tren yirmi üç portakal' },
    ]);
    expect(changes.every((call) => call.csrf === CSRF_TOKEN)).toBe(true);
    expect(api.byName('ayse.yilmaz')?.password).toBe('yeşil tren yirmi üç portakal');
  });

  test('a wrong current password that locks the account ends the session', async ({ page }) => {
    const api = await mockApi(page, { signedInAs: 'tolga' });
    api.get('tolga').failed_attempts = LOCKOUT_ATTEMPTS - 1;
    await page.goto('/sealing');
    await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
    await page.getByRole('menuitem', { name: 'Parolayı değiştir' }).click();
    const dialog = page.getByRole('dialog', { name: 'Parolayı değiştir' });
    await dialog.getByLabel('Mevcut parola').fill('yanlış parola bir iki üç');
    await dialog.getByLabel('Yeni parola', { exact: true }).fill('yeşil tren yirmi üç portakal');
    await dialog.getByLabel('Yeni parola (tekrar)').fill('yeşil tren yirmi üç portakal');
    await dialog.getByRole('button', { name: 'Parolayı değiştir' }).click();
    // Back to the same page (the day in its address too) after the next sign-in.
    await expect(page).toHaveURL(/\/login\?next=%2Fsealing[^&]*&reason=account_locked$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Giriş yap' })).toBeVisible();
    await expect(
      page.getByText(/hesabınız geçici olarak kilitlendi ve oturum kapatıldı/),
    ).toBeVisible();
    expect(api.byName('tolga')?.password).toBe(PASSWORD);
  });
});

for (const theme of ['light', 'dark'] as Theme[]) {
  test.describe(`sign-in accessibility — ${theme}`, () => {
    test.beforeEach(async ({ page }) => {
      await usePreferences(page, { theme });
    });

    test('credentials and TOTP steps', async ({ page }) => {
      await mockApi(page);
      await page.goto('/login?reason=session_idle');
      await expect(page.getByRole('heading', { level: 1, name: 'Giriş yap' })).toBeVisible();
      await page.getByRole('button', { name: 'Giriş yap' }).click(); // validation messages
      await expect(page.getByText('Bu alan zorunludur.').first()).toBeVisible();
      await expectNoA11yViolations(page);
      await submitCredentials(page, 'tolga', PASSWORD);
      await expect(page.getByLabel('Doğrulama kodu')).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('enrolment, recovery codes, password change and KVKK notice', async ({ page }) => {
      const api = await mockApi(page);
      const auditor = api.get('ayse.yilmaz');
      Object.assign(auditor, {
        password: TEMPORARY_PASSWORD,
        must_change_password: true,
        totp_enabled: false,
        privacy_accepted: false,
      });
      await page.goto('/login');
      await submitCredentials(page, 'ayse.yilmaz', TEMPORARY_PASSWORD);
      await expect(page.getByRole('img', { name: /QR/ })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.getByLabel('Uygulamadaki kod').fill(VALID_TOTP);
      await page.getByRole('button', { name: 'Doğrula ve etkinleştir' }).click();
      await expect(page.getByRole('list', { name: 'Kurtarma kodları' })).toBeVisible();
      await expectNoA11yViolations(page);
      await page.getByRole('checkbox').check();
      await page.getByRole('button', { name: 'Devam et' }).click();
      await expect(page.getByLabel('Mevcut (geçici) parola')).toBeVisible();
      await expectNoA11yViolations(page);
      await page.getByLabel('Mevcut (geçici) parola').fill(TEMPORARY_PASSWORD);
      await page.getByLabel('Yeni parola', { exact: true }).fill('mavi bisiklet kırk iki limon');
      await page.getByLabel('Yeni parola (tekrar)').fill('mavi bisiklet kırk iki limon');
      await page.getByRole('button', { name: 'Parolayı değiştir' }).click();
      await expect(page.getByRole('dialog', { name: /KVKK/ })).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('re-authentication dialog', async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga', staleReauth: true });
      await page.goto('/');
      await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
      await page.getByRole('menuitem', { name: 'Kurtarma kodlarını yenile' }).click();
      await page.getByRole('button', { name: 'Yeni kodlar oluştur' }).click();
      await expect(page.getByRole('dialog', { name: 'Kimliğinizi doğrulayın' })).toBeVisible();
      await expectNoA11yViolations(page);
    });

    test('password change dialog of the user menu', async ({ page }) => {
      await mockApi(page, { signedInAs: 'tolga' });
      await page.goto('/');
      await page.getByRole('button', { name: /Kullanıcı menüsü/ }).click();
      await page.getByRole('menuitem', { name: 'Parolayı değiştir' }).click();
      const dialog = page.getByRole('dialog', { name: 'Parolayı değiştir' });
      await expect(dialog.getByLabel('Mevcut parola')).toBeVisible();
      await expectNoA11yViolations(page);
      await dialog.getByRole('button', { name: 'Parolayı değiştir' }).click(); // validation
      await expect(dialog.getByText('Bu alan zorunludur.').first()).toBeVisible();
      await expectNoA11yViolations(page);
    });
  });
}
