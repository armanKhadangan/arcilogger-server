import i18n, { type Resource } from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from '@/locales/en.json';
import tr from '@/locales/tr.json';
import uiKitEn from '@/locales/ui-kit/en.json';
import uiKitTr from '@/locales/ui-kit/tr.json';

import type { Lang } from './format';
import { readPreference, writePreference } from './storage';

const STORAGE_KEY = 'arci.lang';

export const LANGUAGES: readonly Lang[] = ['tr', 'en'];

function storedLanguage(): Lang {
  return readPreference(STORAGE_KEY) === 'en' ? 'en' : 'tr';
}

export function setLanguage(lang: Lang): void {
  writePreference(STORAGE_KEY, lang);
  void i18n.changeLanguage(lang);
}

export function currentLang(): Lang {
  return i18n.language === 'en' ? 'en' : 'tr';
}

i18n.on('languageChanged', (lng) => {
  document.documentElement.lang = lng;
});

/** A locale file: texts under nested keys. */
export type Texts = { [key: string]: string | Texts };

/** `base` with the keys of `extra` it lacks, at any depth; a key `base` has keeps its text. */
export function addTexts(base: Texts, extra: Texts): Texts {
  const merged: Texts = { ...base };
  for (const [key, value] of Object.entries(extra)) {
    const existing = merged[key];
    if (existing === undefined) merged[key] = value;
    else if (typeof existing !== 'string' && typeof value !== 'string')
      merged[key] = addTexts(existing, value);
  }
  return merged;
}

/**
 * Page areas with locale files of their own, `locales/areas/<area>/{tr,en}.json` (Arama, Canlı
 * Akış, Kaynaklar): a feature's texts live beside each other instead of in the one large file.
 * They join the `translation` namespace, so pages look them up like any other key. An area adds
 * keys and never replaces one, neither of the locale file nor of another area
 * (`locales/locales.test.ts`); a changed text gets a new key.
 */
export const LOCALE_AREAS: Readonly<Record<string, Partial<Record<Lang, Texts>>>> = (() => {
  const files = import.meta.glob<Texts>('../locales/areas/*/*.json', {
    eager: true,
    import: 'default',
  });
  const areas: Record<string, Partial<Record<Lang, Texts>>> = {};
  for (const [path, texts] of Object.entries(files)) {
    const found = /\/areas\/([^/]+)\/(tr|en)\.json$/.exec(path);
    if (!found?.[1] || !found[2]) continue;
    areas[found[1]] = { ...areas[found[1]], [found[2]]: texts };
  }
  return areas;
})();

/** The locale file of `lang` with every area's texts added (areas in the order of their names). */
export function withAreas(base: Texts, lang: Lang): Texts {
  return Object.keys(LOCALE_AREAS)
    .sort()
    .reduce((merged, area) => addTexts(merged, LOCALE_AREAS[area]?.[lang] ?? {}), base);
}

const translation = { tr: withAreas(tr, 'tr'), en: withAreas(en, 'en') };

// The developer UI kit's texts (its page, its menu entry, the placeholders' pointer to it) are a
// namespace of their own, looked up for keys the locale files lack, and only where the kit is
// built in (`__UI_KIT__`, vite.config.ts): the shipped bundle has none of them.
const resources: Resource = __UI_KIT__
  ? {
      tr: { translation: translation.tr, uiKit: uiKitTr },
      en: { translation: translation.en, uiKit: uiKitEn },
    }
  : { tr: { translation: translation.tr }, en: { translation: translation.en } };

void i18n.use(initReactI18next).init({
  resources,
  lng: storedLanguage(),
  fallbackLng: 'tr',
  fallbackNS: __UI_KIT__ ? 'uiKit' : false,
  supportedLngs: LANGUAGES,
  interpolation: { escapeValue: false }, // React already escapes
  returnNull: false,
});

document.documentElement.lang = i18n.language;

export default i18n;
