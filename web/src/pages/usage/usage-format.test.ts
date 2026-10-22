import type { TFunction } from 'i18next';

import type { CategoryRow, Metrics, SubjectHour } from '@/lib/api-types';
import i18n from '@/lib/i18n';

import {
  addressOf,
  apiFold,
  cappedHint,
  computedUntilText,
  dativeSuffix,
  dayTotals,
  donutSlices,
  fold,
  hourLabel,
  matchesApiSearch,
  matchesSearch,
  perDay,
  shareText,
  sumMetrics,
} from './usage-format';

const NONE: Metrics = {
  online_seconds: 0,
  active_seconds: 0,
  download_bytes: 0,
  upload_bytes: 0,
  sessions: 0,
  blocked: 0,
  off_hours_active_seconds: 0,
  first_activity: null,
  last_activity: null,
};

function hour(iso: string, active: number, top: string | null, download = 0): SubjectHour {
  return {
    hour: iso,
    active_seconds: active,
    online_seconds: active * 2,
    download_bytes: download,
    upload_bytes: 10,
    top_domain: top,
  };
}

describe('the Turkish dative after a clock time', () => {
  it.each([
    ['14:35', "'e"], // otuz beşe
    ['14:30', "'a"], // otuza
    ['14:20', "'ye"], // yirmiye
    ['14:40', "'a"], // kırka
    ['14:50', "'ye"], // elliye
    ['14:10', "'a"], // ona
    ['14:01', "'e"], // bire
    ['14:02', "'ye"], // ikiye
    ['14:03', "'e"], // üçe
    ['14:04', "'e"], // dörde
    ['14:06', "'ya"], // altıya
    ['14:07', "'ye"], // yediye
    ['14:08', "'e"], // sekize
    ['14:09', "'a"], // dokuza
    ['15:00', "'e"], // on beşe
    ['16:00', "'ya"], // on altıya
    ['20:00', "'ye"], // yirmiye
    ['10:00', "'a"], // ona
    ['00:00', "'a"], // sıfıra
  ])('%s → %s', (clock, suffix) => {
    expect(dativeSuffix(clock)).toBe(suffix);
  });
});

describe('texts', () => {
  const tr: TFunction = i18n.getFixedT('tr');
  const en: TFunction = i18n.getFixedT('en');

  it('says how far the data is processed, in site time', () => {
    // 11:35 UTC = 14:35 in Türkiye.
    expect(computedUntilText('2026-10-13T11:35:00Z', '2026-10-13', tr)).toBe(
      "Veriler 14:35'e kadar işlendi",
    );
    expect(computedUntilText('2026-10-12T20:55:00Z', '2026-10-13', tr)).toBe(
      "Veriler 12.10.2026 23:55'e kadar işlendi",
    );
    expect(computedUntilText('2026-10-13T11:30:00Z', '2026-10-13', en)).toBe(
      'Data processed up to 14:30',
    );
    expect(computedUntilText('2026-10-12T21:00:00Z', '2026-10-13', tr)).toBe(
      'Veriler 12.10.2026 sonuna kadar işlendi',
    );
    expect(computedUntilText('2026-10-12T21:00:00Z', '2026-10-13', en)).toBe(
      'Data processed up to the end of 12.10.2026',
    );
    expect(computedUntilText(null, '2026-10-13', tr)).toBe('Kullanım verileri henüz hesaplanmadı');
  });

  it('formats shares, hours and addresses', () => {
    expect(shareText(0.125, 'tr')).toBe('12,5 %');
    expect(shareText(1, 'en')).toBe('100.0 %');
    expect(shareText(-0.1, 'tr')).toBe('0,0 %');
    expect(hourLabel('2026-10-13T06:00:00Z', true)).toBe('09:00');
    expect(hourLabel('2026-10-13T06:00:00Z', false)).toBe('13.10 09:00');
    expect(
      addressOf({
        kind: 'device',
        key: 'device:00:00:5e:00:53:57',
        label: 'PC',
        pseudonymous: false,
      }),
    ).toBe('00:00:5e:00:53:57');
    expect(addressOf({ kind: 'ip', key: 'ip:172.20.43.10', label: 'x', pseudonymous: false })).toBe(
      '172.20.43.10',
    );
    expect(
      addressOf({ kind: 'person', key: 'person:1', label: 'Selin Kaya', pseudonymous: false }),
    ).toBeNull();
    // The API's own address fields come first; a pseudonym has none.
    expect(
      addressOf({
        kind: 'device',
        key: 'device:3c:52:82:00:10:02',
        label: 'PC-02',
        pseudonymous: false,
        mac: '3c:52:82:00:10:02',
        ip: '172.20.43.81',
        hostname: 'PC-02',
      }),
    ).toBe('3c:52:82:00:10:02');
    expect(
      addressOf({
        kind: 'ip',
        key: 'ip:172.20.41.77',
        label: '172.20.41.77',
        pseudonymous: false,
        mac: null,
        ip: '172.20.41.77',
      }),
    ).toBe('172.20.41.77');
    expect(
      addressOf({ kind: 'device', key: 'd:7cb9', label: 'Cihaz-7CB9', pseudonymous: true }),
    ).toBeNull();
  });

  it('says when a detail list shows fewer rows than there are', () => {
    expect(cappedHint('Kişi veya cihaz başına (en fazla 200)', 12, 12, tr, 'tr')).toBe(
      'Kişi veya cihaz başına (en fazla 200)',
    );
    expect(cappedHint('Kişi veya cihaz başına (en fazla 200)', 200, 1260, tr, 'tr')).toBe(
      'Kişi veya cihaz başına (en fazla 200). 1.260 satırdan en çok süresi olan 200 tanesi ' +
        'gösteriliyor; dışa aktarılan dosyada hepsi var.',
    );
    expect(cappedHint('Per person or device (at most 200)', 200, 1260, en, 'en')).toBe(
      'Per person or device (at most 200). Showing the 200 with the most time of 1,260; the ' +
        'exported file has all of them.',
    );
  });
});

