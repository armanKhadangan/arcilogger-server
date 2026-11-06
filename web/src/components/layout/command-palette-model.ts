/**
 * The "Sayfaya git" palette's model (`command-palette.tsx`): what it lists for a user, how typed
 * text finds an entry, and the keys that open it.
 */

import { KeyRound, ListChecks, Users } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { useAuth } from '@/lib/auth';
import { LICENSE_ADMIN_PERMISSION, LICENSE_PAGE } from '@/lib/license';
import { SETTINGS_SECTIONS } from '@/lib/settings-sections';
import { visibleNavItems } from '@/nav';

/** The first-run wizard's address (`pages/setup-wizard/wizard-mode.ts`, not imported: lazy page). */
const WIZARD_PATH = '/settings/setup';

/** What the palette offers: a page or a section of Ayarlar the user may open. */
export interface PaletteEntry {
  id: string;
  /** The palette's heading the entry is listed under ("nav.groups.*" or "nav.settings"). */
  heading: string;
  label: string;
  /** The same entry's names in the other language, so either finds it. */
  keywords: string[];
  to: string;
  icon: LucideIcon;
}

/**
 * Lower case without Turkish marks, so "muhur" finds "Mühürleme" and "ISTEK" finds "istek": the
 * palette is typed fast, often without switching the keyboard layout.
 */
export function foldText(text: string): string {
  return text
    .toLocaleLowerCase('tr')
    .replace(/ı/g, 'i')
    .replace(/ş/g, 's')
    .replace(/ğ/g, 'g')
    .replace(/ü/g, 'u')
    .replace(/ö/g, 'o')
    .replace(/ç/g, 'c')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '');
}

/**
 * The palette's ranking: every word typed must start a word of the entry's name (or keywords);
 * a name that starts with the first word comes first. 0 hides the entry.
 */
export function paletteScore(value: string, search: string, keywords: readonly string[] = []) {
  const words = foldText(search).split(/\s+/).filter(Boolean);
  if (words.length === 0) return 1;
  const names = [value, ...keywords].map(foldText);
  let best = 0;
  for (const name of names) {
    const parts = name.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
    const every = words.every((word) => parts.some((part) => part.startsWith(word)));
    if (!every) continue;
    best = Math.max(best, name.startsWith(words[0] ?? '') ? 1 : 0.6);
  }
  return best;
}

/** The pages and Ayarlar sections `granted` opens, in the menu's order. */
export function usePaletteEntries(): PaletteEntry[] {
  const { t, i18n } = useTranslation();
  const { permissions } = useAuth();
  return useMemo(() => {
    const other = i18n.getFixedT(i18n.language === 'en' ? 'tr' : 'en');
    const entries: PaletteEntry[] = visibleNavItems(permissions).map((item) => ({
      id: `page:${item.key}`,
      heading: t(`nav.groups.${item.group}`),
      label: t(`nav.${item.key}`),
      keywords: [other(`nav.${item.key}`)],
      to: item.path,
      icon: item.icon,
    }));
    const settings = t('nav.settings');
    const add = (id: string, key: string, to: string, icon: LucideIcon) => {
      entries.push({ id, heading: settings, label: t(key), keywords: [other(key)], to, icon });
    };
    if (permissions.has('users.manage')) add('users', 'users.title', '/settings/users', Users);
    if (permissions.has(LICENSE_ADMIN_PERMISSION)) {
      add('license', 'license.page.title', LICENSE_PAGE, KeyRound);
      add('wizard', 'setupWizard.title', WIZARD_PATH, ListChecks);
    }
    for (const section of SETTINGS_SECTIONS) {
      if (!section.permissions.every((permission) => permissions.has(permission))) continue;
      add(`settings:${section.key}`, `settings.sections.${section.key}`, section.to, section.icon);
    }
    return entries;
  }, [i18n, permissions, t]);
}

/**
 * Opens the palette on Ctrl+K (⌘K on a Mac) anywhere in the application, unless another dialog
 * is open: its keys stay its own.
 */
export function usePaletteShortcut(setOpen: (open: boolean) => void) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== 'k' || event.altKey || event.shiftKey) return;
      if (!(event.ctrlKey || event.metaKey)) return;
      const dialog = document.querySelector('[role="dialog"], [role="alertdialog"]');
      if (dialog && !dialog.hasAttribute('data-command-palette')) return;
      event.preventDefault();
      setOpen(true);
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [setOpen]);
}

/** "Ctrl K", or "⌘ K" on a Mac (the key the user presses). */
export function paletteKeys(): string {
  const agent = typeof navigator === 'undefined' ? '' : navigator.userAgent;
  return /Macintosh|iPhone|iPad/.test(agent) ? '⌘ K' : 'Ctrl K';
}
