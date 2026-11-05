import i18n from '@/lib/i18n';

import { ACTION_PREFIXES, actionKey, KNOWN_ACTIONS } from './audit-labels';

/** The licence audit actions and their published labels (licensing contract 6.7). */
const LICENSE_LABELS: readonly [string, string, string][] = [
  ['license.trial_start', 'Deneme başladı', 'Trial started'],
  ['license.install', 'Lisans yüklendi', 'Licence installed'],
  ['license.remove', 'Lisans kaldırıldı', 'Licence removed'],
  ['license.refuse', 'Lisans reddedildi', 'Licence refused'],
  ['license.limit_refused', 'Lisans sınırı: kullanıcı eklenemedi', 'Licence limit: user not added'],
  ['license.limit_exceeded', 'Lisans sınırı aşıldı', 'Licence limit exceeded'],
  ['license.limit_ok', 'Lisans sınırı içinde', 'Back within licence limit'],
  ['license.state', 'Lisans durumu değişti', 'Licence state changed'],
];

/** The search and live tail actions (Wave 2 contract 2.21) and their labels. */
const SEARCH_LABELS: readonly [string, string, string][] = [
  ['search.run', 'Arama yapıldı', 'Search run'],
  ['search.event', 'Arama kaydı açıldı', 'Search record opened'],
  [
    'search.refs',
    'Arama satırları kanıt paketine aktarıldı',
    'Search lines taken to an evidence package',
  ],
  ['search.export', 'Arama dışa aktarıldı', 'Search exported'],
  ['search.saved_create', 'Kayıtlı arama oluşturuldu', 'Saved search created'],
  ['search.saved_update', 'Kayıtlı arama değiştirildi', 'Saved search changed'],
  ['search.saved_delete', 'Kayıtlı arama silindi', 'Saved search deleted'],
  ['live.open', 'Canlı akış açıldı', 'Live tail opened'],
  ['live.close', 'Canlı akış kapandı', 'Live tail closed'],
  ['raw.tail', 'Kaynağın son satırları görüntülendi', "A source's latest lines viewed"],
];

/**
 * The database window's actions (Wave 5 contract G3, G6): a step of the worker job drops database
 * rows older than the window, never a sealed original in the vault.
 */
const DB_WINDOW_LABELS: readonly [string, string, string][] = [
  ['archive.reload', 'Arşivden yeniden yükleme istendi', 'Archive reload requested'],
  [
    'db.window',
    'Veritabanı penceresi: eski günler veritabanından kaldırıldı',
    'Database window: older days dropped from the database',
  ],
];

describe('audit action labels', () => {
  it.each(SEARCH_LABELS)('%s', (action, turkish, english) => {
    expect(KNOWN_ACTIONS).toContain(action);
    expect(i18n.getFixedT('tr')(actionKey(action))).toBe(turkish);
    expect(i18n.getFixedT('en')(actionKey(action))).toBe(english);
  });

  it('offers the search and live tail prefixes', () => {
    expect(ACTION_PREFIXES).toEqual(expect.arrayContaining(['search.', 'live.', 'raw.']));
  });

  it.each(LICENSE_LABELS)('%s', (action, turkish, english) => {
    expect(KNOWN_ACTIONS).toContain(action);
    expect(i18n.getFixedT('tr')(actionKey(action))).toBe(turkish);
    expect(i18n.getFixedT('en')(actionKey(action))).toBe(english);
  });

  it.each(DB_WINDOW_LABELS)('%s', (action, turkish, english) => {
    expect(KNOWN_ACTIONS).toContain(action);
    expect(i18n.getFixedT('tr')(actionKey(action))).toBe(turkish);
    expect(i18n.getFixedT('en')(actionKey(action))).toBe(english);
  });

  it('offers the archive reload and database window prefixes', () => {
    expect(ACTION_PREFIXES).toEqual(expect.arrayContaining(['archive.', 'db.']));
  });

  it('labels every known action in both languages and offers the licence prefix', () => {
    for (const action of KNOWN_ACTIONS) {
      expect(i18n.exists(actionKey(action), { lng: 'tr' }), action).toBe(true);
      expect(i18n.exists(actionKey(action), { lng: 'en' }), action).toBe(true);
    }
    expect(ACTION_PREFIXES).toContain('license.');
  });
});
