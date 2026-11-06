import type { SettingOut } from '@/lib/settings-types';

import {
  dayParts,
  fromDraft,
  sameValue,
  settingControlId,
  toDraft,
  validHoliday,
  valueText,
  withServerBounds,
  type WorkDayDraft,
  type WorkWeek,
} from './setting-values';
import { integerError, validateClock, validateHost, validateSiteName } from './settings-validation';

function setting(
  overrides: Partial<SettingOut> & Pick<SettingOut, 'key' | 'type' | 'value'>,
): SettingOut {
  return {
    default: overrides.value,
    is_default: true,
    min: null,
    max: null,
    reauth: false,
    locked: false,
    label_tr: overrides.key,
    label_en: overrides.key,
    help_tr: '',
    help_en: '',
    updated_by: null,
    updated_at: null,
    ...overrides,
  };
}

describe('database settings as drafts and back (contract 2.13)', () => {
  it('numbers: typed as text, checked against the bounds of the API', () => {
    const limit = setting({
      key: 'alerts.email_max_per_hour',
      type: 'integer',
      value: 20,
      min: 1,
      max: 1000,
    });
    expect(toDraft(limit)).toBe('20');
    expect(fromDraft(limit, '30')).toEqual({ value: 30 });
    expect(fromDraft(limit, ' 30 ')).toEqual({ value: 30 });
    expect(fromDraft(limit, '0')).toEqual({ error: 'admin.errors.min' });
    expect(fromDraft(limit, '1001')).toEqual({ error: 'admin.errors.max' });
    expect(fromDraft(limit, '2.5')).toEqual({ error: 'admin.errors.integer' });
    // Without a lower bound an integer setting must be >= 0, like `validate_setting`.
    const free = setting({ key: 'usage.change_min_seconds', type: 'integer', value: 3600 });
    expect(fromDraft(free, '-1')).toEqual({ error: 'admin.errors.min' });
  });

  it('switches, texts and lists (one value per line, commas too)', () => {
    const flag = setting({ key: 'lookup.report_timestamp', type: 'boolean', value: false });
    expect(toDraft(flag)).toBe(false);
    expect(fromDraft(flag, true)).toEqual({ value: true });
    const text = setting({ key: 'alerts.web_base_url', type: 'string', value: '' });
    expect(fromDraft(text, '  https://127.0.0.1:8443 ')).toEqual({
      value: 'https://127.0.0.1:8443',
    });
    const domains = setting({
      key: 'usage.background_domains',
      type: 'string_list',
      value: ['update.example.com'],
    });
    expect(toDraft(domains)).toBe('update.example.com');
    expect(fromDraft(domains, 'update.example.com\nexample.org, cdn.example.net\n\n')).toEqual({
      value: ['update.example.com', 'example.org', 'cdn.example.net'],
    });
    const mails = setting({ key: 'alerts.email_recipients', type: 'string_list', value: [] });
    expect(fromDraft(mails, 'bt@ornek.example')).toEqual({ value: ['bt@ornek.example'] });
    expect(fromDraft(mails, 'bt@ornek')).toEqual({ error: 'validation.email' });
    const ports = setting({
      key: 'usage.background_ports',
      type: 'integer_list',
      value: [53, 123],
    });
    expect(toDraft(ports)).toBe('53\n123');
    expect(fromDraft(ports, '53\n853')).toEqual({ value: [53, 853] });
    expect(fromDraft(ports, '53\nport')).toEqual({
      error: 'settings.application.errors.integerList',
    });
  });

  it('work hours: a week of rows, back to the general hours and by_day (W5 P1)', () => {
    const hours = setting({
      key: 'usage.work_hours',
      type: 'object',
      value: { days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' },
    });
    const draft = toDraft(hours) as WorkWeek;
    expect(draft.week).toHaveLength(7);
    expect(draft.week[0]).toEqual({ work: true, start: '08:00', end: '18:00', breaks: [] });
    // A day off shows the general hours, ready for when it becomes a work day.
    expect(draft.week[5]).toEqual({ work: false, start: '08:00', end: '18:00', breaks: [] });
    // Unchanged: the stored three fields come back as they were.
    expect(fromDraft(hours, draft)).toEqual({
      value: { days: [1, 2, 3, 4, 5], start: '08:00', end: '18:00' },
    });
  });

  it('work hours: the hours most days share are the general ones, the rest by_day', () => {
    const hours = setting({
      key: 'usage.work_hours',
      type: 'object',
      value: {
        days: [1, 2, 3, 4, 5, 6],
        start: '08:00',
        end: '18:00',
        breaks: [{ start: '12:30', end: '13:30' }],
        by_day: { '6': { start: '09:00', end: '13:00' } },
      },
    });
    const draft = toDraft(hours) as WorkWeek;
    expect(draft.week[0]?.breaks).toEqual([{ start: '12:30', end: '13:30' }]);
    // A by_day entry without breaks has none (the general ones are not inherited).
    expect(draft.week[5]).toEqual({ work: true, start: '09:00', end: '13:00', breaks: [] });
    expect(fromDraft(hours, draft)).toEqual({ value: hours.value });
    // Friday ends at 15:00: its own entry; the breaks are sorted on save.
    const friday = {
      work: true,
      start: '08:00',
      end: '15:00',
      breaks: [
        { start: '12:30', end: '13:30' },
        { start: '10:00', end: '10:15' },
      ],
    };
    const edited = { week: draft.week.map((row, index) => (index === 4 ? friday : row)) };
    expect(fromDraft(hours, edited)).toEqual({
      value: {
        days: [1, 2, 3, 4, 5, 6],
        start: '08:00',
        end: '18:00',
        breaks: [{ start: '12:30', end: '13:30' }],
        by_day: {
          '5': {
            start: '08:00',
            end: '15:00',
            breaks: [
              { start: '10:00', end: '10:15' },
              { start: '12:30', end: '13:30' },
            ],
          },
          '6': { start: '09:00', end: '13:00' },
        },
      },
    });
    // A day put back to the general hours loses its by_day entry.
    const back = {
      week: draft.week.map((row, index) =>
        index === 5
          ? { ...row, start: '08:00', end: '18:00', breaks: [{ start: '12:30', end: '13:30' }] }
          : row,
      ),
    };
    expect(fromDraft(hours, back)).toEqual({
      value: {
        days: [1, 2, 3, 4, 5, 6],
        start: '08:00',
        end: '18:00',
        breaks: [{ start: '12:30', end: '13:30' }],
      },
    });
  });

  it('work hours: the checks of the server, naming the day', () => {
    const hours = setting({
      key: 'usage.work_hours',
      type: 'object',
      value: { days: [1], start: '08:00', end: '18:00' },
    });
    const week = (monday: Partial<WorkDayDraft>): WorkWeek => ({
      week: [1, 2, 3, 4, 5, 6, 7].map((day) => ({
        work: day === 1,
        start: '08:00',
        end: '18:00',
        breaks: [],
        ...(day === 1 ? monday : {}),
      })),
    });
    const day = { day: 1 };
    expect(fromDraft(hours, week({ work: false }))).toEqual({
      error: 'settings.application.errors.workDays',
    });
    expect(fromDraft(hours, week({ start: '8:00' }))).toEqual({
      error: 'settings.application.errors.workTime',
      params: day,
    });
    expect(fromDraft(hours, week({ start: '18:00', end: '08:00' }))).toEqual({
      error: 'settings.application.errors.workOrder',
      params: day,
    });
    const pause = (start: string, end: string) => ({ start, end });
    expect(fromDraft(hours, week({ breaks: [pause('12:00', '')] }))).toEqual({
      error: 'settings.application.errors.breakTime',
      params: day,
    });
    expect(fromDraft(hours, week({ breaks: [pause('12:00', '12:04')] }))).toEqual({
      error: 'settings.application.errors.breakShort',
      params: { day: 1, min: 5 },
    });
    expect(fromDraft(hours, week({ breaks: [pause('07:30', '08:30')] }))).toEqual({
      error: 'settings.application.errors.breakOutside',
      params: day,
    });
    expect(
      fromDraft(hours, week({ breaks: [pause('12:00', '13:00'), pause('12:30', '13:30')] })),
    ).toEqual({ error: 'settings.application.errors.breakOverlap', params: day });
    // Touching breaks are allowed, as on the server; a seventh is refused.
    expect(
      fromDraft(hours, week({ breaks: [pause('12:00', '12:30'), pause('12:30', '13:00')] })),
    ).toEqual({
      value: {
        days: [1],
        start: '08:00',
        end: '18:00',
        breaks: [pause('12:00', '12:30'), pause('12:30', '13:00')],
      },
    });
    const seven = Array.from({ length: 7 }, (_, at) => pause(`1${at}:00`, `1${at}:10`));
    expect(fromDraft(hours, week({ breaks: seven }))).toEqual({
      error: 'settings.application.errors.breakCount',
      params: { day: 1, max: 6 },
    });
  });

  it('work hours: the strip of a day and the one-line text', () => {
    const row = {
      work: true,
      start: '08:00',
      end: '18:00',
      breaks: [{ start: '12:00', end: '13:00' }],
    };
    expect(dayParts(row)).toEqual([
      { start: 480, end: 720, part: 'work' },
      { start: 720, end: 780, part: 'break' },
      { start: 780, end: 1080, part: 'work' },
    ]);
    expect(dayParts({ ...row, work: false })).toEqual([]);
    expect(dayParts({ ...row, end: '07:00' })).toEqual([]);
    expect(
      valueText({
        days: [1, 2, 3, 4, 5, 6],
        start: '08:00',
        end: '18:00',
        breaks: [{ start: '12:00', end: '13:00' }],
        by_day: { '6': { start: '09:00', end: '13:00' } },
      }),
    ).toBe('1,2,3,4,5,6 08:00–18:00 (12:00–13:00); 6: 09:00–13:00');
  });

  it('holidays: MM-DD every year or YYYY-MM-DD once, real dates only, sorted', () => {
    expect(validHoliday('10-29')).toBe(true);
    expect(validHoliday('02-29')).toBe(true);
    expect(validHoliday('2026-03-20')).toBe(true);
    expect(validHoliday('2026-02-29')).toBe(false);
    expect(validHoliday('13-01')).toBe(false);
    expect(validHoliday('1-1')).toBe(false);
    const holidays = setting({ key: 'usage.holidays', type: 'string_list', value: ['01-01'] });
    expect(toDraft(holidays)).toEqual(['01-01']);
    expect(fromDraft(holidays, ['10-29', '01-01', '2026-03-20', '01-01'])).toEqual({
      value: ['01-01', '10-29', '2026-03-20'],
    });
    expect(fromDraft(holidays, ['31-12'])).toEqual({
      error: 'settings.application.errors.holiday',
    });
  });

  it('W5-G-19: the bounds of the API refuse a value above them, a server row those of the default lease', () => {
    const session = setting({
      key: 'lookup.max_session_seconds',
      type: 'integer',
      value: 3600,
      min: 60,
      max: 172_800,
    });
    expect(fromDraft(session, '172800')).toEqual({ value: 172_800 });
    expect(fromDraft(session, '172801')).toEqual({ error: 'admin.errors.max' });
    const percent = setting({
      key: 'lease.timeout_percent',
      type: 'integer',
      value: 110,
      min: 100,
      max: 125,
    });
    expect(fromDraft(percent, '126')).toEqual({ error: 'admin.errors.max' });
    expect(fromDraft(percent, '99')).toEqual({ error: 'admin.errors.min' });
    const lease = setting({
      key: 'lease.default_seconds',
      type: 'integer',
      value: 86_400,
      min: 60,
      max: 604_800,
    });
    const perServer = setting({
      key: 'lease.server_seconds',
      type: 'object',
      value: { dhcp61: 3600 },
    });
    const [, servers] = withServerBounds([lease, perServer]);
    if (!servers) throw new Error('lease.server_seconds missing');
    expect(servers).toMatchObject({ key: 'lease.server_seconds', min: 60, max: 604_800 });
    expect(fromDraft(servers, [{ server: 'dhcp61', seconds: '604800' }])).toEqual({
      value: { dhcp61: 604_800 },
    });
    const refused = {
      error: 'settings.application.errors.serverSeconds',
      params: { min: 60, max: 604_800 },
    };
    expect(fromDraft(servers, [{ server: 'dhcp61', seconds: '604801' }])).toEqual(refused);
    expect(fromDraft(servers, [{ server: 'dhcp61', seconds: '59' }])).toEqual(refused);
    // A group without `lease.default_seconds` is taken as it is.
    expect(withServerBounds([perServer])).toEqual([perServer]);
    expect(withServerBounds([lease])).toEqual([lease]);
  });

  it('lease time per DHCP server and the two-factor roles', () => {
    const servers = setting({
      key: 'lease.server_seconds',
      type: 'object',
      value: { dhcp60: 3600 },
    });
    expect(toDraft(servers)).toEqual([{ server: 'dhcp60', seconds: '3600' }]);
    expect(fromDraft(servers, [{ server: ' dhcp70 ', seconds: '7200' }])).toEqual({
      value: { dhcp70: 7200 },
    });
    // Without the bounds of `lease.default_seconds` beside it, only the least time is known.
    expect(fromDraft(servers, [{ server: 'dhcp70', seconds: '30' }])).toEqual({
      error: 'settings.application.errors.serverSecondsMin',
      params: { min: 60 },
    });
    expect(fromDraft(servers, [{ server: '', seconds: '3600' }])).toEqual({
      error: 'settings.application.errors.serverName',
    });
    const roles = setting({
      key: 'auth.totp_required_roles',
      type: 'string_list',
      value: ['admin', 'auditor'],
    });
    expect(fromDraft(roles, ['admin', 'auditor', 'hr'])).toEqual({
      value: ['admin', 'auditor', 'hr'],
    });
    expect(fromDraft(roles, ['admin'])).toEqual({ error: 'settings.application.errors.totpRoles' });
  });

  it('any other object is edited as JSON', () => {
    const other = setting({ key: 'x.object', type: 'object', value: { a: 1 } });
    expect(fromDraft(other, '{"b": 2}')).toEqual({ value: { b: 2 } });
    expect(fromDraft(other, '[1]')).toEqual({ error: 'settings.application.errors.object' });
    expect(fromDraft(other, '{')).toEqual({ error: 'settings.application.errors.object' });
  });

  it('compares values regardless of key order and writes them as one line', () => {
    expect(sameValue({ start: '08:00', days: [1] }, { days: [1], start: '08:00' })).toBe(true);
    expect(sameValue([1, 2], [2, 1])).toBe(false);
    expect(valueText({ days: [1, 2], start: '08:00', end: '18:00' })).toBe('1,2 08:00–18:00');
    expect(valueText([])).toBe('—');
    expect(valueText({})).toBe('—');
    expect(valueText({ dhcp60: 3600 })).toBe('dhcp60: 3600');
    expect(settingControlId('usage.work_hours')).toBe('setting-usage-work-hours');
  });
});

describe('machine configuration checks in the browser (contract 2.6)', () => {
  it('site name: required, one line of at most 200 characters', () => {
    expect(validateSiteName('Örnek A.Ş.')).toBeUndefined();
    expect(validateSiteName('  ')).toBe('settings.general.errors.siteNameRequired');
    expect(validateSiteName('ş'.repeat(200))).toBeUndefined();
    expect(validateSiteName('ş'.repeat(201))).toBe('settings.general.errors.siteNameTooLong');
    expect(validateSiteName('Örnek‮A.Ş.')).toBe('validation.controlCharacters');
  });

  it('time servers: a host name or a dotted-quad IPv4 address, at least one of the two', () => {
    expect(validateHost('time.ume.tubitak.gov.tr', false)).toBeUndefined();
    expect(validateHost('172.20.10.1', false)).toBeUndefined();
    expect(validateHost('172.20.10.256', false)).toBe('admin.errors.hostInvalid');
    expect(validateHost('ntp server', false)).toBe('admin.errors.hostInvalid');
    expect(validateHost('', true)).toBeUndefined();
    expect(validateHost('', false)).toBe('validation.required');
    expect(validateClock({ ntp_server: '', fallback_server: '', alert_offset_ms: '1000' })).toEqual(
      {
        fallback_server: 'settings.general.errors.clockServersRequired',
      },
    );
    expect(
      validateClock({ ntp_server: '172.20.10.1', fallback_server: '', alert_offset_ms: '0' }),
    ).toEqual({
      alert_offset_ms: 'admin.errors.min',
    });
    expect(integerError('60001', 1, 60_000)).toBe('admin.errors.max');
  });
});
