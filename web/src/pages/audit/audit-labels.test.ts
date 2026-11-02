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

  it('labels every known action in both languages and offers the licence prefix', () => {
    for (const action of KNOWN_ACTIONS) {
      expect(i18n.exists(actionKey(action), { lng: 'tr' }), action).toBe(true);
      expect(i18n.exists(actionKey(action), { lng: 'en' }), action).toBe(true);
    }
    expect(ACTION_PREFIXES).toContain('license.');
  });
});
