import type { TFunction } from 'i18next';

import i18n from '@/lib/i18n';

import {
  addDays,
  categoryLabel,
  clampRange,
  dayCount,
  daysOf,
  drillTo,
  exportParams,
  hasSelection,
  isSubjectKey,
  monthEnd,
  presetOf,
  presetRange,
  rebuildRequest,
  shiftRange,
  siteCategory,
  tabOfSubject,
  today,
  UNKNOWN_CATEGORY,
  usageParams,
  viewFromSearch,
  viewToSearch,
  weekday,
  withTab,
  type UsageView,
} from './usage-model';

/** Tuesday 13.10.2026 (the E2E sample day). */
const TODAY = '2026-10-13';

function view(search: string): UsageView {
  return viewFromSearch(new URLSearchParams(search), TODAY);
}

describe('site days', () => {
  it('counts days without time zones', () => {
    expect(addDays('2026-10-13', 1)).toBe('2026-10-14');
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28');
    expect(addDays('2028-03-01', -1)).toBe('2028-02-29');
    expect(addDays('2026-12-31', 1)).toBe('2027-01-01');
    expect(dayCount({ from: '2026-10-01', to: '2026-10-13' })).toBe(13);
    expect(daysOf({ from: '2026-09-29', to: '2026-10-02' })).toEqual([
      '2026-09-29',
      '2026-09-30',
      '2026-10-01',
      '2026-10-02',
    ]);
    expect(monthEnd('2026-02-10')).toBe('2026-02-28');
    expect(monthEnd('2026-10-13')).toBe('2026-10-31');
  });

  it('numbers weekdays from Monday', () => {
    expect(weekday('2026-10-12')).toBe(1);
    expect(weekday('2026-10-13')).toBe(2);
    expect(weekday('2026-10-18')).toBe(7);
  });

  it('takes "today" from Europe/Istanbul, whatever the browser zone is', () => {
    // 22:30 UTC is already 01:30 the next morning in Türkiye (UTC+03:00).
    expect(today(new Date('2026-10-12T22:30:00Z'))).toBe('2026-10-13');
    expect(today(new Date('2026-10-12T20:59:59Z'))).toBe('2026-10-12');
    expect(today(new Date('2026-12-31T21:00:00Z'))).toBe('2027-01-01');
  });
});

describe('period presets', () => {
  it('resolves every preset against the site day', () => {
    expect(presetRange('today', TODAY)).toEqual({ from: TODAY, to: TODAY });
    expect(presetRange('yesterday', TODAY)).toEqual({ from: '2026-10-12', to: '2026-10-12' });
    // Weeks start on Monday in Türkiye.
    expect(presetRange('thisWeek', TODAY)).toEqual({ from: '2026-10-12', to: TODAY });
    expect(presetRange('lastWeek', TODAY)).toEqual({ from: '2026-10-05', to: '2026-10-11' });
    expect(presetRange('thisMonth', TODAY)).toEqual({ from: '2026-10-01', to: TODAY });
    expect(presetRange('lastMonth', TODAY)).toEqual({ from: '2026-09-01', to: '2026-09-30' });
  });

  it('handles a Sunday, the first of a month and January', () => {
    expect(presetRange('thisWeek', '2026-10-18')).toEqual({ from: '2026-10-12', to: '2026-10-18' });
    expect(presetRange('lastWeek', '2026-10-18')).toEqual({ from: '2026-10-05', to: '2026-10-11' });
    expect(presetRange('thisMonth', '2026-10-01')).toEqual({
      from: '2026-10-01',
      to: '2026-10-01',
    });
    expect(presetRange('lastMonth', '2027-01-15')).toEqual({
      from: '2026-12-01',
      to: '2026-12-31',
    });
    expect(presetRange('lastMonth', '2028-03-31')).toEqual({
      from: '2028-02-01',
      to: '2028-02-29',
    });
  });

  it('names the preset of a range (first match), null for a custom one', () => {
    expect(presetOf({ from: TODAY, to: TODAY }, TODAY)).toBe('today');
    expect(presetOf({ from: '2026-10-12', to: '2026-10-12' }, TODAY)).toBe('yesterday');
    expect(presetOf({ from: '2026-10-12', to: TODAY }, TODAY)).toBe('thisWeek');
    expect(presetOf({ from: '2026-09-01', to: '2026-09-30' }, TODAY)).toBe('lastMonth');
    expect(presetOf({ from: '2026-10-02', to: '2026-10-09' }, TODAY)).toBeNull();
    // On a Monday "this week" and "today" are the same days: "Bugün" wins.
    expect(presetOf({ from: '2026-10-12', to: '2026-10-12' }, '2026-10-12')).toBe('today');
  });

  it('orders a range and keeps it out of the future', () => {
    expect(clampRange({ from: '2026-10-10', to: '2026-10-01' }, TODAY)).toEqual({
      from: '2026-10-01',
      to: '2026-10-10',
    });
    expect(clampRange({ from: '2026-10-10', to: '2026-10-20' }, TODAY)).toEqual({
      from: '2026-10-10',
      to: TODAY,
    });
    expect(clampRange({ from: '2026-11-01', to: '2026-11-02' }, TODAY)).toEqual({
      from: TODAY,
      to: TODAY,
    });
  });

  it('moves a period back and forth by its length, months by calendar months', () => {
    expect(shiftRange({ from: TODAY, to: TODAY }, -1, TODAY)).toEqual({
      from: '2026-10-12',
      to: '2026-10-12',
    });
    expect(shiftRange({ from: TODAY, to: TODAY }, 1, TODAY)).toBeNull();
    expect(shiftRange({ from: '2026-10-05', to: '2026-10-11' }, 1, TODAY)).toEqual({
      from: '2026-10-12',
      to: TODAY,
    });
    expect(shiftRange({ from: '2026-09-01', to: '2026-09-30' }, -1, TODAY)).toEqual({
      from: '2026-08-01',
      to: '2026-08-31',
    });
    expect(shiftRange({ from: '2026-10-01', to: TODAY }, -1, TODAY)).toEqual({
      from: '2026-09-01',
      to: '2026-09-30',
    });
    expect(shiftRange({ from: '2026-09-01', to: '2026-09-30' }, 1, TODAY)).toEqual({
      from: '2026-10-01',
      to: TODAY,
    });
    // "Bugün" on the first of a month moves by one day.
    expect(shiftRange({ from: '2026-10-01', to: '2026-10-01' }, -1, '2026-10-01')).toEqual({
      from: '2026-09-30',
      to: '2026-09-30',
    });
  });
});

