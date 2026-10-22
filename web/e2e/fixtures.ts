import AxeBuilder from '@axe-core/playwright';
import { expect, type Page } from '@playwright/test';

export type Theme = 'light' | 'dark';

/** Preload preferences before the app starts (same keys the app uses). */
export async function usePreferences(
  page: Page,
  prefs: { theme?: Theme; lang?: 'tr' | 'en'; collapsed?: boolean },
): Promise<void> {
  await page.addInitScript((p) => {
    if (p.theme) window.localStorage.setItem('arci.theme', p.theme);
    if (p.lang) window.localStorage.setItem('arci.lang', p.lang);
    if (p.collapsed !== undefined)
      window.localStorage.setItem('arci.sidebar.collapsed', p.collapsed ? '1' : '0');
  }, prefs);
}

/** Type a user name and password on the sign-in page and submit. */
export async function submitCredentials(page: Page, username: string, password: string) {
  await page.getByLabel('Kullanıcı adı').fill(username);
  await page.getByLabel('Parola', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Giriş yap' }).click();
}

/** The mocked users' password (fake test credential of the mocked API). */
export const MOCK_PASSWORD = 'Uzun-ve-Gizli-Parola-2026'; // gitleaks:allow — fake test credential

/** Answer the "Kimliğinizi doğrulayın" dialog (password + TOTP of the mocked API). */
export async function confirmReauth(page: Page, code = '123456'): Promise<void> {
  const reauth = page.getByRole('dialog', { name: 'Kimliğinizi doğrulayın' });
  await expect(reauth).toBeVisible();
  await reauth.getByLabel('Parola', { exact: true }).fill(MOCK_PASSWORD);
  await reauth.getByLabel('Doğrulama kodu').fill(code);
  await reauth.getByRole('button', { name: 'Doğrula', exact: true }).click();
  await expect(reauth).toBeHidden();
}

/** The browser's clock at 15:00 site time on the day the API samples were captured. */
export async function useSampleClock(page: Page, today: string): Promise<void> {
  await page.clock.setFixedTime(new Date(`${today}T15:00:00+03:00`));
}

/**
 * WCAG 2.1 A/AA scan; fails with a readable list of violations. Waits until opening animations
 * (a drawer sliding and fading in) have finished: half-transparent text in mid-animation is not
 * what the user reads, and axe would report its contrast. Endless animations (spinners) are
 * ignored. `exclude`: selectors left out of the scan (e.g. a sandboxed frame with the server's
 * e-mail HTML, where axe cannot run because the sandbox allows no scripts).
 */
export async function expectNoA11yViolations(
  page: Page,
  options: { exclude?: string[] } = {},
): Promise<void> {
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every(
        (animation) =>
          animation.playState !== 'running' ||
          animation.effect?.getComputedTiming().iterations === Infinity,
      ),
  );
  let builder = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']);
  for (const selector of options.exclude ?? []) builder = builder.exclude(selector);
  const results = await builder.analyze();
  const summary = results.violations.map(
    (v) =>
      `${v.id} (${v.impact ?? 'n/a'}): ${v.help}\n  ${v.nodes
        .map((n) => n.target.join(' '))
        .slice(0, 5)
        .join('\n  ')}`,
  );
  expect(summary, summary.join('\n')).toEqual([]);
}
