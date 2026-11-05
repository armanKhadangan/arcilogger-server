/**
 * Wizard mode of the Ayarlar pages (contract 2.8.4): a page opened from the first-run wizard
 * leads back to it ("Kurulum sihirbazına dön") instead of to Ayarlar. It starts with `wizard=1`
 * in the URL and is kept for this tab (`sessionStorage`), because a page's own links may replace
 * the whole query (the Kamu SM steps do); the wizard page and Ayarlar's overview end it. Storage
 * can be unavailable: every access is guarded, and then only the URL counts.
 */

/** The wizard's page. */
export const WIZARD_PATH = '/settings/setup';

const MODE_KEY = 'arci.setupWizard';

/** `path` (with or without a query) with `wizard=1` added. */
export function wizardLink(path: string): string {
  return `${path}${path.includes('?') ? '&' : '?'}wizard=1`;
}

/** The URL's query (`location.search`) opens a page from the wizard. */
export function wizardInUrl(search: string): boolean {
  return new URLSearchParams(search).get('wizard') === '1';
}

/** Remember wizard mode for this tab; `false` when the browser refused. */
export function startWizardMode(storage: () => Storage = () => window.sessionStorage): boolean {
  try {
    storage().setItem(MODE_KEY, '1');
    return true;
  } catch {
    return false;
  }
}

/** Leave wizard mode (back in the wizard or in Ayarlar's overview). Never throws. */
export function endWizardMode(storage: () => Storage = () => window.sessionStorage): void {
  try {
    storage().removeItem(MODE_KEY);
  } catch {
    // Storage unavailable: nothing was kept either.
  }
}

/** This tab is in wizard mode (none when storage is unavailable). */
export function wizardModeKept(storage: () => Storage = () => window.sessionStorage): boolean {
  try {
    return storage().getItem(MODE_KEY) === '1';
  } catch {
    return false;
  }
}