describe('the address', () => {
  it('defaults to the company tab, today and every hour', () => {
    expect(view('')).toEqual({
      tab: 'company',
      from: TODAY,
      to: TODAY,
      hours: 'all',
      department: null,
      subject: null,
      site: null,
      category: null,
    });
    expect(viewToSearch(view(''), TODAY).toString()).toBe('');
  });

  it('round-trips tab, period, hours and the selection', () => {
    const search = 'tab=persons&from=2026-10-05&to=2026-10-11&hours=off&subject=person%3A12';
    const parsed = view(search);
    expect(parsed).toMatchObject({
      tab: 'persons',
      from: '2026-10-05',
      to: '2026-10-11',
      hours: 'off',
      subject: 'person:12',
    });
    expect(viewToSearch(parsed, TODAY).toString()).toBe(search);
    expect(view('tab=departments&department=%C4%B0dari+%C4%B0%C5%9Fler').department).toBe(
      'İdari İşler',
    );
    // A FortiGuard service keeps its case: the API matches a site's name exactly.
    expect(view('tab=sites&site=Microsoft-Azure').site).toBe('Microsoft-Azure');
    expect(view('tab=sites&site=youtube.com').site).toBe('youtube.com');
    expect(view('tab=categories&category=Video').category).toBe('Video');
  });

  it('falls back on anything unknown or malformed', () => {
    expect(view('tab=reports&hours=night&from=13.10.2026&to=2026-02-30')).toMatchObject({
      tab: 'company',
      hours: 'all',
      from: TODAY,
      to: TODAY,
    });
    // A selection only counts on its own tab; control characters are refused.
    expect(view('tab=company&subject=person%3A1&site=a.com').subject).toBeNull();
    expect(view('tab=sites&site=a%E2%80%AEcom').site).toBeNull();
    expect(view('tab=persons&subject=../../etc').subject).toBeNull();
    // Only one day given: a single-day period; a reversed range is ordered; the future is cut.
    expect(view('from=2026-10-05')).toMatchObject({ from: '2026-10-05', to: '2026-10-05' });
    expect(view('from=2026-10-11&to=2026-10-05')).toMatchObject({
      from: '2026-10-05',
      to: '2026-10-11',
    });
    expect(view('from=2026-10-11&to=2027-01-01')).toMatchObject({ to: TODAY });
  });

  it('opens a subject on the tab of its kind', () => {
    expect(view('tab=persons&subject=device%3Aaa%3Abb%3Acc%3A00%3A00%3A01')).toMatchObject({
      tab: 'devices',
      subject: 'device:aa:bb:cc:00:00:01',
    });
    expect(view('tab=devices&subject=p%3AKullan%C4%B1c%C4%B1-7F3A').tab).toBe('persons');
    expect(tabOfSubject('ip:172.20.43.10')).toBe('devices');
    expect(isSubjectKey('person:12')).toBe(true);
    expect(isSubjectKey('d:Cihaz-1A2B')).toBe(true);
    expect(isSubjectKey('user:12')).toBe(false);
    expect(isSubjectKey('person:')).toBe(false);
    expect(isSubjectKey('person:1 2')).toBe(false);
  });

  it('drills down keeping period and hours, and returns to the lists', () => {
    const start = view('from=2026-10-05&to=2026-10-11&hours=work');
    const department = drillTo(start, { department: 'Finans' });
    expect(department).toMatchObject({
      tab: 'departments',
      department: 'Finans',
      from: '2026-10-05',
      hours: 'work',
    });
    const person = drillTo(department, { subject: 'person:1' });
    expect(person).toMatchObject({ tab: 'persons', subject: 'person:1', department: null });
    const site = drillTo(person, { site: 'Microsoft-Azure' });
    expect(site).toMatchObject({ tab: 'sites', site: 'Microsoft-Azure', subject: null });
    expect(drillTo(site, { category: 'Video' })).toMatchObject({
      tab: 'categories',
      category: 'Video',
      site: null,
    });
    expect(hasSelection(site)).toBe(true);
    expect(withTab(site, 'sites')).toMatchObject({ tab: 'sites', site: null });
    expect(hasSelection(withTab(site, 'sites'))).toBe(false);
    expect(viewToSearch(person, TODAY).toString()).toBe(
      'tab=persons&from=2026-10-05&to=2026-10-11&hours=work&subject=person%3A1',
    );
    expect(viewToSearch(site, TODAY).get('site')).toBe('Microsoft-Azure');
  });

  it('names the unknown category like the API in every language', () => {
    expect(siteCategory(null)).toBe('Bilinmiyor');
    expect(siteCategory(null)).toBe(UNKNOWN_CATEGORY);
    expect(siteCategory('Video')).toBe('Video');
  });

  it('shows the unknown category in the UI language, other categories as named', () => {
    const tr: TFunction = i18n.getFixedT('tr');
    const en: TFunction = i18n.getFixedT('en');
    expect(categoryLabel(null, en)).toBe('Unknown');
    expect(categoryLabel(UNKNOWN_CATEGORY, en)).toBe('Unknown');
    expect(categoryLabel(UNKNOWN_CATEGORY, tr)).toBe('Bilinmiyor');
    expect(categoryLabel('Video', en)).toBe('Video');
  });

  it('gives the API and the export their parameters', () => {
    const parsed = view('tab=sites&from=2026-10-05&to=2026-10-11&hours=work');
    expect(usageParams(parsed)).toEqual({ from: '2026-10-05', to: '2026-10-11', hours: 'work' });
    expect(
      exportParams(
        'subjects',
        { ...usageParams(parsed), department: 'Finans' },
        { kind: 'person', q: undefined },
      ),
    ).toEqual({
      view: 'subjects',
      from: '2026-10-05',
      to: '2026-10-11',
      hours: 'work',
      department: 'Finans',
      kind: 'person',
      q: undefined,
    });
  });
});

describe('Yeniden hesapla', () => {
  it('takes Turkish or ISO days and refuses future, unreadable or reversed ones', () => {
    expect(rebuildRequest({ from: '01.10.2026', to: '2026-10-13' }, TODAY)).toEqual({
      body: { from: '2026-10-01', to: '2026-10-13' },
      errors: {},
    });
    expect(rebuildRequest({ from: '31.02.2026', to: '' }, TODAY).errors).toEqual({
      from: 'usage.rebuild.invalidDay',
      to: 'usage.rebuild.invalidDay',
    });
    expect(rebuildRequest({ from: '14.10.2026', to: '14.10.2026' }, TODAY)).toEqual({
      body: null,
      errors: { from: 'usage.rebuild.future', to: 'usage.rebuild.future' },
    });
    expect(rebuildRequest({ from: '10.10.2026', to: '09.10.2026' }, TODAY)).toEqual({
      body: null,
      errors: { to: 'usage.rebuild.order' },
    });
  });
});