describe('category donut', () => {
  const row = (category: string, hours: number): CategoryRow => ({
    category,
    seconds: hours * 3600,
    share: hours / 100,
    download_bytes: 0,
    upload_bytes: 0,
    users: 1,
  });

  it('six slices; "Diğer" is the rest of the active time, also beyond the ten sent', () => {
    // 100 h of active time; the API sends the ten largest categories (90 h).
    const hours = [18, 15, 12, 10, 9, 8, 6, 5, 4, 3];
    const slices = donutSlices(
      hours.map((value, index) => row(`Kategori ${index + 1}`, value)),
      'Diğer',
      100 * 3600,
    );
    expect(slices).toHaveLength(7);
    expect(slices.at(-1)).toEqual({ label: 'Diğer', value: 28 * 3600, other: true });
    expect(slices.reduce((sum, slice) => sum + slice.value, 0)).toBe(100 * 3600);
  });

  it('no "Diğer" for six categories or fewer', () => {
    const slices = donutSlices([row('Video', 2), row('Haber', 5)], 'Diğer', 7 * 3600);
    expect(slices.map((slice) => slice.label)).toEqual(['Haber', 'Video']);
  });
});

describe('per-day table', () => {
  it("sums the hours per site day and names the day's top domain by active time", () => {
    const rows = perDay([
      // 23:00 on the 12th in Türkiye (20:00 UTC) belongs to the 12th.
      hour('2026-10-12T20:00:00Z', 600, 'youtube.com', 100),
      // 00:00 on the 13th (21:00 UTC on the 12th) belongs to the 13th.
      hour('2026-10-12T21:00:00Z', 300, 'google.com', 50),
      hour('2026-10-13T06:00:00Z', 1200, 'hurriyet.com.tr', 70),
      hour('2026-10-13T07:00:00Z', 1000, 'google.com', 30),
      hour('2026-10-13T08:00:00Z', 0, null),
    ]);
    expect(rows.map((row) => row.day)).toEqual(['2026-10-12', '2026-10-13']);
    expect(rows[0]).toMatchObject({
      active_seconds: 600,
      download_bytes: 100,
      top_domain: 'youtube.com',
    });
    // google.com: 300 + 1000 s as top domain beats hurriyet.com.tr's 1200 s.
    expect(rows[1]).toMatchObject({
      active_seconds: 2500,
      online_seconds: 5000,
      download_bytes: 150,
      upload_bytes: 40,
      top_domain: 'google.com',
    });
    expect(dayTotals(rows)).toEqual({
      active_seconds: 3100,
      online_seconds: 6200,
      download_bytes: 250,
      upload_bytes: 50,
    });
  });
});

describe('metrics', () => {
  it('adds metrics up (earliest first, latest last activity)', () => {
    const sum = sumMetrics([
      {
        ...NONE,
        active_seconds: 60,
        sessions: 2,
        first_activity: '2026-10-13T06:00:00Z',
        last_activity: '2026-10-13T10:00:00Z',
      },
      {
        ...NONE,
        active_seconds: 120,
        blocked: 3,
        first_activity: '2026-10-13T05:00:00Z',
        last_activity: '2026-10-13T09:00:00Z',
      },
      NONE,
    ]);
    expect(sum).toMatchObject({
      active_seconds: 180,
      sessions: 2,
      blocked: 3,
      first_activity: '2026-10-13T05:00:00Z',
      last_activity: '2026-10-13T10:00:00Z',
    });
    expect(sumMetrics([])).toEqual(NONE);
  });

  it('searches with Turkish letters folded, every word somewhere', () => {
    expect(fold('İŞLER Çağrı')).toBe('isler cagri');
    expect(matchesSearch('idari selin', ['Selin Kaya', 'İdari İşler'])).toBe(true);
    expect(matchesSearch('isler', ['İdari İşler'])).toBe(true);
    expect(matchesSearch('kaya youtube', ['Selin Kaya', 'google.com'])).toBe(false);
    expect(matchesSearch('  ', ['x'])).toBe(true);
    expect(matchesSearch('x', [null, undefined])).toBe(false);
  });

  it("filters like the API's q: Turkish lower case, the whole text in one field", () => {
    // I, İ and ı all become i, as in the API.
    expect(apiFold('AYŞE Işık İNCİ')).toBe('ayşe işik inci');
    expect(matchesApiSearch('ayşe', ['Ayşe Yılmaz'])).toBe(true);
    // Not folded further than the API: "ayse" does not find "Ayşe".
    expect(matchesApiSearch('ayse', ['Ayşe Yılmaz'])).toBe(false);
    expect(matchesApiSearch(' 10:02 ', ['PC-02', '3c:52:82:00:10:02'])).toBe(true);
    expect(matchesApiSearch('yılmaz ayşe', ['Ayşe Yılmaz'])).toBe(false);
    expect(matchesApiSearch('', [null])).toBe(true);
    expect(matchesApiSearch('x', [null, undefined])).toBe(false);
  });
});
