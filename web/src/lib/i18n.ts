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

// The developer UI kit's texts (its page, its menu entry, the placeholders' pointer to it) are a
// namespace of their own, looked up for keys the locale files lack, and only where the kit is
// built in (`__UI_KIT__`, vite.config.ts): the shipped bundle has none of them.
const resources: Resource = __UI_KIT__
  ? { tr: { translation: tr, uiKit: uiKitTr }, en: { translation: en, uiKit: uiKitEn } }
  : { tr: { translation: tr }, en: { translation: en } };

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
