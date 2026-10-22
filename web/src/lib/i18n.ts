import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';

import en from '@/locales/en.json';
import tr from '@/locales/tr.json';

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

void i18n.use(initReactI18next).init({
  resources: { tr: { translation: tr }, en: { translation: en } },
  lng: storedLanguage(),
  fallbackLng: 'tr',
  supportedLngs: LANGUAGES,
  interpolation: { escapeValue: false }, // React already escapes
  returnNull: false,
});

document.documentElement.lang = i18n.language;

export default i18n;
